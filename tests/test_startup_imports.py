# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Start-up must not wait for what only Open File and Load + Decode need.

cantools, python-can and asammdf (with pandas and canmatrix behind it) took
over a second to import, all before the main window could appear. They are
imported in the background once the window is up instead. Each check runs in
a fresh interpreter, since the test session has imported them long ago.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_LIBRARIES = ('asammdf', 'can', 'cantools', 'canmatrix', 'pandas')

_SCRIPT = r"""
import sys, time
from PySide6.QtWidgets import QApplication
app = QApplication([])
from gui.main_window import MainWindow
window = MainWindow('Osvanta Bus Log Analyzer', 'v00.00.99')
heavy = %r
print('built:', sorted(name for name in heavy if name in sys.modules))
window.show()
import threading
def preload():
    return next((t for t in threading.enumerate() if t.name == 'Measurement support preload'), None)
deadline = time.monotonic() + 60
while preload() is None and 'core.load_worker' not in sys.modules and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)
thread = preload()
if thread is not None:
    thread.join(60)
print('shown:', sorted(name for name in heavy if name in sys.modules))
window.close()
"""


def test_the_window_is_built_first_and_the_measurement_libraries_follow():
    completed = subprocess.run(
        [sys.executable, '-c', _SCRIPT % (MEASUREMENT_LIBRARIES,)],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=180,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    lines = dict(line.split(': ', 1) for line in completed.stdout.splitlines() if ': ' in line)
    assert lines['built'] == '[]'
    assert lines['shown'] == str(sorted(MEASUREMENT_LIBRARIES))


_OPEN_FILE_SCRIPT = r"""
import sys
from PySide6.QtWidgets import QApplication, QFileDialog
app = QApplication([])
from gui.main_window import MainWindow
window = MainWindow('Osvanta Bus Log Analyzer', 'v00.00.99')
def dialog(*args, **kwargs):
    print('core.readers loaded when the dialog opened:', 'core.readers' in sys.modules)
    return '', ''
QFileDialog.getOpenFileName = staticmethod(dialog)
window._toolbar_actions['Open File'].trigger()
"""


def test_open_file_shows_its_dialog_before_loading_the_readers():
    # Clicked while the background preload still ran, Open File first waited
    # for it to finish: the dialog took two to three seconds to appear.
    completed = subprocess.run(
        [sys.executable, '-c', _OPEN_FILE_SCRIPT],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=180,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert 'core.readers loaded when the dialog opened: False' in completed.stdout


def test_the_open_file_dialog_offers_every_readable_format():
    from core.readers import ALL_SUFFIXES
    from gui.main_window import _MEASUREMENT_SUFFIXES

    assert list(_MEASUREMENT_SUFFIXES) == sorted(ALL_SUFFIXES)
