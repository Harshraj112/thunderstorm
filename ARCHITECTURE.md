# PRAMAAN architecture

This document explains how PRAMAAN is built: the components, the path a file takes from upload to signed report,
every analyzer, how findings become a verdict, and how the record is protected from tampering.

---

## 1. Design principles

1. **Integrity and authenticity are separate questions.** Hashes and the custody ledger prove a file has not changed
   since intake; forensic analysis examines whether its content is genuine. A valid hash is never presented as proof
   of authenticity.
2. **Explain, don't score.** Every finding states what was found, where, how much it weighs, and what innocent causes
   could explain it. There is no bare "87 % fake".
3. **Abstain rather than guess.** When evidence is thin the verdict is *Inconclusive*, with the reason and what would
   settle it.
4. **The original is never touched.** Analysis runs on copies; the original is re-hashed afterwards.
5. **Offline by default.** No evidence leaves the machine. Models are downloaded once at setup.
6. **Pluggable detectors.** Deep-learning models are optional plug-ins; a missing one is reported, never assumed.

---

## 2. Components

```
┌────────────────────────── Browser ──────────────────────────┐
│  Next.js dashboard (static export, prebuilt in web_dist/)   │
│  Demo kit · Cases · Exhibit workbench · Case log            │
└───────────────▲─────────────────────────────────────────────┘
                │ HTTP/JSON (same origin)
┌───────────────┴──────────── pramaan.api.server ─────────────┐
│ Starlette ASGI app (uvicorn) · optional password / API key  │
└──────┬──────────────┬───────────────┬───────────────┬───────┘
       │              │               │               │
┌──────▼─────┐ ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
│ pipeline   │ │ custody     │ │ report      │ │ caselog     │
│ intake,    │ │ hashing,    │ │ PDF EN+HI,  │ │ append-only │
│ job queue, │ │ ledger,     │ │ narration,  │ │ hash-chained│
│ analyzers, │ │ Ed25519     │ │ signature,  │ │ case history│
│ fusion     │ │ signing     │ │ QR verify   │ │             │
└──────┬─────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
       └──────────────┴───────┬───────┴───────────────┘
                     ┌────────▼────────┐   ┌──────────────────┐
                     │ SQLite (WAL)    │   │ File store       │
                     │ cases, evidence,│   │ evidence/ (0444) │
                     │ jobs, findings, │   │ work/ artifacts/ │
                     │ verdicts, ledger│   │ reports/ keys/   │
                     └─────────────────┘   └──────────────────┘
```

| Module | Responsibility |
|---|---|
| `pramaan/api/server.py` | REST API, dashboard hosting, password/API-key middleware, start-up recovery of interrupted jobs |
| `pramaan/pipeline.py` | Intake (hash, store), modality detection by file signature, job queue (`ThreadPoolExecutor`), analyzer routing, persistence |
| `pramaan/fusion.py` | Converts findings into a verdict, coverage, abstention reason and "what was found" labels |
| `pramaan/custody/` | `hashing.py` (SHA-256/SHA-1/MD5), `ledger.py` (hash chain, Merkle anchors), `signing.py` (Ed25519 lab key) |
| `pramaan/caselog.py` | Append-only case history; snapshot on deletion |
| `pramaan/report/` | `pdf.py` (ReportLab, bilingual), `hindi.py` (Devanagari shaping), `narrator.py` (plain-language summary) |
| `pramaan/analyzers/` | All forensic tests (section 4) |
| `frontend/` | Next.js 15 / React 19 dashboard source |

---

## 3. Life of an exhibit

```
upload ─▶ 1 RECEIVE ─▶ 2 IDENTIFY ─▶ 3 EXAMINE ─▶ 4 WEIGH ─▶ 5 REVIEW ─▶ 6 REPORT
             │             │              │            │           │          │
             └─────────────┴──── every step is a custody-ledger entry ───────┘
```

1. **Receive** (`pipeline.ingest`): the upload is streamed to disk, hashed with SHA-256, SHA-1 and MD5, and stored
   under its SHA-256 name with read-only permissions. Ledger entries `INGESTED` and `HASH_COMPUTED`; case-log entry
   `EVIDENCE_ADDED`.
2. **Identify** (`pipeline.detect_modality`): the real type is taken from the file's signature bytes
   (`analyzers/filetype.sniff`), not its extension, and routes the file to the image, video, audio or document chain.
3. **Examine** (`pipeline.run_job`): a working copy is made (named with the *real* extension); each analyzer runs in
   turn, and one failing analyzer never stops the others. Progress is written to the job row for the dashboard.
4. **Weigh** (`fusion.fuse`): findings are combined into a verdict (section 5).
5. **Review**: the examiner reads the findings, forensic maps and timelines in the dashboard.
6. **Report** (`report/pdf.build`): a signed PDF; ledger entry `REPORT_SIGNED`.

After analysis the original is re-hashed (`HASH_VERIFIED` or `HASH_MISMATCH`). If a related exhibit (a copy or an
original of this one) was analysed earlier, it is re-queued so it also receives the comparison.

---

## 4. Analyzers

Every analyzer returns `Finding` objects:
`code, family, title, status (pass|info|warn|fail|unavailable), llr, reliability, explanation, innocent[], location, artifacts[], data`.

### 4.1 Common to all files

| Analyzer | File | What it checks |
|---|---|---|
| File signature | `analyzers/filetype.py` | Magic bytes vs extension → `FILE_EXT_MISMATCH`; 0-byte files → `FILE_EMPTY`; HEIC without decoder → clear message |
| Content Credentials | `analyzers/provenance/` | Offline C2PA reader: finds the JUMBF manifest (JPEG APP11, PNG `caBX`, MP3 `GEOB`, WAV, MP4), decodes CBOR claims and assertions, verifies the COSE signature with the embedded certificate, re-hashes the file against `c2pa.hash.data`. Reports generator, signer, IPTC digital source type (e.g. `trainedAlgorithmicMedia` = AI) |
| Metadata table | `analyzers/metadata/standard.py` | The 15 PS-05 fields from EXIF, XMP, PNG text, container tags, Office/PDF properties, plus all additional fields |

### 4.2 Images

| Analyzer | File | What it detects |
|---|---|---|
| Metadata rules | `metadata/rules.py` | Editing software, XMP edit history, timestamp chain, GPS null island / region, GPS time vs camera time, sun elevation vs brightness, thumbnail mismatch, EXIF size vs pixels, missing MakerNote; **forgery rules**: camera fields typed into PNG text, unknown camera maker, transparency in a "camera" photo, FFmpeg-encoded "camera" JPEG, export/processing software, AI-native pixel sizes, screenshot signature |
| Copy vs original | `analyzers/derivative.py` | 32×32 brightness signature finds the same picture among other exhibits in the case; decides which is later (Content Credentials removed, alpha added, metadata added, enlarged, JPEG 8×8 grid in a lossless file) and lists changed fields |
| JPEG | `image/jpeg.py` | Quantisation tables (camera vs editor), JPEG ghosts (local double compression) |
| Noise | `image/noise.py` | Local sensor-noise inconsistency (Immerkaer estimator) |
| Copy-move | `image/copymove.py` | SIFT keypoint self-matching + RANSAC; several clusters at different offsets = repeated design, not a clone |
| ELA | `image/ela.py` | Error Level Analysis, edge-normalised (weak evidence by design) |
| TruFor | `image/dl_plugins.py` | Pixel-level forgery map and integrity score (CVPR 2023); runs one image at a time, downscaled to `PRAMAAN_TRUFOR_MAX_SIDE`, with a timeout |
| CAT-Net | `image/dl_plugins.py` | Optional JPEG-splice localiser (plug-in) |
| AI-image classifier | `image/dl_plugins.py` | Hugging Face SigLIP-based classifier, averaged over the full image and two crops |
| PRNU | `image/prnu.py` | Camera fingerprint match against registered devices (PCE) |

### 4.3 CCTV and video

| Analyzer | File | What it detects |
|---|---|---|
| Container | `video/probe.py` | Frame timestamps (gaps, duplicates), keyframe (GOP) rhythm, encoder/handler tags |
| Double compression | `video/double_compression.py` | Periodic frame-size trace of re-encoding (VPF) |
| Frame continuity | `video/pixels.py` | Frozen runs (with native-frame-rate and DVR frame-dropping awareness), loops verified by sensor noise, abrupt discontinuities |
| Colour filter & framing | `video/grade.py` | Colour computed from brightness (tint), colour suppressed relative to contrast, crushed blacks; pillarbox/letterbox bars. True monochrome (IR) is reported, not flagged |
| On-screen clock | `video/osd.py` | Finds the burned-in DVR clock, reads it with Tesseract, reconciles with frame timestamps: forward jump (deletion), backward (reorder), clock ticking over frozen frames |
| Lip-sync | `video/lipsync.py` | Mouth motion vs speech energy: constant offset (audio re-laid) or breaks |
| Face deepfake | `video/face_deepfake.py` | Optional plug-in (DeepfakeBench-style scorer) |
| Sound track | `audio/*` | The audio analyzers below, run on the video's audio |

### 4.4 Audio

| Analyzer | File | What it detects |
|---|---|---|
| ENF | `audio/enf.py` | 50 Hz mains-hum phase/frequency continuity; 60 Hz grid in an Indian recording |
| Acoustics | `audio/acoustic.py` | Noise-floor breaks, digital silence, bandwidth cut-offs, encoder tags, studio-clean gaps |
| Synthetic voice | `audio/spoof.py` | Back-ends in order: SSL+AASIST command → pretrained Hugging Face wav2vec2 model → trained LFCC baseline; scored on windows so partial fakes are localised |
| Partial spoof | `audio/partial_spoof.py` | Segment-level spoof scores |

### 4.5 Documents and unknown files

`metadata/documents.py` reads Office core properties and PDF Info. Together with the file-signature and Content
Credentials checks, this is what a non-media file receives.

---

## 5. From findings to a verdict (`fusion.py`)

1. Each finding's contribution is `llr × reliability` (llr = log-likelihood ratio: positive supports manipulation,
   negative supports authenticity). `unavailable` findings and zero-reliability findings are ignored.
2. Findings are grouped into **families** (metadata, compression, pixel, provenance, synthetic-image, synthetic-voice,
   temporal-container, temporal-pixel, osd, enf, acoustic, colour-filter, framing, derivative, file-integrity,
   cross-modal). Within a family the strongest positive finding counts; passes only count when nothing in the family
   is positive. Each family has a cap, so no single family dominates unless it is cryptographic (provenance).
3. Families test different manipulations, so once any family flags, the passes of other families are discounted
   (×0.25): "no splice found" does not argue against "metadata was back-dated".
4. Two or more strong families add a corroboration bonus.
5. Ordinal categories (SWGDE style):

| Total | Category |
|---|---|
| ≥ 4.0 | Strong indications of manipulation |
| ≥ 1.5 | Indications of manipulation |
| ≤ −0.75, coverage ≥ 50 %, nothing flagged | No indications of manipulation detected |
| otherwise | Inconclusive (with reason and what is missing) |

6. **Coverage** = share of the families expected for the modality that produced a reliable result.
7. **"What was found" labels** are derived from the strongest flagged findings, e.g. *AI-generated (signed Content
   Credentials)*, *Disguised file type*, *Metadata altered*, *Colour filter applied*, *Footage removed (clock jump)*,
   *Modified copy of another exhibit*.

Deliberate weight limits: the pretrained AI-image and voice classifiers can reach *Indications* but not *Strong* on
their own, because their published accuracy is self-reported. A valid Content Credentials declaration of AI
generation can reach *Strong* on its own, because it is a signed statement by the generator.

---

## 6. Integrity and tamper evidence

| Mechanism | Detail |
|---|---|
| Write-once store | `data/evidence/<sha256>.<ext>`, mode 0444, content-addressed |
| Custody ledger | `custody_events` table; `entry_hash = SHA-256(prev_hash ‖ canonical_json(event))`; SQLite triggers block UPDATE/DELETE; `/api/ledger/verify` recomputes the chain |
| Anchors | Merkle root of the ledger, signed with the lab's Ed25519 key (`data/keys/`) |
| Case log | `case_log` table, same hash-chain construction; CASE_CREATED, EVIDENCE_ADDED, CASE_DELETED (with a full snapshot) |
| Reports | Manifest (exhibit hashes, verdict, every finding code and score, custody head) signed with Ed25519; PDF SHA-256 stored; QR code → `/verify/<report id>` |
| Restart safety | Jobs left running at shutdown are marked interrupted and re-queued at start-up |

---

## 7. Plug-in detectors

| Plug-in | How it is connected |
|---|---|
| TruFor | Official `test.py`, run as a subprocess with the current Python (`PRAMAAN_TRUFOR_DIR`, `PRAMAAN_TRUFOR_PYTHON`, `PRAMAAN_TRUFOR_GPU`) |
| AI-image classifier | Hugging Face model id in `PRAMAAN_AIGI_MODEL`; cached in `models/hf` |
| Synthetic voice | `PRAMAAN_VOICE_MODEL` (Hugging Face) or `PRAMAAN_SSL_SPOOF_CMD` (external SSL+AASIST) or `models/lfcc_spoof.npz` |
| CAT-Net | `PRAMAAN_CATNET_CMD` |
| Face deepfake | `PRAMAAN_DEEPFAKE_CMD` |
| Partial spoof | `PRAMAAN_PARTIALSPOOF_CMD` |
| Narration LLM | `PRAMAAN_OLLAMA_URL` (local Ollama) |

A plug-in that is not configured yields an `unavailable` finding with install instructions.

---

## 8. Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Starlette + uvicorn |
| Signal & image processing | OpenCV, NumPy, SciPy, scikit-image, Pillow (+ pillow-heif) |
| Media | FFmpeg / ffprobe, Tesseract OCR |
| Deep learning | PyTorch, Hugging Face transformers, TruFor |
| Storage | SQLite (WAL), content-addressed file store |
| Cryptography | SHA-256/SHA-1/MD5, Ed25519 (cryptography), C2PA COSE verification (ECDSA/RSA-PSS/Ed25519) |
| Reports | ReportLab, PIL/raqm for Devanagari, QR codes |
| Frontend | Next.js 15, React 19, TypeScript, IBM Plex fonts (self-hosted) |
| Deployment | Docker, docker-compose, Render blueprint |

---

## 9. Limitations

- AI content without Content Credentials relies on pretrained classifiers whose published accuracy is measured on
  their own test data; real-world accuracy (new generators, Indian languages, phone audio) is lower and unmeasured.
- Converting a JPEG to PNG/WebP erases compression evidence; such files rely on noise, TruFor, classifiers and metadata.
- A clip trimmed to a middle section without an internal cut leaves no trace in the file itself.
- Content Credentials are verified offline: signature and content hash yes; trust-list check of the signer's
  certificate and MP4 (BMFF) content hashes no.
- TruFor on CPU takes about a minute per photo; it runs one image at a time.
