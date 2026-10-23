#!/usr/bin/env python3
"""Fetch REAL-WORLD test material from public GitHub repositories and build tampered versions.

    python scripts/fetch_real_samples.py            # -> samples/real/ + samples/real/manifest.json

Only `git` (HTTPS to github.com) and `ffmpeg` are needed. Files are sparse-checked-out, so only the
selected media are downloaded (~40 MB). Every item records its source repository so the demo can
credit it. These datasets are published for research; check each licence before redistribution.

What you get
  images : real camera JPEGs with original EXIF/thumbnails (ianare/exif-samples), one genuinely
           Photoshop-processed, a real GPS photo + a back-dated copy (EXIF date edited in place,
           pixels untouched), (ManTra-Net web forgeries deliberately excluded: they show real, identifiable people)
  CCTV   : real pedestrian surveillance footage (OpenCV sample vtest.avi, PETS-2009 style) exported
           as a DVR-style file with an OSD clock, plus frame-deletion and loop-insert edits made by
           decoding and re-encoding (exactly how editors do it)
  AV     : a real TV talking-head clip (TaoRuijie/TalkNet-ASD demo) and a dubbed copy with the audio
           shifted, for lip-sync analysis
  audio  : real recordings carrying 50 Hz mains hum (ghua-ac/ENF-WHU-Dataset) and edited copies
           (cut / insert from another recording); a genuine vs voice-cloned speech pair
           (resemble-ai/Resemblyzer demo data)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "real_sources"
OUT = ROOT / "samples" / "real"
MANIFEST: list[dict] = []

SOURCES = {
    "exif": ("ianare/exif-samples", ["/jpg/hdr/iphone_hdr_NO.jpg", "/jpg/mobile/HMD_Nokia_8.3_5G.jpg",
                                     "/jpg/Canon_40D.jpg"]),
    "opencv": ("opencv/opencv", ["/samples/data/vtest.avi"]),
    "haar": ("opencv/opencv@4.x", ["/data/haarcascades/haarcascade_frontalface_default.xml"]),
    "grid": ("rizkiarm/LipNet", ["/evaluation/samples/GRID/*"]),
    "enf": ("ghua-ac/ENF-WHU-Dataset", ["/ENF-WHU-Dataset/H1/001.wav", "/ENF-WHU-Dataset/H1/005.wav",
                                         "/ENF-WHU-Dataset/H0/01.wav"]),
    "voice": ("resemble-ai/Resemblyzer", ["audio_data/donald_trump/real/4glfwiMXgwQ.mp3",
                                          "audio_data/donald_trump/fake/1bB5db0Srrw.mp3"]),
    "font": ("notofonts/notofonts.github.io", ["/fonts/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Regular.ttf",
                                               "/fonts/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Bold.ttf"]),
}


def sh(*args, cwd=None):
    subprocess.run(list(args), check=True, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def fetch(key: str) -> Path:
    repo, paths = SOURCES[key]
    repo, _, branch = repo.partition("@")
    d = CACHE / key
    wanted = [p.lstrip("/") for p in paths if "*" not in p]
    if wanted and all((d / p).exists() for p in wanted):   # already cached: work offline
        return d
    if not (d / ".git").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        print(f"  cloning {repo} (sparse)…", flush=True)
        sh("git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout", "--sparse",
           *(["--branch", branch] if branch else []), f"https://github.com/{repo}", str(d))
    sh("git", "sparse-checkout", "set", "--no-cone", *paths, cwd=d)
    sh("git", "checkout", "-q", cwd=d)
    return d


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def add(name: str, kind: str, tampered: bool | None, title: str, story: str, source: str, expect: str = ""):
    MANIFEST.append({"file": name, "kind": kind, "tampered": tampered, "title": title, "story": story,
                     "source": source, "expect": expect, "real": True})


# ------------------------------------------------------------------ images
def images():
    d = fetch("exif")
    shutil.copy(d / "jpg/hdr/iphone_hdr_NO.jpg", OUT / "R01_iphone_original.jpg")
    add("R01_iphone_original.jpg", "image", False, "iPhone photo, straight from the phone",
        "Full-resolution iPhone JPEG with its original EXIF, GPS block and embedded thumbnail.",
        "github.com/ianare/exif-samples")
    shutil.copy(d / "jpg/Canon_40D.jpg", OUT / "R02_canon_resized_in_gimp.jpg")
    add("R02_canon_resized_in_gimp.jpg", "image", True, "Camera photo re-saved in GIMP",
        "A Canon EOS 40D photo that the dataset author shrank in GIMP. Not a camera original, even though the "
        "camera's EXIF is still inside.", "github.com/ianare/exif-samples", "editing software in metadata")
    shutil.copy(d / "jpg/mobile/HMD_Nokia_8.3_5G.jpg", OUT / "R03_nokia_gps_original.jpg")
    add("R03_nokia_gps_original.jpg", "image", False, "Geotagged phone photo (Nokia 8.3)",
        "Real phone photo whose GPS block records the satellite clock.", "github.com/ianare/exif-samples")
    # back-dated copy: change every copy of the capture timestamp in place (what `exiftool -AllDates-=` does)
    data = (d / "jpg/mobile/HMD_Nokia_8.3_5G.jpg").read_bytes()
    m = re.search(rb"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", data)
    if m:
        orig = datetime.strptime(m.group(0).decode(), "%Y:%m:%d %H:%M:%S")
        data = data.replace(m.group(0), (orig - timedelta(days=3)).strftime("%Y:%m:%d %H:%M:%S").encode())
    (OUT / "R04_nokia_photo_backdated.jpg").write_bytes(data)
    add("R04_nokia_photo_backdated.jpg", "image", True, "Same phone photo, date edited",
        "Capture date moved 3 days earlier with a metadata editor; pixels untouched. The GPS satellite clock "
        "still records the true time.", "github.com/ianare/exif-samples (edited)", "GPS clock vs capture date")
    # ManTra-Net's web forgeries were dropped on purpose: they are doctored photos of real, identifiable people
    # (including private individuals), which is not appropriate material for a demo.


# ------------------------------------------------------------------ CCTV
def cctv():
    haar = fetch("haar") / "data/haarcascades/haarcascade_frontalface_default.xml"
    d = fetch("opencv")
    shutil.copy(haar, ROOT / "pramaan/analyzers/video/haarcascade_frontalface_default.xml")
    src = d / "samples/data/vtest.avi"
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
    start = int(datetime(2026, 9, 21, 19, 42, 5).timestamp())
    ct = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    osd = (f"drawbox=x=0:y=0:w=iw:h=36:color=black@0.85:t=fill,"
           f"drawtext=fontfile={font}:text='%{{pts\\:localtime\\:{start}}}':x=10:y=7:fontsize=22:fontcolor=white,"
           f"drawtext=fontfile={font}:text='CH02 MARKET RD':x=w-tw-10:y=7:fontsize=18:fontcolor=white")
    dvr = OUT / "R11_cctv_real_dvr_export.mp4"
    # "DVR export": native single encode, fixed GOP 25 like most NVRs
    ff("-i", str(src), "-vf", osd, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "25",
       "-keyint_min", "25", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-metadata", f"creation_time={ct}", str(dvr))
    add("R11_cctv_real_dvr_export.mp4", "video", False, "Real street CCTV (DVR export)",
        "Real pedestrian surveillance footage (OpenCV/PETS sample) with a DVR on-screen clock, encoded once.",
        "github.com/opencv/opencv (samples/data/vtest.avi); OSD overlay added")
    # Editor workflow: decode → cut 8 s at 30 s → re-export (new GOP 30)
    ff("-i", str(dvr), "-vf", "select='not(between(t,30,38))',setpts=N/FRAME_RATE/TB", "-c:v", "libx264",
       "-preset", "veryfast", "-crf", "20", "-g", "30", "-keyint_min", "30", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
       "-metadata", f"creation_time={ct}", str(OUT / "R12_cctv_real_8s_deleted.mp4"))
    add("R12_cctv_real_8s_deleted.mp4", "video", True, "Same CCTV with 8 s removed",
        "Eight seconds cut at 0:30 in an editor and the clip re-exported; the picture flows on without a jump.",
        "derived from R11", "OSD clock skip, re-encoding footprint")
    # loop insert: 26–34 s replaced by a copy of 10–18 s (hide a person)
    ff("-i", str(dvr), "-filter_complex",
       "[0:v]trim=0:26,setpts=PTS-STARTPTS[a];[0:v]trim=10:18,setpts=PTS-STARTPTS[b];"
       "[0:v]trim=34,setpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=1[v]", "-map", "[v]",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "30", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
       str(OUT / "R13_cctv_real_loop_insert.mp4"))
    add("R13_cctv_real_loop_insert.mp4", "video", True, "Same CCTV with footage looped",
        "0:26–0:34 overwritten with a copy of 0:10–0:18, the classic way to make a person 'disappear'.",
        "derived from R11", "repeated footage, OSD reversal")


def av():
    """Adult speaker from the GRID audio-visual corpus (Cooke et al. 2006), samples bundled with LipNet."""
    d = fetch("grid") / "evaluation/samples/GRID"
    clips = sorted(d.glob("*.mpg"))
    lst = OUT / "_grid_list.txt"
    lst.write_text("".join(f"file '{c}'\n" for c in clips))
    orig = OUT / "R14_statement_original.mp4"
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
       "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", str(orig))
    lst.unlink()
    add("R14_statement_original.mp4", "video", False, "Recorded statement (real speaker)",
        "Frontal recording of a speaker reading sentences; lips and sound in sync.",
        "GRID audio-visual corpus via github.com/rizkiarm/LipNet")
    ff("-i", str(orig), "-filter_complex", "[0:a]adelay=500:all=1[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy",
       "-c:a", "aac", "-b:a", "128k", "-shortest", str(OUT / "R15_statement_audio_relaid.mp4"))
    add("R15_statement_audio_relaid.mp4", "video", True, "Same statement, audio re-laid",
        "The sound track was exported and laid back 500 ms late – as when a statement's audio is replaced.",
        "derived from R14", "lip-sync offset")
    # partial re-voicing: second half of the speech replaced with OTHER sentences by the same voice
    ff("-i", str(orig), "-filter_complex",
       "[0:a]atrim=0:15,asetpts=PTS-STARTPTS[a];[0:a]atrim=15:30,asetpts=PTS-STARTPTS,atempo=0.8[b];[a][b]concat=n=2:v=0:a=1[aout]",
       "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
       str(OUT / "R16_statement_words_replaced.mp4"))
    add("R16_statement_words_replaced.mp4", "video", True, "Same statement, words replaced",
        "From 0:15 the speech was replaced by a re-recording at a different pace (same voice); the picture is untouched.",
        "derived from R14", "lip-sync breaks mid-clip")


def audio():
    d = fetch("enf")
    h1 = d / "ENF-WHU-Dataset/H1/001.wav"
    other = d / "ENF-WHU-Dataset/H1/005.wav"
    base = OUT / "R20_room_recording_50hz.wav"
    ff("-i", str(h1), "-t", "60", "-ac", "1", str(base))
    add("R20_room_recording_50hz.wav", "audio", False, "Real room recording with mains hum",
        "Genuine recording made on a 50 Hz power grid; its electrical-network-frequency trace is continuous.",
        "github.com/ghua-ac/ENF-WHU-Dataset (H1)")
    ff("-i", str(h1), "-filter_complex",
       "[0:a]atrim=0:24,asetpts=PTS-STARTPTS[a];[0:a]atrim=27.3:63.3,asetpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=0:a=1",
       "-ac", "1", str(OUT / "R21_room_recording_cut.wav"))
    add("R21_room_recording_cut.wav", "audio", True, "Same recording, 3.3 s cut out",
        "A sentence removed at 0:24 and the ends joined seamlessly.", "derived from R20",
        "hard case – ENF abstains")
    ff("-i", str(h1), "-i", str(other), "-filter_complex",
       "[0:a]atrim=0:20,asetpts=PTS-STARTPTS[a];[1:a]atrim=100:106,asetpts=PTS-STARTPTS[b];"
       "[0:a]atrim=20:54,asetpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=0:a=1", "-ac", "1",
       str(OUT / "R22_room_recording_insert.wav"))
    add("R22_room_recording_insert.wav", "audio", True, "Same recording with a 6 s insert",
        "Six seconds from a DIFFERENT recording session spliced in at 0:20.", "derived from R20 + ENF-WHU H1/05",
        "background-noise step at the splice")
    ff("-i", str(d / "ENF-WHU-Dataset/H0/01.wav"), "-t", "60", "-ac", "1", str(OUT / "R25_battery_recorder_no_hum.wav"))
    add("R25_battery_recorder_no_hum.wav", "audio", False, "Real recording without mains hum",
        "Captured away from the power grid – the ENF test must abstain rather than guess.",
        "github.com/ghua-ac/ENF-WHU-Dataset (H0)", "ENF abstains")
    v = fetch("voice")
    ff("-i", str(v / "audio_data/donald_trump/real/4glfwiMXgwQ.mp3"), "-t", "25", str(OUT / "R23_speech_genuine.mp3"))
    ff("-i", str(v / "audio_data/donald_trump/fake/1bB5db0Srrw.mp3"), "-t", "25", str(OUT / "R24_speech_voice_clone.mp3"))
    add("R23_speech_genuine.mp3", "audio", False, "Genuine public speech",
        "Real speech recording from the Resemblyzer fake-speech demo.", "github.com/resemble-ai/Resemblyzer")
    add("R24_speech_voice_clone.mp3", "audio", True, "AI voice clone of the same speaker",
        "Synthetic speech generated with a commercial voice-cloning model (Resemblyzer demo).",
        "github.com/resemble-ai/Resemblyzer", "needs a synthetic-voice model (see README)")


def fonts():
    d = fetch("font")
    dst = ROOT / "pramaan" / "report" / "fonts"
    dst.mkdir(exist_ok=True)
    for p in SOURCES["font"][1]:
        shutil.copy(d / p.lstrip("/"), dst / Path(p).name)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    steps = [("Devanagari font", fonts), ("images", images), ("CCTV", cctv), ("talking-head AV", av), ("audio", audio)]
    only = set(sys.argv[1:])
    for name, fn in steps:
        if only and fn.__name__ not in only:
            continue
        print(f"[{name}]", flush=True)
        try:
            fn()
        except subprocess.CalledProcessError as e:
            print(f"  ! {name} failed: {e.stderr.decode()[:300] if e.stderr else e}")
        except Exception as e:  # keep going: one unreachable source must not block the others
            print(f"  ! {name} failed: {e}")
    old = json.loads((OUT / "manifest.json").read_text()) if (OUT / "manifest.json").exists() and only else []
    names = {m["file"] for m in MANIFEST}
    merged = [m for m in old if m["file"] not in names] + MANIFEST
    (OUT / "manifest.json").write_text(json.dumps(sorted(merged, key=lambda m: m["file"]), indent=2))
    print(f"{len(merged)} real samples in {OUT}")
