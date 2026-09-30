# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Loading a measurement again must release the previous load."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from core.load_worker import LoadWorker
from gui.main_window import MainWindow

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _wait_for_load(window: MainWindow, qapp: QApplication) -> None:
    deadline = time.monotonic() + 30.0
    while window._thread is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert window._thread is None, "Load + Decode did not finish"


@pytest.fixture()
def window(qapp, monkeypatch):
    measurement = FIXTURES / "sample_wide.csv"
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(measurement), ""),
    )
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, name,
            lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
        )
    widget = MainWindow("Osvanta Bus Log Analyzer", "00.00.99")
    yield widget
    _wait_for_load(widget, qapp)
    widget.close()
    qapp.processEvents()


def _open_and_load(window: MainWindow, qapp: QApplication) -> None:
    window.choose_blf()
    window.load_data()
    _wait_for_load(window, qapp)


def test_reloading_releases_the_previous_measurement(window, qapp):
    # Each finished LoadWorker used to survive, holding the whole decoded
    # store of its load in _live_store, so every Load + Decode added another
    # copy of the measurement to memory until the application crashed.
    _open_and_load(window, qapp)
    first_store = weakref.ref(window.store)

    _open_and_load(window, qapp)

    assert window.store is not None
    assert first_store() is None


def test_load_worker_is_deleted_on_the_gui_thread(window, qapp, monkeypatch):
    # Released anywhere but the GUI thread, a worker's destructor can wait for
    # the GIL while holding a Qt mutex the GUI thread needs (see the matching
    # CalculationWorker test), so the leak fix must not move deletion there.
    deleted_on = []

    class _TrackedWorker(LoadWorker):
        def __init__(self, *args) -> None:
            super().__init__(*args)
            self.destroyed.connect(
                lambda *_: deleted_on.append(threading.get_ident()),
                Qt.ConnectionType.DirectConnection,
            )

    monkeypatch.setattr("gui.main_window.LoadWorker", _TrackedWorker)

    _open_and_load(window, qapp)

    assert window.store is not None
    assert deleted_on == [threading.get_ident()]


def test_a_second_load_is_refused_while_one_runs(window, qapp, monkeypatch):
    # A click made while the window looked stuck started a second load, and
    # the first load's cleanup then deleted the second's running thread.
    workers = []
    dialogs = []

    class _CountedWorker(LoadWorker):
        def __init__(self, *args) -> None:
            super().__init__(*args)
            workers.append(self)

    def open_dialog(*args, **kwargs):
        dialogs.append(args)
        return str(FIXTURES / "sample_wide.csv"), ""

    monkeypatch.setattr("gui.main_window.LoadWorker", _CountedWorker)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", open_dialog)
    load_actions = [
        window._toolbar_actions[name]
        for name in ("Open File", "Load + Decode", "Load Config")
    ]

    window.choose_blf()
    window.load_data()
    # No events are processed until _wait_for_load, so the window still sees
    # this load as running however quickly its thread finishes.
    running = window._thread

    assert [action.isEnabled() for action in load_actions] == [False] * 3
    # The debug window's buttons call these directly, past the toolbar.
    window.load_data()
    window.choose_blf()
    window.load_configuration()
    assert window._thread is running
    assert len(workers) == 1
    assert len(dialogs) == 1

    _wait_for_load(window, qapp)
    assert window.store is not None
    assert [action.isEnabled() for action in load_actions] == [True] * 3


_IMPATIENT_CLICK_SCRIPT = r"""
import json, sys, threading
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

measurement = sys.argv[1]
app = QApplication(sys.argv[:1])
import core.load_worker as load_worker
import gui.main_window as main_window

QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (measurement, ""))
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))

# Held in the reader factory, on the load thread. A LoadWorker subclass that
# overrides run() would be called on the GUI thread by PySide6 instead.
gates = []
open_reader = load_worker.reader_factory

def gated_reader_factory(*args):
    gate = threading.Event()
    gates.append(gate)
    gate.wait(10)
    return open_reader(*args)

load_worker.reader_factory = gated_reader_factory
window = main_window.MainWindow("Osvanta Bus Log Analyzer", "00.00.99")
window.show()
window.choose_blf()
load = window._toolbar_actions["Load + Decode"]

def start_load():
    load.trigger()
    wait_until_loading()

def wait_until_loading():
    if gates:
        click_again()
    else:
        QTimer.singleShot(10, wait_until_loading)

def click_again():
    load.trigger()      # the impatient click, while the first load runs
    gates[0].set()      # then the first load finishes
    QTimer.singleShot(1300, release_all)

def release_all():
    for gate in gates:
        gate.set()
    settle()

def settle():
    if window._thread is None and not any(
        thread.isRunning() for thread in window.findChildren(QThread)
    ):
        print(json.dumps({"loads_started": len(gates)}))
        window.close()
        app.quit()
    else:
        QTimer.singleShot(50, settle)

# Inside the event loop, as in the application: Qt only delivers the
# deleteLater() that destroys the thread from a running event loop. Each step
# schedules the next, since timers already due fire in no guaranteed order.
QTimer.singleShot(0, start_load)
sys.exit(app.exec())
"""


def test_an_impatient_second_click_does_not_terminate_the_application():
    # "QThread: Destroyed while thread is still running" is a Qt fatal error:
    # the process ends through Windows fail-fast (0xC0000409) without
    # printing it anywhere, so the exit code is all a test can see.
    completed = subprocess.run(
        [sys.executable, "-c", _IMPATIENT_CLICK_SCRIPT,
         str(FIXTURES / "sample_wide.csv")],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                 PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "loads_started": 1,
    }
