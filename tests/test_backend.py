"""Tests for app/backend.py — the QObject bridge between QML and the engine.

The backend needs a QGuiApplication (offscreen, set up in conftest) and persists
profiles via ``engine.save_json(engine.PROFILES, ...)``. ``engine.PROFILES`` is
computed once at import time from XDG_CONFIG_HOME, so per-test isolation can't
rely on env alone — each test redirects ``engine.PROFILES`` / ``engine.CAPTURES``
/ ``engine.CONFIG_DIR`` at its own temp dir (and restores them), so profiles
persist independently and never touch the real user config.

The single QGuiApplication is created once at module import (Qt forbids more than
one). Backend instances are cheap to recreate, which lets us prove persistence
across re-instantiation.

Daemon note: ``applyToHardware`` is tested for its graceful, well-shaped return
contract, NOT for a specific ok/True — whether a real input-remapper daemon is
reachable varies by box. The one test that asserts an exact result monkeypatches
``engine.service_ready`` to False to exercise the daemon-unreachable branch
deterministically. No test requires a running input-remapper or openrazer.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import conftest  # noqa: F401  -- sys.path + offscreen + warning filter

from PySide6.QtGui import QGuiApplication

import engine
import backend
import lighting

# Exactly one QGuiApplication for the whole process (Qt requirement).
_app = QGuiApplication.instance() or QGuiApplication([])


class _IsolatedBackendTest(unittest.TestCase):
    """Base: redirect engine persistence paths into a fresh temp dir per test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        cfg = tmp / "keyzer"
        # Save + redirect the module-level paths the backend writes through.
        self._saved = {
            "CONFIG_DIR": engine.CONFIG_DIR,
            "PROFILES": engine.PROFILES,
            "CAPTURES": engine.CAPTURES,
        }
        engine.CONFIG_DIR = cfg
        engine.PROFILES = cfg / "profiles.json"
        engine.CAPTURES = cfg / "captures.json"

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(engine, k, v)
        self._tmpdir.cleanup()

    def new_backend(self) -> backend.Backend:
        return backend.Backend()


class SeedAndPersistenceTests(_IsolatedBackendTest):
    def test_seeds_defaults_on_first_run(self):
        b = self.new_backend()
        self.assertEqual(b.profileList, ["Gaming", "Work", "Default"])
        self.assertEqual(b.activeProfile, "Gaming")
        # First run writes the seed to disk.
        self.assertTrue(engine.PROFILES.exists())

    def test_persists_across_reinstantiation(self):
        b1 = self.new_backend()
        b1.createProfile("Streaming")
        b1.setBinding("Streaming", "tartarus", "TAR_01", "F5")
        # A brand-new instance reading the same (redirected) paths sees it.
        b2 = self.new_backend()
        self.assertIn("Streaming", b2.profileList)
        self.assertEqual(b2.activeProfile, "Streaming")
        self.assertEqual(b2.bindings["Streaming"]["tartarus"]["TAR_01"], "F5")

    def test_settings_persist_across_reinstantiation(self):
        b1 = self.new_backend()
        self.assertFalse(b1.getSetting("lighting", False))   # default when unset
        b1.setSetting("lighting", True)
        b2 = self.new_backend()
        self.assertTrue(b2.getSetting("lighting", False))    # survived a restart

    def test_active_profile_repaired_if_invalid(self):
        # Write a profiles.json whose 'active' points at a deleted profile.
        engine.save_json(engine.PROFILES, {
            "version": 1, "active": "Ghost", "live": {},
            "profiles": {"Solo": {"tartarus": {}, "naga": {}}},
        })
        b = self.new_backend()
        self.assertEqual(b.activeProfile, "Solo")  # repaired to a real one


class ProfileCrudTests(_IsolatedBackendTest):
    def setUp(self):
        super().setUp()
        self.b = self.new_backend()

    def test_create_profile(self):
        r = self.b.createProfile("Coding")
        self.assertEqual(r, {"ok": True, "name": "Coding"})
        self.assertIn("Coding", self.b.profileList)
        self.assertEqual(self.b.activeProfile, "Coding")
        # New profile is seeded with an empty bind map per device.
        self.assertEqual(set(self.b.bindings["Coding"]), {"tartarus", "naga"})

    def test_create_trims_whitespace(self):
        r = self.b.createProfile("  Spaced  ")
        self.assertEqual(r["name"], "Spaced")
        self.assertIn("Spaced", self.b.profileList)

    def test_create_empty_name_rejected(self):
        r = self.b.createProfile("   ")
        self.assertFalse(r["ok"])
        self.assertIn("empty", r["error"].lower())

    def test_create_duplicate_name_rejected(self):
        r = self.b.createProfile("Gaming")
        self.assertFalse(r["ok"])
        self.assertIn("taken", r["error"].lower())

    def test_rename_profile(self):
        r = self.b.renameProfile("Work", "Office")
        self.assertEqual(r, {"ok": True, "name": "Office"})
        self.assertIn("Office", self.b.profileList)
        self.assertNotIn("Work", self.b.profileList)

    def test_rename_updates_active(self):
        self.b.setActiveProfile("Work")
        self.b.renameProfile("Work", "Office")
        self.assertEqual(self.b.activeProfile, "Office")

    def test_rename_missing_profile_rejected(self):
        r = self.b.renameProfile("Nope", "Whatever")
        self.assertFalse(r["ok"])

    def test_rename_to_existing_name_rejected(self):
        r = self.b.renameProfile("Work", "Gaming")
        self.assertFalse(r["ok"])
        self.assertIn("taken", r["error"].lower())

    def test_rename_to_same_name_allowed(self):
        # Renaming a profile to itself is a no-op success, not a collision.
        r = self.b.renameProfile("Work", "Work")
        self.assertTrue(r["ok"])
        self.assertIn("Work", self.b.profileList)

    def test_rename_empty_rejected(self):
        r = self.b.renameProfile("Work", "   ")
        self.assertFalse(r["ok"])
        self.assertIn("empty", r["error"].lower())

    def test_duplicate_profile_is_deep_copy(self):
        self.b.setBinding("Work", "tartarus", "TAR_01", "Esc")
        r = self.b.duplicateProfile("Work", "Work Copy")
        self.assertTrue(r["ok"])
        self.assertEqual(self.b.activeProfile, "Work Copy")
        # Mutating the copy must not bleed into the source (deep copy).
        self.b.setBinding("Work Copy", "tartarus", "TAR_01", "Tab")
        self.assertEqual(self.b.bindings["Work"]["tartarus"]["TAR_01"], "Esc")
        self.assertEqual(self.b.bindings["Work Copy"]["tartarus"]["TAR_01"], "Tab")

    def test_duplicate_missing_source_rejected(self):
        r = self.b.duplicateProfile("Ghost", "X")
        self.assertFalse(r["ok"])

    def test_duplicate_to_existing_name_rejected(self):
        r = self.b.duplicateProfile("Work", "Gaming")
        self.assertFalse(r["ok"])
        self.assertIn("taken", r["error"].lower())

    def test_delete_profile(self):
        r = self.b.deleteProfile("Work")
        self.assertEqual(r, {"ok": True})
        self.assertNotIn("Work", self.b.profileList)

    def test_delete_active_reassigns_active(self):
        self.b.setActiveProfile("Work")
        self.b.deleteProfile("Work")
        self.assertNotEqual(self.b.activeProfile, "Work")
        self.assertIn(self.b.activeProfile, self.b.profileList)

    def test_delete_missing_rejected(self):
        r = self.b.deleteProfile("Ghost")
        self.assertFalse(r["ok"])

    def test_delete_last_profile_guard(self):
        self.b.deleteProfile("Work")
        self.b.deleteProfile("Default")
        r = self.b.deleteProfile("Gaming")  # would be the last one
        self.assertFalse(r["ok"])
        self.assertIn("at least one", r["error"].lower())
        self.assertEqual(self.b.profileList, ["Gaming"])

    def test_set_active_profile(self):
        self.b.setActiveProfile("Default")
        self.assertEqual(self.b.activeProfile, "Default")

    def test_set_active_invalid_is_ignored(self):
        self.b.setActiveProfile("Ghost")
        self.assertEqual(self.b.activeProfile, "Gaming")  # unchanged


class BindingTests(_IsolatedBackendTest):
    def setUp(self):
        super().setUp()
        self.b = self.new_backend()

    def test_set_and_clear_binding(self):
        self.b.setBinding("Gaming", "tartarus", "TAR_01", "F1")
        self.assertEqual(self.b.bindings["Gaming"]["tartarus"]["TAR_01"], "F1")
        self.b.clearBinding("Gaming", "tartarus", "TAR_01")
        self.assertNotIn("TAR_01", self.b.bindings["Gaming"]["tartarus"])

    def test_clear_unknown_binding_is_safe(self):
        # No KeyError on a hotspot/profile/device that was never set.
        self.b.clearBinding("Gaming", "tartarus", "NEVER_SET")
        self.b.clearBinding("Ghost", "tartarus", "TAR_01")

    def test_set_binding_creates_missing_containers(self):
        self.b.setBinding("FreshProfile", "naga", "NAGA_01", "1")
        self.assertEqual(self.b.bindings["FreshProfile"]["naga"]["NAGA_01"], "1")


class ImportExportTests(_IsolatedBackendTest):
    def setUp(self):
        super().setUp()
        self.b = self.new_backend()

    def test_export_round_trips_through_import(self):
        self.b.setBinding("Work", "tartarus", "TAR_01", "Esc")
        exported = self.b.exportProfile("Work")
        self.assertTrue(exported["ok"])
        payload = json.loads(exported["json"])
        self.assertEqual(payload["keyzer"], 1)
        self.assertEqual(payload["name"], "Work")
        # Re-import the exact JSON; name de-duplicates because "Work" exists.
        imported = self.b.importProfile(exported["json"])
        self.assertTrue(imported["ok"])
        self.assertEqual(imported["name"], "Work 2")
        self.assertEqual(self.b.bindings["Work 2"]["tartarus"]["TAR_01"], "Esc")

    def test_export_missing_profile(self):
        r = self.b.exportProfile("Ghost")
        self.assertFalse(r["ok"])

    def test_import_invalid_json(self):
        r = self.b.importProfile("not json at all {")
        self.assertFalse(r["ok"])
        self.assertIn("json", r["error"].lower())

    def test_import_wrong_shape(self):
        r = self.b.importProfile(json.dumps({"hello": "world"}))
        self.assertFalse(r["ok"])

    def test_import_coerces_non_string_binds(self):
        # Exported binds should be strings, but a hand-edited file might carry
        # ints; import must coerce hotspot values to str.
        payload = {"keyzer": 1, "name": "Numbers",
                   "binds": {"tartarus": {"TAR_02": 5}, "naga": {}}}
        r = self.b.importProfile(json.dumps(payload))
        self.assertTrue(r["ok"])
        val = self.b.bindings["Numbers"]["tartarus"]["TAR_02"]
        self.assertEqual(val, "5")
        self.assertIsInstance(val, str)

    def test_import_default_name_when_absent(self):
        payload = {"keyzer": 1, "binds": {"tartarus": {}, "naga": {}}}
        r = self.b.importProfile(json.dumps(payload))
        self.assertTrue(r["ok"])
        self.assertEqual(r["name"], "Imported")


class CaptureAndGeometryTests(_IsolatedBackendTest):
    def setUp(self):
        super().setUp()
        self.b = self.new_backend()

    def test_capture_summary_totals(self):
        summary = self.b.captureSummary()
        self.assertEqual(set(summary), {"tartarus", "naga"})
        for dev in ("tartarus", "naga"):
            self.assertIn("captured", summary[dev])
            self.assertIn("total", summary[dev])
            self.assertIn("name", summary[dev])
            self.assertIsInstance(summary[dev]["captured"], int)
            self.assertIsInstance(summary[dev]["total"], int)
            # 'total' is the sum of hotspots across all views from layouts.json.
            self.assertGreater(summary[dev]["total"], 0)

    def test_capture_summary_total_matches_hotspot_count(self):
        summary = self.b.captureSummary()
        # tartarus: 22 main + 8 thumb = 30 ; naga: 7 top + 12 side = 19.
        self.assertEqual(summary["tartarus"]["total"], 30)
        self.assertEqual(summary["naga"]["total"], 19)

    def test_captures_source_default_in_isolation(self):
        # No user captures.json in the temp XDG -> bundled default map is used.
        self.assertEqual(self.b.capturesSource(), "default")

    def test_hotspot_ids_collected_from_all_views(self):
        self.assertIn("TAR_01", self.b._hotspot_ids["tartarus"])
        self.assertIn("TAR_TPAD_NE", self.b._hotspot_ids["tartarus"])  # thumb view
        self.assertIn("NAGA_12", self.b._hotspot_ids["naga"])          # side view
        self.assertEqual(len(self.b._hotspot_ids["tartarus"]), 30)

    def test_unavailable_filtering(self):
        # Naga wheel-tilt hotspots are flagged unavailable in layouts.json.
        self.assertEqual(self.b._unavailable["naga"], {"NAGA_WHL_L", "NAGA_WHL_R"})
        self.assertEqual(self.b._unavailable["tartarus"], set())

    def test_combos_collected(self):
        self.assertEqual(
            set(self.b._combos["tartarus"]),
            {"TAR_TPAD_NE", "TAR_TPAD_SE", "TAR_TPAD_SW", "TAR_TPAD_NW"})
        self.assertEqual(self.b._combos["tartarus"]["TAR_TPAD_NE"],
                         ["TAR_TPAD_N", "TAR_TPAD_E"])
        self.assertEqual(self.b._combos["naga"], {})


class ApplyToHardwareTests(_IsolatedBackendTest):
    """Graceful return contract — no running daemon required."""

    def setUp(self):
        super().setUp()
        self.b = self.new_backend()

    def test_apply_returns_well_shaped_dict(self):
        # Whether the real daemon is up or not, the result is a graceful dict.
        r = self.b.applyToHardware("Gaming", "")
        self.assertIsInstance(r, dict)
        self.assertEqual(set(r) >= {"ok", "message", "devices"}, True)
        self.assertIsInstance(r["ok"], bool)
        self.assertIsInstance(r["message"], str)
        self.assertIsInstance(r["devices"], list)

    def test_apply_empty_profile_reports_no_applicable_binds(self):
        # 'Default' has empty bind maps. Because the bundled default capture map
        # supplies device_name for each device, apply_profile still produces a
        # per-device report (each "no applicable binds") rather than the empty
        # 'Nothing to apply' branch. Force the daemon-reachable + layout-synced
        # path so this exercises the device loop deterministically on any box
        # (no real daemon or xmodmap required).
        # Each captured device produces no mappings, so apply reverts it (stop +
        # clear_autoload); stub those engine externals so no daemon/real config is hit.
        saved = (engine.service_ready, engine.sync_keyboard_layout,
                 engine.device_keys, engine.stop, engine.clear_autoload)
        engine.service_ready = lambda: True
        engine.sync_keyboard_layout = lambda: {"ok": True, "count": 1,
                                               "path": "/tmp/xmodmap.json", "error": None}
        engine.device_keys = lambda: []
        engine.stop = lambda key: True
        engine.clear_autoload = lambda key=None: None
        try:
            r = self.b.applyToHardware("Default", "")
        finally:
            (engine.service_ready, engine.sync_keyboard_layout,
             engine.device_keys, engine.stop, engine.clear_autoload) = saved
        self.assertFalse(r["ok"])
        self.assertEqual({d["dev"] for d in r["devices"]}, {"tartarus", "naga"})
        for d in r["devices"]:
            self.assertFalse(d["ok"])
            self.assertEqual(d["count"], 0)
            self.assertEqual(d["error"], "no applicable binds")

    def test_apply_nonexistent_profile(self):
        r = self.b.applyToHardware("Ghost", "")
        self.assertFalse(r["ok"])
        self.assertEqual(r["devices"], [])

    def test_apply_daemon_unreachable_is_graceful(self):
        # Force the daemon-unreachable branch deterministically.
        saved = engine.service_ready
        engine.service_ready = lambda: False
        try:
            r = self.b.applyToHardware("Gaming", "")
        finally:
            engine.service_ready = saved
        self.assertEqual(r["ok"], False)
        self.assertEqual(r["devices"], [])
        self.assertIsInstance(r["message"], str)
        self.assertTrue(r["message"])  # carries the engine's _error reason

    def test_apply_single_device_scope(self):
        # Passing a device restricts the report to that device (or fewer).
        r = self.b.applyToHardware("Gaming", "naga")
        self.assertIsInstance(r, dict)
        self.assertIsInstance(r.get("devices"), list)
        for d in r["devices"]:
            self.assertEqual(d["dev"], "naga")

    def test_preset_name_for_slot(self):
        name = self.b.presetNameFor("Gaming")
        self.assertTrue(name.startswith("keyzer-"))
        self.assertEqual(name, engine.preset_name_for("Gaming"))


class PersistenceTests(_IsolatedBackendTest):
    """Apply unconditionally persists (input-remapper autoload) so mappings survive
    a restart/replug; Stop is the only off-switch and drops the autoload entries.
    Engine functions are stubbed so no daemon or real config.json is touched
    (mirrors the existing monkeypatch style in this file)."""

    def setUp(self):
        super().setUp()
        self.b = self.new_backend()

    def test_apply_always_persists(self):
        captured = {}
        saved = engine.apply_profile

        def spy(profile, binds, captures, *, autoload=False, combos=None, **_):
            captured["autoload"] = autoload
            return {}      # empty report -> graceful "nothing to apply", no daemon

        engine.apply_profile = spy
        try:
            self.b.applyToHardware("Gaming", "")
        finally:
            engine.apply_profile = saved
        self.assertTrue(captured["autoload"])   # no toggle — Apply persists, always

    def test_stop_all_clears_autoload_and_live(self):
        calls = []
        saved = (engine.available, engine.stop_all, engine.clear_autoload)
        engine.available = lambda: True
        engine.stop_all = lambda: True
        engine.clear_autoload = lambda *a, **k: calls.append("clear")
        try:
            self.b._live = {"tartarus": "Gaming"}
            r = self.b.stopAll()
        finally:
            engine.available, engine.stop_all, engine.clear_autoload = saved
        self.assertTrue(r["ok"])
        self.assertEqual(self.b._live, {})      # back to no live devices
        self.assertEqual(calls, ["clear"])      # and autoload dropped

    def test_full_apply_covers_all_known_devices(self):
        # A full apply (dev="") must include EVERY known device — even ones the
        # profile leaves unbound — so they get reverted, not left on the old preset.
        self.b.importProfile(json.dumps(
            {"keyzer": 1, "name": "OnlyTar", "binds": {"tartarus": {"TAR_01": "Esc"}}}))
        captured = {}
        saved = engine.apply_profile
        engine.apply_profile = (lambda profile, binds, caps, *, autoload=False, combos=None, **_:
                                captured.update(keys=set(binds)) or {})
        try:
            self.b.applyToHardware("OnlyTar", "")
        finally:
            engine.apply_profile = saved
        self.assertEqual(captured["keys"], {"tartarus", "naga"})  # naga in scope -> revert

    def test_apply_drops_reverted_device_from_live(self):
        # naga was live under a prior profile; applying a profile where naga comes
        # back not-ok (reverted) must remove naga from the live set.
        self.b._live = {"naga": "Gaming"}
        saved = engine.apply_profile
        engine.apply_profile = (lambda *a, **k: {
            "tartarus": {"ok": True, "count": 3},
            "naga": {"ok": False, "error": "no applicable binds"}})
        try:
            self.b.applyToHardware("Gaming", "")
        finally:
            engine.apply_profile = saved
        self.assertEqual(self.b.liveStatus, {"tartarus": "Gaming"})  # naga dropped

    def test_unknown_profile_applies_nothing(self):
        captured = {}
        saved = engine.apply_profile
        engine.apply_profile = (lambda profile, binds, caps, *, autoload=False, combos=None, **_:
                                captured.update(keys=set(binds)) or {})
        try:
            r = self.b.applyToHardware("Ghost", "")
        finally:
            engine.apply_profile = saved
        self.assertEqual(captured["keys"], set())   # no devices in scope
        self.assertEqual(r["devices"], [])


class CalibrationTests(_IsolatedBackendTest):
    """Orchestration guards for in-app calibration. The live evdev/thread flow
    needs hardware, so only the device-independent guard paths are unit-tested;
    classify_event + record_capture cover the data logic elsewhere."""

    def setUp(self):
        super().setUp()
        self.b = self.new_backend()

    def test_begin_unknown_device_rejected(self):
        r = self.b.beginCalibration("ghost")
        self.assertFalse(r["ok"])
        self.assertIn("unknown", r["error"].lower())
        self.assertFalse(self.b.calibrating())

    def test_begin_rejected_when_already_calibrating(self):
        self.b._cal_worker = object()      # pretend a session is active
        try:
            r = self.b.beginCalibration("tartarus")
        finally:
            self.b._cal_worker = None
        self.assertFalse(r["ok"])
        self.assertIn("already", r["error"].lower())

    def test_end_without_session_is_safe(self):
        self.assertEqual(self.b.endCalibration(), {"ok": True})
        self.assertFalse(self.b.calibrating())

    def test_arm_disarm_without_session_are_noops(self):
        self.b.armCalibration("TAR_01")    # must not raise with no worker
        self.b.disarmCalibration()

    def test_begin_failure_does_not_stop_remapping(self):
        # A failed begin (device not open) must NOT have stopped input-remapper,
        # or the user is left un-remapped with no restore. Regression guard for the
        # stop_all-before-open ordering bug.
        try:
            import capture
        except (ImportError, SystemExit):
            self.skipTest("python-evdev not available")
        calls = []
        saved = (engine.available, engine.stop_all, capture.open_nodes)
        engine.available = lambda: True
        engine.stop_all = lambda: calls.append("stop") or True
        capture.open_nodes = lambda layout: (None, [])
        try:
            r = self.b.beginCalibration("tartarus")
        finally:
            engine.available, engine.stop_all, capture.open_nodes = saved
        self.assertFalse(r["ok"])
        self.assertEqual(calls, [])        # remapping left untouched on a failed begin
        self.assertFalse(self.b.calibrating())


class DemoModeTests(_IsolatedBackendTest):
    """Demo mode (KEYZER_DEMO=1) runs fully populated with no hardware: deps report
    present, apply/stop/lighting/calibrate are simulated and never touch the engine
    or OpenRazer. (We flip _demo on the instance to avoid env coupling.)"""

    def setUp(self):
        super().setUp()
        self.b = self.new_backend()
        self.b._demo = True

    def test_deps_all_true(self):
        self.assertEqual(self.b.deps, {"inputRemapper": True, "openrazer": True})
        self.assertTrue(self.b.demo)

    def test_apply_is_simulated(self):
        # The demo path must never reach the engine — make engine.apply_profile blow
        # up so the test fails if it's called.
        def boom(*a, **k):
            raise AssertionError("engine touched in demo mode")
        saved = engine.apply_profile
        engine.apply_profile = boom
        try:
            r = self.b.applyToHardware("Gaming", "")
        finally:
            engine.apply_profile = saved
        self.assertTrue(r["ok"])
        self.assertIn("demo", r["message"].lower())
        self.assertTrue(all(d["ok"] for d in r["devices"]))
        self.assertEqual({d["dev"] for d in r["devices"]}, {"tartarus", "naga"})
        self.assertTrue(self.b.liveStatus)        # live set populated

    def test_apply_unknown_profile_is_empty(self):
        r = self.b.applyToHardware("Ghost", "")
        self.assertFalse(r["ok"])
        self.assertEqual(r["devices"], [])

    def test_stop_all_clears_live(self):
        self.b._live = {"tartarus": "Gaming"}
        r = self.b.stopAll()
        self.assertTrue(r["ok"])
        self.assertEqual(self.b.liveStatus, {})

    def test_lighting_devices_are_sampled(self):
        d = self.b.lightingDevices()
        self.assertIsNone(d["error"])
        self.assertEqual({x["id"] for x in d["devices"]}, {"tartarus", "naga"})

    def test_light_setters_succeed(self):
        self.assertTrue(self.b.setLightEffect("naga", "static", 1, 2, 3, "")["ok"])
        self.assertTrue(self.b.setLightBrightness("naga", 50)["ok"])
        self.assertTrue(self.b.setLightingSync(True)["ok"])

    def test_demo_persists_nothing_to_disk(self):
        # A demo built in demo from the start must never write profiles.json — not on
        # first-run seed, not on edits the demo user makes while clicking around.
        import os as _os
        if engine.PROFILES.exists():
            engine.PROFILES.unlink()   # drop the file setUp's non-demo backend wrote
        saved = _os.environ.get("KEYZER_DEMO")
        _os.environ["KEYZER_DEMO"] = "1"
        try:
            demo = backend.Backend()
            demo.setBinding("Gaming", "tartarus", "TAR_01", "Esc")
            demo.setActiveProfile("Work")
        finally:
            if saved is None:
                _os.environ.pop("KEYZER_DEMO", None)
            else:
                _os.environ["KEYZER_DEMO"] = saved
        self.assertFalse(engine.PROFILES.exists())   # demo wrote nothing

    def test_calibration_is_simulated(self):
        r = self.b.beginCalibration("tartarus")
        self.assertTrue(r["ok"])
        self.assertTrue(self.b.calibrating())     # demo session active, no worker
        self.assertIsNone(self.b._cal_worker)
        self.assertEqual(self.b.endCalibration(), {"ok": True})
        self.assertFalse(self.b.calibrating())


class ImageUrlGuardTests(_IsolatedBackendTest):
    """imageUrl confines the image loader to the repo so a community-contributed
    layouts.json can't point it at arbitrary files. Pins the traversal guard so a
    refactor dropping .resolve()/is_relative_to fails CI."""

    def test_in_repo_path_yields_file_uri(self):
        b = self.new_backend()
        url = b.imageUrl("layouts.json")
        self.assertTrue(url.startswith("file://"))
        self.assertIn(str(backend.REPO), url)

    def test_traversal_and_absolute_paths_are_rejected(self):
        b = self.new_backend()
        self.assertEqual(b.imageUrl("../../etc/passwd"), "")
        self.assertEqual(b.imageUrl("/etc/passwd"), "")
        self.assertEqual(b.imageUrl("app/../../etc/shadow"), "")


class CaptureWorkerClaimTests(unittest.TestCase):
    """The _CaptureWorker 're-arm mid-read wins' invariant: a press is recorded only
    for the hotspot armed when it arrived. Tested directly (no thread, no devices)."""

    def _worker(self):
        return backend._CaptureWorker("tartarus", [], "Dev", "1532:0244", [])

    def test_claim_succeeds_only_once_per_arm(self):
        w = self._worker()
        w.arm("TAR_01")
        self.assertTrue(w._claim("TAR_01"))    # the armed key wins
        self.assertFalse(w._claim("TAR_01"))   # already consumed -> no double record

    def test_rearm_supersedes_the_previous_target(self):
        w = self._worker()
        w.arm("TAR_01")
        w.arm("TAR_02")                        # re-armed before the press landed
        self.assertFalse(w._claim("TAR_01"))   # stale target loses
        self.assertTrue(w._claim("TAR_02"))    # current target wins

    def test_disarm_blocks_any_claim(self):
        w = self._worker()
        w.arm("TAR_01")
        w.disarm()
        self.assertFalse(w._claim("TAR_01"))

    def test_idle_worker_claims_nothing(self):
        self.assertFalse(self._worker()._claim("TAR_01"))


class _FakeLighting:
    """Stand-in for the lighting module: records set_effect calls and lets a test
    drive which devices are 'present', with NO OpenRazer/DBus. (The real module's
    DeviceManager() blocks ~25s when the daemon is down — never touch it in tests.)"""

    def __init__(self):
        self.applied = []        # (dev, effect, color, zone, direction, react_ms) in call order
        self.present = {}         # what devices() returns; matches lighting.devices() shape
        self.available_flag = True
        self.fail = False         # make set_effect report failure
        self.raise_for = set()    # dev ids whose set_effect raises (a transient OpenRazer error)

    def available(self):
        return self.available_flag

    def devices(self):
        return dict(self.present)

    def set_effect(self, dev, effect, color=(68, 214, 44), color2=(34, 200, 255),
                   *, direction="left", react_ms=1000, zone=""):
        if dev in self.raise_for:
            raise RuntimeError("simulated OpenRazer/DBus error")
        if self.fail:
            return {"ok": False, "error": "device not connected"}
        self.applied.append((dev, effect, tuple(color), zone, direction, react_ms))
        return {"ok": True}


class _LightingBackendTest(_IsolatedBackendTest):
    """Isolated backend + the lighting module swapped for a recording fake."""

    def setUp(self):
        super().setUp()
        self.fake = _FakeLighting()
        self._saved_light = {n: getattr(lighting, n)
                             for n in ("available", "devices", "set_effect")}
        lighting.available = self.fake.available
        lighting.devices = self.fake.devices
        lighting.set_effect = self.fake.set_effect

    def tearDown(self):
        for n, v in self._saved_light.items():
            setattr(lighting, n, v)
        super().tearDown()


class LightingPersistenceTests(_LightingBackendTest):
    """The applied 'look' (effect + colour + params) must survive an app restart —
    KEYZER keeps its own record because OpenRazer can't read the effect back."""

    def test_set_effect_records_and_persists_across_restart(self):
        b1 = self.new_backend()
        r = b1.setLightEffect("naga", "static", 10, 20, 30, "")
        self.assertTrue(r["ok"])
        self.assertEqual(self.fake.applied[-1],
                         ("naga", "static", (10, 20, 30), "", "left", 1000))
        self.assertEqual(b1.lightState(), {"naga|": {
            "effect": "static", "r": 10, "g": 20, "b": 30,
            "direction": "left", "react_ms": 1000}})
        # A fresh instance (a restart) reads the same persisted look from disk.
        b2 = self.new_backend()
        self.assertEqual(b2.lightState()["naga|"]["effect"], "static")
        self.assertEqual(b2.lightState()["naga|"]["r"], 10)
        # ...and it's actually on disk under settings.lightState.
        on_disk = json.loads(engine.PROFILES.read_text())
        self.assertEqual(on_disk["settings"]["lightState"]["naga|"]["b"], 30)

    def test_failed_apply_is_not_remembered(self):
        self.fake.fail = True
        b = self.new_backend()
        r = b.setLightEffect("naga", "static", 1, 2, 3, "")
        self.assertFalse(r["ok"])
        self.assertEqual(b.lightState(), {})   # a look that didn't take isn't recorded

    def test_demo_does_not_persist_a_look(self):
        b = self.new_backend()
        b._demo = True
        r = b.setLightEffect("naga", "static", 1, 2, 3, "")
        self.assertTrue(r["ok"])               # demo short-circuits to ok
        self.assertEqual(b.lightState(), {})   # ...but records/persists nothing


class LightingReapplyTests(_LightingBackendTest):
    """The reconnect/startup watcher re-applies KEYZER's saved look whenever a
    device (re)appears — devices forget software-set effects on unplug/power loss."""

    def test_reapply_only_on_absent_to_present_transition(self):
        b = self.new_backend()
        b.setLightEffect("naga", "static", 1, 2, 3, "")   # seed a saved look
        self.fake.applied.clear()

        self.fake.present = {}                              # device absent
        b._lighting_poll()
        self.assertEqual(self.fake.applied, [])            # nothing to re-apply

        self.fake.present = {"naga": {"name": "Razer Naga Pro"}}   # it returns
        b._lighting_poll()
        self.assertEqual(self.fake.applied,
                         [("naga", "static", (1, 2, 3), "", "left", 1000)])

        self.fake.applied.clear()
        b._lighting_poll()                                 # still present: no re-apply
        self.assertEqual(self.fake.applied, [])            # (no flicker on steady state)

        self.fake.present = {}; b._lighting_poll()          # unplug
        self.fake.present = {"naga": {"name": "Razer Naga Pro"}}; b._lighting_poll()
        self.assertEqual(self.fake.applied,
                         [("naga", "static", (1, 2, 3), "", "left", 1000)])  # replug re-applies

    def test_first_poll_after_restart_reapplies_present_devices(self):
        b1 = self.new_backend()
        b1.setLightEffect("naga", "static", 7, 8, 9, "")
        b2 = self.new_backend()                            # a restart: _seen starts empty
        self.fake.applied.clear()
        self.fake.present = {"naga": {"name": "Razer Naga Pro"}}
        b2._lighting_poll()                                # first tick = startup re-apply
        self.assertEqual(self.fake.applied,
                         [("naga", "static", (7, 8, 9), "", "left", 1000)])

    def test_present_devices_ignores_errors(self):
        b = self.new_backend()
        self.fake.present = {"_error": "daemon not reachable"}
        self.assertEqual(b._present_devices(), set())
        self.fake.present = {"naga": {"_error": "x"}, "tartarus": {"name": "T"}}
        self.assertEqual(b._present_devices(), {"tartarus"})

    def test_reapply_applies_whole_device_before_zones(self):
        b = self.new_backend()
        b.setLightEffect("naga", "spectrum", 0, 0, 0, "")     # whole device
        b.setLightEffect("naga", "static", 1, 2, 3, "logo")   # one zone
        self.fake.applied.clear()
        self.fake.present = {"naga": {"name": "Razer Naga Pro"}}
        b._lighting_poll()
        zones = [a[3] for a in self.fake.applied if a[0] == "naga"]
        self.assertEqual(zones, ["", "logo"])   # whole-device first so zone looks win

    def test_demo_poll_is_a_noop(self):
        b = self.new_backend()
        b.setLightEffect("naga", "static", 1, 2, 3, "")
        b._demo = True
        self.fake.applied.clear()
        self.fake.present = {"naga": {"name": "Razer Naga Pro"}}
        b._lighting_poll()
        self.assertEqual(self.fake.applied, [])

    def test_watcher_not_started_in_demo_or_when_unavailable(self):
        b = self.new_backend()
        b._demo = True
        b.startLightingWatcher()
        self.assertIsNone(b._light_watcher)
        b._demo = False
        self.fake.available_flag = False
        b.startLightingWatcher()
        self.assertIsNone(b._light_watcher)
        b.shutdownLighting()   # safe to call with no watcher running

    def test_watcher_starts_and_stops_cleanly(self):
        b = self.new_backend()
        self.fake.present = {}   # no devices -> each poll returns instantly, no DBus, no delay
        b.startLightingWatcher()
        self.assertIsNotNone(b._light_watcher)
        self.assertTrue(b._light_watcher.is_alive())
        b.shutdownLighting()     # stop() + join() must return promptly
        self.assertIsNone(b._light_watcher)

    def test_nondefault_direction_and_react_ms_survive_and_replay(self):
        b1 = self.new_backend()
        b1.setLightEffect("naga", "wave", 1, 2, 3, "", "right", 1000)
        b1.setLightEffect("naga", "reactive", 4, 5, 6, "logo", "left", 500)
        self.assertEqual(b1.lightState()["naga|"]["direction"], "right")
        self.assertEqual(b1.lightState()["naga|logo"]["react_ms"], 500)
        b2 = self.new_backend()                      # restart, then reconnect
        self.fake.applied.clear()
        self.fake.present = {"naga": {"name": "Razer Naga Pro"}}
        b2._lighting_poll()
        self.assertIn(("naga", "wave", (1, 2, 3), "", "right", 1000), self.fake.applied)
        self.assertIn(("naga", "reactive", (4, 5, 6), "logo", "left", 500), self.fake.applied)

    def test_off_look_persists_and_replays(self):
        b = self.new_backend()
        b.setLightEffect("naga", "none", 0, 0, 0, "")   # lights off is a real, kept choice
        self.assertEqual(b.lightState()["naga|"]["effect"], "none")
        self.fake.applied.clear()
        self.fake.present = {"naga": {"name": "Razer Naga Pro"}}
        b._lighting_poll()
        self.assertEqual(self.fake.applied, [("naga", "none", (0, 0, 0), "", "left", 1000)])

    def test_reapply_uses_razer_green_for_a_legacy_record_missing_rgb(self):
        b = self.new_backend()
        b._reapply_device("naga", {"naga|": {"effect": "static", "direction": "left", "react_ms": 1000}})
        self.assertEqual(self.fake.applied, [("naga", "static", (68, 214, 44), "", "left", 1000)])

    def test_corrupt_lightstate_does_not_crash_startup(self):
        engine.save_json(engine.PROFILES, {
            "version": 2, "active": "Gaming", "live": {},
            "settings": {"lighting": True, "lightState": ["not", "a", "dict"]},
            "profiles": {"Gaming": {"tartarus": {}, "naga": {}}}})
        b = self.new_backend()                 # must boot, not raise
        self.assertEqual(b.lightState(), {})    # the bad value is dropped, not honoured

    def test_one_corrupt_look_value_does_not_starve_the_others(self):
        engine.save_json(engine.PROFILES, {
            "version": 2, "active": "Gaming", "live": {},
            "settings": {"lightState": {
                "naga|": "CORRUPT",      # a non-dict value must be skipped, not crash
                "naga|logo": {"effect": "static", "r": 1, "g": 2, "b": 3,
                              "direction": "left", "react_ms": 1000}}},
            "profiles": {"Gaming": {"tartarus": {}, "naga": {}}}})
        b = self.new_backend()
        self.fake.present = {"naga": {"name": "Razer Naga Pro"}}
        b._lighting_poll()
        self.assertEqual(self.fake.applied, [("naga", "static", (1, 2, 3), "logo", "left", 1000)])

    def test_one_device_error_does_not_starve_the_others(self):
        b = self.new_backend()
        b.setLightEffect("tartarus", "static", 9, 9, 9, "")
        b.setLightEffect("naga", "static", 1, 2, 3, "")
        self.fake.applied.clear()
        self.fake.raise_for = {"tartarus"}      # tartarus throws this tick
        self.fake.present = {"tartarus": {"name": "T"}, "naga": {"name": "N"}}
        b._lighting_poll()                       # must not propagate
        self.assertEqual(self.fake.applied, [("naga", "static", (1, 2, 3), "", "left", 1000)])


class HypershiftTests(_IsolatedBackendTest):
    """Hypershift second layer: layer-routed binds, the hold key, apply plumbing,
    and that shift data survives rename/duplicate/delete/export/restart."""

    def setUp(self):
        super().setUp()
        self.b = self.new_backend()

    def test_set_binding_routes_by_layer(self):
        self.b.setBinding("Gaming", "tartarus", "TAR_01", "F1", "shift")
        self.assertEqual(self.b.shiftBindings["Gaming"]["tartarus"]["TAR_01"], "F1")
        self.assertEqual(self.b.bindings["Gaming"]["tartarus"]["TAR_01"], "Esc")  # base untouched

    def test_set_and_clear_hold_key(self):
        self.b.setShiftKey("Gaming", "tartarus", "TAR_11")
        self.assertEqual(self.b.shiftKeyFor("Gaming", "tartarus"), "TAR_11")
        self.b.setShiftKey("Gaming", "tartarus", "")
        self.assertEqual(self.b.shiftKeyFor("Gaming", "tartarus"), "")

    def test_apply_passes_shift_layer(self):
        self.b.setBinding("Gaming", "tartarus", "TAR_01", "F1", "shift")
        self.b.setShiftKey("Gaming", "tartarus", "TAR_11")
        cap = {}
        saved = engine.apply_profile

        def spy(profile, binds, caps, *, autoload=False, combos=None, shift=None, shift_keys=None, **_):
            cap["shift"], cap["shift_keys"] = shift, shift_keys
            return {}

        engine.apply_profile = spy
        try:
            self.b.applyToHardware("Gaming", "")
        finally:
            engine.apply_profile = saved
        self.assertEqual(cap["shift"]["tartarus"].get("TAR_01"), "F1")
        self.assertEqual(cap["shift_keys"]["tartarus"], "TAR_11")

    def test_rename_carries_shift(self):
        self.b.setBinding("Work", "tartarus", "TAR_01", "F1", "shift")
        self.b.setShiftKey("Work", "tartarus", "TAR_11")
        self.b.renameProfile("Work", "Office")
        self.assertEqual(self.b.shiftBindings["Office"]["tartarus"]["TAR_01"], "F1")
        self.assertEqual(self.b.shiftKeyFor("Office", "tartarus"), "TAR_11")
        self.assertNotIn("Work", self.b.shiftBindings)
        self.assertNotIn("Work", self.b.shiftKeysMap)        # old name gone from both maps

    def test_duplicate_deep_copies_shift(self):
        self.b.setBinding("Work", "tartarus", "TAR_01", "F1", "shift")
        self.b.setShiftKey("Work", "tartarus", "TAR_11")
        self.b.duplicateProfile("Work", "Work2")
        self.assertEqual(self.b.shiftKeyFor("Work2", "tartarus"), "TAR_11")   # hold key carried
        self.b.setBinding("Work2", "tartarus", "TAR_01", "F2", "shift")
        self.b.setShiftKey("Work2", "tartarus", "TAR_12")
        self.assertEqual(self.b.shiftBindings["Work"]["tartarus"]["TAR_01"], "F1")   # source binds intact
        self.assertEqual(self.b.shiftBindings["Work2"]["tartarus"]["TAR_01"], "F2")
        self.assertEqual(self.b.shiftKeyFor("Work", "tartarus"), "TAR_11")           # source hold key intact
        self.assertEqual(self.b.shiftKeyFor("Work2", "tartarus"), "TAR_12")          # copy diverges

    def test_delete_drops_shift(self):
        self.b.setBinding("Work", "tartarus", "TAR_01", "F1", "shift")
        self.b.setShiftKey("Work", "tartarus", "TAR_11")
        self.b.deleteProfile("Work")
        self.assertNotIn("Work", self.b.shiftBindings)
        self.assertNotIn("Work", self.b.shiftKeysMap)

    def test_export_import_round_trips_shift(self):
        self.b.setBinding("Work", "tartarus", "TAR_01", "F1", "shift")
        self.b.setShiftKey("Work", "tartarus", "TAR_11")
        imp = self.b.importProfile(self.b.exportProfile("Work")["json"])
        name = imp["name"]
        self.assertEqual(self.b.shiftBindings[name]["tartarus"]["TAR_01"], "F1")
        self.assertEqual(self.b.shiftKeyFor(name, "tartarus"), "TAR_11")

    def test_shift_persists_across_restart(self):
        self.b.setBinding("Gaming", "tartarus", "TAR_01", "F1", "shift")
        self.b.setShiftKey("Gaming", "tartarus", "TAR_11")
        b2 = self.new_backend()
        self.assertEqual(b2.shiftBindings["Gaming"]["tartarus"]["TAR_01"], "F1")
        self.assertEqual(b2.shiftKeyFor("Gaming", "tartarus"), "TAR_11")


if __name__ == "__main__":
    unittest.main()
