#!/usr/bin/env python3
"""Download the pretrained deep-learning detectors ONCE (needs internet), so PRAMAAN can run them offline afterwards.

    pip install -r requirements-ml.txt
    python scripts/install_models.py            # AI-image classifier (≈370 MB)
    export HF_HUB_OFFLINE=1                     # optional: forbid any further network access

Weights are stored in models/hf/ (Hugging Face cache layout). Copy that folder to the air-gapped lab machine.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "hf"))
sys.path.insert(0, str(ROOT))


def install_voice() -> int:
    from pramaan.analyzers.audio import spoof
    print(f"Downloading voice-deepfake model {spoof.VOICE_MODEL} into {os.environ['HF_HOME']} …", flush=True)
    fe, model, fake, labels = spoof._hf_voice()
    print(f"  loaded: labels {labels}, 'synthetic' = {labels[fake]!r}")
    import numpy as np
    p = spoof._hf_voice_scores([np.zeros(spoof.SR * 4, np.float32) + 1e-3 * np.random.randn(spoof.SR * 4).astype(np.float32)])
    print(f"  smoke test ok (P(synthetic) on noise = {p[0]:.2f})")
    print("Check it on labelled clips from your cases before relying on it: python scripts/eval_dataset.py <folder>")
    return 0


def main() -> int:
    if "--voice" in sys.argv:
        return install_voice()
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        print("PyTorch and transformers are missing. Run:  pip install -r requirements-ml.txt")
        return 1
    from pramaan.analyzers.image import dl_plugins
    print(f"Downloading {dl_plugins.AIGI_MODEL} into {os.environ['HF_HOME']} …", flush=True)
    proc, model, ai_idx, labels = dl_plugins._hf_classifier()
    print(f"  loaded: labels {labels}, 'AI' = {labels[ai_idx]!r}")
    # smoke test on a flat grey image (should not crash; the score itself is meaningless here)
    from PIL import Image
    tmp = ROOT / "models" / "_smoke.png"
    Image.new("RGB", (384, 384), (128, 128, 128)).save(tmp)
    p, views = dl_plugins._hf_score(tmp)
    tmp.unlink()
    print(f"  smoke test ok (P(AI) on a grey square = {p:.2f})")
    print("Done. The AI-image check is now active; restart the PRAMAAN server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
