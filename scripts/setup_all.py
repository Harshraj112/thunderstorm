#!/usr/bin/env python3
"""One-command setup for PRAMAAN. Run from the project folder, with internet, once:

    python scripts/setup_all.py              # core + AI-image detector + demo samples   (recommended)
    python scripts/setup_all.py --all        # … plus TruFor and the Next.js dashboard build
    python scripts/setup_all.py --trufor     # just add TruFor later
    python scripts/setup_all.py --check      # only report what is installed

Options
    --trufor            clone TruFor, download + verify its weights, run it with THIS Python (needs only torch,
                        timm, yacs, tqdm – a few MB on top of the AI-model install)
    --trufor-conda      instead build TruFor's original 2022 conda env (Python 3.7, PyTorch 1.11, ~2 GB).
                        Does NOT work on very new Linux (glibc ≥ 2.41 refuses old PyTorch: "cannot enable
                        executable stack") – use the default instead
    --trufor-zip PATH   use a TruFor_weights.zip you downloaded yourself (no weights download)
    --gpu               use an NVIDIA GPU for TruFor (default: CPU)
    --frontend          npm install + npm run build for the Next.js dashboard (needs Node.js 18+)
    --cpu-torch         install the small CPU-only PyTorch build (recommended without an NVIDIA GPU)
    --skip-ml           do not install PyTorch / the AI-image detector
    --dry-run           print what would run, change nothing

System tools (ffmpeg, tesseract, git, conda, node) cannot be installed portably from Python; the script
checks them and prints the exact install command for your OS.
Everything it decides is saved to models/local_settings.json, which PRAMAAN reads on start.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
SETTINGS = MODELS / "local_settings.json"
TRUFOR_REPO = "https://github.com/grip-unina/TruFor"
TRUFOR_WEIGHTS_URL = "https://www.grip.unina.it/download/prog/TruFor/TruFor_weights.zip"
TRUFOR_WEIGHTS_MD5 = "7bee48f3476c75616c3c5721ab256ff8"          # published in TruFor's README
TRUFOR_DIR = MODELS / "TruFor" / "TruFor_train_test"
OS = platform.system()          # Windows / Linux / Darwin

RESULTS: list[tuple[str, str, str]] = []      # (step, status, note)
DRY = False


# ------------------------------------------------------------------ helpers
def say(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def record(step: str, ok: bool | None, note: str = "") -> None:
    RESULTS.append((step, {True: "OK", False: "FAILED", None: "SKIPPED"}[ok], note))


def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess | None:
    print("   $", " ".join(str(a) for a in args), flush=True)
    if DRY:
        return None
    return subprocess.run([str(a) for a in args], cwd=cwd, check=check)


def pip(*args: str) -> bool:
    try:
        run([sys.executable, "-m", "pip", "install", *args])
        return True
    except subprocess.CalledProcessError:
        return False


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except Exception:
        return {}


def save_settings(**kv) -> None:
    s = load_settings()
    s.update({k: v for k, v in kv.items() if v is not None})
    if not DRY:
        MODELS.mkdir(exist_ok=True)
        SETTINGS.write_text(json.dumps(s, indent=2))
    print(f"   saved to {SETTINGS.relative_to(ROOT)}: {kv}")


def hint(tool: str) -> str:
    table = {
        "ffmpeg": {"Windows": "winget install Gyan.FFmpeg", "Linux": "sudo apt install ffmpeg", "Darwin": "brew install ffmpeg"},
        "tesseract": {"Windows": "winget install UB-Mannheim.TesseractOCR  (then add C:\\Program Files\\Tesseract-OCR to PATH)",
                      "Linux": "sudo apt install tesseract-ocr", "Darwin": "brew install tesseract"},
        "git": {"Windows": "winget install Git.Git", "Linux": "sudo apt install git", "Darwin": "xcode-select --install"},
        "conda": {"Windows": "winget install Anaconda.Miniconda3", "Linux": "https://docs.conda.io/en/latest/miniconda.html",
                  "Darwin": "brew install --cask miniconda"},
        "node": {"Windows": "winget install OpenJS.NodeJS.LTS", "Linux": "https://nodejs.org (LTS)", "Darwin": "brew install node"},
    }
    return table[tool].get(OS, table[tool]["Linux"])


def find_conda() -> str | None:
    for name in ("conda", "mamba", "micromamba"):
        p = shutil.which(name)
        if p:
            return p
    for base in (os.environ.get("CONDA_EXE"), Path.home() / "miniconda3", Path.home() / "anaconda3",
                 Path.home() / "AppData/Local/miniconda3", Path("C:/ProgramData/miniconda3")):
        if not base:
            continue
        base = Path(base)
        for c in (base, base / "condabin" / "conda.bat", base / "Scripts" / "conda.exe", base / "bin" / "conda"):
            if c.is_file():
                return str(c)
    return None


# ------------------------------------------------------------------ steps
def check_system() -> None:
    say("Checking system tools")
    if sys.version_info < (3, 10):
        record("Python ≥ 3.10", False, f"found {platform.python_version()}")
    else:
        record("Python ≥ 3.10", True, platform.python_version())
    for tool, exe, required in (("ffmpeg", "ffmpeg", True), ("tesseract", "tesseract", True)):
        if shutil.which(exe):
            record(tool, True, "found")
        else:
            record(tool, False if required else None, f"install it: {hint(tool)}")


def install_core() -> None:
    say("Installing core Python requirements")
    record("requirements.txt", pip("-r", str(ROOT / "requirements.txt")))


def install_ml(cpu_torch: bool) -> bool:
    say("Installing the deep-learning extras (PyTorch + transformers)")
    torch_args = ["torch", "--index-url", "https://download.pytorch.org/whl/cpu"] if cpu_torch else ["torch"]
    ok = pip(*torch_args) and pip("-r", str(ROOT / "requirements-ml.txt"))
    record("PyTorch + transformers", ok, "CPU build" if cpu_torch else "")
    for extra, why in (("c2pa-python", "Content Credentials verification"), ("open_clip_torch", "CLIP probe (optional)")):
        record(extra, pip(extra) or None, why)          # optional: failure is reported as SKIPPED
    return ok


def install_ai_model() -> None:
    say("Downloading the AI-image classifier (≈370 MB, once)")
    hf_home = str(MODELS / "hf")
    save_settings(HF_HOME=hf_home)
    if DRY:
        return record("AI-image classifier", None, "dry run")
    env = {**os.environ, "HF_HOME": hf_home}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "install_models.py")], env=env)
    record("AI-image classifier", r.returncode == 0, "models/hf" if r.returncode == 0 else "see the error above")
    say("Downloading the synthetic-voice detector (≈380 MB, once)")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "install_models.py"), "--voice"], env=env)
    record("Synthetic-voice detector", r.returncode == 0, "models/hf" if r.returncode == 0 else "see the error above")


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    print(f"   downloading {url}", flush=True)
    if DRY:
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            done += len(b)
            if total:
                print(f"\r   {done / 1e6:7.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    tmp.replace(dest)


def place_trufor_weights(zip_path: Path) -> bool:
    """Unzip so that pretrained_models/trufor.pth.tar exists, whatever folder layout the zip uses."""
    target = TRUFOR_DIR / "pretrained_models" / "trufor.pth.tar"
    if DRY:
        return True
    got = md5(zip_path)
    if got != TRUFOR_WEIGHTS_MD5:
        print(f"   ! MD5 {got} does not match TruFor's published {TRUFOR_WEIGHTS_MD5} – the download is corrupt "
              "or a different version. Delete it and try again.")
        return False
    print("   MD5 verified")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        member = next((m for m in z.namelist() if m.endswith("trufor.pth.tar")), None)
        if not member:
            print(f"   ! trufor.pth.tar not found inside {zip_path.name}: {z.namelist()[:5]}")
            return False
        with z.open(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    print(f"   weights in place: {target.relative_to(ROOT)}")
    return True


def env_python(conda: str, env_name: str) -> str | None:
    if DRY:
        return f"<{env_name} python>"
    r = subprocess.run([conda, "run", "-n", env_name, "python", "-c", "import sys; print(sys.executable)"],
                       capture_output=True, text=True)
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    return lines[-1] if r.returncode == 0 and lines else None


TRUFOR_INFERENCE_PKGS = ["timm", "yacs", "tqdm"]       # all that TruFor's test.py imports besides torch/numpy/PIL


def install_trufor(zip_override: Path | None, gpu: bool, use_conda: bool = False) -> None:
    say("Setting up TruFor (code + weights)")
    git = shutil.which("git")
    if not git:
        return record("TruFor", False, f"git is missing: {hint('git')}")
    # 1. code
    if not (TRUFOR_DIR / "test.py").exists():
        try:
            run([git, "clone", "--depth", "1", TRUFOR_REPO, MODELS / "TruFor"])
        except subprocess.CalledProcessError:
            return record("TruFor", False, "git clone failed (network?) – run the same command again")
    else:
        print("   code already present")
    # 2. which Python runs it
    if use_conda:
        py = _trufor_conda_python()
        if not py:
            return
    else:
        try:
            import torch  # noqa: F401
        except ImportError:
            return record("TruFor", False, "PyTorch is not installed in this Python – run without --skip-ml first")
        print("   using this Python (modern PyTorch) – TruFor's inference needs only: " + ", ".join(TRUFOR_INFERENCE_PKGS))
        if not pip(*TRUFOR_INFERENCE_PKGS):
            return record("TruFor", False, "pip install timm yacs tqdm failed (network?) – run again")
        py = sys.executable
    # 3. weights
    weights = TRUFOR_DIR / "pretrained_models" / "trufor.pth.tar"
    if not weights.exists():
        z = zip_override or (MODELS / "TruFor_weights.zip")
        if not z.exists():
            try:
                download(TRUFOR_WEIGHTS_URL, z)
            except Exception as e:
                return record("TruFor", False, f"weights download failed ({e}). Download {TRUFOR_WEIGHTS_URL} in a "
                                               "browser, then run: python scripts/setup_all.py --trufor --trufor-zip <file>")
        if not place_trufor_weights(z):
            return record("TruFor", False, "weights not usable (see above)")
    else:
        print("   weights already present")
    save_settings(PRAMAAN_TRUFOR_PYTHON=py, PRAMAAN_TRUFOR_GPU="0" if gpu else "-1")
    # 4. prove it works on a real image
    if DRY:
        return record("TruFor", None, "dry run")
    ok, note = _trufor_smoke(py, gpu)
    record("TruFor", ok, note)


def _trufor_conda_python() -> str | None:
    conda = find_conda()
    if not conda:
        record("TruFor", False, f"conda is missing: {hint('conda')}")
        return None
    yaml = TRUFOR_DIR / "trufor_conda.yaml"
    env_name = "trufor"
    if yaml.exists():
        for line in yaml.read_text().splitlines():
            if line.startswith("name:"):
                env_name = line.split(":", 1)[1].strip()
                break
    py = env_python(conda, env_name)
    if not py:
        try:
            run([conda, "env", "create", "-f", yaml], cwd=TRUFOR_DIR)
        except subprocess.CalledProcessError:
            record("TruFor", False, "conda env create failed. On new Linux this is expected (old PyTorch vs new glibc): "
                                    "run without --trufor-conda to use your current Python instead")
            return None
        py = env_python(conda, env_name)
    if not py:
        record("TruFor", False, f"could not locate the python of conda env '{env_name}'")
    return py


def _trufor_smoke(py: str, gpu: bool) -> tuple[bool, str]:
    """Run PRAMAAN's TruFor hook on a bundled test photo – the real model, end to end."""
    img = ROOT / "demo_samples" / "images" / "02_spliced_photoshop.jpg"
    if not img.exists():
        return True, "installed (no demo image to smoke-test yet)"
    print("   smoke test: running TruFor on a demo photo (CPU: about a minute)…", flush=True)
    code = ("import sys, tempfile, json; from pathlib import Path; sys.path.insert(0, r'%s');"
            "from pramaan.analyzers.base import Context; from pramaan.analyzers.image import dl_plugins;"
            "w = Path(tempfile.mkdtemp()); f = dl_plugins.trufor(Context('t', Path(r'%s'), w, w, 'image'))[0];"
            "print(json.dumps({'status': f.status, 'title': f.title, 'why': f.explanation[:300]}))") % (ROOT, img)
    env = {**os.environ, "PRAMAAN_TRUFOR_PYTHON": py, "PRAMAAN_TRUFOR_GPU": "0" if gpu else "-1"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT)
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return False, f"smoke test crashed: {(r.stderr.strip().splitlines() or ['?'])[-1][:200]}"
    if out["status"] == "unavailable":
        return False, f"TruFor did not run: {out['why']}"
    print(f"   TruFor says: {out['title']}")
    return True, f"{'GPU' if gpu else 'CPU'} – smoke test: {out['title']}"


def make_samples() -> None:
    say("Creating the demo samples")
    if (ROOT / "demo_samples" / "expected.json").exists():
        record("Generated demo set", True, "already present")
    else:
        try:
            run([sys.executable, ROOT / "scripts" / "make_demo_samples.py", ROOT / "demo_samples"])
            record("Generated demo set", True, "demo_samples/")
        except subprocess.CalledProcessError:
            record("Generated demo set", False, "needs ffmpeg")
    if (ROOT / "samples" / "real" / "manifest.json").exists():
        record("Real-world samples", True, "bundled in samples/real")
    else:
        try:
            run([sys.executable, ROOT / "scripts" / "fetch_real_samples.py"])
            record("Real-world samples", True, "samples/real")
        except subprocess.CalledProcessError:
            record("Real-world samples", None, "fetch failed – the demo kit still has the generated set")


def build_frontend() -> None:
    say("Building the Next.js dashboard")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        return record("Next.js build", None, f"Node.js not found ({hint('node')}); the prebuilt dashboard is used")
    try:
        run([npm, "install"], cwd=ROOT / "frontend")
        run([npm, "run", "build"], cwd=ROOT / "frontend")
        record("Next.js build", True, "frontend/out (served automatically)")
    except subprocess.CalledProcessError:
        record("Next.js build", False, "npm failed – the prebuilt dashboard in pramaan/web_dist still works")


def final_check() -> None:
    say("Checking what PRAMAAN can run")
    code = ("import json, sys; sys.path.insert(0, r'%s'); from pramaan.api.server import _capabilities; "
            "print(json.dumps(_capabilities()))" % ROOT)
    env = {**os.environ, "PRAMAAN_SEED_DEMO": "0"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT)
    if r.returncode != 0:
        return record("PRAMAAN imports", False, (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    caps = json.loads(r.stdout.strip().splitlines()[-1])
    record("PRAMAAN imports", True)
    for k, v in caps.items():
        status = v["status"] if isinstance(v, dict) else v
        record(f"  detector: {k}", True if status == "ready" else None, status)


def summary() -> int:
    print("\n" + "=" * 72)
    w = max(len(s) for s, _, _ in RESULTS) + 2
    for step, st, note in RESULTS:
        mark = {"OK": "✓", "FAILED": "✗", "SKIPPED": "–"}[st]
        print(f" {mark} {step:<{w}} {st:<8} {note}")
    print("=" * 72)
    failed = [s for s, st, _ in RESULTS if st == "FAILED"]
    if failed:
        print("Fix the ✗ items above, then run this script again (finished steps are skipped).")
    print("Start PRAMAAN:  uvicorn pramaan.api.server:app --port 8000   →  http://localhost:8000")
    return 1 if failed else 0


def main() -> int:
    global DRY
    ap = argparse.ArgumentParser(description="One-command PRAMAAN setup")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--trufor", action="store_true")
    ap.add_argument("--trufor-zip", type=Path)
    ap.add_argument("--trufor-conda", action="store_true")
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--frontend", action="store_true")
    ap.add_argument("--cpu-torch", action="store_true")
    ap.add_argument("--skip-ml", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    DRY = a.dry_run
    os.chdir(ROOT)
    check_system()
    if not a.check:
        only_trufor = (a.trufor or a.trufor_conda or a.trufor_zip) and not a.all and not a.frontend
        if not only_trufor:
            install_core()
            if not a.skip_ml and install_ml(a.cpu_torch):
                install_ai_model()
            make_samples()
        if a.all or a.trufor or a.trufor_zip or a.trufor_conda:
            install_trufor(a.trufor_zip, a.gpu, use_conda=a.trufor_conda)
        if a.all or a.frontend:
            build_frontend()
    final_check()
    return summary()


if __name__ == "__main__":
    sys.exit(main())
