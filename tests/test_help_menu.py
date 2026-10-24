# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for gui/help_menu.py: the top row's Help menu."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from gui.help_menu import (
    DISCUSSIONS_URL, GETTING_STARTED_URL, GUIDE_URL, ISSUES_URL, HelpMenu,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def opened(monkeypatch):
    from PySide6.QtGui import QDesktopServices

    open_url = Mock(return_value=True)
    monkeypatch.setattr(QDesktopServices, 'openUrl', open_url)
    return open_url


@pytest.fixture()
def menu(qapp):
    help_menu = HelpMenu('Osvanta Bus Log Analyzer')
    yield help_menu
    help_menu.deleteLater()
    qapp.processEvents()


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    yield w
    w.deleteLater()
    qapp.processEvents()


def _items(menu):
    return ['---' if action.isSeparator() else action.text() for action in menu.actions()]


def test_the_menu_holds_the_guide_the_ways_to_reach_us_and_about(menu):
    assert _items(menu) == [
        'Getting started',
        'Keyboard shortcuts',
        '---',
        'Ask a question or share an idea',
        'Report a problem or request a feature',
        'Open the log folder',
        'Take the user survey',
        '---',
        'About Osvanta Bus Log Analyzer',
    ]


@pytest.mark.parametrize('action, url', [
    ('guide_action', GETTING_STARTED_URL),
    ('ask_action', DISCUSSIONS_URL),
    ('report_action', ISSUES_URL),
])
def test_the_project_pages_open_in_the_browser(menu, opened, action, url):
    from PySide6.QtCore import QUrl

    getattr(menu, action).trigger()

    opened.assert_called_once_with(QUrl(url))


def test_the_log_folder_opens_where_the_running_log_is(menu, opened, monkeypatch, tmp_path):
    from PySide6.QtCore import QUrl

    from gui import app_log

    # The log fell back to another folder: the menu follows it.
    monkeypatch.setattr(app_log, 'log_path', lambda: tmp_path / app_log.LOG_NAME)

    menu.logs_action.trigger()

    opened.assert_called_once_with(QUrl.fromLocalFile(str(tmp_path)))
    assert 'File Explorer' in menu.logs_action.toolTip()

def test_the_links_lead_to_pages_that_last():
    # A copy of the application keeps its links for as long as it is used:
    # the Discussions front page rather than one discussion, and the issue list.
    assert GETTING_STARTED_URL == 'https://osvanta.github.io/bus-log-analyzer/getting-started/'
    assert DISCUSSIONS_URL == 'https://github.com/osvanta/bus-log-analyzer/discussions'
    assert ISSUES_URL == 'https://github.com/osvanta/bus-log-analyzer/issues'


def test_the_guide_publishes_the_page_the_menu_opens():
    # mkdocs.yml builds guide/ into the site at GUIDE_URL; a page named
    # getting-started.md is published at GUIDE_URL + 'getting-started/'.
    config = (REPO / 'mkdocs.yml').read_text(encoding='utf-8')
    assert f'site_url: {GUIDE_URL}' in config
    assert 'docs_dir: guide' in config
    assert 'getting-started.md' in config
    assert (REPO / 'guide' / 'getting-started.md').is_file()
    assert GETTING_STARTED_URL == GUIDE_URL + 'getting-started/'


@pytest.mark.parametrize('action, signal', [
    ('shortcuts_action', 'shortcutsRequested'),
    ('survey_action', 'surveyRequested'),
    ('about_action', 'aboutRequested'),
])
def test_the_window_shows_the_rest(menu, opened, action, signal):
    requested = []
    getattr(menu, signal).connect(lambda: requested.append(signal))

    getattr(menu, action).trigger()

    assert requested == [signal]
    opened.assert_not_called()


def test_the_links_say_where_they_lead(menu):
    assert menu.toolTipsVisible()
    assert 'web browser' in menu.guide_action.toolTip()
    assert 'GitHub Discussions' in menu.ask_action.toolTip()
    assert 'GitHub' in menu.report_action.toolTip()
    assert 'minutes' in menu.survey_action.toolTip()


def test_help_is_the_top_rows_last_button_and_opens_at_a_click(window):
    from PySide6.QtWidgets import QToolButton

    toolbar = window.title_row.toolbar
    help_action = window.help_menu.menuAction()
    button = toolbar.widgetForAction(help_action)

    assert toolbar.actions()[-1] is help_action
    assert button.text() == 'Help'
    assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    # Shortcuts and About moved into the menu.
    texts = [action.text() for action in toolbar.actions()]
    assert 'Shortcuts' not in texts
    assert 'About' not in texts


def test_the_help_menu_shows_the_shortcuts(window, monkeypatch):
    from PySide6.QtWidgets import QDialog

    shown = []
    monkeypatch.setattr(QDialog, 'exec', lambda dialog: shown.append(dialog.windowTitle()) or 0)

    window.help_menu.shortcuts_action.trigger()

    assert shown == ['Keyboard Shortcuts']


def test_the_help_menu_shows_about(window, monkeypatch):
    from gui.about_dialog import AboutDialog

    shown = []
    monkeypatch.setattr(AboutDialog, 'exec', lambda dialog: shown.append(dialog.windowTitle()) or 0)

    window.help_menu.about_action.trigger()

    assert shown == ['About Osvanta Bus Log Analyzer']


def test_f1_belongs_to_getting_started_and_works_with_the_menu_closed(window):
    from PySide6.QtGui import QKeySequence, QShortcut

    action = window.help_menu.guide_action
    assert action.shortcut() == QKeySequence('F1')
    assert action in window.actions()
    # A second shortcut on the same key would make Qt fire neither.
    assert all(s.key() != QKeySequence('F1') for s in window.findChildren(QShortcut))
    assert 'F1' in dict(window._SHORTCUTS)


def test_f1_opens_getting_started(window, opened):
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtTest import QTest

    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window, 2000)

    QTest.keyClick(window, Qt.Key.Key_F1)

    opened.assert_called_once_with(QUrl(GETTING_STARTED_URL))
    window.close()
