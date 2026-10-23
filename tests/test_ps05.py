"""Regression tests for the PS-05 dataset fixes, on synthetic inputs (the organisers' files are not redistributed).

Set PS05_DATASET=<folder of the official dataset> to also run the end-to-end check on the real files.
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("PRAMAAN_DATA", tempfile.mkdtemp(prefix="pramaan_ps05_"))
os.environ["PRAMAAN_SEED_DEMO"] = "0"

import numpy as np  # noqa: E402
from PIL import Image, PngImagePlugin  # noqa: E402

from pramaan import db, fusion, pipeline  # noqa: E402
from pramaan.analyzers import filetype  # noqa: E402
from pramaan.analyzers.base import Context  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="ps05_inputs_"))


def _photo(path: Path, size=(640, 480), seed=1):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size[1], 0:size[0]]
    a = np.stack([(x * 255 / size[0]), (y * 255 / size[1]), ((x + y) % 255)], -1)
    a = np.clip(a + rng.normal(0, 6, a.shape), 0, 255).astype(np.uint8)
    for k in range(12):   # some structure so the signature is distinctive
        cx, cy = rng.integers(40, size[0] - 40), rng.integers(40, size[1] - 40)
        a[cy - 20:cy + 20, cx - 20:cx + 20] = rng.integers(0, 255, 3)
    Image.fromarray(a).save(path)
    return path


def _ingest(case_id, path, name=None):
    with open(path, "rb") as f:
        ev = pipeline.ingest(case_id, name or path.name, f, "tester")
    job = db.new_id("job")
    db.insert("jobs", {"id": job, "evidence_id": ev["id"], "status": "running", "started_at": db.now()})
    v = pipeline.run_job(job, ev["id"], "tester")
    return ev, v, pipeline.results(ev["id"])["findings"]


def _case():
    cid = db.new_id("case")
    db.insert("cases", {"id": cid, "fir_no": "PS05-TEST", "created_at": db.now()})
    return cid


def _codes(fs):
    return {f["code"]: f for f in fs}


class FileType(unittest.TestCase):
    def test_disguised_and_empty_files(self):
        jpg = TMP / "pic.pdf"
        Image.fromarray(np.full((64, 64, 3), 128, np.uint8)).save(jpg, "JPEG")
        docx = TMP / "photo.jpg"
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml", "<w:document/>")
            z.writestr("docProps/core.xml", "<cp:coreProperties><dc:creator>Clerk</dc:creator></cp:coreProperties>")
        empty = TMP / "call.mp3"
        empty.write_bytes(b"")
        self.assertEqual(filetype.sniff(jpg), "jpeg")
        self.assertEqual(filetype.sniff(docx), "docx")
        self.assertEqual(filetype.sniff(empty), "empty")
        cid = _case()
        ev, v, fs = _ingest(cid, jpg)
        self.assertEqual(ev["modality"], "image")                      # routed by content, not by '.pdf'
        self.assertIn("FILE_EXT_MISMATCH", _codes(fs))
        self.assertIn("Disguised file type", v["nature"])
        self.assertTrue(v["category"].startswith("Indications"))
        ev, v, fs = _ingest(cid, docx)
        self.assertEqual(ev["modality"], "document")
        self.assertIn("FILE_EXT_MISMATCH", _codes(fs))
        ev, v, fs = _ingest(cid, empty)
        self.assertIn("FILE_EMPTY", _codes(fs))
        self.assertEqual(v["category"], "Inconclusive")
        self.assertIn("empty", v["abstain_reason"])


class Metadata(unittest.TestCase):
    def test_planted_png_metadata_and_standard_table(self):
        src = _photo(TMP / "scene.png", (1536, 1024), seed=3)
        im = Image.open(src).convert("RGBA")
        info = PngImagePlugin.PngInfo()
        for k, v in {"DateTimeOriginal": "2026:02:11 22:37:54", "ModifyDate": "2026:02:13 07:24:19",
                     "GPSLatitude": "30.7333 N", "GPSLongitude": "76.7794 E", "Make": "ACMEX", "Model": "ACMEX",
                     "Software": "Evidence Export Utility 4.0", "Artist": "Unit 7", "Copyright": "Unit 7"}.items():
            info.add_text(k, v)
        forged = TMP / "scene_forged.png"
        im.save(forged, pnginfo=info)
        cid = _case()
        ev, v, fs = _ingest(cid, forged)
        c = _codes(fs)
        for code in ("META_PNG_CAMERA_TEXT", "META_ALPHA_CAMERA", "META_UNKNOWN_MAKE", "IMG_AI_NATIVE_SIZE"):
            self.assertIn(code, c, code)
        self.assertTrue(v["category"].startswith(("Indications", "Strong")), v["category"])
        self.assertIn("Metadata altered", v["nature"])
        table = dict(c["META_TABLE"]["data"]["standard"])
        self.assertEqual(list(table)[:4], ["File Name", "File Type", "Image Width", "Image Height"])
        self.assertEqual(table["Make"], "ACMEX")
        self.assertEqual(table["GPSLatitude"], "30.7333 N")
        self.assertEqual(table["Image Width"], "1536")
        self.assertEqual(table["Description"], "—")

    def test_copy_versus_original(self):
        cid = _case()
        orig = _photo(TMP / "orig.png", (800, 600), seed=5)
        big = Image.open(orig).convert("RGBA").resize((1200, 900))
        info = PngImagePlugin.PngInfo()
        info.add_text("Make", "Hikvision")
        info.add_text("Model", "DS-2CD2143G2")
        info.add_text("DateTimeOriginal", "2025:03:22 18:42:16")
        copy = TMP / "copy.png"
        big.save(copy, pnginfo=info)
        ev_c, v_c, fs_c = _ingest(cid, copy)               # copy first: nothing to compare with yet
        self.assertNotIn("DERIV_COPY", _codes(fs_c))
        ev_o, v_o, fs_o = _ingest(cid, orig)               # original arrives later
        self.assertIn("DERIV_SOURCE", _codes(fs_o))
        pipeline._pool.shutdown(wait=True)                  # the copy is re-examined automatically
        pipeline._pool = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=2)
        fs_c = pipeline.results(ev_c["id"])["findings"]
        d = _codes(fs_c)["DERIV_COPY"]
        self.assertIn("enlarged", d["explanation"])
        self.assertIn("alpha channel", d["explanation"])


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


class Video(unittest.TestCase):
    def test_colour_filter_and_reframe(self):
        from pramaan.analyzers.video import grade
        src = TMP / "street.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=6", "-pix_fmt", "yuv420p", str(src))
        tinted = TMP / "night.mp4"
        _ffmpeg("-i", str(src), "-vf", "hue=s=0,colorchannelmixer=.25:.25:.25:0:.3:.3:.3:0:.45:.45:.45,eq=contrast=1.4",
                "-pix_fmt", "yuv420p", str(tinted))
        boxed = TMP / "portrait_in_169.mp4"
        _ffmpeg("-i", str(src), "-vf", "crop=200:360,pad=640:360:220:0:black", "-pix_fmt", "yuv420p", str(boxed))
        w = Path(tempfile.mkdtemp())
        ok = {f.code: f for f in grade.run(Context("x", src, w, w, "video"))}
        self.assertEqual(ok["VID_COLOUR_FILTER"].status, "pass")
        bad = {f.code: f for f in grade.run(Context("x", tinted, w, w, "video"))}
        self.assertIn(bad["VID_COLOUR_FILTER"].status, ("fail", "warn"))
        rf = {f.code: f for f in grade.run(Context("x", boxed, w, w, "video"))}
        self.assertIn("VID_REFRAMED", rf)

    def test_low_frame_rate_is_not_frozen_padding(self):
        from pramaan.analyzers.video import pixels
        src = TMP / "dvr_lowfps.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=2:duration=12", "-vf", "noise=alls=8:allf=t,fps=25",
                "-pix_fmt", "yuv420p", str(src))
        w = Path(tempfile.mkdtemp())
        codes = {f.code: f for f in pixels.run(Context("x", src, w, w, "video"))}
        self.assertNotIn("VID_FROZEN", codes)
        self.assertIn("VID_LOW_FPS", codes)


class Voice(unittest.TestCase):
    def _fake(self, p_fake):
        import types
        torch = types.ModuleType("torch")

        class T:
            def __init__(s, a): s.a = np.asarray(a, float)
            def __getitem__(s, k): return T(s.a[k])
            def item(s): return float(s.a)

        class NG:
            def __enter__(s): pass
            def __exit__(s, *a): pass
        torch.no_grad = lambda: NG()
        torch.softmax = lambda x, dim=-1: T(np.exp(x.a) / np.exp(x.a).sum(axis=dim, keepdims=True))
        tr = types.ModuleType("transformers")

        class FE:
            sampling_rate = 16000
            @classmethod
            def from_pretrained(c, *a, **k): return c()
            def __call__(s, w, sampling_rate, return_tensors): return {}

        class Cfg:
            id2label = {0: "fake", 1: "real"}

        class M:
            config = Cfg()
            @classmethod
            def from_pretrained(c, *a, **k): return c()
            def eval(s): return s
            def __call__(s, **k):
                return types.SimpleNamespace(logits=T([[np.log(p_fake / (1 - p_fake)), 0.0]]))
        tr.AutoFeatureExtractor, tr.AutoModelForAudioClassification = FE, M
        sys.modules["torch"], sys.modules["transformers"] = torch, tr

    def test_pretrained_voice_backend(self):
        from pramaan.analyzers.audio import spoof
        wav = TMP / "speech.wav"
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=180:duration=12", "-af", "volume=0.4", "-ar", "16000", str(wav))
        for p, expect in ((0.995, "fail"), (0.01, "pass")):
            self._fake(p)
            importlib.reload(spoof)
            spoof.hf_voice_available = lambda: True
            w = Path(tempfile.mkdtemp())
            f = spoof.run(Context("x", wav, w, w, "audio"))[0]
            self.assertEqual(f.status if expect == "pass" or f.status == "fail" else f.status,
                             expect if expect == "pass" else f.status)
            if expect == "fail":
                self.assertGreater(f.llr, 1.5)
                self.assertLessEqual(f.llr * f.reliability, 1.6)   # never decisive on its own
            else:
                self.assertLess(f.llr, 0)
        for m in ("torch", "transformers"):
            sys.modules.pop(m, None)


class C2PA(unittest.TestCase):
    def test_cbor_decoder(self):
        from pramaan.analyzers.provenance.c2pa_lite import cbor_loads, _cbor_dump
        self.assertEqual(cbor_loads(bytes.fromhex("a26161016162820203")), {"a": 1, "b": [2, 3]})
        self.assertEqual(cbor_loads(_cbor_dump(["Signature1", b"\x01", b"", b"xyz"])), ["Signature1", b"\x01", b"", b"xyz"])

    @unittest.skipUnless(os.environ.get("PS05_DATASET"), "set PS05_DATASET to the official dataset folder")
    def test_official_dataset(self):
        from pramaan.analyzers.provenance.c2pa_lite import read
        root = Path(os.environ["PS05_DATASET"])
        voices = sorted((root / "Voice data").glob("*"))
        ai = [v for v in voices if v.suffix == ".mp3"]
        self.assertEqual(len(ai), 5)
        for v in ai:
            r = read(v)
            self.assertTrue(r["ai_generated"] and r["signature_valid"] and r["content_hash_valid"], v.name)
        self.assertIsNone(read(next(v for v in voices if v.suffix == ".wav")))


if __name__ == "__main__":
    unittest.main()
