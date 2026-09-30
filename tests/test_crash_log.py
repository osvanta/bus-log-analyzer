# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A crash, a freeze or an uncaught exception must leave a readable record.

Each check runs in a fresh interpreter: the crash log installs process-wide
hooks, and most of what it records ends the process.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from gui.crash_log import MAX_BYTES, CrashLog

REPO_ROOT = Path(__file__).resolve().parents[1]
# Windows fail-fast: how both Qt's fatal errors and native crashes end.
FAIL_FAST = 0xC0000409
ACCESS_VIOLATION = 0xC0000005

_PRELUDE = r"""
import sys, threading, time
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QThread, QTimer
app = QCoreApplication([])
app.setApplicationName("Osvanta Bus Log Analyzer")
app.setApplicationVersion("v00.00.99")
from gui.crash_log import CrashLog
crash_log = CrashLog(app, Path(sys.argv[1]), freeze_seconds=float(sys.argv[2]))
"""


def _session(text: str) -> str:
    """The last session's lines, without the file's explanatory preamble."""
    return text.rsplit("\n==== ", 1)[1]


def _run(script: str, log: Path, freeze_seconds: float = 20.0):
    completed = subprocess.run(
        [sys.executable, "-c", _PRELUDE + script, str(log), str(freeze_seconds)],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                 PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )
    return completed, _session(log.read_text(encoding="utf-8"))


def test_qt_fatal_error_is_recorded_with_every_stack(tmp_path):
    # The error behind the Load + Decode crash: Qt ends the process through
    # fail-fast and prints the reason nowhere a user can find it.
    completed, log = _run(r"""
def destroy_running_thread():
    thread = QThread()
    thread.start()
    while not thread.isRunning():
        time.sleep(0.01)
    del thread

destroy_running_thread()
""", tmp_path / "crash.log")

    # Usually fail-fast; now and then the still-running thread faults first
    # while the process is being torn down.
    assert completed.returncode & 0xFFFFFFFF in (FAIL_FAST, ACCESS_VIOLATION)
    assert ("Qt fatal error, the application is terminated: "
            "QThread: Destroyed while thread") in log
    assert "destroy_running_thread" in log
    assert "Session ended normally." not in log


def test_uncaught_exceptions_are_recorded_on_every_thread(tmp_path):
    completed, log = _run(r"""
def failing_slot():
    raise RuntimeError("raised in a slot")

def failing_worker():
    raise ValueError("raised in a worker thread")

worker = threading.Thread(target=failing_worker, name="Worker-1")
worker.start()
worker.join()
QTimer.singleShot(0, failing_slot)
QTimer.singleShot(200, app.quit)
app.exec()
""", tmp_path / "crash.log")

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert "Uncaught exception in thread Worker-1" in log
    assert "ValueError: raised in a worker thread" in log
    assert "Uncaught exception in thread MainThread" in log
    assert "RuntimeError: raised in a slot" in log
    assert log.rstrip().endswith("Session ended normally.")


def test_a_frozen_gui_thread_dumps_every_stack(tmp_path):
    completed, log = _run(r"""
def block_gui_thread():
    time.sleep(2.5)

QTimer.singleShot(100, block_gui_thread)
QTimer.singleShot(200, app.quit)
app.exec()
""", tmp_path / "crash.log", freeze_seconds=1.0)

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert "Timeout (0:00:01)!" in log
    assert "block_gui_thread" in log


def test_a_responsive_gui_thread_dumps_nothing(tmp_path):
    completed, log = _run(r"""
QTimer.singleShot(3000, app.quit)
app.exec()
""", tmp_path / "crash.log", freeze_seconds=1.5)

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert "Timeout" not in log


def test_a_busy_gui_thread_is_recorded_without_crashing(tmp_path):
    # Busy, not blocked: the GUI thread pushes and pops frames while its
    # stack is written. faulthandler's timed dump read those frames without
    # the GIL, and a slow file open on the GUI thread crashed the application.
    completed, log = _run(r"""
def dive(depth):
    return dive(depth - 1) if depth else 0

def keep_busy(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for depth in range(20, 400, 7):
            dive(depth)

def busy_spells():
    for _ in range(8):
        keep_busy(0.8)
        app.processEvents()  # one heartbeat, so each spell is a new freeze
    app.quit()

QTimer.singleShot(0, busy_spells)
app.exec()
""", tmp_path / "crash.log", freeze_seconds=0.5)

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert log.count("Timeout (0:00:00.500000)!") >= 4
    assert "keep_busy" in log
    assert log.rstrip().endswith("Session ended normally.")


def test_native_crash_is_recorded(tmp_path):
    completed, log = _run(r"""
import ctypes

def crash_natively():
    ctypes.string_at(0)

crash_natively()
""", tmp_path / "crash.log")

    assert completed.returncode != 0
    assert "Windows fatal exception: access violation" in log
    assert "crash_natively" in log


def test_python_fatal_errors_are_recorded_without_a_console(tmp_path):
    # Python writes its fatal errors to the C runtime's stderr. A windowed
    # build has none, so the report of an overrun buffer was lost.
    log = tmp_path / "crash.log"
    completed = subprocess.run(
        [sys.executable, "-X", "dev", "-X", "tracemalloc=5", "-c", r"""
import ctypes, sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
sys.stderr = None
from gui.crash_log import CrashLog
crash_log = CrashLog(app, Path(sys.argv[1]))

def overrun_a_buffer():
    buf = bytearray(16)
    view = (ctypes.c_char * 16).from_buffer(buf)
    address = ctypes.addressof(view)
    del view
    ctypes.memset(address + 17, 0x41, 4)
    del buf

overrun_a_buffer()
""", str(log)],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                 PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )

    session = _session(log.read_text(encoding="utf-8"))
    assert completed.returncode != 0
    assert "Fatal Python error: _PyMem_DebugRawFree: bad trailing pad byte" in session
    assert "Memory block allocated at" in session
    assert "overrun_a_buffer" in session


def test_application_installs_the_crash_log(tmp_path):
    log = tmp_path / "crash.log"
    completed = subprocess.run(
        [sys.executable, "-c", r"""
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
import gui.crash_log
gui.crash_log.default_log_path = lambda: Path(sys.argv[1])
import app as entry
QApplication.exec = lambda self: 0
entry.main()
""", str(log)],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                 PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    session = _session(log.read_text(encoding="utf-8"))
    assert re.match(r"\S+ \S+  Osvanta Bus Log Analyzer v\d", session)
    assert session.rstrip().endswith("Session ended normally.")


def test_exiting_with_a_running_thread_is_not_called_normal(tmp_path):
    # Closing the main window during Load + Decode: the process only dies
    # later in shutdown, after the last chance to record anything.
    log = tmp_path / "crash.log"
    completed = subprocess.run(
        [sys.executable, "-c", r"""
import sys
from pathlib import Path
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
from gui.crash_log import CrashLog
crash_log = CrashLog(app, Path(sys.argv[1]))
window = QWidget()
thread = QThread(window)
thread.start()
""", str(log)],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                 PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )

    session = _session(log.read_text(encoding="utf-8"))
    assert "Shutting down with 1 thread(s) still running." in session
    assert "Session ended normally." not in session
    assert completed.returncode & 0xFFFFFFFF == FAIL_FAST


def test_an_oversized_log_is_rotated(tmp_path):
    log = tmp_path / "crash.log"
    log.write_text("x" * (MAX_BYTES + 1), encoding="utf-8")

    path, handle = CrashLog._open(log)
    handle.close()

    assert path == log
    assert (tmp_path / "crash.log.1").stat().st_size == MAX_BYTES + 1
    assert log.read_text(encoding="utf-8").startswith("Osvanta Bus Log Analyzer crash log.")
