#!/usr/bin/env python3
"""KEYZER screenshot factory — render marketing / README shots with no hardware.

Drives the app in demo mode (KEYZER_DEMO=1) through the KEYZER_* QA hooks and
saves a curated set of PNGs to docs/screenshots/. With --gif it also stitches a
looping tour GIF (needs ImageMagick's `convert` on PATH).

    python3 tools/make_screenshots.py            # all stills
    python3 tools/make_screenshots.py --gif      # stills + a tour.gif

No Razer hardware, input-remapper, or OpenRazer needed — demo mode simulates the
devices, lighting, and capture state. This is the same offscreen render path the
test gate uses (tools/render_check.py), aimed at content instead of CI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app" / "main.py"
OUT = REPO / "docs" / "screenshots"
# Demo mode + a generous render size for crisp shots; offscreen so it needs no display.
BASE = {"QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
        "QSG_RENDER_LOOP": "basic", "KEYZER_DEMO": "1",
        "KEYZER_W": "1600", "KEYZER_H": "1000"}

# (filename, extra env hooks) — a curated tour of the app.
SHOTS = [
    ("01-keypad.png",    {"KEYZER_DEV": "tartarus", "KEYZER_VIEW": "main"}),
    ("02-bind.png",      {"KEYZER_DEV": "tartarus", "KEYZER_SELECT": "TAR_08"}),
    ("03-calibrate.png", {"KEYZER_DEV": "tartarus", "KEYZER_CALIBRATE": "1"}),
    ("04-thumb.png",     {"KEYZER_DEV": "tartarus", "KEYZER_VIEW": "thumb"}),
    ("05-mouse.png",     {"KEYZER_DEV": "naga", "KEYZER_VIEW": "side"}),
    ("06-lighting.png",  {"KEYZER_DEV": "naga", "KEYZER_LIGHTPANEL": "1"}),
    ("07-live.png",      {"KEYZER_DEV": "tartarus", "KEYZER_LIVE": "1"}),
]
GIF_FRAMES = ["01-keypad.png", "03-calibrate.png", "06-lighting.png", "05-mouse.png"]


def render(name: str, extra: dict) -> bool:
    dest = OUT / name
    env = {**os.environ, **BASE, **extra, "KEYZER_SHOT": str(dest)}
    try:
        subprocess.run([sys.executable, str(APP)], env=env,
                       capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print(f"  FAIL  {name} (offscreen render timed out)")
        return False
    ok = dest.exists() and dest.stat().st_size > 0
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}")
    return ok


def make_gif() -> None:
    frames = [str(OUT / n) for n in GIF_FRAMES if (OUT / n).exists()]
    if not frames:
        print("  GIF   skipped — no frames rendered")
        return
    if not shutil.which("convert"):
        print("  GIF   skipped — install ImageMagick (`convert`) to build tour.gif")
        return
    gif = OUT / "tour.gif"
    subprocess.run(["convert", "-loop", "0", "-delay", "180",
                    *frames, "-resize", "900x", str(gif)], check=False)
    print(f"  GIF   {gif}" if gif.exists() else "  GIF   failed")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"KEYZER screenshots -> {OUT}\n")
    results = [render(n, e) for n, e in SHOTS]
    if "--gif" in sys.argv:
        make_gif()
    n_ok = sum(results)
    print(f"\n{n_ok}/{len(SHOTS)} stills rendered")
    return 0 if n_ok == len(SHOTS) else 1


if __name__ == "__main__":
    sys.exit(main())
