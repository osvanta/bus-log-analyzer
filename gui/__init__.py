# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""GUI widgets for Osvanta Bus Log Analyzer."""

import os

# pyqtgraph picks its Qt binding when first imported and, if none is loaded
# yet, tries PyQt6 before PySide6. PyQt6 is GPL; this application uses Qt
# under the LGPL through PySide6 only. Fixed here, before any module in this
# package can import pyqtgraph, and assigned rather than defaulted so that an
# environment naming another binding cannot win.
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
