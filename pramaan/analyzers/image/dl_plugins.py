"""Optional deep-learning detectors. They switch on automatically when their weights exist;
otherwise they report 'unavailable' (and are excluded from fusion) instead of guessing.

* TruFor (CVPR 2023, GRIP-UNINA) – manipulation localisation + reliability map.
  Install (see README "Install TruFor"): clone https://github.com/grip-unina/TruFor into models/TruFor, create its
  conda env (TruFor_train_test/trufor_conda.yaml) and unzip TruFor_weights.zip into
  models/TruFor/TruFor_train_test/pretrained_models/. PRAMAAN then runs TruFor's OWN test.py:
      python test.py -g -1 -in <img> -out <dir> -exp trufor_ph3 TEST.MODEL_FILE pretrained_models/trufor.pth.tar
  with PRAMAAN_TRUFOR_PYTHON = the python of the TruFor env. Output .npz: 'map', 'conf', 'score' in [0,1].
  PRAMAAN_TRUFOR_CMD ("... {inp} ... {out}") still works for a custom/Docker setup.

* CLIP linear probe for AI-generated images (Ojha et al. CVPR 2023 / Cozzolino et al. CVPRW 2024):
  frozen open_clip ViT-L/14 features + logistic regression trained by scripts/train_aigi_probe.py
  -> models/aigi_probe.npz.  A CLIP linear probe reaches ~81% on in-the-wild deepfakes, close to the
  best commercial detector (Deepfake-Eval-2024), so it is a strong, cheap baseline.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import numpy as np

from ... import config
from ..base import Context, Finding, calibrated, save_heatmap, status_from_llr, unavailable
from .common import load_rgb


TRUFOR_DIR = Path(os.environ.get("PRAMAAN_TRUFOR_DIR", str(config.MODELS_DIR / "TruFor" / "TruFor_train_test")))
TRUFOR_WEIGHTS = Path(os.environ.get("PRAMAAN_TRUFOR_WEIGHTS", str(TRUFOR_DIR / "pretrained_models" / "trufor.pth.tar")))


def trufor_status() -> tuple[bool, str]:
    """(ready, explanation) – used by the analyzer and by /api/health."""
    if os.environ.get("PRAMAAN_TRUFOR_CMD"):
        return True, "custom command"
    if not (TRUFOR_DIR / "test.py").exists():
        return False, f"TruFor code not found in {TRUFOR_DIR} (see README: Install TruFor)"
    if not TRUFOR_WEIGHTS.exists():
        return False, f"weights missing: unzip TruFor_weights.zip so that {TRUFOR_WEIGHTS} exists"
    return True, "ready"


TRUFOR_MAX_SIDE = int(os.environ.get("PRAMAAN_TRUFOR_MAX_SIDE", "1600"))   # CPU time grows with pixel count
TRUFOR_TIMEOUT = int(os.environ.get("PRAMAAN_TRUFOR_TIMEOUT", "300"))       # seconds per image, then give up
_trufor_lock = __import__("threading").Lock()   # one TruFor at a time: two parallel runs starve each other of CPU/RAM


def _trufor_input(src: Path, work: Path) -> Path:
    """Downscale very large photos (a 12 MP phone image takes minutes on CPU). Maps come back at this size and are
    scaled to the original's pixel grid afterwards, so boxes still land on the right place."""
    from PIL import Image
    with Image.open(src) as im:
        if max(im.size) <= TRUFOR_MAX_SIDE:
            return src
        im = im.convert("RGB")
        im.thumbnail((TRUFOR_MAX_SIDE, TRUFOR_MAX_SIDE), Image.LANCZOS)
        out = work / "trufor_input.png"            # PNG: no extra JPEG generation
        im.save(out)
        return out


def _trufor_run(inp: Path, out: Path) -> None:
    cmd = os.environ.get("PRAMAAN_TRUFOR_CMD")
    if cmd:
        subprocess.run(shlex.split(cmd.format(inp=inp, out=out)), check=True, timeout=TRUFOR_TIMEOUT, capture_output=True)
        return
    import sys
    py = os.environ.get("PRAMAAN_TRUFOR_PYTHON", sys.executable)    # the Python that runs TruFor
    args = [py, "test.py", "-g", os.environ.get("PRAMAAN_TRUFOR_GPU", "-1"), "-in", str(inp.resolve()),
            "-out", str(out.resolve()), "-exp", "trufor_ph3", "TEST.MODEL_FILE", str(TRUFOR_WEIGHTS.resolve())]
    # PyTorch >= 2.6 loads checkpoints with weights_only=True and rejects TruFor's 2022 checkpoint (it holds
    # non-tensor entries). The file is MD5-verified against TruFor's published checksum by setup_all.py, so
    # full loading is allowed for this subprocess only.
    threads = str(max(1, config.usable_cpus() - (0 if config.usable_cpus() <= 2 else 1)))
    env = {**os.environ, "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1", "OMP_NUM_THREADS": threads, "MKL_NUM_THREADS": threads}
    r = subprocess.run(args, cwd=TRUFOR_DIR, timeout=TRUFOR_TIMEOUT, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1][:300] if (r.stderr or r.stdout) else
                           f"exit code {r.returncode}")


def trufor(ctx: Context) -> list[Finding]:
    ready, why = trufor_status()
    if not ready:
        return [unavailable("IMG_TRUFOR", "image", "pixel", "TruFor localiser",
                            f"Not installed: {why}. Classical localisers are used instead.")]
    out_dir = ctx.work_dir / "trufor"
    out_dir.mkdir(exist_ok=True)
    try:
        inp = _trufor_input(ctx.path, ctx.work_dir)
        if _trufor_lock.locked():
            ctx.progress("waiting for TruFor (one photo at a time)")
        with _trufor_lock:
            ctx.progress(f"running TruFor on {'GPU' if os.environ.get('PRAMAAN_TRUFOR_GPU', '-1') != '-1' else 'CPU'} "
                         f"(up to {TRUFOR_TIMEOUT // 60} min)")
            _trufor_run(inp, out_dir)
        npz = next(out_dir.rglob("*.npz"))
        res = np.load(npz)
    except subprocess.TimeoutExpired:
        return [unavailable("IMG_TRUFOR", "image", "pixel", "TruFor localiser",
                            f"TruFor took longer than {TRUFOR_TIMEOUT} s and was stopped (CPU too slow or out of memory). "
                            "Other checks are unaffected. Raise PRAMAAN_TRUFOR_TIMEOUT or lower PRAMAAN_TRUFOR_MAX_SIDE.")]
    except StopIteration:
        return [unavailable("IMG_TRUFOR", "image", "pixel", "TruFor localiser", "TruFor ran but wrote no .npz output")]
    except Exception as e:
        return [unavailable("IMG_TRUFOR", "image", "pixel", "TruFor localiser", f"TruFor failed: {e}")]
    score = float(res["score"])
    heat = res["map"].astype(float)
    conf = res["conf"].astype(float) if "conf" in res else np.ones_like(heat)
    rgb = load_rgb(ctx.path)
    art = save_heatmap(rgb, heat, ctx.artifact("trufor_map.png"))
    art2 = save_heatmap(rgb, conf, ctx.artifact("trufor_conf.png"))
    rel = float(np.clip(conf.mean(), 0.2, 0.9))
    llr = calibrated("trufor_score", score, a=6.0, b=-3.0, cap=3.5)
    # where does TruFor point? bounding box of the confident high-anomaly area (for the dashboard / report)
    loc = {}
    hot = (heat >= 0.5) & (conf >= 0.5)
    if hot.mean() > 0.002 and score >= 0.5:
        from PIL import Image
        ys, xs = np.nonzero(hot)
        W, H = Image.open(ctx.path).size
        sx, sy = W / heat.shape[1], H / heat.shape[0]
        loc = {"bbox": {"x": int(xs.min() * sx), "y": int(ys.min() * sy), "w": int((xs.max() - xs.min() + 1) * sx),
                        "h": int((ys.max() - ys.min() + 1) * sy)}, "area": round(float(hot.mean()), 4)}
    return [Finding(code="IMG_TRUFOR", module="image", family="pixel",
                    title=f"TruFor: {score:.0%} likely manipulated" + (" (region localised)" if loc else ""),
                    status=status_from_llr(llr), raw_score=score, llr=llr, reliability=rel, location=loc,
                    explanation="TruFor (CVPR 2023) combines the RGB image with a learned camera-noise fingerprint "
                                "(Noiseprint++) to find edited regions, and outputs a confidence map saying where its "
                                f"own map can be trusted (mean confidence here {conf.mean():.0%}).",
                    innocent=["Fully AI-generated images are out of scope for TruFor (see the AI-image detector)",
                              "Very dark, saturated or heavily compressed areas lower its confidence"],
                    artifacts=[art, art2])]


def catnet(ctx: Context) -> list[Finding]:
    """CAT-Net v2 (Kwon et al., WACV 2021 / IJCV 2022): RGB + DCT-coefficient stream pretrained on
    double-JPEG detection – complements TruFor on JPEG evidence (WhatsApp/phone photos).

    The official repo's tools/infer.py reads input/ and writes heat maps to output_pred/. Wire it as:
      PRAMAAN_CATNET_CMD="bash -c 'cp {inp} models/CAT-Net/input/ && cd models/CAT-Net &&
                          python tools/infer.py && cp output_pred/* {out}/'"
    Any .npy (float map) or .png (grey heat map) written to {out} is read back.
    """
    from .common import is_jpeg
    cmd = os.environ.get("PRAMAAN_CATNET_CMD")
    if not is_jpeg(ctx.path):
        return []
    if not cmd:
        return [unavailable("IMG_CATNET", "image", "compression", "CAT-Net (DCT-domain) localiser",
                            "CAT-Net weights not installed (set PRAMAAN_CATNET_CMD). JPEG-ghost and quantisation "
                            "analysis cover the compression domain meanwhile.")]
    out_dir = ctx.work_dir / "catnet"
    out_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(shlex.split(cmd.format(inp=ctx.path, out=out_dir)), check=True, timeout=900,
                       capture_output=True)
        f = next(iter(sorted(out_dir.glob("*.npy")) + sorted(out_dir.glob("*.png"))))
        if f.suffix == ".npy":
            heat = np.load(f).astype(float)
        else:
            from PIL import Image
            heat = np.asarray(Image.open(f).convert("L"), float) / 255.0
    except Exception as e:
        return [unavailable("IMG_CATNET", "image", "compression", "CAT-Net (DCT-domain) localiser",
                            f"CAT-Net failed: {e}")]
    heat = (heat - heat.min()) / (heat.max() - heat.min() + 1e-9)
    score = float(np.mean(np.sort(heat.ravel())[-max(1, heat.size // 100):]))   # top-1 % mean
    area = float((heat > 0.5).mean())
    rgb = load_rgb(ctx.path)
    art = save_heatmap(rgb, heat, ctx.artifact("catnet_map.png"))
    llr = calibrated("catnet_score", score * (1 if area < 0.6 else 0.3), a=5.0, b=-3.0, cap=3.0)
    return [Finding(code="IMG_CATNET", module="image", family="compression",
                    title=f"CAT-Net: {'localised manipulation' if llr >= 0.5 else 'no clear manipulation'} "
                          f"(peak {score:.2f}, area {area:.0%})",
                    status=status_from_llr(llr), raw_score=score, llr=llr, reliability=0.6,
                    explanation="CAT-Net reads JPEG DCT coefficients directly; regions whose compression history "
                                "differs from the rest (pasted from another JPEG) light up.",
                    innocent=["Non-aligned double compression of the whole image can produce diffuse maps"],
                    artifacts=[art])]


_clip = None


AIGI_MODEL = os.environ.get("PRAMAAN_AIGI_MODEL", "Ateeqq/ai-vs-human-image-detector")
_hf = None
_hf_lock = __import__("threading").Lock()


def _hf_classifier():
    """Pretrained real-vs-AI image classifier from the Hugging Face Hub (default: Ateeqq/ai-vs-human-image-detector,
    SigLIP-2, Apache-2.0, fine-tuned on 60k AI + 60k real images incl. Midjourney v6.1, Flux 1.1, SD 3.5, GPT-4o).
    Weights are cached under models/hf after the first download (scripts/install_models.py), then it runs offline."""
    global _hf
    with _hf_lock:
        if _hf is None:
            os.environ.setdefault("HF_HOME", str(config.MODELS_DIR / "hf"))
            import torch  # raises ImportError if the ML extras are not installed
            if hasattr(torch, "set_num_threads"):       # containers report the host's cores, not the plan's
                torch.set_num_threads(config.usable_cpus())
            from transformers import AutoImageProcessor, AutoModelForImageClassification
            offline = os.environ.get("HF_HUB_OFFLINE") == "1"
            proc = AutoImageProcessor.from_pretrained(AIGI_MODEL, local_files_only=offline)
            model = AutoModelForImageClassification.from_pretrained(AIGI_MODEL, local_files_only=offline).eval()
            labels = {int(k): str(v).lower() for k, v in model.config.id2label.items()}
            ai_idx = [i for i, l in labels.items() if any(t in l for t in ("ai", "fake", "artificial", "generated", "synthetic"))
                      and not any(t in l for t in ("hum", "real"))]
            if len(ai_idx) != 1:
                raise RuntimeError(f"cannot tell which label means AI in {labels}")
            _hf = (proc, model, ai_idx[0], labels)
    return _hf


def _hf_score(path: Path) -> tuple[float, list[float]]:
    """P(AI) averaged over the whole image and two crops (test-time augmentation: a single crop can be fooled
    by borders, stickers or captions)."""
    import torch
    from PIL import Image
    proc, model, ai, _ = _hf_classifier()
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = min(w, h)
    views = [im, im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2)),
             im.crop((0, 0, s, s)) if h > w else im.crop((w - s, 0, w, s))]
    with torch.no_grad():
        logits = model(**proc(images=views, return_tensors="pt")).logits
    probs = torch.softmax(logits, dim=-1)[:, ai].tolist()
    return float(np.mean(probs)), [round(float(p), 4) for p in probs]


def aigi(ctx: Context) -> list[Finding]:
    if AIGI_MODEL:
        try:
            return [_aigi_hf(ctx)]
        except ImportError:
            hf_err = "PyTorch + transformers are not installed (pip install -r requirements-ml.txt)"
        except Exception as e:  # model missing offline, download blocked, …
            hf_err = f"{AIGI_MODEL} could not be loaded ({str(e)[:160]}). Run scripts/install_models.py once with internet"
    else:
        hf_err = "disabled (PRAMAAN_AIGI_MODEL is empty)"
    probe = config.MODELS_DIR / "aigi_probe.npz"
    if not probe.exists():
        return [unavailable("IMG_AIGI", "image", "synthetic-image", "AI-generated image detector",
                            f"Not installed: {hf_err}. Until it is, an AI image without generator metadata cannot be "
                            "recognised, so such images come back Inconclusive.")]
    try:
        feats = clip_features([ctx.path])
    except Exception as e:
        return [unavailable("IMG_AIGI", "image", "synthetic-image", "AI-generated image detector",
                            f"open_clip/torch not available: {e}")]
    p = np.load(probe)
    logit = float(feats[0] @ p["w"] + p["b"])
    llr = calibrated("aigi_clip", logit, a=1.0, b=0.0, cap=3.0)
    return [Finding(code="IMG_AIGI", module="image", family="synthetic-image",
                    title=f"AI-generated image detector (CLIP probe) logit {logit:+.2f}",
                    status=status_from_llr(llr), raw_score=logit, llr=llr, reliability=0.55,
                    explanation="Frozen CLIP ViT-L/14 features with a linear probe trained on real vs generated images. "
                                "Generalises better than CNN detectors but is not decisive on its own.",
                    innocent=["Heavy beautification filters can look synthetic",
                              "New generators may be under-detected (arms race)"])]


def _aigi_hf(ctx: Context) -> Finding:
    from PIL import Image
    p_ai, views = _hf_score(ctx.path)
    p = min(max(p_ai, 1e-4), 1 - 1e-4)
    logit = float(np.log(p / (1 - p)))
    # Conservative: the model card reports 99 % on its own test split, but in-the-wild accuracy of every AI-image
    # detector is far lower, so the classifier alone can reach "Indications", never "Strong indications".
    llr = calibrated("aigi_hf", logit, a=0.8, b=0.0, cap=3.0)
    w, h = Image.open(ctx.path).size
    q = ctx.shared.get("jpeg_quality", 95)
    rel = 0.6
    notes = []
    if min(w, h) < 320:
        rel = 0.35
        notes.append("the image is very small")
    elif q < 60:
        rel = 0.45
        notes.append(f"heavy JPEG compression (quality ≈{q})")
    agree = max(views) - min(views) < 0.5
    if not agree:
        rel = min(rel, 0.4)
        notes.append("the crops disagree")
    verdict = ("looks AI-generated" if p_ai >= 0.8 else "looks like a camera photo" if p_ai <= 0.2 else "is ambiguous")
    return Finding(
        code="IMG_AIGI", module="image", family="synthetic-image",
        title=f"AI-image classifier: {p_ai:.0%} likely AI-generated",
        status=status_from_llr(llr), raw_score=p_ai, llr=llr, reliability=rel,
        location={"views": views, "model": AIGI_MODEL},
        explanation=(f"A pretrained real-vs-AI classifier ({AIGI_MODEL}) scored the whole image and two crops "
                     f"({', '.join(f'{v:.0%}' for v in views)}); on average the image {verdict}. "
                     + (f"Trust reduced because {', '.join(notes)}. " if notes else "")
                     + "Detectors trained on today's generators miss tomorrow's, so this is one signal among several."),
        innocent=["Beauty filters, AR stickers or heavy retouching can look synthetic",
                  "Screenshots, renders and digital art are not camera photos but are not forgeries either"])


def clip_features(paths: list[Path]) -> np.ndarray:
    global _clip
    import open_clip  # type: ignore
    import torch  # type: ignore
    from PIL import Image
    if _clip is None:
        model, _, pre = open_clip.create_model_and_transforms("ViT-L-14", pretrained="openai")
        model.eval()
        _clip = (model, pre)
    model, pre = _clip
    with torch.no_grad():
        x = torch.stack([pre(Image.open(p).convert("RGB")) for p in paths])
        f = model.encode_image(x)
        f = f / f.norm(dim=-1, keepdim=True)
    return f.cpu().numpy()
