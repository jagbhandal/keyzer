#!/usr/bin/env bash
# KEYZER pre-commit gate — run before committing any change.
# Compiles the Python, runs the unit suite, and loads every UI state offscreen
# to catch QML errors. Exits non-zero on the first failure.
set -uo pipefail
cd "$(dirname "$0")/.."

echo "== byte-compile =="
python3 -m py_compile app/*.py tools/*.py || { echo "compile FAILED"; exit 1; }

echo "== unit tests =="
python3 -m unittest discover tests || { echo "tests FAILED"; exit 1; }

echo "== render gate (every UI state) =="
# The render gate runs the full Qt app offscreen. It needs an apt-built PySide6
# (the pip wheels return a null context property in QML in slim containers), so
# CI runs it only in the apt-Qt job. Set KEYZER_SKIP_RENDER=1 to skip it where
# that environment isn't available; it always runs locally (the dev pre-commit gate).
if [ "${KEYZER_SKIP_RENDER:-0}" = "1" ]; then
    echo "SKIPPED (KEYZER_SKIP_RENDER=1) — needs apt-Qt; runs locally + in the apt-Qt CI job"
else
    python3 tools/render_check.py || { echo "render gate FAILED"; exit 1; }
fi

echo
echo "ALL CHECKS PASSED — safe to commit"
