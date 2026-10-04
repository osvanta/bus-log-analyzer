# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The user's tag under the signal table: their own text, their user name and
today's date, in any combination, so a screenshot of an analysis says whose
it is and when it was taken.

The tag is the user's own, not the plot's: it is kept in the application's
user settings, never in a plot configuration that colleagues may share.
"""
from __future__ import annotations

import datetime
import getpass
import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

# Between the tag's parts, as between the parts of the cursor readout.
SEPARATOR = '   |   '
MAX_TEXT_LENGTH = 80
_SETTINGS_KEY = 'plot_tag'


def current_user_name() -> str:
    """The name the user signed in to the computer with; '' when unknown."""
    try:
        return getpass.getuser()
    except Exception:
        return ''


@dataclass(frozen=True)
class PlotTag:
    text: str = ''
    user_name: bool = False
    date: bool = False

    def compose(self, user: str, today: datetime.date) -> str:
        """The tag as shown: the text, then the user name, then the date."""
        parts = [self.text.strip()]
        if self.user_name:
            parts.append(user)
        if self.date:
            # Year first: one reading in every country.
            parts.append(today.isoformat())
        return SEPARATOR.join(part for part in parts if part)


def load_plot_tag(path: Path) -> PlotTag:
    """The tag saved in the user settings at *path*; no tag when there is
    none or the file cannot be read."""
    try:
        saved = json.loads(path.read_text(encoding='utf-8')).get(_SETTINGS_KEY)
    except (OSError, ValueError, AttributeError):
        return PlotTag()
    if not isinstance(saved, dict):
        return PlotTag()
    return PlotTag(
        text=str(saved.get('text') or '')[:MAX_TEXT_LENGTH],
        user_name=bool(saved.get('user_name', False)),
        date=bool(saved.get('date', False)),
    )


def save_plot_tag(path: Path, tag: PlotTag) -> None:
    """Save *tag* in the user settings at *path*, keeping the other settings.
    Raises OSError when the file cannot be written."""
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    settings[_SETTINGS_KEY] = {
        'text': tag.text, 'user_name': tag.user_name, 'date': tag.date,
    }
    path.write_text(json.dumps(settings, indent=2), encoding='utf-8')


class PlotTagDialog(QDialog):
    """Edits the tag: free text, and whether the user name and the date follow."""

    def __init__(self, tag: PlotTag, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Tag')
        self._user = current_user_name()

        intro = QLabel(
            'Shown under the signal table while signals are plotted, '
            'so screenshots of your analysis carry it.'
        )
        intro.setWordWrap(True)
        self.text_edit = QLineEdit(tag.text)
        self.text_edit.setMaxLength(MAX_TEXT_LENGTH)
        self.text_edit.setPlaceholderText('Your name, department, company…')
        self.user_check = QCheckBox(
            f'User name ({self._user})' if self._user else 'User name (unknown)')
        self.user_check.setChecked(tag.user_name and bool(self._user))
        self.user_check.setEnabled(bool(self._user))
        self.date_check = QCheckBox(
            f"Today's date ({datetime.date.today().isoformat()})")
        self.date_check.setChecked(tag.date)
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.TextFormat.PlainText)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.text_edit)
        layout.addWidget(self.user_check)
        layout.addWidget(self.date_check)
        layout.addWidget(self.preview)
        layout.addWidget(buttons)

        self.text_edit.textChanged.connect(self._show_preview)
        self.user_check.toggled.connect(self._show_preview)
        self.date_check.toggled.connect(self._show_preview)
        self._show_preview()
        self.resize(max(self.sizeHint().width(), 420), self.sizeHint().height())

    def tag(self) -> PlotTag:
        return PlotTag(
            text=self.text_edit.text().strip(),
            user_name=self.user_check.isChecked(),
            date=self.date_check.isChecked(),
        )

    def _show_preview(self) -> None:
        shown = self.tag().compose(self._user, datetime.date.today())
        self.preview.setText(f'Preview: {shown}' if shown else 'Preview: (no tag)')
