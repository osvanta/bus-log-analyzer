# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""pyqtgraph must run on PySide6, never on a PyQt binding.

pyqtgraph picks its Qt binding when first imported and, if none is loaded yet,
tries PyQt6 before PySide6. PyQt6 is GPL; Qt is used here under the LGPL
through PySide6 only. ``gui/__init__.py`` fixes the choice. Each check runs in a
fresh interpreter whose environment asks for PyQt6, so a missing pin shows up
as the wrong binding, or as a failed import where PyQt6 is not installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, '-c', script],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYQTGRAPH_QT_LIB='PyQt6'),
        capture_output=True, text=True, timeout=120,
    )


def test_importing_the_gui_package_pins_pyqtgraph_to_pyside6():
    completed = _run("import os, gui; print(os.environ['PYQTGRAPH_QT_LIB'])")

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert completed.stdout.strip() == 'PySide6'


def test_the_plot_module_runs_pyqtgraph_on_pyside6_with_no_pyqt_loaded():
    # pyqtgraph is imported by gui.plot_widget, so this is the import the
    # application actually makes.
    completed = _run(
        "import sys, gui.plot_widget, pyqtgraph.Qt as q\n"
        "print(q.QT_LIB)\n"
        "print(sorted(m for m in ('PyQt5', 'PyQt6', 'PySide2') if m in sys.modules))\n"
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert completed.stdout.split() == ['PySide6', '[]']


def test_this_test_session_runs_pyqtgraph_on_pyside6():
    import pyqtgraph.Qt

    assert pyqtgraph.Qt.QT_LIB == 'PySide6'
