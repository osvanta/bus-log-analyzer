# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The plot toolbar: the layout in one picker, the view and measuring buttons,
the display choices and Clear Plot in menus, and Trace, Table and XY in a
glass list that the pointer brings out of the plot's right edge.

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
    assert row._end_buttons == [window.btn_views, window.btn_sync_tabs, window.btn_more]
    assert not row.overflowed_buttons()
    assert all(button.isVisible() for button in row._buttons + row._end_buttons)


def test_what_the_menus_stand_for_is_not_on_the_row(window):
    for button in (window.btn_multi_axis, window.btn_stacked, window.btn_multistack,
                   window.btn_line_mode, window.btn_points, window.btn_hide_line,
                   window.btn_delete_markers, window.btn_clear_plot):
        assert not button.isVisible()
        assert button not in window.plot_button_row._buttons


def test_every_button_is_one_height(window):
    row = window.plot_button_row
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
    assert list(entries) == ['Step lines', 'Straight lines', 'Show data points', 'Hide lines',
                             'Show panel titles']
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


def test_markers_deletes_one_with_its_bin_or_all(window):
    # Each marker's row has a waste bin, as Views ▾ and Go to marker have
    # (feedback item 13): the bin deletes it, the menu stays open on the rest.
    panel = window.plot_panel
    first = panel.add_marker(0.5)
    second = panel.add_marker(1.5)
    menu = window.btn_markers.menu()
    entries = {action.text().split('\t')[0]: action
               for action in menu.actions() if not action.isSeparator()}
    assert 'Delete all markers (2)' in entries
    rows = [action for name, action in entries.items()
            if name.startswith((first.name, second.name))]
    assert menu.bin_rows() == rows
    menu.delete_row(rows[0])
    assert [marker.time for marker in panel.markers()] == [1.5]
    assert entries['Delete all markers (2)'].text() == 'Delete all markers (1)'
    assert rows[0] not in menu.actions() and menu.bin_rows() == [rows[1]]
    menu.delete_row(rows[1])
    assert panel.markers() == []
    assert not entries['Delete all markers (2)'].isEnabled()
    panel.add_marker(2.5)
    _entries(window.btn_markers)['Delete all markers (1)'].trigger()
    assert panel.markers() == []


def test_a_marker_row_goes_to_the_marker(window, monkeypatch):
    panel = window.plot_panel
    marker = panel.add_marker(0.5)
    went = []
    monkeypatch.setattr(panel, 'go_to_marker', went.append)
    row = next(action for name, action in _entries(window.btn_markers).items()
               if name.startswith(marker.name))
    assert row.toolTip() == f'Go to {marker.name}'
    row.trigger()
    assert went == [marker] and panel.markers() == [marker]


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


# ── Views: the panels beside the plot (gui.panel_area) ──────────────────


def test_the_views_are_in_a_menu_not_on_the_row(window):
    for button in (window.btn_table, window.btn_trace):
        assert not button.isVisible()
        assert button not in window.plot_button_row._buttons
    assert not hasattr(window, 'pane_rail') and not hasattr(window, 'btn_xy')
    assert window.btn_views.text() == 'Views ▾'


def test_views_offers_the_table_new_xy_views_and_the_trace(window):
    entries = _entries(window.btn_views)
    assert list(entries) == ['Table', 'Add XY plot', 'Add histogram', 'Add residency map',
                             'Add GPS track', 'CAN Trace', 'Reopen closed panel']
    assert not any(action.isChecked() for action in entries.values())
    assert not entries['CAN Trace'].isEnabled()           # nothing decoded
    assert not entries['Reopen closed panel'].isEnabled()  # nothing closed


def test_views_opens_a_panel_and_ticks_it(window, qapp):
    panel = window.plot_panel
    _entries(window.btn_views)['Add XY plot'].trigger()
    qapp.processEvents()
    assert panel.xy_view_active() and panel.xy_view.isVisible()
    assert list(_entries(window.btn_views))[:3] == ['Table', 'XY plot', 'Add XY plot']
    assert _entries(window.btn_views)['XY plot'].isChecked()
    _entries(window.btn_views)['Table'].trigger()
    qapp.processEvents()
    assert panel.tabular.isVisible() and panel.xy_view.isVisible()   # both at once
    _entries(window.btn_views)['XY plot'].trigger()
    qapp.processEvents()
    assert not panel.xy_view_active() and not panel.xy_view.isVisible()
    assert panel.tabular.isVisible()


def test_a_panel_closed_by_its_cross_comes_back_where_it_was(window, qapp):
    panel = window.plot_panel
    panel.set_xy_view(True)
    window.btn_table.setChecked(True)
    qapp.processEvents()
    width = panel.xy_view.width()
    panel.panels.close_panel('xy', user=True)                 # its ✕
    qapp.processEvents()
    assert not panel.xy_view_active()
    assert not _entries(window.btn_views)['XY plot'].isChecked()
    reopen = _entries(window.btn_views)['Reopen XY plot']
    assert reopen.isEnabled()
    reopen.trigger()
    qapp.processEvents()
    assert panel.xy_view_active() and panel.xy_view.isVisible()
    assert abs(panel.xy_view.width() - width) <= 2


def test_views_trace_follows_its_action(window, monkeypatch):
    action = window._act_can_trace
    action.setEnabled(True)
    action.setToolTip('Open the Trace')
    entry = _entries(window.btn_views)['CAN Trace']
    assert entry.isEnabled() and entry.toolTip() == 'Open the Trace'
    opened = []
    monkeypatch.setattr(window, 'show_raw_frames', lambda *_: opened.append(1))
    entry.trigger()
    assert opened == [1]


def test_trace_is_not_on_the_top_row(window):
    toolbar = window.title_row.toolbar
    assert window._act_can_trace not in toolbar.actions()


def test_display_keeps_panel_titles_in_view_on_request(window, qapp):
    from gui.user_settings import read_setting

    titles = _entries(window.btn_display)['Show panel titles']
    assert titles.isCheckable() and not titles.isChecked()
    titles.trigger()
    assert all(panel.panels.titles_shown() for panel in window.plot_tabs.panels())
    assert read_setting(window._user_settings_path, 'panel_titles_shown') is True
    # A tab added later shows them too.
    window.plot_tabs.add_tab()
    assert window.plot_panel.panels.titles_shown()
    assert _entries(window.btn_display)['Show panel titles'].isChecked()
