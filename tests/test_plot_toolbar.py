# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The plot toolbar: the layout in one picker, the view and measuring buttons,
the display choices and Clear Plot in menus, and Table and XY on a rail at
the plot's right edge.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest


def _series(name, ts, vs):
    from core.signal_store import SignalSeries
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit='u',
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float),
    )


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    panel = w.plot_panel
    panel.add_series('Speed', _series('Speed', [0.0, 1.0, 2.0], [10, 20, 30]))
    panel.add_series('Temp', _series('Temp', [0.5, 1.5], [5, 6]))
    panel._rebuild_curves(preserve_selection=True)
    w.resize(1700, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def _entries(button):
    """The menu's entries by their text, without the key named after a tab."""
    return {action.text().split('\t')[0]: action
            for action in button.menu().actions() if not action.isSeparator()}


# ── What is on the row ───────────────────────────────────────────────────


def test_the_row_is_layout_then_view_then_measuring_then_display(window):
    row = window.plot_button_row
    assert row._buttons == [
        window.btn_layout,
        window.btn_fit, window.btn_fit_v, window.btn_zoom_rect,
        window.btn_cursor1, window.btn_cursor2, window.btn_markers,
        window.btn_display,
    ]
    assert row._end_buttons == [window.btn_sync_tabs, window.btn_more, window.btn_show_rail]
    assert not row.overflowed_buttons()
    # All but the one that brings a hidden rail back.
    assert all(button.isVisible() for button in row._buttons + row._end_buttons[:2])


def test_what_the_menus_stand_for_is_not_on_the_row(window):
    for button in (window.btn_multi_axis, window.btn_stacked, window.btn_multistack,
                   window.btn_line_mode, window.btn_points, window.btn_hide_line,
                   window.btn_delete_markers, window.btn_clear_plot):
        assert not button.isVisible()
        assert button not in window.plot_button_row._buttons


def test_every_button_is_one_height(window):
    row = window.plot_button_row
    window.pane_rail.hide_button.click()             # so that « is laid out too
    assert len({button.height() for button in row._buttons + row._end_buttons}) == 1


# ── The layout picker ────────────────────────────────────────────────────


def test_the_picker_names_the_layout_and_offers_all_four(window):
    assert window.btn_layout.text() == 'Stacked ▾'
    entries = _entries(window.btn_layout)
    assert list(entries) == ['Single plot', 'Multi-Axis', 'Stacked', 'MultiStack']
    assert [name for name, action in entries.items() if action.isChecked()] == ['Stacked']


@pytest.mark.parametrize('name, mode', [
    ('Single plot', 'normal'), ('Multi-Axis', 'multi_axis'),
    ('MultiStack', 'multistack'), ('Stacked', 'stacked')])
def test_choosing_a_layout_sets_it(window, name, mode):
    from gui.plot_tabs import plot_type
    if mode == 'stacked':
        _entries(window.btn_layout)['Multi-Axis'].trigger()
    _entries(window.btn_layout)[name].trigger()
    assert plot_type(window.plot_panel) == mode
    assert window.btn_layout.text() == f'{name} ▾'
    assert [n for n, a in _entries(window.btn_layout).items() if a.isChecked()] == [name]


def test_the_picker_follows_the_tab_on_screen(window):
    _entries(window.btn_layout)['Multi-Axis'].trigger()
    window.plot_tabs.add_tab()
    assert window.btn_layout.text() == 'Stacked ▾'          # a new tab's layout
    window.plot_tabs.bar.setCurrentIndex(0)
    assert window.btn_layout.text() == 'Multi-Axis ▾'


def test_a_configurations_layout_shows_on_the_picker(window):
    window._apply_tab_settings({'plot_type': 'multistack'})
    assert window.btn_layout.text() == 'MultiStack ▾'


# ── Display ──────────────────────────────────────────────────────────────


def test_display_switches_between_step_and_straight_lines(window):
    panel = window.plot_panel
    entries = _entries(window.btn_display)
    assert list(entries) == ['Step lines', 'Straight lines', 'Show data points', 'Hide lines']
    assert entries['Step lines'].isChecked() and not entries['Straight lines'].isChecked()
    entries['Straight lines'].trigger()
    assert not panel.step_lines()
    entries = _entries(window.btn_display)
    assert entries['Straight lines'].isChecked() and not entries['Step lines'].isChecked()
    entries['Step lines'].trigger()
    assert panel.step_lines()


def test_display_shows_data_points_and_only_then_hides_the_lines(window):
    panel = window.plot_panel
    entries = _entries(window.btn_display)
    assert not entries['Show data points'].isChecked()
    assert not entries['Hide lines'].isEnabled()
    entries['Show data points'].trigger()
    assert panel._show_points
    entries = _entries(window.btn_display)
    assert entries['Show data points'].isChecked() and entries['Hide lines'].isEnabled()
    entries['Hide lines'].trigger()
    assert panel._hide_lines
    assert _entries(window.btn_display)['Hide lines'].isChecked()
    _entries(window.btn_display)['Show data points'].trigger()
    assert not panel._show_points and not panel._hide_lines


# ── Markers ──────────────────────────────────────────────────────────────


def test_markers_adds_one_at_cursor_1_once_the_cursor_is_on(window):
    panel = window.plot_panel
    entries = _entries(window.btn_markers)
    assert not entries['Add marker at Cursor 1'].isEnabled()
    assert not entries['Delete markers'].isEnabled()
    window.btn_cursor1.setChecked(True)
    panel.move_cursor1(1.25)
    _entries(window.btn_markers)['Add marker at Cursor 1'].trigger()
    assert [marker.time for marker in panel.markers()] == [pytest.approx(1.25)]


def test_markers_deletes_one_or_all(window):
    panel = window.plot_panel
    first = panel.add_marker(0.5)
    panel.add_marker(1.5)
    entries = _entries(window.btn_markers)
    assert 'Delete all markers (2)' in entries
    next(action for name, action in entries.items()
         if name.startswith(first.name)).trigger()
    assert [marker.time for marker in panel.markers()] == [1.5]
    _entries(window.btn_markers)['Delete all markers (1)'].trigger()
    assert panel.markers() == []


# ── More ─────────────────────────────────────────────────────────────────


def test_more_clears_the_plot_when_there_is_one_to_clear(window):
    panel = window.plot_panel
    assert not _entries(window.btn_more)['Clear Plot'].isEnabled()   # nothing decoded
    window.btn_clear_plot.setEnabled(True)
    clear = _entries(window.btn_more)['Clear Plot']
    assert clear.isEnabled()
    clear.trigger()
    assert panel.plotted_keys() == []


# ── The pane rail ────────────────────────────────────────────────────────


def test_trace_table_and_xy_are_on_a_rail_right_of_the_plot(window, qapp):
    from PySide6.QtCore import QPoint
    rail = window.pane_rail
    assert rail.buttons() == [window.btn_trace, window.btn_table, window.btn_xy]
    assert [button.text() for button in rail.buttons()] == ['Trace', 'Table', 'XY']
    assert all(button.isVisible() for button in rail.buttons())
    assert window.btn_trace.y() < window.btn_table.y()
    assert window.btn_table not in window.plot_button_row._buttons
    assert window.btn_xy not in window.plot_button_row._buttons
    plots = window.plot_tabs.plots
    left = rail.mapTo(window, QPoint(0, 0)).x()
    assert left >= plots.mapTo(window, QPoint(plots.width(), 0)).x()
    # Under the button row, one under the other, one width.
    assert rail.mapTo(window, QPoint(0, 0)).y() >= window.plot_button_row.mapTo(
        window, QPoint(0, window.plot_button_row.height())).y()
    assert window.btn_xy.y() > window.btn_table.y()
    assert window.btn_xy.width() == window.btn_table.width()
    # A narrow strip: the widest text and little more.
    assert window.btn_table.width() == (
        window.btn_table.fontMetrics().horizontalAdvance('Table') + 22)


def test_the_rail_buttons_open_their_panes(window, qapp):
    panel = window.plot_panel
    window.btn_table.click()
    window.btn_xy.click()
    qapp.processEvents()
    assert panel.table_view_active() and panel.xy_view_active()
    assert panel.tabular.isVisible() and panel.xy_view.isVisible()
    window.btn_table.click()
    window.btn_xy.click()
    assert not panel.table_view_active() and not panel.xy_view_active()


# ── The menu button itself ───────────────────────────────────────────────


def test_a_menu_is_filled_anew_each_time_and_not_kept(qapp):
    from gui.plot_toolbar import MenuButton
    filled = []
    button = MenuButton(lambda menu: (filled.append(1), menu.addAction(f'Entry {len(filled)}')))
    assert [a.text() for a in button.menu().actions()] == ['Entry 1']
    assert [a.text() for a in button.menu().actions()] == ['Entry 2']


def test_a_click_opens_the_menu_under_the_button(qapp, monkeypatch):
    from PySide6.QtCore import QPoint
    from gui.plot_toolbar import MenuButton

    class _Menu:
        opened_at = []
        deleted = 0

        def exec(self, at):
            self.opened_at.append(at)

        def deleteLater(self):
            _Menu.deleted += 1

    button = MenuButton(lambda menu: None)
    monkeypatch.setattr(button, 'menu', _Menu)
    button.resize(80, 30)
    button.show()
    qapp.processEvents()
    button.click()
    assert _Menu.opened_at == [button.mapToGlobal(QPoint(0, 30))]
    assert _Menu.deleted == 1          # not kept once closed
    button.close()


# ── Hiding the rail ──────────────────────────────────────────────────────


def test_the_rail_is_shown_at_first_under_its_hide_button(window):
    rail = window.pane_rail
    assert rail.isVisible() and rail.hide_button.isVisible()
    assert rail.hide_button.text() == '»'
    assert rail.hide_button.y() < window.btn_trace.y()
    assert not window.btn_show_rail.isVisible()


def test_hiding_the_rail_gives_the_plot_its_room_and_offers_it_back(window, qapp):
    from PySide6.QtCore import QPoint
    rail, plots, row = window.pane_rail, window.plot_tabs.plots, window.plot_button_row
    width = plots.width()
    rail.hide_button.click()
    qapp.processEvents()
    assert not rail.isVisible()
    assert plots.width() > width
    show = window.btn_show_rail
    assert show.isVisible() and show.text() == '«'
    # At the right end of the plot buttons, after More.
    assert row._end_buttons[-1] is show
    assert show.x() > window.btn_more.x()
    assert show.size() == window.btn_more.size()      # one more square button
    assert show.mapTo(window, QPoint(show.width(), 0)).x() <= row.mapTo(
        window, QPoint(row.width(), 0)).x()

    show.click()
    qapp.processEvents()
    assert rail.isVisible() and not show.isVisible()
    assert plots.width() == width


def test_panes_stay_open_while_the_rail_is_hidden(window, qapp):
    panel = window.plot_panel
    window.btn_xy.click()
    window.pane_rail.hide_button.click()
    qapp.processEvents()
    assert panel.xy_view_active() and panel.xy_view.isVisible()


def test_a_hidden_rail_is_hidden_at_the_next_start(window, qapp, tmp_path):
    from gui.main_window import MainWindow
    from gui.user_settings import read_setting
    settings = tmp_path / 'osvanta_user_settings.json'
    assert read_setting(settings, 'pane_rail_shown') is None      # not written at start
    window.pane_rail.hide_button.click()
    assert read_setting(settings, 'pane_rail_shown') is False

    again = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    again.show()
    qapp.processEvents()
    assert not again.pane_rail.isVisible() and again.btn_show_rail.isVisible()
    again.btn_show_rail.click()
    assert read_setting(settings, 'pane_rail_shown') is True
    again.close()


def test_a_hidden_show_button_takes_no_room_on_the_row(window, qapp):
    row = window.plot_button_row
    shown = row._end_width()
    window.pane_rail.hide_button.click()
    qapp.processEvents()
    assert row._end_width() == shown + window.btn_show_rail.sizeHint().width() + row.SPACING


# ── Trace on the rail ────────────────────────────────────────────────────


def test_the_trace_button_follows_its_action_and_opens_the_trace(window, monkeypatch):
    button, action = window.btn_trace, window._act_can_trace
    assert not button.isEnabled()                    # nothing decoded
    action.setEnabled(True)
    action.setToolTip('Open the Trace')
    assert button.isEnabled() and button.toolTip() == 'Open the Trace'
    opened = []
    action.triggered.disconnect()
    action.triggered.connect(lambda *_: opened.append(1))
    button.click()
    assert opened == [1]
    action.setEnabled(False)
    assert not button.isEnabled()


def test_trace_is_not_on_the_top_row(window):
    toolbar = window.title_row.toolbar
    assert window._act_can_trace not in toolbar.actions()
