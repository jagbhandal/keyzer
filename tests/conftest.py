"""Shared test setup for the KEYZER suite.

Importing this module (pytest auto-imports conftest.py; the unittest files
import it explicitly) guarantees, before any test runs:

  * ``app/`` is on ``sys.path`` so ``import engine`` / ``import backend`` work
    exactly as they do when the app launches from the repo root.
  * Qt runs headless. The backend constructs a ``QGuiApplication`` and touches
    the clipboard, so the offscreen platform must be selected *before* PySide6
    is imported anywhere.
  * input-remapper's noisy pydantic-v1 deprecation warning (emitted on import
    of the engine's transitive deps) is silenced so ``pytest -q`` stays clean.

It deliberately does NOT touch ``XDG_CONFIG_HOME`` globally: each backend test
points it at its own temp dir so profiles persist in isolation, and the engine
tests that write presets do the same. Setting it here would couple every test
to one shared dir and defeat that isolation.
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

# Headless Qt — must be set before PySide6 is imported by anyone.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
