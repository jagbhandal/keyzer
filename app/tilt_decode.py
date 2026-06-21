#!/usr/bin/env python3
"""Decode Razer Naga Pro wheel-tilt from raw mouse HID reports — pure logic.

The Naga Pro (USB 1532:008f) reports wheel tilt in the *main mouse* HID report
(/dev/hidraw for interface .0001) as two bits of byte 0 that its own HID report
descriptor declares as constant padding — so the kernel discards them and no
evdev event is ever produced. That is why the tilt looks "Synapse only": the
bytes are on the wire, but nothing in the normal input stack surfaces them.

  data[0] bit 5 (0x20) = tilt LEFT     (OpenRazer BIT_TILT_L)
  data[0] bit 6 (0x40) = tilt RIGHT    (OpenRazer BIT_TILT_R)

This module turns a stream of those raw reports into clean press/release events.
It is deliberately free of any I/O (no hidraw, no uinput, no evdev) so the edge
logic is fully unit-testable against captured bytes; tilt_shim.py wraps it with
the real device plumbing.
"""
from __future__ import annotations

# Public vocabulary (kept as plain strings so callers/tests need no imports).
LEFT = "left"
RIGHT = "right"
DOWN = "down"
UP = "up"

LEFT_BIT = 0x20   # byte0 bit 5
RIGHT_BIT = 0x40  # byte0 bit 6

# How long a tilt is held open after the last report bearing its bit, before we
# synthesize a release. This is a safety net for a missed "all clear" report; it
# also sets the tap-vs-hold feel when the device only pulses the bit rather than
# repeating it while held.
DEFAULT_RELEASE_TIMEOUT = 0.15


class TiltDecoder:
    """Edge-detects tilt-left / tilt-right from successive raw mouse reports.

    Call ``feed(report, now)`` for every HID report read from the device, and
    ``tick(now)`` periodically (e.g. on a select() timeout) so a held tilt that
    stops sending reports still gets released. Both return a list of
    ``(direction, action)`` tuples, where direction is ``LEFT``/``RIGHT`` and
    action is ``DOWN``/``UP``; the list is empty when nothing changed.
    """

    def __init__(self, release_timeout: float = DEFAULT_RELEASE_TIMEOUT):
        self._timeout = release_timeout
        # per-direction: is the synthetic key currently down, and when did we
        # last see its bit set.
        self._down = {LEFT: False, RIGHT: False}
        self._last_seen = {LEFT: 0.0, RIGHT: 0.0}

    def feed(self, report: bytes, now: float) -> list[tuple[str, str]]:
        if not report:
            return []
        byte0 = report[0]
        active = {LEFT: bool(byte0 & LEFT_BIT), RIGHT: bool(byte0 & RIGHT_BIT)}
        events: list[tuple[str, str]] = []
        # Release before press so a direction switch reads as up-then-down.
        for direction in (LEFT, RIGHT):
            if active[direction]:
                self._last_seen[direction] = now
            elif self._down[direction]:
                self._down[direction] = False
                events.append((direction, UP))
        for direction in (LEFT, RIGHT):
            if active[direction] and not self._down[direction]:
                self._down[direction] = True
                events.append((direction, DOWN))
        return events

    def tick(self, now: float) -> list[tuple[str, str]]:
        """Release any direction whose bit hasn't been seen within the timeout."""
        events: list[tuple[str, str]] = []
        for direction in (LEFT, RIGHT):
            if self._down[direction] and now - self._last_seen[direction] >= self._timeout:
                self._down[direction] = False
                events.append((direction, UP))
        return events

    def release_all(self, now: float = 0.0) -> list[tuple[str, str]]:
        """Force-release everything (used on shutdown so no key sticks down)."""
        events: list[tuple[str, str]] = []
        for direction in (LEFT, RIGHT):
            if self._down[direction]:
                self._down[direction] = False
                events.append((direction, UP))
        return events
