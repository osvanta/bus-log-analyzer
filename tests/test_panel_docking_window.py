# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The panels of a plot tab in the window: the time plot's name, the XY plot,
the Table and the CAN Trace as panels beside the plot, kept in
configurations, and the keys that maximise, close and reopen them.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest

_TIMES = [0.0, 1.0, 2.0, 3.0, 4.0]


def _series(name, ts, vs):
    from core.signal_store import SignalSeries
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit='u',
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float),
    )


def _frames(count: int = 500):
    from core.raw_frame_store import RawFrameStore
    frames = RawFrameStore()
    frames.append_numpy_batch(
        np.arange(count) * 0.01, np.ones(count, dtype=np.uint8),
        np.full(count, 0x123, dtype=np.uint32), np.full(count, 8, dtype=np.uint8),
        np.zeros(count, dtype=np.uint8), np.zeros(count, dtype=np.uint8),
        np.zeros((count, 8), dtype=np.uint8))
    frames.seal()
    return frames


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    panel = w.plot_panel
    panel.add_series('Speed', _series('Speed', _TIMES, [10, 20, 30, 40, 50]))
    panel.add_series('Torque', _series('Torque', _TIMES, [1, 2, 3, 4, 5]))
    panel._rebuild_curves(preserve_selection=True)
    w.store = SimpleNamespace(raw_frame_store=_frames())
    w._update_action_states()
    w.resize(1500, 850)
    w.show()
    qapp.processEvents()
    yield w
    for each in w.plot_tabs.panels():
        each.panels.dock_all()
    w._close_trace_window()
    w.store = None
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _settle(qapp):
    from PySide6.QtCore import QCoreApplication, QEvent
    for _ in range(3):
        qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# ── The time plot's name ─────────────────────────────────────────────────


def test_the_signal_tables_name_column_is_headed_by_the_plots_name(window):
    from gui.plot_widget import PLOT_NAME

    panel = window.plot_panel
    assert panel.table.horizontalHeaderItem(1).text() == PLOT_NAME == panel.plot_name()
    panel.set_plot_name('  Drive   cycle  ')
    assert panel.table.horizontalHeaderItem(1).text() == 'Drive cycle'
    assert panel.panels.title('plot') == 'Drive cycle'
    panel.set_plot_name('')                       # an empty name falls back
    assert panel.plot_name() == PLOT_NAME


def test_renaming_the_plot_in_place(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from gui.tab_drawing import _NameEditor

    panel = window.plot_panel
    named = []
    panel.plotNameChanged.connect(named.append)
    panel.rename_plot()
    editor = panel.table.horizontalHeader().findChild(_NameEditor)
    assert editor is not None and editor.text() == panel.plot_name()
    editor.setText('Thermal')
    QTest.keyClick(editor, Qt.Key.Key_Return)
    _settle(qapp)
    assert panel.plot_name() == 'Thermal' and named == ['Thermal']


# ── Panels kept in a configuration ───────────────────────────────────────


def test_a_tab_is_described_with_its_panels_and_its_plots_name(window, qapp):
    from gui.plot_tabs import describe_tab
    from gui.panel_area import layout_keys

    panel = window.plot_panel
    panel.set_plot_name('Drive')
    window.plot_panel.set_xy_view(True)
    window.btn_table.setChecked(True)
    _settle(qapp)
    saved = describe_tab(panel, 'Tab 1')
    assert saved['plot_name'] == 'Drive'
    assert sorted(layout_keys(saved['panels'])) == ['plot', 'table', 'xy']
    assert [(view['key'], view['name'], view['view']) for view in saved['xy_views']] == [
        ('xy', 'XY plot', 'scatter')]


def test_a_configurations_panels_come_back_in_their_places(window, qapp):
    from PySide6.QtCore import QPoint
    from gui.plot_tabs import describe_tab

    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    window.btn_table.setChecked(True)
    _settle(qapp)
    # The table below the plot, rather than where it opened.
    area = panel.panels
    area.close_panel('table')
    area.panel('table').home = {'beside': 'plot', 'side': 'bottom', 'share': 0.3}
    area.open_panel('table')
    _settle(qapp)
    saved = describe_tab(panel, 'Tab 1')

    window.plot_tabs.add_tab()
    other = window.plot_panel
    window._apply_tab_settings(saved)
    _settle(qapp)
    assert other.xy_view_active() and other.table_view_active()
    assert other.panels.is_open('xy') and window.btn_table.isChecked()
    plot = other.view_stack.mapTo(other, QPoint(0, 0))
    table = other.tabular.mapTo(other, QPoint(0, 0))
    xy = other.xy_view.mapTo(other, QPoint(0, 0))
    assert table.y() > plot.y() and table.x() == plot.x()     # below the plot
    assert xy.x() > plot.x()                                   # right of it


def test_a_configuration_from_before_panels_opens_its_xy_plot_on_the_right(window, qapp):
    from PySide6.QtCore import QPoint

    window.plot_tabs.add_tab()
    other = window.plot_panel
    window._apply_tab_settings({'plot_type': 'stacked', 'xy': {'shown': True, 'x': 'Speed'}})
    _settle(qapp)
    assert other.xy_view_active() and other.xy_view.isVisible()
    assert other.xy_view.mapTo(other, QPoint(0, 0)).x() > other.view_stack.mapTo(
        other, QPoint(0, 0)).x()
    assert not other.table_view_active()


def test_closing_a_panel_by_its_cross_unticks_its_button(window, qapp):
    panel = window.plot_panel
    window.btn_table.setChecked(True)
    _settle(qapp)
    panel.panels.close_panel('table', user=True)
    assert not panel.table_view_active() and not window.btn_table.isChecked()


# ── The CAN Trace as a panel ─────────────────────────────────────────────


def test_the_trace_opens_as_a_panel_of_the_tab_on_screen(window, qapp):
    area = window.plot_panel.panels
    window.show_raw_frames()
    _settle(qapp)
    dialog = window._raw_frame_dialog
    assert area.is_open('trace') and not dialog.isWindow() and dialog.isVisible()
    assert dialog.window() is window
    # Its header band across the top of its card: name, search and the rest.
    from gui.panel_area import HeaderBand
    assert isinstance(dialog.header, HeaderBand)
    assert dialog.name_label.parentWidget() is dialog.header
    assert dialog.search_edit.parentWidget() is dialog.header
    assert dialog.header.geometry().top() == 0
    assert dialog.header.width() == dialog.width()
    entries = {a.text().split('\t')[0]: a for a in window.btn_views.menu().actions()}
    assert entries['CAN Trace'].isChecked()
    window.show_raw_frames()                      # again: still the one Trace
    assert window._raw_frame_dialog is dialog


def test_escape_does_not_close_the_trace_panel(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.show_raw_frames()
    _settle(qapp)
    dialog = window._raw_frame_dialog
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    _settle(qapp)
    assert dialog.isVisible() and window.plot_panel.panels.is_open('trace')


def test_a_closed_trace_opens_again_as_it_was(window, qapp):
    area = window.plot_panel.panels
    window.show_raw_frames()
    dialog = window._raw_frame_dialog
    dialog.search_edit.setText('0x123')
    area.close_panel('trace', user=True)
    _settle(qapp)
    assert not area.is_open('trace') and window._raw_frame_dialog is dialog
    window._reopen_panel()                        # Ctrl+Shift+T
    _settle(qapp)
    assert area.is_open('trace') and dialog.search_edit.text() == '0x123'


def test_the_trace_moves_to_the_tab_it_is_opened_in(window, qapp):
    first = window.plot_panel
    window.show_raw_frames()
    dialog = window._raw_frame_dialog
    window.plot_tabs.add_tab()
    second = window.plot_panel
    window.show_raw_frames()
    _settle(qapp)
    assert first.panels.panel('trace') is None
    assert second.panels.is_open('trace') and window._raw_frame_dialog is dialog


def test_closing_the_tab_closes_its_trace(window, qapp):
    from gui.raw_frame_dialog import RawFrameDialog

    window.plot_tabs.add_tab()
    window.show_raw_frames()
    window.plot_panel.clear_all()                 # nothing to ask about
    window.plot_tabs.close_tab(1)
    _settle(qapp)
    assert window._raw_frame_dialog is None
    assert window.findChildren(RawFrameDialog) == []


def test_a_configurations_trace_opens_where_it_was_once_decoded(window, qapp):
    from gui.plot_tabs import SavedTabs, describe_tab

    window.show_raw_frames()
    _settle(qapp)
    saved = describe_tab(window.plot_panel, 'Tab 1')
    window._close_trace_window()
    _settle(qapp)
    tabs = SavedTabs.from_config({'tabs': [saved]})
    window._apply_tab_settings(saved)
    window._open_saved_trace(tabs)
    _settle(qapp)
    assert window.plot_panel.panels.is_open('trace')


# ── Keys ─────────────────────────────────────────────────────────────────


def test_escape_gives_a_maximised_panels_room_back(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    area = window.plot_panel.panels
    window.plot_panel.set_xy_view(True)
    _settle(qapp)
    window._toggle_panel_maximised()              # Ctrl+Shift+Enter: the plot
    assert area.maximised() == 'plot' and not window.plot_panel.xy_view.isVisible()
    assert window._esc_rect_zoom.isEnabled()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    _settle(qapp)
    assert area.maximised() is None and window.plot_panel.xy_view.isVisible()
    assert not window._esc_rect_zoom.isEnabled()  # Esc is free again


def test_ctrl_f4_closes_the_panel_with_the_keyboard_focus(window, qapp):
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    _settle(qapp)
    panel.xy_view.plot.setFocus()
    _settle(qapp)
    window._close_focused_panel()
    assert not panel.xy_view_active() and not panel.panels.is_open('xy')
    window._reopen_panel()
    assert panel.xy_view_active() and panel.panels.is_open('xy')


def test_ctrl_f4_leaves_the_time_plot(window, qapp):
    window.plot_panel.table.setFocus()
    window._close_focused_panel()
    assert window.plot_panel.panels.is_open('plot')


# ── Own windows, and synchronized tabs ───────────────────────────────────


def test_a_panel_in_its_own_window_names_its_tab(window, qapp):
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    panel.panels.float_panel('xy')
    _settle(qapp)
    assert panel.xy_view.window().windowTitle() == 'Tab 1 · XY plot'


def test_a_synchronized_tabs_own_window_follows_the_tab_on_screen(window, qapp):
    first = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.plot_panel.set_xy_view(True)
    first.panels.float_panel('xy')                # on screen while its tab is not
    window.plot_tabs.add_tab()
    second = window.plot_panel
    second.add_series('Speed', first._items['Speed'].series)
    second._rebuild_curves(preserve_selection=True)
    window.btn_cursor1.setChecked(True)
    second.move_cursor1(3.0)
    _settle(qapp)
    assert first.cursor_state()[0] and first.cursor_state()[1] == pytest.approx(3.0)
    # Not synchronized: each tab keeps its own time.
    window.btn_sync_tabs.setChecked(False)
    second.move_cursor1(1.0)
    assert first.cursor_state()[1] == pytest.approx(3.0)


def test_closing_a_tab_brings_its_own_windows_back_first(window, qapp):
    window.plot_tabs.add_tab()
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    panel.panels.float_panel('xy')
    window_of_xy = panel.xy_view.window()
    window.plot_tabs.close_tab(1)
    _settle(qapp)
    from shiboken6 import Shiboken
    assert not Shiboken.isValid(window_of_xy) or not window_of_xy.isVisible()


def test_closing_the_main_window_brings_own_windows_back(window, qapp):
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    panel.panels.float_panel('xy')
    assert panel.panels.is_floating('xy')
    window.close()
    _settle(qapp)
    # Left open, it would keep the application running behind the closed window.
    assert not panel.panels.is_floating('xy')


def test_the_shortcuts_list_names_the_panel_keys(window):
    keys = dict(window._SHORTCUTS)
    assert 'Maximise' in keys['Ctrl + Shift + Enter']
    assert 'Close the panel' in keys['Ctrl + F4']
    assert 'closed last' in keys['Ctrl + Shift + T']
    assert 'Rectangle Zoom' in keys['Esc'] and 'maximised panel' in keys['Esc']


def test_opening_the_trace_again_leaves_it_in_its_own_window(window, qapp):
    area = window.plot_panel.panels
    window.show_raw_frames()
    area.float_panel('trace')
    _settle(qapp)
    window.show_raw_frames()                      # Ctrl+Shift+R again: brought up
    _settle(qapp)
    assert area.is_floating('trace')


def test_a_configuration_opened_unchanged_is_described_unchanged(window, qapp):
    # The session's "save changes?" question compares the two.
    from gui.plot_tabs import describe_tab

    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    window.btn_table.setChecked(True)
    _settle(qapp)
    saved = describe_tab(panel, 'Tab 1')
    window.plot_tabs.add_tab()
    other = window.plot_panel
    window._apply_tab_settings(saved)
    window.resize(1333, 777)
    _settle(qapp)
    again = describe_tab(other, 'Tab 1')
    assert again['panels'] == saved['panels']
    assert again['xy_views'] == saved['xy_views']
