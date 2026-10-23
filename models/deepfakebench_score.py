#!/usr/bin/env python3
"""Adapter: score face crops with a DeepfakeBench detector (https://github.com/SCLBD/DeepfakeBench).

Setup (GPU box, once):
    git clone https://github.com/SCLBD/DeepfakeBench models/DeepfakeBench
    pip install -r models/DeepfakeBench/install.sh-equivalent (torch, timm, albumentations, ...)
    download a checkpoint from the DeepfakeBench releases page, e.g. ucf_best.pth or xception_best.pth
Then:
    export PRAMAAN_DEEPFAKE_CMD="python models/deepfakebench_score.py --detector xception \
        --config models/DeepfakeBench/training/config/detector/xception.yaml \
        --weights models/xception_best.pth --list {list}"

Prints one fake probability per input crop (same order). DeepfakeBench models expect 256x256 aligned
crops with a 1.3x margin – PRAMAAN's extractor produces exactly that.
"""
import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--root", default=str(Path(__file__).with_name("DeepfakeBench")))
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.root) / "training"))
    import cv2
    import numpy as np
    import torch
    import yaml
    from detectors import DETECTOR  # DeepfakeBench registry

    cfg = yaml.safe_load(open(a.config))
    model = DETECTOR[cfg["model_name"]](cfg)
    state = torch.load(a.weights, map_location="cpu")
    model.load_state_dict(state.get("state_dict", state), strict=False)
    model.eval()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(dev)
    mean = np.array(cfg.get("mean", [0.5, 0.5, 0.5]), np.float32)
    std = np.array(cfg.get("std", [0.5, 0.5, 0.5]), np.float32)
    size = cfg.get("resolution", 256)
    paths = [p.strip() for p in open(a.list) if p.strip()]
    with torch.no_grad():
        for i in range(0, len(paths), 16):
            batch = []
            for p in paths[i:i + 16]:
                im = cv2.cvtColor(cv2.imread(p), cv2.COLOR_BGR2RGB)
                im = cv2.resize(im, (size, size)).astype(np.float32) / 255.0
                batch.append(((im - mean) / std).transpose(2, 0, 1))
            x = torch.from_numpy(np.stack(batch)).to(dev)
            pred = model({"image": x, "label": torch.zeros(len(batch), dtype=torch.long, device=dev)}, inference=True)
            for p in pred["prob"].cpu().numpy().ravel():
                print(f"{float(p):.5f}")


if __name__ == "__main__":
    main()
