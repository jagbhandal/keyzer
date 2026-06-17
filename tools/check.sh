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
python3 tools/render_check.py || { echo "render gate FAILED"; exit 1; }

echo
echo "ALL CHECKS PASSED — safe to commit"
