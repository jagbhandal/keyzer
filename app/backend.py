"""KEYZER backend — the bridge between the QML UI and the Python engine layer.

Source of truth for device geometry is ``layouts.json`` at the repo root. The
engine wiring (input-remapper preset generation, daemon control, evdev capture)
lands here later; for now this just serves the layout data to the UI.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot

REPO = Path(__file__).resolve().parent.parent


class Backend(QObject):
    layoutsChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        data = json.loads((REPO / "layouts.json").read_text())
        # Drop documentation keys (e.g. "_comment"); keep only devices.
        self._layouts = {k: v for k, v in data.items() if not k.startswith("_")}

    @Property("QVariant", notify=layoutsChanged)
    def layouts(self) -> dict:
        return self._layouts

    @Slot(result="QVariant")
    def deviceIds(self) -> list[str]:
        return list(self._layouts.keys())

    @Slot(str, result=str)
    def imageUrl(self, rel: str) -> str:
        """Resolve a repo-relative image path to a file:// URL for QML."""
        return (REPO / rel).resolve().as_uri()
