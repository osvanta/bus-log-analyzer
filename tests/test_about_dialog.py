# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for gui/about_dialog.py — the LGPL section 4(c) notice surface.

These assertions are compliance checks, not cosmetic ones. The dialog is what
makes the LGPL notice and the GPL/LGPL texts reachable while the application is
running, so a change that quietly drops them should fail the suite.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gui.about_dialog import AboutDialog, _resource_root

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def dialog(qapp):
    dlg = AboutDialog("Osvanta Bus Log Analyzer", "v00.01.00")
    yield dlg
    dlg.close()
    qapp.processEvents()


def test_resource_root_is_project_root_when_running_from_source():
    assert _resource_root() == PROJECT_ROOT


def test_dialog_has_about_and_licence_tabs(dialog):
    from PySide6.QtWidgets import QTabWidget

    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    assert labels == ["About", "Third-Party Licences"]


def test_about_text_carries_the_lgpl_notice_and_pointer(dialog):
    """LGPL-3.0 4(c) — name the LGPL components and point at the licence copies."""
    from gui.about_dialog import _ABOUT_HTML

    text = _ABOUT_HTML.format(name="Osvanta Bus Log Analyzer", version="v00.01.00")
    assert "Lesser General Public License" in text
    assert "GNU GPL" in text
    for component in ("Qt", "PySide6", "python-can", "asammdf"):
        assert component in text, f"{component} missing from the About notice"
    assert "Mozilla Public License" in text
    assert "without any warranty" in text.lower()


def test_licence_list_is_populated_and_includes_the_gnu_texts(dialog):
    titles = [dialog._list.item(i).text() for i in range(dialog._list.count())]
    assert titles, "no licence entries were loaded"
    assert titles[0].startswith("Overview")
    assert "GNU-LGPL-3.0" in titles
    assert "GNU-GPL-3.0" in titles
    assert "MPL-2.0" in titles


def test_selecting_an_entry_shows_its_text(dialog):
    index = next(
        i for i in range(dialog._list.count())
        if dialog._list.item(i).text() == "GNU-LGPL-3.0"
    )
    dialog._list.setCurrentRow(index)
    shown = dialog._viewer.toPlainText()
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in shown
    assert "0. Additional Definitions." in shown


def test_every_bundled_licence_file_is_readable(dialog):
    """A truncated or unreadable licence file fails the notice requirement."""
    for i in range(dialog._list.count()):
        dialog._list.setCurrentRow(i)
        title = dialog._list.item(i).text()
        assert len(dialog._viewer.toPlainText()) > 100, f"{title} looks empty"


def test_the_survey_link_asks_for_the_survey(dialog):
    """The link stays in the dialog however the survey request was answered;
    the main window opens the survey."""
    requested = []
    dialog.surveyRequested.connect(lambda: requested.append(True))

    assert "user survey" in dialog.survey_link.text()
    dialog.survey_link.linkActivated.emit("survey")

    assert requested == [True]


def test_dialog_survives_missing_licence_files(qapp, monkeypatch, tmp_path):
    """A source checkout without generated licences must still open."""
    monkeypatch.setattr("gui.about_dialog._resource_root", lambda: tmp_path)
    dlg = AboutDialog("Osvanta Bus Log Analyzer", "v00.01.00")
    try:
        assert dlg._list.count() == 0
        assert not dlg._list.isEnabled()
        assert "not found" in dlg._viewer.toPlainText()
    finally:
        dlg.close()
        qapp.processEvents()
