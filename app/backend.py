"""KEYZER backend — the bridge between the QML UI and the Python engine layer.

Source of truth for device geometry is ``layouts.json`` at the repo root.
Binding state lives in in-memory profiles (seeded to match the validated HTML
prototype); generating real input-remapper presets hangs off ``setBinding`` in
a later phase. The UI never imports engine internals — it talks to this object.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

REPO = Path(__file__).resolve().parent.parent


def _default_profiles() -> dict:
    """Sample profiles, mirroring the prototype's defaults."""
    return {
        "Gaming": {
            "tartarus": {
                "TAR_01": "Esc", "TAR_02": "1", "TAR_03": "2", "TAR_04": "3", "TAR_05": "4",
                "TAR_06": "Tab", "TAR_07": "Q", "TAR_08": "W", "TAR_09": "E", "TAR_10": "R",
                "TAR_11": "Shift", "TAR_12": "A", "TAR_13": "S", "TAR_14": "D", "TAR_15": "F",
                "TAR_16": "Ctrl", "TAR_17": "Z", "TAR_18": "X", "TAR_19": "C", "TAR_20": "V",
                "TAR_THUMB": "Space", "TAR_TPAD": "WASD", "TAR_WHEEL": "Vol",
            },
            "naga": {
                "NAGA_L": "LMB", "NAGA_R": "RMB", "NAGA_WHL": "MMB",
                "NAGA_WHL_L": "Prev", "NAGA_WHL_R": "Next", "NAGA_DPI1": "DPI +", "NAGA_DPI2": "DPI -",
                "NAGA_01": "1", "NAGA_02": "2", "NAGA_03": "3", "NAGA_04": "4", "NAGA_05": "5",
                "NAGA_06": "6", "NAGA_07": "Q", "NAGA_08": "E", "NAGA_09": "R",
                "NAGA_10": "F", "NAGA_11": "G", "NAGA_12": "T",
            },
        },
        "Work": {
            "tartarus": {"TAR_01": "Esc", "TAR_07": "C+C", "TAR_08": "C+V",
                         "TAR_09": "C+Z", "TAR_12": "C+S", "TAR_THUMB": "Super"},
            "naga": {"NAGA_L": "LMB", "NAGA_R": "RMB", "NAGA_01": "Copy", "NAGA_02": "Paste"},
        },
        "Default": {"tartarus": {}, "naga": {}},
    }


class Backend(QObject):
    bindingsChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        data = json.loads((REPO / "layouts.json").read_text())
        self._layouts = {k: v for k, v in data.items() if not k.startswith("_")}
        self._profiles = _default_profiles()

    # ----- geometry (single source of truth) -----
    @Property("QVariant", constant=True)
    def layouts(self) -> dict:
        return self._layouts

    @Slot(result="QVariant")
    def deviceIds(self) -> list[str]:
        return list(self._layouts.keys())

    @Slot(str, result=str)
    def imageUrl(self, rel: str) -> str:
        return (REPO / rel).resolve().as_uri()

    @Slot(str, result="QVariant")
    def viewNames(self, dev: str) -> list[str]:
        # Insertion order (Qt's QVariantMap sorts keys, so the UI can't use Object.keys).
        return list(self._layouts[dev]["views"].keys())

    # ----- bindings / profiles -----
    @Property("QVariant", notify=bindingsChanged)
    def bindings(self) -> dict:
        return self._profiles

    @Slot(result="QVariant")
    def profileNames(self) -> list[str]:
        return list(self._profiles.keys())

    @Slot(str, str, str, str)
    def setBinding(self, profile: str, dev: str, key: str, value: str) -> None:
        self._profiles.setdefault(profile, {}).setdefault(dev, {})[key] = value
        self.bindingsChanged.emit()

    @Slot(str, str, str)
    def clearBinding(self, profile: str, dev: str, key: str) -> None:
        self._profiles.get(profile, {}).get(dev, {}).pop(key, None)
        self.bindingsChanged.emit()

    # ----- align / copy-layout -----
    @Slot(str)
    def copyToClipboard(self, text: str) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text)

    # ----- offscreen QA: drive initial UI state from env vars -----
    @Slot(result="QVariant")
    def qaState(self) -> dict:
        names = ("KEYZER_DEV", "KEYZER_VIEW", "KEYZER_PROFILE", "KEYZER_SELECT",
                 "KEYZER_LIGHTING", "KEYZER_ALIGN", "KEYZER_APPAWARE")
        return {n: os.environ.get(n, "") for n in names}
