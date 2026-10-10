# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Closing the window while a worker thread runs must not end the application.

Every worker thread is a child of the main window, and Qt ends the process
through Windows fail-fast (0xC0000409) when one is destroyed while it runs.
Nothing is printed, so each check runs the application in a fresh
interpreter and reads its exit code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]
HELD_FOR = 3.0

_CLOSE_WHILE_BUSY = r"""
import json, sys, threading, time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

measurement, scenario, held_for = sys.argv[1], sys.argv[2], float(sys.argv[3])
app = QApplication(sys.argv[:1])
import gui.load_worker as load_worker
import gui.main_window as main_window

QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (measurement, ""))
dialogs = []

def record_dialog(*args, **kwargs):
    dialogs.append(str(args[1]))
    return QMessageBox.StandardButton.Ok

for name in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, name, staticmethod(record_dialog))

busy, release = threading.Event(), threading.Event()

def hold():  # on the worker thread, until after the window was closed
    busy.set()
    release.wait(30)

if scenario == "inspection":
    def held_inspection(path, app_version=""):
        hold()
        return "OSVANTA BUS LOG ANALYZER LOAD DEBUG\nheld inspection"
    main_window.inspect_measurement = held_inspection
else:
    # Held in the reader factory, on the load thread. A LoadWorker subclass
    # that overrides run() would be called on the GUI thread by PySide6.
    open_reader = load_worker.reader_factory
    def held_reader_factory(*args):
        hold()
        if scenario == "failing load":
            raise RuntimeError("unreadable measurement")
        return open_reader(*args)
    load_worker.reader_factory = held_reader_factory

window = main_window.MainWindow("Osvanta Bus Log Analyzer", "00.00.99")
window.show()
window.choose_blf()
closed_at = []

def start():
    if scenario == "inspection":
        window.toggle_debug_mode()  # inspects the open measurement
    else:
        window._toolbar_actions["Load + Decode"].trigger()
    close_when_busy()

def close_when_busy():
    if not busy.is_set():
        QTimer.singleShot(10, close_when_busy)
        return
    window.close()
    closed_at.append(time.monotonic())
    visible = [w.windowTitle() for w in app.topLevelWidgets() if w.isVisible()]
    print(json.dumps({"visible_after_close": visible}), flush=True)
    threading.Timer(held_for, release.set).start()

QTimer.singleShot(0, start)
code = app.exec()
print(json.dumps({
    "exit": code,
    "result_applied": window.store is not None,
    "dialogs": dialogs,
    "seconds_after_close": time.monotonic() - closed_at[0],
}), flush=True)
sys.exit(code)
"""


# "inspection" outlasts the 2 s the window waits for the debug thread, which
# was then deleted while it still ran.
@pytest.mark.parametrize("scenario", ["load", "failing load", "inspection"])
def test_closing_while_busy_hides_at_once_and_exits_cleanly(scenario):
    completed = subprocess.run(
        [sys.executable, "-c", _CLOSE_WHILE_BUSY,
         str(FIXTURES / "sample_wide.csv"), scenario, str(HELD_FOR)],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                 PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    closed, ended = (
        json.loads(line) for line in completed.stdout.splitlines()
        if line.startswith("{")
    )
    assert closed == {"visible_after_close": []}
    # What the abandoned work produced is dropped: no store, no "Load
    # failed" or partial-load dialog, no debug report on a closed window.
    assert ended["result_applied"] is False
    assert ended["dialogs"] == []
    # Closed as soon as the work it waited for had finished.
    assert HELD_FOR - 0.5 < ended["seconds_after_close"] < HELD_FOR + 5
