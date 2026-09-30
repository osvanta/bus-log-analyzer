# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

APP_NAME    = "Osvanta Bus Log Analyzer"
APP_VERSION = "v00.01.02"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # Before any worker thread exists: an automatic collection on a worker
    # thread destroys discarded plot items there and crashes Qt.
    from gui.gc_guard import GuiThreadGarbageCollector
    gc_guard = GuiThreadGarbageCollector(app)  # noqa: F841 — alive until exit

    # Resolve resource path for both dev and frozen EXE
    _res_root = (
        Path(sys._MEIPASS) if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
        else Path(__file__).resolve().parent
    )
    # Prefer PNG (full RGBA, sharp on HiDPI); fall back to ICO
    for _icon_name in ('app_icon.png', 'app_icon.ico'):
        _icon_path = _res_root / 'resources' / _icon_name
        if _icon_path.exists():
            app.setWindowIcon(QIcon(str(_icon_path)))
            break

    # ── Show splash immediately — before any heavy imports ────────────────
    # gui.splash only imports PySide6 (already loaded) + pathlib.
    # All heavy modules (cantools, python-can, asammdf, pyqtgraph) are
    # imported lazily when MainWindow / PlotPanel are first constructed.
    from gui.splash import SplashScreen
    splash = SplashScreen(version=APP_VERSION)
    splash.show()
    app.processEvents()

    splash.set_status('Loading UI components...')
    from gui.main_window import MainWindow   # ← heavy imports happen here

    splash.set_status('Building main window...')
    window = MainWindow(app_name=APP_NAME, version=APP_VERSION, splash=splash)

    window.show()
    splash.finish(window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
