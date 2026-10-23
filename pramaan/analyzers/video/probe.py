"""Container / codec forensics with ffprobe.

* PTS gaps, non-monotonic PTS, duplicate PTS        -> deleted / reordered / inserted frames
* GOP (I-frame) cadence irregularity                -> cut or splice points in fixed-GOP CCTV streams
* encoder / handler tags                            -> re-muxed or exported by editing software
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from ..base import Context, Finding, status_from_llr

NLE_TAGS = ["premiere", "adobe", "final cut", "davinci", "resolve", "capcut", "kinemaster", "inshot", "filmora",
            "imovie", "vegas", "handbrake", "shotcut", "openshot", "vn video", "movavi", "camtasia", "powerdirector"]


def ffprobe_json(path: Path, args: list[str]) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-of", "json", *args, str(path)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:500])
    return json.loads(r.stdout or "{}")


def probe(path: Path) -> dict:
    return ffprobe_json(path, ["-show_format", "-show_streams"])


def video_frames(path: Path) -> list[dict]:
    d = ffprobe_json(path, ["-select_streams", "v:0", "-show_frames", "-show_entries",
                            "frame=pict_type,key_frame,pts_time,best_effort_timestamp_time,pkt_size,pkt_pos"])
    out = []
    for f in d.get("frames", []):
        t = f.get("pts_time", f.get("best_effort_timestamp_time"))
        if t in (None, "N/A"):
            continue
        out.append({"t": float(t), "type": f.get("pict_type", "?"), "key": int(f.get("key_frame", 0)),
                    "size": int(f.get("pkt_size", 0) or 0)})
    return out


def _rate(s: str | None) -> float:
    try:
        n, d = s.split("/")
        return float(n) / float(d) if float(d) else 0.0
    except Exception:
        return 0.0


def run(ctx: Context) -> list[Finding]:
    info = probe(ctx.path)
    ctx.shared["probe"] = info
    vs = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
               and not s.get("disposition", {}).get("attached_pic")), None)
    out: list[Finding] = []
    if not vs:
        return out
    fps_nom = _rate(vs.get("avg_frame_rate")) or _rate(vs.get("r_frame_rate")) or 25.0
    frames = video_frames(ctx.path)
    ctx.shared["frames"] = frames
    ctx.shared["fps"] = fps_nom
    fmt = info.get("format", {})
    tags = {**{k.lower(): v for k, v in fmt.get("tags", {}).items()},
            **{k.lower(): v for k, v in vs.get("tags", {}).items()}}
    out.append(Finding(code="VID_CONTAINER", module="video", family="metadata", title="Container summary",
                       status="info", explanation=f"{fmt.get('format_long_name')} · {vs.get('codec_name')} "
                       f"{vs.get('width')}x{vs.get('height')} @ {fps_nom:.2f} fps · {len(frames)} frames · "
                       f"duration {float(fmt.get('duration', 0)):.1f}s",
                       data={"format": fmt, "video_stream": {k: vs.get(k) for k in (
                           "codec_name", "profile", "width", "height", "avg_frame_rate", "r_frame_rate", "nb_frames",
                           "duration", "bit_rate", "pix_fmt")}, "tags": tags}))

    # ---- encoder / software tags ----
    blob = json.dumps(tags).lower()
    nle = [t for t in NLE_TAGS if t in blob]
    if nle:
        out.append(Finding(code="VID_EDITOR_TAG", module="video", family="metadata",
                           title=f"Editing software tag: {', '.join(nle)}", status="fail", llr=1.6, reliability=0.75,
                           explanation="Container tags name video-editing software, so the file is an export from an "
                                       "editor, not a native DVR/camera export.",
                           innocent=["Clip trimmed for court presentation (should be documented in custody log)"]))
    elif "lavf" in blob or "lavc" in blob:
        out.append(Finding(code="VID_LAVF", module="video", family="metadata", title="Re-muxed / encoded with FFmpeg (Lavf)",
                           status="info", llr=0.3, reliability=0.4,
                           explanation="The encoder tag shows FFmpeg/libavformat. Native DVR exports rarely carry it; "
                                       "conversion tools and many export utilities do.",
                           innocent=["DVR vendor export tool built on FFmpeg", "Format conversion for playback"]))

    if len(frames) < 10:
        return out
    t = np.array([f["t"] for f in frames])
    dt = np.diff(t)
    nominal = 1.0 / fps_nom
    med = float(np.median(dt)) if len(dt) else nominal
    step = med if med > 0 else nominal

    # ---- non-monotonic & duplicate PTS ----
    back = np.nonzero(dt < -step * 0.5)[0]
    dup = np.nonzero(np.abs(dt) < step * 0.05)[0]
    if len(back):
        out.append(Finding(code="VID_PTS_BACKWARD", module="video", family="temporal-container",
                           title=f"Timestamps go backwards at {len(back)} point(s)", status="fail",
                           llr=2.2, reliability=0.8, raw_score=len(back),
                           location={"times": [round(float(t[i + 1]), 3) for i in back[:20]]},
                           explanation="Presentation timestamps must increase in display order. Backward steps indicate "
                                       "concatenated or reordered segments.",
                           innocent=["Corrupted stream / recorder reboot mid-file"]))
    if len(dup) > 2:
        out.append(Finding(code="VID_PTS_DUP", module="video", family="temporal-container",
                           title=f"{len(dup)} duplicate timestamps", status="warn", llr=0.9, reliability=0.6,
                           location={"times": [round(float(t[i]), 3) for i in dup[:20]]},
                           explanation="Several frames share a timestamp, which happens when segments are joined "
                                       "without re-timing.", innocent=["Variable-frame-rate muxing quirks"]))

    # ---- PTS gaps (deleted frames) ----
    gaps = np.nonzero(dt > step * 1.5)[0]
    vfr = (len(gaps) / max(len(dt), 1)) > 0.05     # many gaps => variable frame rate / motion-triggered recording
    ctx.shared["pts_gaps"] = [float(t[i + 1]) for i in gaps[:200]]
    if len(gaps) and not vfr:
        missing = [{"at": round(float(t[i]), 3), "gap_s": round(float(dt[i]), 3),
                    "missing_frames": int(round(dt[i] / step)) - 1} for i in gaps[:50]]
        total = sum(m["missing_frames"] for m in missing)
        llr = float(min(2.5, 1.0 + 0.3 * len(gaps)))
        out.append(Finding(code="VID_PTS_GAP", module="video", family="temporal-container",
                           title=f"{len(gaps)} timestamp gap(s): ≈{total} frame(s) missing", status=status_from_llr(llr),
                           llr=llr, reliability=0.75, raw_score=total, location={"gaps": missing},
                           explanation="The interval between consecutive frames jumps well beyond the nominal "
                                       f"{step * 1000:.0f} ms, so frames are absent at these points.",
                           innocent=["Dropped frames when the DVR was overloaded",
                                     "Motion-triggered recording pauses (check DVR configuration)"]))
    elif vfr:
        out.append(Finding(code="VID_VFR", module="video", family="temporal-container",
                           title="Irregular frame timing throughout (variable frame rate)", status="info",
                           reliability=0.2, explanation="Frame intervals vary across the file, consistent with "
                           "variable-frame-rate or motion-triggered recording. Gap analysis is down-weighted."))
    else:
        out.append(Finding(code="VID_PTS_GAP", module="video", family="temporal-container",
                           title="Frame timestamps continuous", status="pass", llr=-0.4, reliability=0.6,
                           explanation=f"All {len(frames)} frame intervals are within 1.5× nominal."))

    # ---- GOP cadence ----
    iidx = np.array([i for i, f in enumerate(frames) if f["type"] == "I" or f["key"]])
    if len(iidx) >= 4:
        gops = np.diff(iidx)
        mode = int(np.bincount(gops).argmax())
        irregular = [int(iidx[k]) for k, g in enumerate(gops[:-1]) if abs(g - mode) > max(1, 0.1 * mode)]
        cv = float(np.std(gops[:-1]) / (np.mean(gops[:-1]) + 1e-9)) if len(gops) > 2 else 0.0
        regular_share = float(np.mean(np.abs(gops[:-1] - mode) <= max(1, 0.1 * mode))) if len(gops) > 2 else 1.0
        ctx.shared["gop_breaks"] = [round(float(frames[i]["t"]), 3) for i in irregular]
        if regular_share >= 0.6 and 0 < len(irregular) <= 10:
            out.append(Finding(code="VID_GOP_BREAK", module="video", family="temporal-container",
                               title=f"GOP cadence broken at {len(irregular)} point(s) (normal GOP = {mode} frames)",
                               status="warn", llr=1.2, reliability=0.6, raw_score=cv,
                               location={"times": ctx.shared["gop_breaks"]},
                               explanation="This stream uses a fixed keyframe interval, typical of CCTV encoders. Where "
                                           "the cadence breaks, a new segment usually starts – a cut, insertion or "
                                           "re-encode boundary.",
                               innocent=["Encoder scene-change keyframes", "Recording restarted by motion trigger"]))
        else:
            out.append(Finding(code="VID_GOP", module="video", family="temporal-container",
                               title=f"GOP structure: mode {mode} frames, CV {cv:.2f}", status="info", reliability=0.2,
                               explanation="Keyframe cadence " + ("regular." if not irregular else
                                                                  "irregular throughout (scene-adaptive encoder) – "
                                                                  "not usable as a splice indicator.")))
    ctx.shared["timeline_frames"] = {"t": [round(float(x), 3) for x in t[:: max(1, len(t) // 4000)]],
                                     "i_frames": [round(float(frames[i]["t"]), 3) for i in iidx[:2000]] if len(iidx) else [],
                                     "sizes": [frames[i]["size"] for i in range(0, len(frames), max(1, len(frames) // 4000))]}
    return out
