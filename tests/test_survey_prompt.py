# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The survey request: the tenth start asks, once, whether the user will take the
user survey, which opens in the web browser.

"Remind me later" puts a "Help us improve" link in the top row until the user
opens the survey with it; taking the survey or "Don't ask again" shows nothing
more. The About dialog keeps a link to the survey. The count and the answer
live in the user settings beside the application. A busy window,
OSVANTA_SURVEY_PROMPT=0 and a release-test run never show the request.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from gui import survey_prompt
from gui.survey_prompt import (
    Answer,
    SurveyState,
    answered,
    count_start,
    load_survey_state,
    save_survey_state,
)


def _settings(path):
    return json.loads(path.read_text(encoding='utf-8'))


# ── When the request is due ──────────────────────────────────────────────


def test_the_tenth_start_is_the_first_to_ask(tmp_path):
    path = tmp_path / 'osvanta_user_settings.json'

    states = [count_start(path) for _ in range(10)]

    assert [state.due for state in states] == [False] * 9 + [True]
    assert load_survey_state(path).starts == 10


@pytest.mark.parametrize('answer', list(Answer))
def test_the_request_is_asked_only_once(tmp_path, answer):
    path = tmp_path / 'osvanta_user_settings.json'
    save_survey_state(path, answered(SurveyState(starts=10), answer))

    assert not any(count_start(path).due for _ in range(30))


@pytest.mark.parametrize('answer, shown', [
    (Answer.LATER, True),
    (Answer.TAKE, False),
    (Answer.NEVER, False),
])
def test_the_link_shows_only_after_remind_me_later(answer, shown):
    assert not SurveyState(starts=10).link_shown
    assert answered(SurveyState(starts=10), answer).link_shown is shown


def test_opening_the_survey_from_the_link_takes_the_link_away():
    later = answered(SurveyState(starts=10), Answer.LATER)

    assert not answered(later, Answer.TAKE).link_shown


@pytest.mark.parametrize('answer', list(Answer))
def test_the_answer_survives_a_save(tmp_path, answer):
    path = tmp_path / 'osvanta_user_settings.json'
    state = answered(SurveyState(starts=10), answer)

    save_survey_state(path, state)

    assert load_survey_state(path) == state


def test_counting_keeps_the_other_settings(tmp_path):
    path = tmp_path / 'osvanta_user_settings.json'
    path.write_text(json.dumps({'plot_tag': {'text': 'Powertrain'}}), encoding='utf-8')

    count_start(path)

    assert _settings(path)['plot_tag'] == {'text': 'Powertrain'}
    assert _settings(path)['survey']['starts'] == 1


@pytest.mark.parametrize('content', [
    None, '', '{not json', '[]', '{"survey": 5}',
    '{"survey": {"starts": true, "answer": "maybe"}}',
    '{"survey": {"starts": -3, "answer": 7}}',
])
def test_missing_or_unreadable_settings_start_afresh(tmp_path, content):
    path = tmp_path / 'osvanta_user_settings.json'
    if content is not None:
        path.write_text(content, encoding='utf-8')

    assert load_survey_state(path) == SurveyState()


def test_settings_that_cannot_be_written_raise(tmp_path):
    # A folder where the file should be: the caller logs it and asks nothing.
    path = tmp_path / 'osvanta_user_settings.json'
    path.mkdir()

    with pytest.raises(OSError):
        count_start(path)


@pytest.mark.parametrize('variable, argv, expected', [
    (None, ['app.py'], True),
    ('1', ['app.py'], True),
    ('0', ['app.py'], False),
    (None, ['app.py', '--release-test-scenario', 'spec.json'], False),
])
def test_switched_off_by_the_variable_and_in_release_tests(monkeypatch, variable, argv, expected):
    if variable is None:
        monkeypatch.delenv(survey_prompt.DISABLE_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(survey_prompt.DISABLE_VARIABLE, variable)

    assert survey_prompt.enabled(argv) is expected


# ── The dialog and the link ──────────────────────────────────────────────


# exec() is replaced by the user's action: once a test in the session has
# asked the application to quit, Qt returns from every later exec() at once
# and leaves the dialog open, where it would make later windows look busy.


@pytest.mark.parametrize('button, answer', [
    ('take_button', Answer.TAKE),
    ('later_button', Answer.LATER),
    ('never_button', Answer.NEVER),
])
def test_each_button_gives_its_answer(qapp, monkeypatch, button, answer):
    from gui.survey_prompt import SurveyRequestDialog

    def exec_(dialog):
        getattr(dialog, button).click()
        return dialog.result()

    monkeypatch.setattr(SurveyRequestDialog, 'exec', exec_)

    assert SurveyRequestDialog().ask() is answer


def test_closing_the_dialog_means_later(qapp, monkeypatch):
    from gui.survey_prompt import SurveyRequestDialog

    def exec_(dialog):
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(SurveyRequestDialog, 'exec', exec_)

    assert SurveyRequestDialog().ask() is Answer.LATER


def test_taking_the_survey_is_the_default_button(qapp):
    from gui.survey_prompt import SurveyRequestDialog

    dialog = SurveyRequestDialog()

    assert dialog.take_button.isDefault()


def test_the_link_is_written_in_the_link_colour_of_the_theme(qapp):
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication
    from gui.survey_prompt import SurveyLink

    link = SurveyLink()
    assert (link.palette().color(QPalette.ColorRole.ButtonText)
            == QApplication.palette(link).color(QPalette.ColorRole.Link))

    # A switch to the other theme while the application runs.
    original = QApplication.palette()
    switched = QPalette(original)
    switched.setColor(QPalette.ColorRole.Link, QColor('#123456'))
    try:
        QApplication.setPalette(switched)
        qapp.processEvents()
        assert link.palette().color(QPalette.ColorRole.ButtonText) == QColor('#123456')
    finally:
        QApplication.setPalette(original)
        qapp.processEvents()


# ── In the main window ───────────────────────────────────────────────────


@pytest.fixture()
def settings_path(tmp_path, monkeypatch):
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_application_root', staticmethod(lambda: tmp_path))
    monkeypatch.setenv(survey_prompt.DISABLE_VARIABLE, '1')
    monkeypatch.setattr('sys.argv', ['app.py'])
    return tmp_path / 'osvanta_user_settings.json'


@pytest.fixture()
def asked(monkeypatch):
    """Records each request; the user takes the survey."""
    from gui.survey_prompt import SurveyRequestDialog

    ask = Mock(return_value=Answer.TAKE)
    monkeypatch.setattr(SurveyRequestDialog, 'ask', lambda self: ask())
    return ask


@pytest.fixture()
def opened(monkeypatch):
    from PySide6.QtGui import QDesktopServices

    open_url = Mock(return_value=True)
    monkeypatch.setattr(QDesktopServices, 'openUrl', open_url)
    return open_url


def _window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w.resize(1200, 700)
    return w


@pytest.fixture()
def window(qapp, monkeypatch, settings_path):
    w = _window(qapp, monkeypatch)
    w.show()
    # These tests count the start themselves; the window's own count would
    # land two seconds later, in whichever test runs then.
    w._survey_timer.stop()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def test_the_tenth_start_asks_and_opens_the_survey(qapp, window, settings_path, asked, opened):
    from PySide6.QtCore import QUrl

    save_survey_state(settings_path, SurveyState(starts=9))

    window._count_start_and_ask_for_survey()

    asked.assert_called_once()
    opened.assert_called_once_with(QUrl(survey_prompt.SURVEY_URL))
    assert load_survey_state(settings_path) == SurveyState(starts=10, answer='taken')
    assert window.survey_link.isHidden()


def test_an_earlier_start_only_counts(qapp, window, settings_path, asked, opened):
    save_survey_state(settings_path, SurveyState(starts=3))

    window._count_start_and_ask_for_survey()

    asked.assert_not_called()
    opened.assert_not_called()
    assert load_survey_state(settings_path).starts == 4
    assert window.survey_link.isHidden()


def test_remind_me_later_shows_the_link_and_asks_no_more(
        qapp, window, settings_path, asked, opened):
    asked.return_value = Answer.LATER
    save_survey_state(settings_path, SurveyState(starts=9))

    window._count_start_and_ask_for_survey()
    assert not window.survey_link.isHidden()
    for _ in range(30):
        window._count_start_and_ask_for_survey()

    asked.assert_called_once()
    opened.assert_not_called()
    assert load_survey_state(settings_path) == SurveyState(starts=40, answer='later')
    assert not window.survey_link.isHidden()


@pytest.mark.parametrize('answer', [Answer.TAKE, Answer.NEVER])
def test_taking_the_survey_or_declining_shows_nothing_more(
        qapp, window, settings_path, asked, opened, answer):
    asked.return_value = answer
    save_survey_state(settings_path, SurveyState(starts=9))

    for _ in range(30):
        window._count_start_and_ask_for_survey()

    asked.assert_called_once()
    assert window.survey_link.isHidden()


def test_the_link_shows_on_a_start_after_remind_me_later(
        qapp, window, settings_path, asked):
    save_survey_state(settings_path, SurveyState(starts=12, answer='later'))

    window._count_start_and_ask_for_survey()

    asked.assert_not_called()
    assert not window.survey_link.isHidden()


def test_the_link_opens_the_survey_and_goes_away(
        qapp, window, settings_path, asked, opened):
    from PySide6.QtCore import QUrl

    save_survey_state(settings_path, SurveyState(starts=12, answer='later'))
    window._count_start_and_ask_for_survey()

    window.survey_link.click()

    opened.assert_called_once_with(QUrl(survey_prompt.SURVEY_URL))
    assert window.survey_link.isHidden()
    assert load_survey_state(settings_path) == SurveyState(starts=13, answer='taken')


def test_the_link_sits_at_the_right_end_of_the_top_row(qapp, window):
    from gui.window_frame import HTCLIENT

    row = window.title_row
    window.survey_link.show()
    qapp.processEvents()
    link = window.survey_link

    # Before the window buttons, which the offscreen tests leave hidden:
    # see tests/test_window_frame.py.
    assert link.parentWidget() is row
    assert row.toolbar.geometry().right() + 48 < link.x()
    assert row.hit_test(link.geometry().center()) == HTCLIENT


@pytest.mark.parametrize('saved', [
    SurveyState(starts=3),
    SurveyState(starts=12, answer='later'),
    SurveyState(starts=12, answer='taken'),
    SurveyState(starts=12, answer='declined'),
])
def test_the_about_dialog_opens_the_survey_whatever_was_answered(
        qapp, window, settings_path, opened, monkeypatch, saved):
    from PySide6.QtCore import QUrl
    from gui.about_dialog import AboutDialog

    def exec_(dialog):
        dialog.survey_link.linkActivated.emit('survey')
        return 0

    monkeypatch.setattr(AboutDialog, 'exec', exec_)
    save_survey_state(settings_path, saved)
    window._count_start_and_ask_for_survey()

    window.show_about()

    opened.assert_called_once_with(QUrl(survey_prompt.SURVEY_URL))
    assert window.survey_link.isHidden()
    assert load_survey_state(settings_path).answer == 'taken'


def test_a_busy_window_leaves_the_request_for_the_next_start(
        qapp, window, settings_path, asked, monkeypatch):
    monkeypatch.setattr(window, '_running_threads', lambda: [object()])
    save_survey_state(settings_path, SurveyState(starts=9))

    window._count_start_and_ask_for_survey()

    asked.assert_not_called()
    assert load_survey_state(settings_path).due


def test_unwritable_settings_are_logged_and_ask_nothing(qapp, window, settings_path, asked):
    settings_path.mkdir()

    window._count_start_and_ask_for_survey()

    asked.assert_not_called()
    assert 'Survey count warning' in window.log_box.toPlainText()


def test_showing_the_window_asks_once_the_delay_has_passed(
        qapp, settings_path, asked, opened, monkeypatch):
    import time

    monkeypatch.setattr(survey_prompt, 'REQUEST_DELAY_MS', 0)
    save_survey_state(settings_path, SurveyState(starts=9))
    w = _window(qapp, monkeypatch)
    try:
        w.show()
        deadline = time.monotonic() + 10
        while not asked.called and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        # Shown again, the same window counts no second start.
        w.hide()
        w.show()
        for _ in range(20):
            qapp.processEvents()
    finally:
        w.close()
        qapp.processEvents()

    asked.assert_called_once()
    assert load_survey_state(settings_path).starts == 10


@pytest.mark.parametrize('variable, argv', [
    ('0', ['app.py']),
    ('1', ['app.py', '--release-test-scenario', 'spec.json']),
])
def test_switched_off_the_window_neither_counts_nor_asks(
        qapp, settings_path, asked, monkeypatch, variable, argv):
    monkeypatch.setenv(survey_prompt.DISABLE_VARIABLE, variable)
    monkeypatch.setattr('sys.argv', argv)
    save_survey_state(settings_path, SurveyState(starts=12, answer='later'))
    w = _window(qapp, monkeypatch)
    try:
        w.show()
        qapp.processEvents()
        timer_started = w._survey_timer.isActive()
    finally:
        w.close()
        qapp.processEvents()

    assert not timer_started
    asked.assert_not_called()
    assert load_survey_state(settings_path).starts == 12
    assert w.survey_link.isHidden()
