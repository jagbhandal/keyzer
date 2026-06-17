"""Tests for engine punctuation translation — the 'Ctrl+- emitted Shift' bug.

input-remapper rejects the literal char "-" as an output symbol; it wants the X
keysym name "minus". Before the fix, a bind like Ctrl+- was dropped server-side
and the key fell through to its default. engine._PUNCT maps the printable chars
to their keysym names so chords like Ctrl+- translate correctly.
"""
from __future__ import annotations

import unittest

import conftest  # noqa: F401  -- sys.path + offscreen + warning filter

import engine


class PunctTranslationTests(unittest.TestCase):
    def test_bare_punctuation_becomes_keysym_name(self):
        self.assertEqual(engine.output("-"), ("keyboard", "minus"))
        self.assertEqual(engine.output("="), ("keyboard", "equal"))
        self.assertEqual(engine.output("/"), ("keyboard", "slash"))

    def test_ctrl_minus_was_the_reported_bug(self):
        self.assertEqual(engine.output("Ctrl+-"), ("keyboard", "Control_L+minus"))

    def test_ctrl_equal_and_shift_semicolon(self):
        self.assertEqual(engine.output("Ctrl+="), ("keyboard", "Control_L+equal"))
        self.assertEqual(engine.output("Shift+;"), ("keyboard", "Shift_L+semicolon"))

    def test_plus_keysym_passes_through(self):
        # the UI sends the '+' key as the keysym name 'plus' (it can't use the raw
        # char without clashing with the chord delimiter).
        self.assertEqual(engine.output("Ctrl+plus"), ("keyboard", "Control_L+plus"))

    def test_punct_keysyms_are_in_the_validation_vocab(self):
        for name in ("minus", "equal", "slash", "semicolon", "comma", "period"):
            self.assertIn(name, engine._KNOWN_KEYSYMS, name)


if __name__ == "__main__":
    unittest.main()
