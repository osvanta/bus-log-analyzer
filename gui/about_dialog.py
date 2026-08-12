# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
About / licence dialog for CAN Scope.

This dialog is not decorative. CAN Scope links against Qt (via PySide6),
python-can, asammdf and chardet, all of which are used under the LGPL. Section
4(c) of the LGPL-3.0 requires that an application which displays copyright
notices while running also shows the notice for the linked libraries, together
with a reference pointing the user at the GPL and LGPL texts. Serving those
texts from here is what satisfies that clause — shipping the files alongside
the executable on its own does not.

The licence files are produced by the build (see ``CANScope.spec``) and land
next to the frozen application; in a source checkout they are read from the
repository root. If they are missing the dialog says so plainly rather than
failing, so a developer running from source without them still gets a usable
window.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

_NOTICES_FILE = 'THIRD_PARTY_NOTICES.md'
_LICENCE_DIR = 'licenses'

# Shown on the About tab. The LGPL notice and the pointer to the licence texts
# are the parts required by LGPL-3.0 section 4(c) — edit with that in mind.
_ABOUT_HTML = """
<h2>{name}</h2>
<p><b>Version {version}</b></p>
<p>Copyright &copy; 2025-2026 Dinakaran Ganesan</p>
<p>CAN Scope is free and open source software, licensed under the
<b>Mozilla Public License, Version 2.0</b>. You may use it freely, including
inside commercial and proprietary work. If you distribute a modified version of
a file that carries the MPL notice, the source of that file must be made
available under the MPL.</p>
<p>Releases up to and including v00.00.59 were published under the MIT License
and remain available under those terms.</p>
<p>This program is distributed in the hope that it will be useful, but
<b>without any warranty</b>; without even the implied warranty of
merchantability or fitness for a particular purpose.</p>
<hr>
<p>CAN Scope uses Qt (through PySide6), python-can, asammdf and chardet under
the <b>GNU Lesser General Public License</b>. These components are unmodified
and remain the copyright of their respective authors.</p>
<p>Copies of the GNU LGPL and the GNU GPL, together with the licence for every
other bundled component, are in the <b>Third-Party Licences</b> tab of this
dialog and in the <code>licenses</code> folder next to the application.</p>
<p>You are entitled to run CAN Scope against your own build of any LGPL
component. The Qt libraries ship as separate DLLs that you may replace with
interface-compatible builds of your own.</p>
"""

_MISSING_TEXT = (
    'Licence files were not found.\n\n'
    'Expected "{notices}" and a "{folder}" directory in:\n    {root}\n\n'
    'A packaged build always contains them. In a source checkout they are '
    'generated as part of the release preparation.\n\n'
    'The full text of every licence used by CAN Scope is also available from '
    'each component\'s own project page, listed in THIRD_PARTY_NOTICES.md.'
)


def _resource_root() -> Path:
    """
    Return the directory holding the bundled licence files.

    * Frozen EXE (PyInstaller):  ``sys._MEIPASS`` — where ``datas`` are unpacked
    * Development / source run:  the project root (parent of ``gui/``)
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str:
    """Read a licence file, degrading to an explanatory message on failure."""
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        return f'Could not read {path.name}:\n\n{exc}'


class AboutDialog(QDialog):
    """Application details plus the third-party licence texts."""

    def __init__(self, app_name: str, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f'About {app_name}')
        self.resize(820, 620)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_about_tab(app_name, version), 'About')
        tabs.addTab(self._build_licences_tab(), 'Third-Party Licences')

        close_btn = QPushButton('Close', self)
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addLayout(button_row)

    def _build_about_tab(self, app_name: str, version: str) -> QWidget:
        label = QLabel(_ABOUT_HTML.format(name=app_name, version=version))
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        label.setOpenExternalLinks(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop)

        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(label)
        layout.addStretch(1)
        return page

    def _build_licences_tab(self) -> QWidget:
        root = _resource_root()
        self._viewer = QPlainTextEdit(self)
        self._viewer.setReadOnly(True)
        self._viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._viewer.setFont(QFont('Consolas', 9))

        self._list = QListWidget(self)
        self._list.currentItemChanged.connect(self._on_selection_changed)

        entries: list[tuple[str, Path]] = []
        notices = root / _NOTICES_FILE
        if notices.is_file():
            entries.append(('Overview — all components', notices))
        licence_dir = root / _LICENCE_DIR
        if licence_dir.is_dir():
            entries.extend(
                (path.stem, path) for path in sorted(licence_dir.glob('*.txt'))
            )

        if not entries:
            self._viewer.setPlainText(
                _MISSING_TEXT.format(notices=_NOTICES_FILE, folder=_LICENCE_DIR, root=root)
            )
            self._list.setEnabled(False)
        else:
            for title, path in entries:
                item = QListWidgetItem(title)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self._list.addItem(item)
            self._list.setCurrentRow(0)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._list)
        splitter.addWidget(self._viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 560])

        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(splitter)
        return page

    def _on_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        self._viewer.setPlainText(_read_text(Path(current.data(Qt.ItemDataRole.UserRole))))
        self._viewer.moveCursor(self._viewer.textCursor().MoveOperation.Start)
