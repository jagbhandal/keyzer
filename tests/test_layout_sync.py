"""Tests for engine.sync_keyboard_layout — the load-bearing fix.

input-remapper's root daemon can't run ``xmodmap -pke`` (no session), so it
validates every symbolic output against the ``xmodmap.json`` the GUI writes.
KEYZER replaces that GUI, so it must trigger that write before applying — or the
daemon silently drops every keysym mapping and keys emit their hardware defaults.

KEYZER delegates the write to input-remapper itself (``input-remapper-control
--symbol-names`` populates the layout and writes the file with input-remapper's
own, version-matched code) rather than reproducing its parser. These tests pin
that contract: the file gets produced on success, and failures are surfaced with
an actionable reason (never a silent no-op). The control client and xmodmap are
faked via mock so this runs anywhere (the dev box has neither a keymap nor perms).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import conftest  # noqa: F401  -- sys.path + offscreen + warning filter

import engine


def _cp(returncode=0, stdout=""):
    return subprocess.CompletedProcess(["input-remapper-control"], returncode, stdout, "")


class SyncKeyboardLayoutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name
        self.xmodmap_path = Path(self._tmp.name) / "input-remapper-2" / "xmodmap.json"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self._tmp.cleanup()

    def test_ok_when_control_produces_the_file(self):
        def fake_control(*args, **kw):
            # mimic input-remapper writing xmodmap.json as a side effect
            engine.save_json(self.xmodmap_path, {"w": 17, "minus": 12})
            return _cp(0, "w\nminus\n")
        with mock.patch.object(engine, "available", return_value=True), \
             mock.patch.object(engine, "_control", side_effect=fake_control):
            res = engine.sync_keyboard_layout()
        self.assertTrue(res["ok"], res)
        self.assertEqual(Path(res["path"]), self.xmodmap_path)
        self.assertTrue(self.xmodmap_path.exists())
        self.assertIsNone(res["error"])

    def test_fails_actionably_when_input_remapper_absent(self):
        with mock.patch.object(engine, "available", return_value=False):
            res = engine.sync_keyboard_layout()
        self.assertFalse(res["ok"])
        self.assertIn("input-remapper", res["error"])

    def test_diagnoses_missing_xmodmap(self):
        # control ran but produced no file, and xmodmap isn't installed.
        with mock.patch.object(engine, "available", return_value=True), \
             mock.patch.object(engine, "_control", return_value=_cp(0)), \
             mock.patch.object(engine.shutil, "which", return_value=None):
            res = engine.sync_keyboard_layout()
        self.assertFalse(res["ok"])
        self.assertIn("x11-xserver-utils", res["error"])
        self.assertFalse(self.xmodmap_path.exists())

    def test_diagnoses_missing_keymap(self):
        # control ran, no file, but xmodmap IS installed -> blame the session.
        with mock.patch.object(engine, "available", return_value=True), \
             mock.patch.object(engine, "_control", return_value=_cp(0)), \
             mock.patch.object(engine.shutil, "which", return_value="/usr/bin/xmodmap"):
            res = engine.sync_keyboard_layout()
        self.assertFalse(res["ok"])
        self.assertIn("X/XWayland", res["error"])

    def test_does_not_reproduce_input_remapper_internals(self):
        # Guard the altitude decision: KEYZER must NOT hand-parse xmodmap output.
        # (No XKB offset constant; the write is delegated to the control client.)
        self.assertFalse(hasattr(engine, "_XKB_KEYCODE_OFFSET"))


class ApplyProfileLayoutGuardTests(unittest.TestCase):
    """apply_profile must hard-fail (not silently no-op) when it can't produce a
    layout map and none exists — that's the bug the user hit."""

    def test_hard_errors_when_layout_cannot_be_produced(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = {"ok": False, "path": str(Path(tmp) / "nope.json"),
                   "error": "xmodmap isn't installed — install x11-xserver-utils"}
            with mock.patch.object(engine, "available", return_value=True), \
                 mock.patch.object(engine, "service_ready", return_value=True), \
                 mock.patch.object(engine, "sync_keyboard_layout", return_value=bad):
                report = engine.apply_profile("Gaming", {"tartarus": {"TAR_01": "w"}}, {})
        self.assertIn("_error", report)
        self.assertIn("key names", report["_error"])

    def test_proceeds_when_a_layout_file_already_exists(self):
        """A stale-but-present xmodmap.json (e.g. the user once ran the GUI) is
        good enough — don't block on a failed refresh."""
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "xmodmap.json"
            existing.write_text(json.dumps({"w": 17}))
            bad = {"ok": False, "path": str(existing), "error": "transient hiccup"}
            with mock.patch.object(engine, "available", return_value=True), \
                 mock.patch.object(engine, "service_ready", return_value=True), \
                 mock.patch.object(engine, "device_keys", return_value=[]), \
                 mock.patch.object(engine, "sync_keyboard_layout", return_value=bad):
                # no captures -> per-device "not captured", but NOT a layout _error
                report = engine.apply_profile("Gaming", {"tartarus": {"TAR_01": "w"}}, {})
        self.assertNotIn("_error", report)


if __name__ == "__main__":
    unittest.main()
