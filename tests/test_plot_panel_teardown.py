# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A plot panel is deleted quietly when the garbage collector frees it.

The panel lies in a reference cycle, so the collector empties it before Qt
deletes it, and the children whose events the panel watches still send it
their last ones while Qt does. Its event filter then read attributes from
an emptied panel and printed a traceback for each event: dozens at the end
of a test run.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# In a process of its own: the test session keeps the collector off once
# a Qt test has run (see _no_gc_during_qt in conftest.py).
#
# The collector runs as in the application: from the GUI thread's timer,
# never on its own. An automatic collection at one point inside pyqtgraph's
# plot menus, while the panel was being built, left the panel in an order
# that the final collection tore down twice, and the process died with
# 0xC0000374 (heap corruption). The application turns automatic collection
# off before it builds a window, so it never collects there.
_SCRIPT = r"""
import gc
from PySide6.QtWidgets import QApplication
app = QApplication([])
from gui.gc_guard import GuiThreadGarbageCollector
gc_guard = GuiThreadGarbageCollector(app)
from gui.plot_widget import PlotPanel

panel = PlotPanel()
panel.resize(900, 500)
panel.show()
app.processEvents()
panel.close()
app.processEvents()
del panel
gc.collect()
app.processEvents()
print('collected')
"""


def test_a_panel_freed_by_the_garbage_collector_is_deleted_quietly():
    completed = subprocess.run(
        [sys.executable, '-c', _SCRIPT],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert completed.stdout.strip() == 'collected'
    assert 'Error calling Python override' not in completed.stderr, completed.stderr[-4000:]
