"""KEYZER — entry point.

Run the app:               python3 app/main.py
Offscreen QA screenshot:   QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \\
                           QSG_RENDER_LOOP=basic KEYZER_SHOT=/tmp/keyzer.png python3 app/main.py

We host the UI in a QQuickView (a real QQuickWindow, so grabWindow() works for
offscreen QA) with an Item-rooted Main.qml.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQuick import QQuickView

from backend import Backend

APP_DIR = Path(__file__).resolve().parent
ICON = APP_DIR.parent / "packaging" / "keyzer.svg"


def _env_int(name: str, default: int) -> int:
    """An int from the environment, falling back to default on a bad/absent value."""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def main() -> int:
    # Demo mode: `--demo` (or KEYZER_DEMO=1) runs the app fully populated with no
    # Razer hardware — for trying KEYZER out and for the screenshot factory.
    if "--demo" in sys.argv:
        os.environ["KEYZER_DEMO"] = "1"

    app = QGuiApplication(sys.argv)
    app.setApplicationName("KEYZER")
    app.setOrganizationName("KEYZER")
    # Show KEYZER's own icon instead of a generic cogwheel. On Wayland/GNOME the
    # taskbar icon comes from the matching .desktop file, so the window's app-id
    # must equal its basename ("keyzer.desktop" -> install.sh installs it); on
    # X11/XWayland the window icon below is used directly.
    app.setDesktopFileName("keyzer")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))

    view = QQuickView()
    backend = Backend()
    # On quit, cleanly stop any in-progress calibration so its worker thread ungrabs
    # the device and isn't torn down mid-run (avoids a 'QThread destroyed' abort).
    app.aboutToQuit.connect(backend.shutdownCalibration)
    view.rootContext().setContextProperty("backend", backend)
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setColor("#0b0b0e")
    view.setTitle("KEYZER")
    # Render size (the screenshot factory bumps this for crisp marketing shots).
    view.resize(_env_int("KEYZER_W", 1200), _env_int("KEYZER_H", 760))
    view.setSource(QUrl.fromLocalFile(str(APP_DIR / "qml" / "Main.qml")))
    if view.status() == QQuickView.Status.Error:
        for err in view.errors():
            print("QML error:", err.toString(), flush=True)
        return 1
    view.show()

    # Offscreen QA hook: KEYZER_SHOT=<path> renders once and exits.
    # NOTE: run with QSG_RENDER_LOOP=basic offscreen, or grabWindow() deadlocks.
    shot = os.environ.get("KEYZER_SHOT")
    if shot:
        def grab() -> None:
            try:
                img = view.grabWindow()
                ok = (not img.isNull()) and img.save(shot)
                print(f"[shot] null={img.isNull()} saved={ok} "
                      f"size={img.width()}x{img.height()}", flush=True)
            except Exception as exc:  # never hang the dev loop
                print(f"[shot] grab failed: {exc}", flush=True)
            app.quit()

        QTimer.singleShot(1200, grab)
        QTimer.singleShot(6000, app.quit)  # hard safety net

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
