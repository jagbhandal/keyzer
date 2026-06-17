#!/usr/bin/env python3
"""KEYZER render gate — load every UI state offscreen and fail on any QML error.

Catches the bugs that only surface when the app actually loads: grouped-brace +
';' parse errors, a property named 'data' shadowing Item's children, undefined
bindings, TypeErrors, etc. Run before committing any UI change:

    python3 tools/render_check.py

Exits non-zero if any state errors or fails to produce a frame.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app" / "main.py"
BASE = {"QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software", "QSG_RENDER_LOOP": "basic"}

# Every meaningful state, driven through the KEYZER_* QA env hooks.
STATES = [
    ("tartarus / keypad", {"KEYZER_DEV": "tartarus", "KEYZER_VIEW": "main"}),
    ("tartarus / keypad + selected", {"KEYZER_DEV": "tartarus", "KEYZER_SELECT": "TAR_08"}),
    ("tartarus / keypad + align", {"KEYZER_DEV": "tartarus", "KEYZER_ALIGN": "1"}),
    ("tartarus / thumb (8-way)", {"KEYZER_DEV": "tartarus", "KEYZER_VIEW": "thumb"}),
    ("tartarus / thumb + align", {"KEYZER_DEV": "tartarus", "KEYZER_VIEW": "thumb", "KEYZER_ALIGN": "1"}),
    ("naga / top", {"KEYZER_DEV": "naga", "KEYZER_VIEW": "top"}),
    ("naga / top + selected", {"KEYZER_DEV": "naga", "KEYZER_VIEW": "top", "KEYZER_SELECT": "NAGA_01"}),
    ("naga / side", {"KEYZER_DEV": "naga", "KEYZER_VIEW": "side"}),
    ("profile / Work", {"KEYZER_PROFILE": "Work"}),
    ("profile / Default", {"KEYZER_PROFILE": "Default"}),
    ("overlay / apply result", {"KEYZER_RESULT": "1"}),
    ("overlay / name dialog", {"KEYZER_DIALOG": "name"}),
    ("overlay / import dialog", {"KEYZER_DIALOG": "import"}),
    ("overlay / lighting panel", {"KEYZER_LIGHTPANEL": "1"}),
    ("hint / calibrate bar", {"KEYZER_HINT": "1"}),
    ("header / LIVE pill", {"KEYZER_LIVE": "1"}),
]
BAD = ("error", "qml:", "unexpected token", "typeerror", "referenceerror",
       "cannot read", "is not a function", "is not defined", "traceback")


def check(extra: dict) -> tuple[bool, list[str]]:
    env = {**os.environ, **BASE, **extra}
    shot = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    env["KEYZER_SHOT"] = shot
    try:
        p = subprocess.run([sys.executable, str(APP)], env=env,
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, ["timed out (offscreen render hung)"]
    lines = (p.stdout + p.stderr).splitlines()
    errs = [ln.strip() for ln in lines
            if any(b in ln.lower() for b in BAD) and "xmodmap" not in ln.lower()]
    framed = os.path.exists(shot) and os.path.getsize(shot) > 0
    try:
        os.unlink(shot)
    except OSError:
        pass
    return (not errs and framed), errs or ([] if framed else ["no frame produced"])


def main() -> int:
    print(f"KEYZER render gate — {len(STATES)} UI states\n")
    failed = []
    for label, extra in STATES:
        ok, errs = check(extra)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        for e in errs[:3]:
            print(f"          {e}")
        if not ok:
            failed.append(label)
    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(STATES)}: {', '.join(failed)}")
        return 1
    print(f"OK — all {len(STATES)} states render with no QML errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
