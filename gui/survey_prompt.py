# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The survey request: on the tenth start the application asks, once, whether the
user will spend a few minutes on the user survey, which opens in the web
browser.

"Remind me later" puts a "Help us improve" link at the right end of the
window's top row, where it stays until the user opens the survey with it.
Taking the survey or "Don't ask again" ends it: nothing more is shown. The
About dialog keeps a link to the survey whatever the answer.

Only the number of starts and the answer are kept, in the user settings beside
the application. Nothing is sent anywhere: the browser opens the survey only
when the user asks for it, so the application never learns whether it was
sent.

OSVANTA_SURVEY_PROMPT=0 turns the request and the top row's link off, and a
release-test run neither counts nor asks: its timings and its dialog answering
must not meet a window that only some starts show.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import release_test

SURVEY_URL = (
    'https://forms.office.com/Pages/ResponsePage.aspx?id='
    'DQSIkWdsW0yxEjajBLZtrQAAAAAAAAAAAAO__aPYdXhUQk8wSTBJUEY3WEJXVVA3RFIxSzNONjYzWS4u'
)
REQUEST_AT = 10
# After the window has painted, and before the user is likely to be at work.
REQUEST_DELAY_MS = 2000
DISABLE_VARIABLE = 'OSVANTA_SURVEY_PROMPT'
_SETTINGS_KEY = 'survey'
_LATER = 'later'
_TAKEN = 'taken'
_DECLINED = 'declined'


class Answer(Enum):
    TAKE = 'take'
    LATER = 'later'
    NEVER = 'never'


_SAVED_ANSWERS = {Answer.TAKE: _TAKEN, Answer.LATER: _LATER, Answer.NEVER: _DECLINED}


@dataclass(frozen=True)
class SurveyState:
    starts: int = 0
    # '' until the user answers the request, or opens the survey from the
    # About dialog first.
    answer: str = ''

    @property
    def due(self) -> bool:
        return not self.answer and self.starts >= REQUEST_AT

    @property
    def link_shown(self) -> bool:
        return self.answer == _LATER


def enabled(argv: list[str]) -> bool:
    """Whether this start may count towards the request and show it."""
    return (os.environ.get(DISABLE_VARIABLE, '1') != '0'
            and release_test.SCENARIO_FLAG not in argv)


def load_survey_state(path: Path) -> SurveyState:
    """The survey state saved in the user settings at *path*; a fresh one when
    there is none or the file cannot be read."""
    try:
        saved = json.loads(path.read_text(encoding='utf-8')).get(_SETTINGS_KEY)
    except (OSError, ValueError, AttributeError):
        return SurveyState()
    if not isinstance(saved, dict):
        return SurveyState()
    starts = saved.get('starts')
    answer = saved.get('answer')
    return SurveyState(
        starts=starts if _is_count(starts) else 0,
        answer=answer if answer in _SAVED_ANSWERS.values() else '',
    )


def save_survey_state(path: Path, state: SurveyState) -> None:
    """Save *state* in the user settings at *path*, keeping the other settings.
    Raises OSError when the file cannot be written."""
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    settings[_SETTINGS_KEY] = {'starts': state.starts, 'answer': state.answer}
    path.write_text(json.dumps(settings, indent=2), encoding='utf-8')


def count_start(path: Path) -> SurveyState:
    """Count one start in the user settings at *path* and return the new state.
    Raises OSError when the file cannot be written."""
    state = load_survey_state(path)
    state = replace(state, starts=state.starts + 1)
    save_survey_state(path, state)
    return state


def answered(state: SurveyState, answer: Answer) -> SurveyState:
    """*state* once the user has given *answer*."""
    return replace(state, answer=_SAVED_ANSWERS[answer])


def _is_count(value) -> bool:
    # bool is an int to Python, and true would read as one start.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class SurveyRequestDialog(QDialog):
    """Asks for the survey. Closing the dialog counts as "Remind me later"."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('User survey')
        self._answer = Answer.LATER

        heading = QLabel('Help shape Osvanta Bus Log Analyzer')
        font = heading.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.2)
        heading.setFont(font)
        body = QLabel(
            'Thank you for using Osvanta Bus Log Analyzer! Would you spare 3–4 '
            'minutes for a short survey? Your answers decide which features we '
            'build next.'
        )
        body.setWordWrap(True)
        note = QLabel(
            'The survey opens in your web browser. It is anonymous unless you '
            'choose to leave your email address.'
        )
        note.setWordWrap(True)
        # Dimmed, in the light and the dark theme alike.
        note.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        for label in (heading, body, note):
            label.setTextFormat(Qt.TextFormat.PlainText)

        self.take_button = QPushButton('Take the survey')
        self.later_button = QPushButton('Remind me later')
        self.never_button = QPushButton("Don't ask again")
        self.later_button.setToolTip(
            'A "Help us improve" link at the top of the window opens the survey '
            'whenever you like')
        self.take_button.setDefault(True)
        self.take_button.clicked.connect(lambda: self._finish(Answer.TAKE))
        self.later_button.clicked.connect(lambda: self._finish(Answer.LATER))
        self.never_button.clicked.connect(lambda: self._finish(Answer.NEVER))

        buttons = QHBoxLayout()
        buttons.addWidget(self.never_button)
        buttons.addStretch(1)
        buttons.addWidget(self.later_button)
        buttons.addWidget(self.take_button)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addWidget(note)
        layout.addSpacing(8)
        layout.addLayout(buttons)
        self.resize(max(self.sizeHint().width(), 460), self.sizeHint().height())

    def ask(self) -> Answer:
        """Show the dialog and return the user's answer."""
        self._answer = Answer.LATER
        self.exec()
        return self._answer

    def _finish(self, answer: Answer) -> None:
        self._answer = answer
        self.accept()


class SurveyLink(QToolButton):
    """"Help us improve", in the link colour, for the window's top row."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText('Help us improve')
        self.setToolTip('Take the user survey: 3–4 minutes, in your web browser')
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_link_colour()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # A switch between light and dark mode while the application runs.
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._use_link_colour()

    def _use_link_colour(self) -> None:
        link = QApplication.palette(self).color(QPalette.ColorRole.Link)
        palette = self.palette()
        # Setting the palette is itself a palette change: only when it differs.
        if palette.color(QPalette.ColorRole.ButtonText) != link:
            palette.setColor(QPalette.ColorRole.ButtonText, link)
            self.setPalette(palette)
