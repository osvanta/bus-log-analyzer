# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The top row's Help menu: the user guide, the tutorial videos, the keyboard
shortcuts, the ways to reach the project, and About.

The user guide is a website built from guide/ in the repository and
published on GitHub Pages. Its Getting started page opens from the menu and
with F1. The tutorial videos are on the project's YouTube channel.

The project is reached on GitHub: questions and ideas in its Discussions,
problems and feature requests in its issues. A copy of the application keeps
its links for as long as it is used, so they lead to pages that last: the
guide's Getting started page, which mkdocs.yml says never to move, the
channel rather than any one video, the Discussions front page rather than any
one discussion, and the issue list.

The About dialog has the video, Discussions and issue links. The pages open
in the web browser, and only when the user picks one.

The logs a problem report needs are in the user's own folder, which Explorer
hides (gui/app_data.py), so the menu opens it.

An entry that opens a web page ends in an arrow, so it is plain which entries
leave the application: where browsing is blocked, those are the ones that do
nothing. The keyboard shortcuts open with Ctrl+/, as the guide does with F1.
"""
from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QMenu, QWidget

from gui import app_data, app_log

GUIDE_URL = 'https://osvanta.github.io/bus-log-analyzer/'
GETTING_STARTED_URL = GUIDE_URL + 'getting-started/'
VIDEOS_URL = 'https://www.youtube.com/@osvanta'
DISCUSSIONS_URL = 'https://github.com/osvanta/bus-log-analyzer/discussions'
ISSUES_URL = 'https://github.com/osvanta/bus-log-analyzer/issues'

# After the name of each entry that opens a page in the web browser.
WEB_MARK = '↗'
GUIDE_KEY = 'F1'
SHORTCUTS_KEY = 'Ctrl+/'

GUIDE_TEXT = 'Getting started'
GUIDE_TIP = 'The user guide, in your web browser (F1)'
VIDEOS_TEXT = 'Tutorial videos'
VIDEOS_TIP = 'The tutorial videos on YouTube, in your web browser'
ASK_TEXT = 'Ask a question or share an idea'
ASK_TIP = 'How-to questions, ideas and anything else about the tool, on GitHub Discussions'
REPORT_TEXT = 'Report a problem or request a feature'
REPORT_TIP = 'Problems and feature requests, on GitHub. We read every one.'
LOGS_TEXT = 'Open the log folder'
LOGS_TIP = 'The two logs to attach to a problem report, in File Explorer'
SURVEY_TEXT = 'Take the user survey'
SURVEY_TIP = '3–4 minutes, in your web browser'


def open_page(url: str) -> None:
    """Open one of the project's pages in the web browser."""
    QDesktopServices.openUrl(QUrl(url))


def open_log_folder() -> None:
    """Open the running log's folder: the temp folder when it fell back there."""
    running = app_log.log_path()
    folder = running.parent if running is not None else app_data.logs_folder()
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


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
                                      GUIDE_TIP, web=True)
        # The window holds the two keys too, so that they work while the menu
        # is closed.
        self.guide_action.setShortcut(QKeySequence(GUIDE_KEY))
        self.videos_action = self._add(VIDEOS_TEXT, lambda: open_page(VIDEOS_URL), VIDEOS_TIP,
                                       web=True)
        self.shortcuts_action = self._add('Keyboard shortcuts', self.shortcutsRequested.emit)
        self.shortcuts_action.setShortcut(QKeySequence(SHORTCUTS_KEY))
        self.addSeparator()
        self.ask_action = self._add(ASK_TEXT, lambda: open_page(DISCUSSIONS_URL), ASK_TIP,
                                    web=True)
        self.report_action = self._add(REPORT_TEXT, lambda: open_page(ISSUES_URL), REPORT_TIP,
                                       web=True)
        self.logs_action = self._add(LOGS_TEXT, open_log_folder, LOGS_TIP)
        # The window opens the survey in the web browser.
        self.survey_action = self._add(SURVEY_TEXT, self.surveyRequested.emit, SURVEY_TIP,
                                       web=True)
        self.addSeparator()
        self.about_action = self._add(f'About {app_name}', self.aboutRequested.emit)

    def _add(self, text: str, slot, tip: str = '', web: bool = False) -> QAction:
        action = self.addAction(f'{text}  {WEB_MARK}' if web else text)
        if tip:
            action.setToolTip(tip)
        action.triggered.connect(slot)
        return action
