# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The top row's Help menu: the user guide, the keyboard shortcuts, the ways to
reach the project, and About.

The user guide is a website built from guide/ in the repository and
published on GitHub Pages. Its Getting started page opens from the menu and
with F1.

The project is reached on GitHub: questions and ideas in its Discussions,
problems and feature requests in its issues. A copy of the application keeps
its links for as long as it is used, so they lead to pages that last: the
guide's Getting started page, which mkdocs.yml says never to move, the
Discussions front page rather than any one discussion, and the issue list.

The About dialog has the Discussions and issue links. The pages open in the
web browser, and only when the user picks one.
"""
from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QMenu, QWidget

GUIDE_URL = 'https://osvanta.github.io/bus-log-analyzer/'
GETTING_STARTED_URL = GUIDE_URL + 'getting-started/'
DISCUSSIONS_URL = 'https://github.com/osvanta/bus-log-analyzer/discussions'
ISSUES_URL = 'https://github.com/osvanta/bus-log-analyzer/issues'

GUIDE_TEXT = 'Getting started'
GUIDE_TIP = 'The user guide, in your web browser (F1)'
ASK_TEXT = 'Ask a question or share an idea'
ASK_TIP = 'How-to questions, ideas and anything else about the tool, on GitHub Discussions'
REPORT_TEXT = 'Report a problem or request a feature'
REPORT_TIP = 'Problems and feature requests, on GitHub. We read every one.'
SURVEY_TEXT = 'Take the user survey'
SURVEY_TIP = '3–4 minutes, in your web browser'


def open_page(url: str) -> None:
    """Open one of the project's pages in the web browser."""
    QDesktopServices.openUrl(QUrl(url))


class HelpMenu(QMenu):
    """Opens the project's pages itself; the window shows the rest."""

    shortcutsRequested = Signal()
    # The main window opens the survey, which records that it was taken: see
    # gui.survey_prompt.
    surveyRequested = Signal()
    aboutRequested = Signal()

    def __init__(self, app_name: str, parent: QWidget | None = None) -> None:
        super().__init__('Help', parent)
        self.setToolTipsVisible(True)
        self.guide_action = self._add(GUIDE_TEXT, lambda: open_page(GETTING_STARTED_URL),
                                      GUIDE_TIP)
        # The window holds it too, so that F1 works while the menu is closed.
        self.guide_action.setShortcut(QKeySequence('F1'))
        self.shortcuts_action = self._add('Keyboard shortcuts', self.shortcutsRequested.emit)
        self.addSeparator()
        self.ask_action = self._add(ASK_TEXT, lambda: open_page(DISCUSSIONS_URL), ASK_TIP)
        self.report_action = self._add(REPORT_TEXT, lambda: open_page(ISSUES_URL), REPORT_TIP)
        self.survey_action = self._add(SURVEY_TEXT, self.surveyRequested.emit, SURVEY_TIP)
        self.addSeparator()
        self.about_action = self._add(f'About {app_name}', self.aboutRequested.emit)

    def _add(self, text: str, slot, tip: str = '') -> QAction:
        action = self.addAction(text)
        if tip:
            action.setToolTip(tip)
        action.triggered.connect(slot)
        return action
