"""Tests for app/engine.py — the pure, deterministic translation + preset layer.

These lock down the stable backend logic: label/key translation, combo joining,
the preset JSON shape, collision-free preset names, path sanitisation, atomic
JSON round-trips, and the capture-source/fallback rules.

Runs identically under ``python3 -m unittest discover tests`` and
``python3 -m pytest tests/`` (pure stdlib unittest, no pytest-only features).

Observed-behaviour notes (asserted as the engine actually behaves on this box,
not as one might guess):
  * "Copy" maps to "Control_L+c" (copy=Ctrl+C), not Ctrl+V — see engine._LABELS.
  * engine.output() validates keyboard symbols against the bundled vocab unioned
    with the daemon's ``--symbol-names`` when reachable; we only assert symbols
    that are stable members of the bundled _KNOWN_KEYSYMS set, plus mouse/media
    BTN_*/KEY_* names which the live daemon's symbol list supplies. We do NOT
    assert anything that needs a running input-remapper to *succeed*.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import conftest  # noqa: F401  -- sys.path + offscreen + warning filter

import engine


class OutputTranslationTests(unittest.TestCase):
    """engine.output(): friendly label / key -> (target_uinput, output_symbol)."""

    def test_named_keys(self):
        self.assertEqual(engine.output("Esc"), ("keyboard", "Escape"))
        self.assertEqual(engine.output("Tab"), ("keyboard", "Tab"))
        self.assertEqual(engine.output("Space"), ("keyboard", "space"))
        self.assertEqual(engine.output("Enter"), ("keyboard", "Return"))

    def test_single_letter_lowercased(self):
        self.assertEqual(engine.output("W"), ("keyboard", "w"))
        self.assertEqual(engine.output("a"), ("keyboard", "a"))

    def test_digits_passthrough(self):
        self.assertEqual(engine.output("1"), ("keyboard", "1"))
        self.assertEqual(engine.output("0"), ("keyboard", "0"))

    def test_modifier_labels(self):
        self.assertEqual(engine.output("Shift")[1], "Shift_L")
        self.assertEqual(engine.output("Ctrl")[1], "Control_L")
        self.assertEqual(engine.output("Control")[1], "Control_L")

    def test_prototype_c_plus_combo(self):
        # "C+C" is the prototype's shorthand for Ctrl+C, lowercased member key.
        self.assertEqual(engine.output("C+C"), ("keyboard", "Control_L+c"))
        self.assertEqual(engine.output("C+x"), ("keyboard", "Control_L+x"))

    def test_action_labels_expand_to_chords(self):
        # Observed: Copy -> Ctrl+C, Paste -> Ctrl+V, Cut -> Ctrl+X.
        self.assertEqual(engine.output("Copy"), ("keyboard", "Control_L+c"))
        self.assertEqual(engine.output("Paste"), ("keyboard", "Control_L+v"))
        self.assertEqual(engine.output("Cut"), ("keyboard", "Control_L+x"))

    def test_mouse_button_labels(self):
        self.assertEqual(engine.output("LMB"), ("mouse", "BTN_LEFT"))
        self.assertEqual(engine.output("RMB"), ("mouse", "BTN_RIGHT"))
        self.assertEqual(engine.output("MMB"), ("mouse", "BTN_MIDDLE"))

    def test_function_key_passthrough(self):
        self.assertEqual(engine.output("F5"), ("keyboard", "F5"))
        self.assertEqual(engine.output("F12"), ("keyboard", "F12"))

    def test_multikey_combos(self):
        self.assertEqual(engine.output("Ctrl+1"), ("keyboard", "Control_L+1"))
        self.assertEqual(engine.output("Ctrl+Shift+1"),
                         ("keyboard", "Control_L+Shift_L+1"))

    def test_held_letter_combo(self):
        # W+A -> input-remapper holds both while the key is held (diagonal movement);
        # extra spaces are tolerated.
        self.assertEqual(engine.output("W+A"), ("keyboard", "w+a"))
        self.assertEqual(engine.output("w + a"), ("keyboard", "w+a"))

    def test_already_valid_symbol_passthrough(self):
        self.assertEqual(engine.output("Control_L+c"), ("keyboard", "Control_L+c"))


class TargetUinputTests(unittest.TestCase):
    """The keyboard / mouse / "keyboard + mouse" classification."""

    def test_keyboard_only(self):
        self.assertEqual(engine.output("W")[0], "keyboard")
        self.assertEqual(engine.output("Ctrl+1")[0], "keyboard")

    def test_mouse_only(self):
        self.assertEqual(engine.output("LMB")[0], "mouse")

    def test_mixed_keyboard_and_mouse(self):
        # A chord with both a key and a BTN_ token -> "keyboard + mouse".
        self.assertEqual(engine.output("Control_L+BTN_LEFT")[0], "keyboard + mouse")


class UntranslatableTests(unittest.TestCase):
    """Everything engine.output() must reject."""

    def test_empty_string(self):
        with self.assertRaises(engine.Untranslatable):
            engine.output("")

    def test_whitespace_only(self):
        with self.assertRaises(engine.Untranslatable):
            engine.output("   ")

    def test_non_string(self):
        for bad in (None, 5, ["W"], {"a": 1}):
            with self.assertRaises(engine.Untranslatable):
                engine.output(bad)  # type: ignore[arg-type]

    def test_unknown_key(self):
        with self.assertRaises(engine.Untranslatable):
            engine.output("WASD")
        with self.assertRaises(engine.Untranslatable):
            engine.output("ZZZ_not_a_key")

    def test_unsupported_placeholder(self):
        for v in ("DPI +", "DPI -", "DPI1", "DPI2"):
            with self.assertRaises(engine.Untranslatable):
                engine.output(v)


class BuildPresetTests(unittest.TestCase):
    """engine.build_preset(binds, captures, combos)."""

    def setUp(self):
        self.captures = {
            "TAR_01": {"type": 1, "code": 1, "origin_hash": "a1"},
            "TAR_08": {"type": 1, "code": 17, "origin_hash": "a1"},
            "TAR_TPAD": {"type": 1, "code": 40, "origin_hash": "a1"},
            # pointer movement (EV_REL=2, code 0/1) -> must be skipped:
            "TAR_PTR": {"type": 2, "code": 0, "origin_hash": "b2"},
            # combo members (analog wheel-ish capture with threshold):
            "TAR_TPAD_N": {"type": 1, "code": 103, "origin_hash": "c3",
                           "analog_threshold": 1},
            "TAR_TPAD_E": {"type": 1, "code": 106, "origin_hash": "c3"},
        }

    def test_single_input_mapping_shape(self):
        mappings, warnings = engine.build_preset({"TAR_01": "Esc"}, self.captures)
        self.assertEqual(warnings, [])
        self.assertEqual(len(mappings), 1)
        m = mappings[0]
        self.assertEqual(set(m), {"input_combination", "target_uinput", "output_symbol"})
        self.assertEqual(m["target_uinput"], "keyboard")
        self.assertEqual(m["output_symbol"], "Escape")
        ic = m["input_combination"]
        self.assertIsInstance(ic, list)
        self.assertEqual(len(ic), 1)
        self.assertEqual(ic[0]["type"], 1)
        self.assertEqual(ic[0]["code"], 1)
        self.assertEqual(ic[0]["origin_hash"], "a1")

    def test_uncaptured_hotspot_warns_and_skips(self):
        mappings, warnings = engine.build_preset({"TAR_99": "X"}, self.captures)
        self.assertEqual(mappings, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("TAR_99", warnings[0])

    def test_pointer_movement_skipped(self):
        mappings, warnings = engine.build_preset({"TAR_PTR": "W"}, self.captures)
        self.assertEqual(mappings, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("pointer", warnings[0].lower())

    def test_untranslatable_value_warns_and_skips(self):
        mappings, warnings = engine.build_preset({"TAR_01": "WASD"}, self.captures)
        self.assertEqual(mappings, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("TAR_01", warnings[0])

    def test_combo_joins_member_captures(self):
        combos = {"TAR_TPAD_NE": ["TAR_TPAD_N", "TAR_TPAD_E"]}
        mappings, warnings = engine.build_preset(
            {"TAR_TPAD_NE": "Tab"}, self.captures, combos)
        self.assertEqual(warnings, [])
        self.assertEqual(len(mappings), 1)
        ic = mappings[0]["input_combination"]
        self.assertEqual(len(ic), 2)
        self.assertEqual([x["code"] for x in ic], [103, 106])
        # analog_threshold preserved where present, absent otherwise:
        self.assertEqual(ic[0].get("analog_threshold"), 1)
        self.assertNotIn("analog_threshold", ic[1])

    def test_combo_with_uncaptured_member_skips_whole_hotspot(self):
        combos = {"TAR_TPAD_NE": ["TAR_TPAD_N", "MISSING"]}
        mappings, warnings = engine.build_preset(
            {"TAR_TPAD_NE": "Tab"}, self.captures, combos)
        self.assertEqual(mappings, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("MISSING", warnings[0])

    def test_mixed_batch_collects_all_warnings(self):
        binds = {"TAR_01": "Esc", "TAR_08": "W", "TAR_TPAD": "WASD", "TAR_99": "X"}
        mappings, warnings = engine.build_preset(binds, self.captures)
        self.assertEqual(len(mappings), 2)            # Esc + W
        self.assertEqual(len(warnings), 2)            # WASD value + uncaptured TAR_99
        symbols = sorted(m["output_symbol"] for m in mappings)
        self.assertEqual(symbols, ["Escape", "w"])


class PresetNameTests(unittest.TestCase):
    """engine.preset_name_for(): keyzer- prefix, collision-free."""

    def test_prefix_and_slug(self):
        name = engine.preset_name_for("Gaming")
        self.assertTrue(name.startswith("keyzer-"))
        self.assertIn("gaming", name)

    def test_slug_collision_gets_distinct_files(self):
        # "Gaming" and "gaming" slug identically but must produce distinct names.
        a = engine.preset_name_for("Gaming")
        b = engine.preset_name_for("gaming")
        self.assertNotEqual(a, b)

    def test_deterministic(self):
        self.assertEqual(engine.preset_name_for("Work"), engine.preset_name_for("Work"))

    def test_empty_falls_back_to_profile_slug(self):
        # Pure-symbol name slugs to "" then to "profile"; still prefixed + hashed.
        name = engine.preset_name_for("!!!")
        self.assertTrue(name.startswith("keyzer-profile-"))


class SanitizeAndPathTests(unittest.TestCase):
    """engine._sanitize / engine.preset_path."""

    def test_sanitize_replaces_path_hostile_chars(self):
        self.assertEqual(engine._sanitize('a/b:c|d"e<f>'), "a_b_c_d_e_f_")

    def test_sanitize_leaves_safe_chars(self):
        self.assertEqual(engine._sanitize("Razer Naga Pro"), "Razer Naga Pro")

    def test_preset_path_honours_xdg_and_sanitizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
                p = engine.preset_path("Razer/Naga", "keyzer-x")
            self.assertTrue(str(p).startswith(tmp))
            self.assertIn("input-remapper-2", str(p))
            self.assertIn("Razer_Naga", str(p))   # '/' sanitised to '_'
            self.assertTrue(str(p).endswith("keyzer-x.json"))


class JsonRoundTripTests(unittest.TestCase):
    """engine.save_json / load_json: atomic write, read-back, corrupt fallback."""

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "data.json"
            data = {"version": 1, "profiles": {"Gaming": {"tartarus": {}}}}
            engine.save_json(path, data)
            self.assertTrue(path.exists())
            self.assertEqual(engine.load_json(path), data)

    def test_no_leftover_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            engine.save_json(path, {"a": 1})
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_missing_file_returns_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "absent.json"
            self.assertIsNone(engine.load_json(path))
            self.assertEqual(engine.load_json(path, fallback={"x": 1}), {"x": 1})

    def test_corrupt_file_returns_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrupt.json"
            path.write_text("{ this is not json ]")
            self.assertEqual(engine.load_json(path, fallback="FB"), "FB")


class WritePresetTests(unittest.TestCase):
    """engine.write_preset round-trip: a JSON array on disk under XDG."""

    def test_write_preset_array_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
                caps = {"TAR_01": {"type": 1, "code": 1, "origin_hash": "a1"}}
                mappings, _ = engine.build_preset({"TAR_01": "Esc"}, caps)
                path = engine.write_preset("Razer Razer Tartarus Pro",
                                           "keyzer-test", mappings)
            self.assertTrue(path.exists())
            self.assertTrue(str(path).startswith(tmp))
            loaded = json.loads(path.read_text())
            self.assertIsInstance(loaded, list)
            self.assertEqual(loaded[0]["output_symbol"], "Escape")


class CapturesOriginTests(unittest.TestCase):
    """engine.captures_origin / load_captures: user vs bundled-default fallback.

    The bundled default lives at the repo root (an absolute path, NOT under
    XDG_CONFIG_HOME), so pointing XDG at an empty temp dir leaves the user file
    absent while the default remains -> 'default'.
    """

    def _origin_with_xdg(self, tmp: str) -> str:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            # CAPTURES is computed at import time from the original XDG; recompute
            # the user path against the temp dir to mirror a fresh process.
            user = Path(tmp) / "keyzer" / "captures.json"
            return engine.captures_origin(user)

    def test_origin_default_when_user_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._origin_with_xdg(tmp), "default")

    def test_origin_user_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "keyzer" / "captures.json"
            user.parent.mkdir(parents=True, exist_ok=True)
            user.write_text(json.dumps({"tartarus": {"captured": {}}}))
            self.assertEqual(engine.captures_origin(user), "user")

    def test_load_captures_falls_back_to_default(self):
        # With an absent user path, load_captures reads the bundled default and
        # strips underscore-prefixed metadata keys (e.g. "_comment").
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "keyzer" / "captures.json"
            caps = engine.load_captures(user)
            self.assertIn("tartarus", caps)
            self.assertIn("naga", caps)
            self.assertNotIn("_comment", caps)

    def test_load_captures_reads_user_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "keyzer" / "captures.json"
            user.parent.mkdir(parents=True, exist_ok=True)
            user.write_text(json.dumps({"_meta": "x", "naga": {"captured": {}}}))
            caps = engine.load_captures(user)
            self.assertEqual(set(caps), {"naga"})  # "_meta" stripped


if __name__ == "__main__":
    unittest.main()
