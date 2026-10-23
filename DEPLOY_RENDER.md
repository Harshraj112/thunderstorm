# Put PRAMAAN online on Render (with TruFor and the AI-image model)

The repo already contains everything Render needs:

| File | What it does |
| --- | --- |
| `Dockerfile.render` | Builds the image: ffmpeg, Tesseract, CPU PyTorch, downloads TruFor + the AI-image model **at build time**, generates the demo exhibits |
| `render.yaml` | Blueprint: one web service, 2 CPU / 4 GB RAM, a 5 GB persistent disk at `/var/data`, health check, password prompt |
| `.dockerignore` | Keeps your local `data/`, `models/TruFor`, `models/hf` and `node_modules` out of the upload |

## 1. Put the code on GitHub

Render deploys from a Git repository. In the `pramaan` folder:

```bash
git init
git add .
git commit -m "PRAMAAN by Team ThunderStorm"
```

Create an **empty** repository on github.com (private is fine), then:

```bash
git remote add origin https://github.com/<your-user>/pramaan.git
git branch -M main
git push -u origin main
```

Do **not** commit `data/` (real case files) or `models/TruFor` / `models/hf` (hundreds of MB). `.gitignore` already
excludes them; the image downloads the models itself.

## 2. Create the service from the Blueprint

1. Sign in at **dashboard.render.com** with GitHub.
2. **New → Blueprint**, pick your `pramaan` repository, branch `main`.
3. Render reads `render.yaml` and asks for **`PRAMAAN_PASSWORD`**. Type a strong password (this protects the whole site).
4. Add a payment method when asked: the `2c-4g` plan and the disk are paid (disk: $0.25 per GB per month, so 5 GB = $1.25).
5. Click **Apply**. The first build takes about 10–20 minutes (PyTorch, ~370 MB AI model, TruFor weights).

Watch the **Logs** tab. A good build ends with lines like:

```
TruFor: ready
 smoke test ok (P(AI) on a grey square = …)
```

If either model fails to download, the build **fails on purpose**, so you never get a site that silently runs without them.
Just click **Manual Deploy → Clear build cache & deploy** to retry.

## 3. Open it

Your URL is `https://pramaan-xxxx.onrender.com` (shown at the top of the service page).

- The browser asks for a login: user **`pramaan`**, password = the one you set.
- On first start the server seeds the demo case and analyses the test exhibits. Give it **5–10 minutes** before
  showing it to anyone (TruFor runs one image at a time on CPU).
- Check `https://…onrender.com/api/health`: `"trufor": "ready"` and `"ai_image": {"status": "ready"}`.

Report QR codes automatically point at your Render URL (`RENDER_EXTERNAL_URL`), and the verification page
`/verify/<report id>` stays public so anyone scanning the QR code can check a report without the password.

## 4. Updating

`git push` to `main` → Render rebuilds and redeploys automatically. Cases, evidence, the custody ledger, the case log,
reports and the lab signing key live on the disk at `/var/data` and survive every deploy.

(With a disk attached, each deploy has a few seconds of downtime. That's normal.)

## Choosing the size

| Plan | RAM | What runs | Notes |
| --- | --- | --- | --- |
| `free` | 512 MB | not suitable | Sleeps after 15 min, no disk: cases vanish on every restart; PyTorch does not fit |
| `1c-2g` | 2 GB | everything **except** TruFor | Build with `WITH_TRUFOR=0` (below) |
| **`2c-4g`** (default) | 4 GB | everything | Recommended for the demo |
| `2c-8g` | 8 GB | everything, larger photos | Use if logs show the service restarting with "out of memory" |

To change the plan, edit `plan:` in `render.yaml` and push, or change it under **Settings → Instance Type**.

**Smaller image without TruFor:** in the service's **Settings → Docker → Docker Build Args**, add `WITH_TRUFOR=0`
(and/or `WITH_AIGI=0`). Those detectors then show "not installed" and every other check still runs.

## Settings you can change (Environment tab)

| Variable | Default | Meaning |
| --- | --- | --- |
| `PRAMAAN_PASSWORD` | (asked) | Site password. Remove it only for a private network |
| `PRAMAAN_USER` | `pramaan` | Login name |
| `PRAMAAN_LAB` | Team ThunderStorm … | Lab name printed on reports |
| `PRAMAAN_WORKERS` | `1` | Exhibits analysed at the same time |
| `PRAMAAN_THREADS` | `2` | CPU threads for the models (match the plan's CPUs) |
| `PRAMAAN_TRUFOR_MAX_SIDE` | `1280` | TruFor input size; lower = faster, less RAM |
| `PRAMAAN_TRUFOR_TIMEOUT` | `240` | Seconds before TruFor gives up on one image |

## Before you put real evidence online

This setup is for demos and judging. The platform was designed to run offline inside a lab; on the internet,
real case material would leave the lab. Use the demo kit and your own test files online, and keep real evidence
on an offline machine (`docker compose up` from the same repo).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Build fails at `install_models.py` or `setup_all.py --trufor` | Network hiccup while downloading; **Clear build cache & deploy** |
| Service keeps restarting, logs say "Killed" / out of memory | Move to `2c-8g`, or set `PRAMAAN_TRUFOR_MAX_SIDE=1024` |
| Exhibits stay on "trufor" for minutes | Normal for the first few after start; each photo takes ~1 min on 2 CPUs |
| Browser keeps asking for the password | Username is `pramaan` (lower case) unless you changed `PRAMAAN_USER` |
| Cases disappeared after a deploy | The disk is missing: check **Disks** on the service page is mounted at `/var/data` |
