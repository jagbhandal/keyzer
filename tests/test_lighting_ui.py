"""Offscreen UI-behaviour tests for lighting persistence (bug #2) and the colour
wheel following the saved look (bug #3) — these drive the REAL app/qml/Main.qml
against a real Backend, which the render gate (errors-only) can't assert.

The lighting module is patched so enterLighting()'s device probe returns instantly
(the real OpenRazer DeviceManager blocks ~25s when the daemon is down), and so no
hardware is touched. Each test points engine's persistence paths at a temp dir.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import conftest  # noqa: F401  -- sys.path + offscreen + warning filter

from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtCore import QUrl

import engine
import backend as backend_mod
import lighting

_app = QGuiApplication.instance() or QGuiApplication([])
_QML = Path(__file__).resolve().parent.parent / "app" / "qml" / "Main.qml"


class LightingUiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        cfg = Path(self._tmp.name) / "keyzer"
        self._saved = {k: getattr(engine, k) for k in ("CONFIG_DIR", "PROFILES", "CAPTURES")}
        engine.CONFIG_DIR = cfg
        engine.PROFILES = cfg / "profiles.json"
        engine.CAPTURES = cfg / "captures.json"
        # Keep enterLighting() fast + hardware-free: no real DeviceManager (~25s when
        # the daemon is down), no real effect calls.
        self._light = {n: getattr(lighting, n)
                       for n in ("devices", "sync_enabled", "available", "set_brightness")}
        lighting.devices = lambda: {}
        lighting.sync_enabled = lambda: False
        lighting.available = lambda: False
        lighting.set_brightness = lambda *a, **k: {"ok": True}
        self._view = None
        self._backend = None   # held so the view's context object can't be GC'd mid-test

    def tearDown(self):
        # Unload the QML (so bindings stop reading 'backend') BEFORE dropping the
        # backend, otherwise teardown evaluates bindings against a null context object.
        if self._view is not None:
            self._view.setSource(QUrl())
            for _ in range(3):
                _app.processEvents()
            self._view.deleteLater()
            for _ in range(3):
                _app.processEvents()
            self._view = None
        self._backend = None
        for n, v in self._light.items():
            setattr(lighting, n, v)
        for k, v in self._saved.items():
            setattr(engine, k, v)
        self._tmp.cleanup()

    def _boot(self, light_state):
        engine.save_json(engine.PROFILES, {
            "version": 2, "active": "Gaming", "live": {},
            "settings": {"lighting": True, "lightState": light_state},
            "profiles": {"Gaming": {"tartarus": {}, "naga": {}}}})
        self._backend = backend_mod.Backend()
        self._view = QQuickView()
        self._view.rootContext().setContextProperty("backend", self._backend)
        self._view.setSource(QUrl.fromLocalFile(str(_QML)))
        for _ in range(5):
            _app.processEvents()
        self.assertEqual(self._view.status(), QQuickView.Status.Ready,
                         [e.toString() for e in self._view.errors()])
        return self._view, self._view.rootObject()

    def _wheel_rgb(self, root):
        c = QColor.fromHsvF(root.property("lcH") % 1.0, root.property("lcS"), root.property("lcV"))
        return (c.red(), c.green(), c.blue())

    def test_saved_look_hydrates_and_wheel_follows_it(self):
        # tartarus is the default on-stage device on startup, so key is "tartarus|".
        _view, root = self._boot({"tartarus|": {"effect": "static", "r": 40, "g": 120,
                                  "b": 255, "direction": "left", "react_ms": 1000}})
        ls = root.property("lightState")
        self.assertTrue(ls.get("tartarus|"), "bug #2: saved look not hydrated into the UI")
        self.assertEqual(ls["tartarus|"]["effect"], "static")
        rgb = self._wheel_rgb(root)
        self.assertNotEqual(rgb, (5, 255, 0), "bug #3: wheel reverted to #05ff00")
        self.assertEqual(rgb, (40, 120, 255), "bug #3: wheel didn't follow the saved blue")

    def test_unset_wheel_defaults_to_razer_green_not_05ff00(self):
        _view, root = self._boot({})   # nothing ever saved
        # The wheel now follows curLight()'s default (Razer Green 68,214,44),
        # never the old fixed #05ff00.
        self.assertNotEqual(self._wheel_rgb(root), (5, 255, 0))
        self.assertEqual(self._wheel_rgb(root), (68, 214, 44))
