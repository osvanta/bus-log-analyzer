# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The garbage collector must only free Qt objects on the GUI thread.

Each check runs in a fresh interpreter. conftest.py keeps the collector off
for the whole session once a Qt test has run, and collecting in-process after
earlier tests closed their widgets is itself a known crash, so the automatic
collection this guards against cannot be exercised inside the suite.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(script: str, *args: str) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(REPO_ROOT))
    completed = subprocess.run(
        [sys.executable, "-c", script, *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


_GUARD_SCRIPT = r"""
import gc, json, threading, time
from PySide6.QtCore import QCoreApplication
from gui.gc_guard import GuiThreadGarbageCollector

app = QCoreApplication([])
gui = threading.get_ident()
guard = GuiThreadGarbageCollector(app, interval_ms=20)
result = {"enabled_while_installed": gc.isenabled()}

class Node:
    def __del__(self):
        freed_on.append(threading.get_ident())

freed_on = []
node = Node()
node.cycle = node
del node

# Kept alive: the collector schedules on allocations minus deallocations,
# so garbage the worker freed itself would not make a collection due.
kept = []
def allocate():
    kept.extend([] for _ in range(200_000))
worker = threading.Thread(target=allocate)
worker.start()
worker.join()
result["freed_by_worker_allocations"] = bool(freed_on)

deadline = time.monotonic() + 5
while not freed_on and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.005)
result["freed_on_gui_thread"] = freed_on == [gui]

guard.stop()
result["enabled_after_stop"] = gc.isenabled()
print(json.dumps(result))
"""


def test_guard_moves_collection_to_the_gui_thread():
    result = _run(_GUARD_SCRIPT)

    assert result == {
        "enabled_while_installed": False,
        "freed_by_worker_allocations": False,
        "freed_on_gui_thread": True,
        "enabled_after_stop": True,
    }


_RELOAD_SCRIPT = r"""
import gc, json, sys, threading, time
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

measurement = sys.argv[1]
app = QApplication(sys.argv[:1])
from gui.gc_guard import GuiThreadGarbageCollector
guard = GuiThreadGarbageCollector(app, interval_ms=100)
from gui.main_window import MainWindow

QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (measurement, ""))
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))

gui = threading.get_ident()
destroyed, tracked = [], set()

def track():
    for obj in gc.get_objects():
        if (isinstance(obj, QObject) and id(obj) not in tracked
                and type(obj).__module__.startswith("pyqtgraph")):
            tracked.add(id(obj))
            obj.destroyed.connect(
                lambda *_, name=type(obj).__name__:
                    destroyed.append((name, threading.get_ident() == gui)),
                Qt.ConnectionType.DirectConnection,
            )

window = MainWindow("Osvanta Bus Log Analyzer", "00.00.99")
window.show()
app.processEvents()
for _ in range(2):
    track()
    window.choose_blf()
    track()
    window.load_data()
    while window._thread is not None:
        app.processEvents()
        time.sleep(0.005)
deadline = time.monotonic() + 1
while time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)
print(json.dumps({
    "off_gui_thread": sorted({name for name, on_gui in destroyed if not on_gui}),
}))
"""


@pytest.fixture(scope="module")
def many_group_mf4(tmp_path_factory) -> Path:
    """A pre-decoded MF4 with enough channel groups that reading it allocates
    far more on the load thread than the GUI thread does meanwhile."""
    from asammdf import MDF, Signal

    path = tmp_path_factory.mktemp("gc_guard") / "many_groups.mf4"
    mdf = MDF(version="4.10")
    for group in range(120):
        timestamps = np.arange(300, dtype=np.float64) * 0.01
        mdf.append(
            [
                Signal(np.full(300, group + channel, dtype=np.float32),
                       timestamps, name=f"Sig_{group}_{channel}", unit="V")
                for channel in range(8)
            ],
            acq_name=f"Raster_{group}",
        )
    mdf.save(path, overwrite=True)
    mdf.close()
    return path


def test_reloading_frees_plot_items_only_on_the_gui_thread(many_group_mf4):
    # Every Load + Decode discards the plot's cursor lines. Without the guard,
    # the automatic collection that frees them runs on the load thread, the
    # one allocating heavily, and Qt crashes after a few loads.
    result = _run(_RELOAD_SCRIPT, str(many_group_mf4))

    assert result["off_gui_thread"] == []


_APP_SCRIPT = r"""
import gc, json, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
import app as entry
from gui.gc_guard import GuiThreadGarbageCollector

seen = {}

def fake_exec(self):
    seen["gc_enabled"] = gc.isenabled()
    seen["guards"] = len(self.findChildren(GuiThreadGarbageCollector))
    return 0

QApplication.exec = fake_exec
entry.main()
print(json.dumps(seen))
"""


def test_application_installs_the_guard():
    result = _run(_APP_SCRIPT)

    assert result == {"gc_enabled": False, "guards": 1}


_INSPECTOR_SCRIPT = r"""
import gc, io, json, sys, threading
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
import pyqtgraph as pg

measurement = sys.argv[1]
app = QApplication(sys.argv[:1])
from gui.gc_guard import GuiThreadGarbageCollector
guard = GuiThreadGarbageCollector(app)
from core.debug_inspector import inspect_measurement

gui = threading.get_ident()
freed_on = []
# A discarded cursor line, as every Load + Decode leaves behind: a cycle that
# only a collection can free.
line = pg.InfiniteLine(label="C1")
line.destroyed.connect(
    lambda *_: freed_on.append(threading.get_ident()),
    Qt.ConnectionType.DirectConnection,
)
del line
result = {"freed_before_inspection": bool(freed_on)}

reports = []
inspection = threading.Thread(
    target=lambda: reports.append(
        inspect_measurement(measurement, app_version="test")
    )
)
inspection.start()
inspection.join()
result["corrupt_pointer_reported"] = "EXACT CORRUPT POINTER" in reports[0]
result["freed_during_inspection"] = bool(freed_on)

# The GUI-thread collector's next pass, run now instead of waited for.
sys.stderr = captured = io.StringIO()
gc.collect()
sys.stderr = sys.__stderr__
result["freed_on_gui_thread"] = freed_on == [gui]
result["finaliser_error_printed"] = "MDF4.__del__" in captured.getvalue()
print(json.dumps(result))
"""


@pytest.fixture()
def corrupt_mf4(tmp_path) -> Path:
    """An MF4 whose first data-group link points past the end of the file,
    so asammdf's constructor raises part-way and strands a half-built MDF4."""
    import can

    path = tmp_path / "bad-first-dg.mf4"
    writer = can.MF4Writer(str(path))
    try:
        writer.on_message_received(
            can.Message(timestamp=time.time(), arbitration_id=0x123,
                        data=bytes(range(8)), channel=1)
        )
    finally:
        writer.stop()
    data = bytearray(path.read_bytes())
    data[88:96] = struct.pack("<Q", len(data) + 0x800)
    path.write_bytes(data)
    return path


def test_debug_inspection_leaves_collection_to_the_gui_thread(corrupt_mf4):
    # The app inspects on its debug thread. Collecting the stranded MDF4 there
    # frees every other unreachable cycle too, discarded plot items included.
    result = _run(_INSPECTOR_SCRIPT, str(corrupt_mf4))

    assert result == {
        "freed_before_inspection": False,
        "corrupt_pointer_reported": True,
        "freed_during_inspection": False,
        "freed_on_gui_thread": True,
        "finaliser_error_printed": False,
    }
