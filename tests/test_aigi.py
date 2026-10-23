"""AI-image classifier wiring: label mapping, crop averaging, weighting and fusion outcome.
Runs without PyTorch by injecting stand-in torch/transformers modules (tests/_fake_hf.py)."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("PRAMAAN_DATA", tempfile.mkdtemp(prefix="pramaan_aigi_"))


class AIGI(unittest.TestCase):
    def _run(self, p_ai: float):
        os.environ["FAKE_P_AI"] = str(p_ai)
        import _fake_hf
        importlib.reload(_fake_hf)
        from pramaan.analyzers.image import dl_plugins
        dl_plugins._hf = None
        from pramaan.analyzers.base import Context
        from pramaan import fusion
        img = ROOT / "demo_samples" / "images" / "06_whatsapp_forward.jpg"
        if not img.exists():
            self.skipTest("run scripts/make_demo_samples.py")
        w = Path(tempfile.mkdtemp())
        f = dl_plugins.aigi(Context("x", img, w, w, "image"))[0]
        return f, fusion.fuse([f.to_dict()], "image")

    def test_confident_ai_is_flagged_but_not_strong(self):
        f, v = self._run(0.99)
        self.assertEqual(f.status, "fail")
        self.assertIn("99%", f.title)
        self.assertLess(f.llr * f.reliability, 4.0)  # one classifier alone never reaches "Strong"

    def test_confident_real_supports_authenticity(self):
        f, _ = self._run(0.02)
        self.assertLess(f.llr, 0)

    def test_ambiguous_stays_neutral(self):
        f, _ = self._run(0.6)
        self.assertLess(abs(f.llr * f.reliability), 0.5)

    @classmethod
    def tearDownClass(cls):
        for m in ("torch", "transformers"):
            sys.modules.pop(m, None)
        from pramaan.analyzers.image import dl_plugins
        dl_plugins._hf = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
