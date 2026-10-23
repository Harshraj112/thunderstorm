# Sample data

PRAMAAN ships with test exhibits whose correct answer is known, so anyone can see what the platform catches and,
just as important, what it leaves alone. They appear in the dashboard's **Demo kit** and are used by the automated
tests.

| Set | Folder | Files | Origin |
|---|---|---|---|
| Real-world | `samples/real/` | 16 | Public datasets and repositories; tampered versions derived by us |
| Generated | `demo_samples/` | 16 | Created by `scripts/make_demo_samples.py` (fully synthetic, labelled) |
| Official PS-05 | not redistributed | 32 | Supplied by the organisers; run with `scripts/eval_dataset.py` |

---

## 1. Real-world samples (`samples/real/`)

Rebuild them at any time with `python scripts/fetch_real_samples.py` (needs internet). Each file is described in
`samples/real/manifest.json` (title, story, source, tampered flag, expected outcome).

| File | Genuine? | What it is | Source |
|---|---|---|---|
| R01_iphone_original.jpg | Original | iPhone photo, straight from the phone | github.com/ianare/exif-samples |
| R02_canon_resized_in_gimp.jpg | Edited | Camera photo re-saved in GIMP | github.com/ianare/exif-samples |
| R03_nokia_gps_original.jpg | Original | Geotagged phone photo (Nokia 8.3) | github.com/ianare/exif-samples |
| R04_nokia_photo_backdated.jpg | Edited | Same phone photo, capture date changed | derived from R03 |
| R11_cctv_real_dvr_export.mp4 | Original | Real street CCTV with an on-screen clock | OpenCV `vtest.avi`; clock overlay added |
| R12_cctv_real_8s_deleted.mp4 | Edited | Same CCTV with 8 s removed | derived from R11 |
| R13_cctv_real_loop_insert.mp4 | Edited | Same CCTV with footage looped | derived from R11 |
| R14_statement_original.mp4 | Original | Recorded statement, real speaker | GRID audio-visual corpus (via LipNet repository) |
| R15_statement_audio_relaid.mp4 | Edited | Same statement, audio re-laid (offset) | derived from R14 |
| R16_statement_words_replaced.mp4 | Edited | Same statement, words replaced | derived from R14 |
| R20_room_recording_50hz.wav | Original | Room recording with 50 Hz mains hum | ENF-WHU dataset (H1) |
| R21_room_recording_cut.wav | Edited | Same recording, 3.3 s cut out | derived from R20 |
| R22_room_recording_insert.wav | Edited | Same recording with a 6 s insert | derived from R20 + ENF-WHU |
| R23_speech_genuine.mp3 | Original | Genuine public speech | Resemblyzer repository |
| R24_speech_voice_clone.mp3 | AI | AI voice clone of the same speaker | Resemblyzer repository |
| R25_battery_recorder_no_hum.wav | Original | Real recording without mains hum | ENF-WHU dataset (H0) |

Photos of identifiable people from public datasets were removed from the kit.

**Expected behaviour:** no original is flagged; R02, R04, R12, R13, R15, R16 and R24 must be flagged. R21 (hum too
noisy) and R22 (below threshold) are allowed to stay *Inconclusive*, and PRAMAAN explains why.

---

## 2. Generated samples (`demo_samples/`)

Created with `python scripts/make_demo_samples.py demo_samples`. The answers are in `demo_samples/expected.json`.

### Images

| File | Genuine? | Manipulation |
|---|---|---|
| 01_pristine_photo.jpg | Original | Camera-style original with vendor quantisation tables |
| 02_spliced_photoshop.jpg | Edited | Object pasted from another JPEG (region 400,300,320,256) |
| 03_copy_move.jpg | Edited | Region cloned within the image (800,600,200,160) |
| 04_metadata_backdated.jpg | Edited | Capture date edited |
| 05_ai_generated.png | Synthetic | Synthetic image |
| 06_whatsapp_forward.jpg | Original | Benign recompression (must not be flagged) |

### Audio

| File | Genuine? | Manipulation |
|---|---|---|
| 01_audio_pristine.wav | Original | Continuous recording with 50 Hz hum |
| 02_audio_words_deleted.wav | Edited | Words deleted at 18 s |
| 03_audio_insert_other_room.wav | Edited | Segment from another room inserted at 15–20 s |
| 04_audio_60hz_grid.wav | Edited | 60 Hz mains hum (not recorded on the Indian grid) |
| 05_audio_digital_silence.wav | Edited | Digital silence inserted at 25 s |

### CCTV

| File | Genuine? | Manipulation |
|---|---|---|
| 01_cctv_pristine.mp4 | Original | Continuous footage with on-screen clock |
| 02_cctv_frames_deleted.mp4 | Edited | 12 s deleted at 20 s |
| 03_cctv_reordered.mp4 | Edited | Segments reordered at 20 s and 40 s |
| 04_cctv_loop_insert.mp4 | Edited | Loop inserted at 30 s |
| 05_cctv_frozen.mp4 | Edited | Frozen frames at 25 s |

---

## 3. Official PS-05 dataset

The organisers' dataset ("Digital evidence tampering detection system") is **not redistributed** with PRAMAAN.
Place it anywhere and run:

```bash
python scripts/eval_dataset.py "<dataset folder>" --out ps05_results
```

Ground truth is read from the organisers' own files:

| Folder | Contents | Ground truth source |
|---|---|---|
| Voice data | 6 clips | "Voice clip details.txt": clips 1–5 AI-generated, clip 6 real |
| Video data | 5 videos | "Video details sheet.txt": 1 clipped, 2 clipped + night theme, 3 night theme, 4–5 original |
| Image data | 5 copies + 5 originals | "copy" files are modified; "Metadata details.docx" defines the metadata output format |
| Mismatched extension | 5 files | Extensions do not match content; one file is empty |
| Data-2 | 5 images + 1 video | Unlabelled |

Results are summarised in [TESTING.md](TESTING.md#4-results-on-the-official-ps-05-dataset).

---

## 4. Using your own data

- **Dashboard:** open a case and drag files in.
- **Batch with known answers:** put files in a folder with a `labels.csv`:

```csv
file,expected
photos/camera_original.jpg,original
photos/edited_in_app.jpg,tampered
voice/cloned_call.mp3,ai
misc/renamed.pdf,mismatch
misc/zero.mp3,empty
```

then run `python scripts/eval_dataset.py <folder> --out results`. Allowed `expected` values: `original`, `tampered`,
`ai`, `mismatch`, `empty`, `?` (unlabelled).

- **Your own CCTV references:** `python -m pramaan.cli register-device "Camera 3" ref1.jpg ref2.jpg ...` stores a sensor
  fingerprint (PRNU) used to check whether later photos came from that camera.
- **Mains-frequency reference:** `scripts/enf_logger.py` records a local ENF reference log for audio timestamp checks.
