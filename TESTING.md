# Testing

PRAMAAN is tested at three levels: unit and integration tests that run on synthetic and real sample data, an
end-to-end evaluation on the official PS-05 dataset, and a manual demo checklist.

---

## 1. Running the tests

```bash
# all automated tests (≈ 10 minutes on a laptop CPU; generates demo_samples/ on first run)
python -m unittest tests.test_platform tests.test_aigi tests.test_ps05

# one suite or one test
python -m unittest tests.test_ps05.FileType
python -m unittest tests.test_platform.Golden.test_cctv

# include the official PS-05 dataset checks
PS05_DATASET="/path/to/PS-05.Digital evidence tampering detection system" python -m unittest tests.test_ps05
#   Windows PowerShell:  $env:PS05_DATASET="D:\path\to\dataset"; python -m unittest tests.test_ps05
```

Tests use a temporary data directory (`PRAMAAN_DATA`), so they never touch real cases. The AI-model tests replace
PyTorch/transformers with stand-in modules, so they run even where the models are not installed.

Last full run: **21 tests, all passing** (with `PS05_DATASET` set).

---

## 2. Test cases

### 2.1 `tests/test_platform.py` — custody, golden samples, reports

| ID | Test | Input | Expected result |
|---|---|---|---|
| TC-01 | `Custody.test_chain_detects_tampering` | 5 ledger entries; an attacker drops the trigger and edits one row in SQLite | `verify_chain()` reports the chain broken |
| TC-02 | `Custody.test_append_only_trigger` | `DELETE FROM custody_events` | Refused by the database |
| TC-03 | `Custody.test_signature` | Sign "hello", verify "hello" and "hellO" | First valid, second invalid |
| TC-04 | `Custody.test_merkle` | Two hashes in different order | Different Merkle roots |
| TC-05 | `CaseDeletion.test_delete_keeps_permanent_log` | Case with one exhibit, deleted with a reason | Case and evidence rows gone; log shows CASE_CREATED, EVIDENCE_ADDED, CASE_DELETED with the SHA-256 and reason; both chains intact; log cannot be deleted |
| TC-06 | `Golden.test_images` | 6 generated images | Every tampered image flagged; pristine and WhatsApp forward not flagged |
| TC-07 | `Golden.test_audio` | 5 generated recordings | Deletion, insert, 60 Hz grid and digital silence flagged; pristine not flagged |
| TC-08 | `Golden.test_cctv` | 5 generated CCTV clips | Deletion, reorder, loop and freeze flagged; pristine not flagged |
| TC-09 | `Golden.test_real_world_samples` | 16 real-world samples | No original flagged; R02, R04, R12, R13, R15, R16, R24 flagged |
| TC-10 | `Golden.test_report_signed_and_verifiable` | Report for the spliced photo; then one byte of the PDF flipped | Original verifies; modified PDF fails verification |

### 2.2 `tests/test_aigi.py` — AI-image classifier wiring

| ID | Test | Input | Expected result |
|---|---|---|---|
| TC-11 | `test_confident_ai_is_flagged_but_not_strong` | Classifier says 99 % AI | Finding fails; verdict *Indications*, never *Strong* alone |
| TC-12 | `test_confident_real_supports_authenticity` | Classifier says 1 % AI | Negative weight (supports authenticity) |
| TC-13 | `test_ambiguous_stays_neutral` | Classifier says ≈50 % | No weight either way |

### 2.3 `tests/test_ps05.py` — PS-05 requirements

| ID | Test | Input | Expected result |
|---|---|---|---|
| TC-14 | `FileType.test_disguised_and_empty_files` | JPEG named `.pdf`, DOCX named `.jpg`, 0-byte `.mp3` | JPEG analysed as an image and flagged *Disguised file type*; DOCX routed to documents and flagged; empty file *Inconclusive* with "empty" reason |
| TC-15 | `Metadata.test_planted_png_metadata_and_standard_table` | 1536×1024 RGBA PNG with Make/Model/GPS/dates in PNG text, unknown maker | `META_PNG_CAMERA_TEXT`, `META_ALPHA_CAMERA`, `META_UNKNOWN_MAKE`, `IMG_AI_NATIVE_SIZE`; verdict flagged with *Metadata altered*; metadata table has the 15 fields in order with correct values and "—" for missing ones |
| TC-16 | `Metadata.test_copy_versus_original` | Copy (enlarged, RGBA, Hikvision metadata) uploaded first, original later | Original reports "is a modified copy of this exhibit"; the copy is re-examined automatically and reports `DERIV_COPY` mentioning enlargement and the alpha channel |
| TC-17 | `Video.test_colour_filter_and_reframe` | Test video, a tinted "night" version, and a portrait crop placed in 16:9 | Original passes the colour check; tinted version flagged `VID_COLOUR_FILTER`; boxed version flagged `VID_REFRAMED` |
| TC-18 | `Video.test_low_frame_rate_is_not_frozen_padding` | 2 fps footage converted to 25 fps | `VID_LOW_FPS` reported; no `VID_FROZEN` |
| TC-19 | `Voice.test_pretrained_voice_backend` | Stand-in voice model returning 99.5 % and 1 % "fake" | High score: fails with weight > 1.5 but contribution ≤ 1.6 (never decisive alone); low score: negative weight |
| TC-20 | `C2PA.test_cbor_decoder` | Known CBOR bytes; COSE Sig_structure round trip | Decoded exactly |
| TC-21 | `C2PA.test_official_dataset` *(needs PS05_DATASET)* | The 6 PS-05 voice clips | 5 MP3s: AI-generated, signature valid, content hash valid; WAV: no manifest |

### 2.4 Additional checks performed during development

| Check | Result |
|---|---|
| Same photo saved as JPG, PNG, WebP, BMP, TIFF, GIF, AVIF | All accepted with no errors; JPEG-only tests skipped on other formats as designed |
| JPEG + its PNG/BMP/TIFF/GIF/WebP conversions in one case | JPEG recognised as the source; conversions labelled *Converted copy*, not manipulation |
| C2PA-signed PNG and MP3 with one content byte flipped | Signature still valid, content hash fails → `PROV_C2PA_ALTERED` |
| Repeated logo backdrop and an AI image with repeated textures | Copy-move reports *Repeated pattern*, not a clone; real cloned image still flagged |
| CCTV regression set after the frozen-frame change | Deletion, loop and freeze samples still flagged; DVR frame dropping no longer flagged |

---

## 3. Manual demo checklist

| Step | Action | Expected |
|---|---|---|
| 1 | Start the server, open the home page | Hero slider animates; custody ledger pill shows "intact" |
| 2 | Demo kit → click *02 spliced photo* | Verdict *Strong/Indications*; lamp shows the pasted region |
| 3 | Cases → open a case → drop 3 own photos | SHA-256 appears immediately, progress bars run |
| 4 | Open an exhibit → **Metadata** tab | 15 PS-05 fields; Copy / CSV / JSON work |
| 5 | **Sign report** | PDF opens with English + Hindi summary, section 2A metadata table, QR code |
| 6 | Scan the QR or open `/verify/<id>` | "Signature valid" |
| 7 | Cases → bin icon → reason + FIR → Delete | Case disappears; **Case log** shows the deletion with what it held |
| 8 | `GET /api/ledger/verify` | `{"ok": true}` |

---

## 4. Results on the official PS-05 dataset

Command: `python scripts/eval_dataset.py "<dataset folder>" --out ps05_results`
(run without TruFor and the AI classifiers installed).

| File | Organisers' answer | PRAMAAN verdict | What was found |
|---|---|---|---|
| Voice data/AMIT voice 1.mp3 | AI | Strong indications | AI-generated (signed Content Credentials) |
| Voice data/Female Voice 2.mp3 | AI | Strong indications | AI-generated (signed Content Credentials) |
| Voice data/Apk voice 3.mp3 | AI | Strong indications | AI-generated (signed Content Credentials) |
| Voice data/Murder voice 4.mp3 | AI | Strong indications | AI-generated (signed Content Credentials) |
| Voice data/Customercare voice 5.mp3 | AI | Strong indications | AI-generated (signed Content Credentials) |
| Voice data/Cyber voice 6.wav | Real | Inconclusive (not flagged) | — |
| Image data/Image-1 copy.jpg | Tampered | Strong indications | Modified copy of another exhibit; Metadata altered |
| Image data/Image-2 copy.png | Tampered | Strong indications | Metadata altered; Modified copy of another exhibit |
| Image data/Image-3 copy.png | Tampered | Indications | Metadata altered; Likely AI-generated |
| Image data/Image-4 copy.png | Tampered | Strong indications | AI-generated, provenance removed; Metadata altered; Modified copy |
| Image data/Image-5 copy.png | Tampered | Strong indications | AI-generated, provenance removed; Metadata altered; Modified copy |
| Mismatched extension/Doc.pdf | Mismatch | Indications | Disguised file type (JPEG) |
| Mismatched extension/Image.jpg | Mismatch | Indications | Disguised file type (Word document) |
| Mismatched extension/Video.mp4 | Mismatch | Indications | Disguised file type (Excel workbook) |
| Mismatched extension/data.xls | Mismatch | Indications | Disguised file type (MP4 video) |
| Mismatched extension/Audio.mp3 | Empty | Inconclusive | Empty file (no content) |
| Video data/Video-1(10-15 -).mp4 | Clipped | Indications | Footage removed (clock jump) |
| Video data/Video-2.mp4 | Clipped + night theme | Indications | Colour filter applied; Frames deleted; Re-framed in an editor |
| Video data/video-3.mp4 | Night theme | Indications | Colour filter applied |
| Video data/video-4.mp4 | Original | Inconclusive (not flagged) | — |
| Video data/video-5.mp4 | Original | Inconclusive (not flagged) | — |

**21 / 21 labelled files correct, 0 false alarms.** Unlabelled files: the OpenAI originals (Image-3/4/5, Data-2
IMG-1/3) are reported as AI-generated through their Content Credentials; screenshots and the Data-2 video remain
*Inconclusive* with the reason shown.

---

## 5. Evaluating detectors on your own data

The published accuracies of the pretrained AI-image (99.23 %) and voice (99.7 %) models are measured on their own test
splits. To measure them on realistic data:

1. Collect files with known answers (e.g. your own camera photos, and AI images converted to JPEG so their Content
   Credentials are removed).
2. Write a `labels.csv` (see [SAMPLE_DATA.md](SAMPLE_DATA.md#4-using-your-own-data)).
3. Run `python scripts/eval_dataset.py <folder> --out results` and read `results.csv`.

Because stripped files carry no signed label, the verdicts then come from the classifiers and other checks alone.
