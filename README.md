# PRAMAAN — Digital Evidence Authenticity and Integrity Verification

**Team ThunderStorm · Problem Statement PS-05**

PRAMAAN ("proof") checks whether a photo, CCTV clip or voice recording **has changed since it was seized** (integrity)
and whether **its content is genuine** (authenticity), then produces a signed, bilingual (English + हिन्दी) report ready
for a Section 63 certificate under the Bharatiya Sakshya Adhiniyam, 2023. Everything runs offline on one computer.

| | |
|---|---|
| **Evidence types** | Images (JPG/JPEG, PNG, WebP, BMP, TIFF, GIF, AVIF, HEIC*), CCTV and video (MP4, MOV, AVI, MKV, …), audio (MP3, WAV, M4A, AAC, OGG, FLAC, AMR), documents and unknown files (file-type check) |
| **Integrity** | SHA-256 / SHA-1 / MD5 at intake, write-once storage, hash-chained custody ledger, signed anchors, case log |
| **Authenticity** | 20+ forensic checks across metadata, compression, sensor noise, on-screen clocks, frame continuity, colour filters, mains-hum (ENF), synthetic voice and AI images |
| **Proof of AI content** | Built-in offline Content Credentials (C2PA) reader: signature and content hash verified |
| **Output** | Ordinal verdict with plain labels, PS-05 metadata table, signed PDF report with QR verification |
| **Result on the PS-05 dataset** | 21 / 21 labelled files correct, 0 false alarms |

\* HEIC needs the optional `pillow-heif` package (installed by the setup script).

More documentation:

- [Architecture](docs/ARCHITECTURE.md): how the system is built and how a verdict is reached
- [Sample data](docs/SAMPLE_DATA.md): the test exhibits shipped with the project and the PS-05 dataset
- [Testing](docs/TESTING.md): automated test cases and how to evaluate on your own data
- [Deploying online (Render)](DEPLOY_RENDER.md)
- [License](LICENSE.md)

---

## 1. Requirements

| Item | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11, Ubuntu 20.04+, macOS 12+ | — |
| Python | 3.10 | 3.11 or 3.12 |
| RAM | 4 GB (core checks only) | 8 GB (with TruFor and AI models) |
| Disk | 1 GB | 5 GB (models ≈ 2–3 GB + cases) |
| GPU | not required | NVIDIA GPU speeds up TruFor and the AI models |
| System tools | FFmpeg, Tesseract OCR, Git | — |
| Node.js | only to rebuild the dashboard | 20 LTS |

The dashboard ships prebuilt (`pramaan/web_dist/`), so Node.js is **not** needed to run PRAMAAN.

---

## 2. Quick start (one command)

### Windows (PowerShell)

```powershell
# 1. System tools (once), then close and reopen the terminal
winget install Gyan.FFmpeg
winget install UB-Mannheim.TesseractOCR
winget install Git.Git

# 2. Python environment (conda or venv)
conda create -n pramaan python=3.11 -y
conda activate pramaan
cd path\to\pramaan

# 3. Everything else: packages, AI-image model, voice model, TruFor, demo samples
python scripts/setup_all.py --all --cpu-torch

# 4. Check, then start
python scripts/setup_all.py --check
uvicorn pramaan.api.server:app --port 8000
```

Open **http://localhost:8000**.

### Linux / macOS

```bash
sudo apt install ffmpeg tesseract-ocr git        # macOS: brew install ffmpeg tesseract git
python3 -m venv .venv && source .venv/bin/activate
python scripts/setup_all.py --all --cpu-torch
python scripts/setup_all.py --check
uvicorn pramaan.api.server:app --port 8000
```

### Docker

```bash
docker compose up --build                          # core checks, data in a named volume
# or, with TruFor + AI models baked into the image:
docker build -f Dockerfile.render -t pramaan .
docker run -p 8000:8000 -v pramaan-data:/var/data pramaan
```

---

## 3. Detailed setup

### 3.1 What `setup_all.py` does

| Step | Flag | What happens |
|---|---|---|
| System check | always | Looks for FFmpeg, Tesseract, Git, Node; prints the install command for your OS if missing |
| Core packages | `--all` | `pip install -r requirements.txt` (OpenCV, NumPy/SciPy, Starlette, ReportLab, cryptography, pillow-heif, …) |
| Deep-learning extras | `--all` | PyTorch + transformers (`--cpu-torch` installs the small CPU build) |
| AI-image model | `--all` | Downloads `Ateeqq/ai-vs-human-image-detector` into `models/hf/` (≈370 MB, once) |
| Synthetic-voice model | `--all` | Downloads `MelodyMachine/Deepfake-audio-detection-V2` into `models/hf/` |
| Demo samples | `--all` | Generates labelled test exhibits in `demo_samples/` |
| TruFor | `--all` or `--trufor` | Clones the code, downloads and MD5-verifies the weights, runs a smoke test |
| Dashboard build | `--all` or `--frontend` | Rebuilds the Next.js dashboard (only if Node.js is present) |
| Report | `--check` | Prints a ready / not-installed table for every detector |

Useful flags: `--trufor-zip <file>` (use weights you downloaded yourself), `--gpu` (run TruFor on GPU 0),
`--skip-ml` (core checks only), `--dry-run`. Re-running is safe: finished steps are skipped.

### 3.2 Individual steps (if you prefer manual control)

```bash
pip install -r requirements.txt                 # core
pip install -r requirements-ml.txt              # PyTorch + transformers
python scripts/install_models.py                # AI-image classifier
python scripts/install_models.py --voice        # synthetic-voice detector
python scripts/setup_all.py --trufor            # TruFor localiser
python scripts/make_demo_samples.py demo_samples
python scripts/fetch_real_samples.py            # rebuild samples/real from public sources (needs internet)
```

### 3.3 GPU

TruFor uses the GPU when set up with `python scripts/setup_all.py --trufor --gpu` (stored as `PRAMAAN_TRUFOR_GPU=0`).
On Windows, `pip install torch` installs a CPU-only build; install a CUDA build from pytorch.org first
(e.g. `pip install torch --index-url https://download.pytorch.org/whl/cu126`), then run `setup_all.py --all` **without**
`--cpu-torch`.

### 3.4 Expected `--check` output

```
✓ PRAMAAN imports                   OK
✓   detector: ai_image              OK       ready
✓   detector: trufor                OK       ready
–   detector: catnet                SKIPPED  not installed     (optional)
–   detector: face_deepfake         SKIPPED  not installed     (optional)
✓   detector: synthetic_voice       OK       ready
✓   detector: content_credentials   OK       ready
```

CAT-Net and the face-deepfake model are optional research plug-ins (see [Architecture § Plug-ins](docs/ARCHITECTURE.md#7-plug-in-detectors)).
Missing detectors are listed as "not installed" and never count against a verdict.

---

## 4. Using PRAMAAN

### 4.1 Dashboard

| Page | What you can do |
|---|---|
| **Demo kit** (home) | Click any test exhibit to see a full examination without uploading anything |
| **Cases** | Open a case (FIR no., police station, IO), drag in files, watch the analysis; delete a case with a reason |
| **Exhibit** | Verdict and labels, integrity hashes, forensic maps under a before/after "lamp", clickable video/audio timeline, findings with innocent explanations, **Metadata** tab (copy / CSV / JSON), chain of custody, **Sign report** |
| **Case log** | Permanent, hash-chained record of every case opened, file received and case deleted |

Set your name in the **Examiner** box (top right); it is written to the custody ledger.

### 4.2 Command line

```bash
python -m pramaan.cli analyze evidence/*.jpg cctv.mp4 --fir 0457/2026 --report --json out.json
python -m pramaan.cli verify-ledger
python -m pramaan.cli register-device "Camera 3" ref1.jpg ref2.jpg ref3.jpg    # PRNU fingerprint
python scripts/verify_report.py data/reports/<id>.manifest.json data/keys/lab_ed25519.pub.pem
```

### 4.3 Run a whole dataset (e.g. the official PS-05 folder)

```bash
python scripts/eval_dataset.py "PS-05.Digital evidence tampering detection system" --out ps05_results
```

Each sub-folder becomes a case in the dashboard; results are scored against the organisers' description sheets
(or a `labels.csv` you provide) and written to `results.csv`, `metadata.csv` and `summary.md`.

### 4.4 REST API (selected)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Version, lab key fingerprint, detector status |
| POST | `/api/cases` | Create a case `{fir_no, police_station, io_name, title}` |
| DELETE | `/api/cases/{id}` | Delete a case `{reason}` (recorded in the case log) |
| POST | `/api/cases/{id}/evidence` | Upload files (multipart `files`), analysis starts automatically |
| GET | `/api/evidence/{id}` | Verdict, findings, narration, reports |
| GET | `/api/evidence/{id}/metadata[?format=csv]` | PS-05 metadata table |
| GET | `/api/cases/{id}/metadata.csv` | Metadata of every exhibit in a case |
| POST | `/api/evidence/{id}/report` | Build and sign the PDF report |
| GET | `/api/reports/{id}/verify` | Verify a report's signature |
| GET | `/api/ledger/verify` | Verify the whole custody chain |
| GET | `/api/caselog` | Case log entries and chain status |

Send `X-Actor: <name>` to record who made a request.

---

## 5. Configuration

All settings are environment variables (or keys in `models/local_settings.json`, written by the setup script).

| Variable | Default | Meaning |
|---|---|---|
| `PRAMAAN_DATA` | `./data` | Cases, evidence store, database, reports, lab keys |
| `PRAMAAN_MODELS` | `./models` | Model weights and calibration |
| `PRAMAAN_LAB` | State Forensic Science Laboratory (Demo) | Lab name on reports |
| `PRAMAAN_BASE_URL` | `http://localhost:8000` | URL printed in report QR codes |
| `PRAMAAN_WORKERS` | `2` | Exhibits analysed in parallel |
| `PRAMAAN_THREADS` | auto | CPU threads for the models |
| `PRAMAAN_GRID_HZ` | `50` | Mains frequency for ENF analysis (India: 50) |
| `PRAMAAN_TZ_OFFSET_MIN` | `330` | Default time zone (IST) for metadata checks |
| `PRAMAAN_REGION_BBOX` | India | Expected GPS region (lat_min,lat_max,lon_min,lon_max) |
| `PRAMAAN_PASSWORD` / `PRAMAAN_USER` | unset / `pramaan` | Site password (use when online) |
| `PRAMAAN_API_KEY` | unset | Require `X-API-Key` on `/api` |
| `PRAMAAN_SEED_DEMO` | `1` | Seed the demo case on start-up |
| `PRAMAAN_TRUFOR_GPU` | `-1` | GPU index for TruFor (`-1` = CPU) |
| `PRAMAAN_TRUFOR_MAX_SIDE` | `1600` | TruFor input size (lower = faster) |
| `PRAMAAN_TRUFOR_TIMEOUT` | `300` | Seconds before TruFor gives up on one image |
| `PRAMAAN_AIGI_MODEL` | `Ateeqq/ai-vs-human-image-detector` | AI-image classifier |
| `PRAMAAN_VOICE_MODEL` | `MelodyMachine/Deepfake-audio-detection-V2` | Synthetic-voice detector |
| `PRAMAAN_CATNET_CMD`, `PRAMAAN_DEEPFAKE_CMD`, `PRAMAAN_SSL_SPOOF_CMD`, `PRAMAAN_PARTIALSPOOF_CMD` | unset | Commands for optional plug-in detectors |
| `PRAMAAN_OLLAMA_URL` / `PRAMAAN_OLLAMA_MODEL` | unset | Optional local LLM for report narration |

---

## 6. Project structure

```
pramaan/
├── pramaan/                    Python package
│   ├── api/server.py           REST API + serves the dashboard
│   ├── pipeline.py             intake → analysis → verdict
│   ├── fusion.py               combines findings into a verdict
│   ├── db.py, caselog.py       SQLite store, case log
│   ├── custody/                hashing, hash-chained ledger, Ed25519 signing
│   ├── analyzers/
│   │   ├── filetype.py         file signature vs extension
│   │   ├── provenance/         offline C2PA (Content Credentials) reader
│   │   ├── derivative.py       copy-vs-original comparison
│   │   ├── metadata/           extraction, PS-05 table, consistency rules
│   │   ├── image/              JPEG, ELA, noise, copy-move, PRNU, TruFor/CAT-Net/AI plug-ins
│   │   ├── video/              container, frames, OSD clock, colour filter, lip-sync, faces
│   │   └── audio/              ENF, acoustics, synthetic voice
│   ├── report/                 PDF (English + Hindi), narration
│   └── web_dist/               prebuilt dashboard
├── frontend/                   Next.js dashboard source
├── scripts/                    setup, model install, samples, evaluation, training
├── samples/real/               real-world test exhibits + manifest
├── tests/                      automated tests
├── docs/                       architecture, sample data, testing
├── Dockerfile, Dockerfile.render, docker-compose.yml, render.yaml
└── requirements.txt, requirements-ml.txt
```

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `tesseract is not installed` | Install it (step 1) and add `C:\Program Files\Tesseract-OCR` to PATH |
| `ffmpeg not found` | Install FFmpeg and reopen the terminal |
| Exhibits stuck on "trufor" | TruFor takes ≈1 min per photo on CPU, one at a time; set `PRAMAAN_TRUFOR_MAX_SIDE=1024` on slow machines |
| Everything shows "Inconclusive" | Run `setup_all.py --check`; install the AI and voice models; read the "Why this is inconclusive" panel on the exhibit |
| HEIC photo not readable | `pip install pillow-heif`, then **Examine again** |
| Server restarted mid-analysis | Interrupted jobs are re-queued automatically on start-up |
| Network errors during setup | Re-run the same command; finished steps are skipped |

---

## 8. Limitations

PRAMAAN supports a qualified examiner; it does not replace one. AI content without Content Credentials relies on
pretrained classifiers whose published accuracy is self-reported; converting a JPEG to PNG/WebP erases compression
evidence; a clip trimmed without an internal cut leaves no trace in the file itself. See
[Architecture § Limitations](docs/ARCHITECTURE.md#9-limitations).

## 9. License

Project code: MIT. Third-party models and samples keep their own licences (TruFor is non-commercial).
See [LICENSE.md](LICENSE.md).
