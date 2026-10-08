# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The user's tag under the signal table: their text, their user name and the
date, in any combination, on the cursor line's level so a screenshot of the
analysis carries it.

A double-click on the strip under the table, or its right-click menu, edits
the tag; it is saved in the user settings beside the application, never in a
plot configuration, and comes back at the next start.
"""
from __future__ import annotations

import array
import datetime
import json
from unittest.mock import Mock

import pytest

from gui.plot_tag import PlotTag, load_plot_tag, save_plot_tag

TODAY = datetime.date(2026, 10, 3)


# ── The tag itself ───────────────────────────────────────────────────────


@pytest.mark.parametrize('tag, shown', [
    (PlotTag(), ''),
    (PlotTag(text='  Powertrain  '), 'Powertrain'),
    (PlotTag(user_name=True), 'tester'),
    (PlotTag(date=True), '2026-10-03'),
    (PlotTag(text='D. Ganesan, Powertrain', user_name=True, date=True),
     'D. Ganesan, Powertrain   |   tester   |   2026-10-03'),
    (PlotTag(user_name=True, date=True), 'tester   |   2026-10-03'),
])
def test_tag_reads_text_then_user_name_then_date(tag, shown):
    assert tag.compose('tester', TODAY) == shown


def test_tag_without_a_known_user_name_leaves_it_out():
    assert PlotTag(text='Powertrain', user_name=True).compose('', TODAY) == 'Powertrain'


def test_saved_tag_comes_back_and_other_settings_stay(tmp_path):
    path = tmp_path / 'osvanta_user_settings.json'
    path.write_text(json.dumps({'other': 1}), encoding='utf-8')
    tag = PlotTag(text='Powertrain', user_name=True, date=True)

    save_plot_tag(path, tag)

    assert load_plot_tag(path) == tag
    assert json.loads(path.read_text(encoding='utf-8'))['other'] == 1


@pytest.mark.parametrize('content', [None, '', '{not json', '[]', '{"plot_tag": 5}'])
def test_missing_or_unreadable_settings_give_no_tag(tmp_path, content):
    path = tmp_path / 'osvanta_user_settings.json'
    if content is not None:
        path.write_text(content, encoding='utf-8')

    assert load_plot_tag(path) == PlotTag()


# ── The dialog ───────────────────────────────────────────────────────────


def test_dialog_edits_the_tag_and_previews_it(qapp, monkeypatch):
    from gui import plot_tag
    from gui.plot_tag import PlotTagDialog

    monkeypatch.setattr(plot_tag, 'current_user_name', lambda: 'tester')
    dialog = PlotTagDialog(PlotTag(text='Powertrain'))

    assert dialog.text_edit.text() == 'Powertrain'
    assert 'tester' in dialog.user_check.text()
    assert dialog.preview.text() == 'Preview: Powertrain'

    dialog.user_check.setChecked(True)
    dialog.text_edit.setText(' Chassis ')
    assert dialog.tag() == PlotTag(text='Chassis', user_name=True)
    assert dialog.preview.text() == 'Preview: Chassis   |   tester'


def test_dialog_offers_no_user_name_it_does_not_know(qapp, monkeypatch):
    from gui import plot_tag
    from gui.plot_tag import PlotTagDialog

    monkeypatch.setattr(plot_tag, 'current_user_name', lambda: '')
    dialog = PlotTagDialog(PlotTag(user_name=True))

    assert not dialog.user_check.isEnabled()
    assert not dialog.tag().user_name


# ── In the main window ───────────────────────────────────────────────────


@pytest.fixture()
def settings_dir(tmp_path, monkeypatch):
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_application_root', staticmethod(lambda: tmp_path))
    monkeypatch.setattr('gui.plot_widget.current_user_name', lambda: 'tester')
    monkeypatch.setattr('gui.plot_tag.current_user_name', lambda: 'tester')
    return tmp_path


@pytest.fixture()
def window(qapp, monkeypatch, settings_dir):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def _plot(qapp, window):
    from core.signal_store import SignalSeries

    ts = array.array('d', (i * 0.1 for i in range(100)))
    window.plot_panel.add_series('Speed', SignalSeries(None, 'Msg', 1, 'Speed', 'km/h', ts, ts))
    qapp.processEvents()


def _answer_dialog(monkeypatch, text='', user_name=False, date=False, accept=True):
    from gui.plot_tag import PlotTagDialog

    def exec_(dialog):
        dialog.text_edit.setText(text)
        dialog.user_check.setChecked(user_name)
        dialog.date_check.setChecked(date)
        return int(accept)

    monkeypatch.setattr(PlotTagDialog, 'exec', exec_)


def _double_click_strip(qapp, window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    strip = window.plot_panel._table_bottom_gap
    QTest.mouseDClick(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      strip.rect().center())
    qapp.processEvents()


def _saved(settings_dir):
    return load_plot_tag(settings_dir / 'osvanta_user_settings.json')


def test_no_tag_by_default(qapp, window, settings_dir):
    _plot(qapp, window)

    assert window.plot_panel.tag() == PlotTag()
    assert not window.plot_panel.tag_label.isVisible()
    assert not (settings_dir / 'osvanta_user_settings.json').exists()


def test_a_hint_says_how_to_add_a_tag_while_there_is_none(qapp, window):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor

    panel = window.plot_panel
    hint, readout = panel.tag_hint, panel.cursor_label
    assert not hint.isVisible()                     # nothing plotted

    _plot(qapp, window)
    splitter = window.center_splitter
    assert hint.isVisible()
    # Shortened, like the tag, when the table is too narrow for it.
    assert hint.full_text() == '<double click here to add custom tag>'
    assert hint.text().startswith('<double click')
    assert hint.mapTo(splitter, QPoint(0, 0)).y() == readout.mapTo(splitter, QPoint(0, 0)).y()
    assert hint.font() == readout.font()
    # Dimmer than a tag: between the strip's colour and its text colour.
    background, text, _border = (QColor(c) for c in panel._header_colors())
    shown = hint.palette().color(hint.foregroundRole())
    assert (min(background.lightness(), text.lightness()) < shown.lightness()
            < max(background.lightness(), text.lightness()))

    panel.set_tag(PlotTag(text='Powertrain'))
    qapp.processEvents()
    assert not hint.isVisible()
    assert panel.tag_label.isVisible()

    panel.set_tag(PlotTag())
    qapp.processEvents()
    assert hint.isVisible()

    panel.clear_all()
    qapp.processEvents()
    assert not hint.isVisible()


def test_double_click_under_the_table_sets_the_tag_and_saves_it(
        qapp, window, monkeypatch, settings_dir):
    _plot(qapp, window)
    _answer_dialog(monkeypatch, text='D. Ganesan, Powertrain', user_name=True, date=True)

    _double_click_strip(qapp, window)

    label = window.plot_panel.tag_label
    today = datetime.date.today().isoformat()
    assert label.isVisible()
    # Shortened, if need be, to the table's width.
    assert label.full_text() == f'D. Ganesan, Powertrain   |   tester   |   {today}'
    assert label.text().startswith('D. Ganesan')
    assert _saved(settings_dir) == PlotTag('D. Ganesan, Powertrain', True, True)


def test_cancelled_dialog_changes_nothing(qapp, window, monkeypatch, settings_dir):
    _plot(qapp, window)
    _answer_dialog(monkeypatch, text='Powertrain', accept=False)

    _double_click_strip(qapp, window)

    assert window.plot_panel.tag() == PlotTag()
    assert not (settings_dir / 'osvanta_user_settings.json').exists()


def test_right_click_under_the_table_opens_the_tag_menu(qapp, window, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent

    _plot(qapp, window)
    panel = window.plot_panel
    strip = panel._table_bottom_gap
    shown = Mock()
    monkeypatch.setattr(panel, '_show_tag_menu', shown)   # a modal menu

    at = strip.mapToGlobal(QPoint(5, 5))
    qapp.sendEvent(strip, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), at))

    shown.assert_called_once_with(at)


def test_tag_menu_adds_and_removes_the_tag(qapp, window, monkeypatch, settings_dir):
    _plot(qapp, window)
    panel = window.plot_panel

    def choose(entry):
        actions = panel._tag_menu().actions()
        next(a for a in actions if a.text() == entry).trigger()
        qapp.processEvents()
        return [a.text() for a in actions]

    _answer_dialog(monkeypatch, text='Powertrain')
    assert choose('Add Tag…') == ['Add Tag…']
    assert panel.tag_label.full_text() == 'Powertrain'
    assert panel.tag_label.isVisible()

    assert choose('Remove Tag') == ['Edit Tag…', 'Remove Tag']
    assert panel.tag() == PlotTag()
    assert not panel.tag_label.isVisible()
    assert _saved(settings_dir) == PlotTag()


def test_tag_comes_back_at_the_next_start(qapp, monkeypatch, settings_dir):
    from gui.main_window import MainWindow

    save_plot_tag(settings_dir / 'osvanta_user_settings.json', PlotTag(text='Powertrain'))

    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        assert w.plot_panel.tag() == PlotTag(text='Powertrain')
    finally:
        w.close()
        qapp.processEvents()


def test_tag_sits_on_the_cursor_line_and_only_while_signals_are_plotted(qapp, window):
    from PySide6.QtCore import QPoint

    window.plot_panel.set_tag(PlotTag(text='Powertrain'))
    qapp.processEvents()
    assert not window.plot_panel.tag_label.isVisible()

    _plot(qapp, window)
    splitter = window.center_splitter
    tag, readout = window.plot_panel.tag_label, window.plot_panel.cursor_label

    def top(widget):
        return widget.mapTo(splitter, QPoint(0, 0)).y()

    assert tag.isVisible()
    assert top(tag) == top(readout)
    assert tag.height() == readout.height()
    assert tag.font() == readout.font()
    # In the cursor line's colour, not the plot background's text colour.
    assert tag.palette().color(tag.foregroundRole()) == readout.palette().color(
        readout.foregroundRole())

    window.plot_panel.clear_all()
    qapp.processEvents()
    assert not tag.isVisible()


def test_long_tag_is_shortened_and_does_not_hold_the_table_wide(qapp, window):
    _plot(qapp, window)
    table_side = window.plot_panel.table_panel
    without_tag = table_side.minimumSizeHint().width()

    window.plot_panel.set_tag(PlotTag(text='Powertrain ' * 7 + 'end'))
    qapp.processEvents()

    label = window.plot_panel.tag_label
    assert table_side.minimumSizeHint().width() == without_tag
    assert label.fontMetrics().horizontalAdvance(label.text()) <= label.width()
    assert label.text().startswith('Powertrain')
    assert label.text().endswith('…')


def test_tag_with_the_date_shows_the_new_day_from_midnight(qapp, window, monkeypatch):
    panel = window.plot_panel
    _plot(qapp, window)

    panel.set_tag(PlotTag(text='Powertrain'))
    assert not panel._tag_date_timer.isActive()

    panel.set_tag(PlotTag(text='Powertrain', date=True))
    timer = panel._tag_date_timer
    assert timer.isActive()
    now = datetime.datetime.now()
    to_midnight = datetime.datetime.combine(now.date() + datetime.timedelta(days=1),
                                            datetime.time()) - now
    assert 0 < timer.interval() - to_midnight.total_seconds() * 1000 <= 1500

    class _Tomorrow(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date(2026, 10, 4)

    monkeypatch.setattr('gui.plot_widget.datetime.date', _Tomorrow)
    timer.timeout.emit()
    assert panel.tag_label.full_text() == 'Powertrain   |   2026-10-04'
