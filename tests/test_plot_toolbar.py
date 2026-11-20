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
    # At rest, and moved by the tests alone: not by the real pointer, nor by
    # the moment the tab is out when the window opens.
    w.pane_rail._timer.stop()
    w.pane_rail._hint_until = 0.0
    w.pane_rail.rest()
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
    assert row._end_buttons == [window.btn_sync_tabs, window.btn_more]
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


def _in_window(window, widget, x=0, y=0):
    from PySide6.QtCore import QPoint
    return widget.mapTo(window, QPoint(x, y))


def _edge(window, widget):
    """The widget's right edge, across the window."""
    return _in_window(window, widget, widget.width()).x()


def _handle_height(window):
    """Where the middle of the rail's mark, « or » lies, down the window."""
    rail = window.pane_rail
    return _in_window(window, rail).y() + rail.handle_offset()


def _pointer(window, where):
    """A global position: 'edge' is on the plots' right edge, low down;
    'rail' on the rail itself; 'away' in the middle of the plots."""
    from PySide6.QtCore import QPoint
    plots, rail = window.plot_tabs.plots, window.pane_rail
    if where == 'rail':
        return rail.mapToGlobal(QPoint(rail.width() // 2, rail.height() // 2))
    x = plots.width() - 5 if where == 'edge' else plots.width() // 2
    return plots.mapToGlobal(QPoint(x, plots.height() - 40))


def test_at_rest_the_rail_is_a_thin_mark_on_the_plots_edge(window, qapp):
    rail, plots = window.pane_rail, window.plot_tabs.plots
    assert rail.state() == 'rest' and rail.isVisible()
    assert (rail.width(), rail.height()) == rail.MARK_SIZE
    assert rail.width() <= 4                                   # nothing on the plot
    assert not rail.handle.isVisible()
    assert not any(button.isVisible() for button in rail.buttons())
    assert rail.parentWidget() is plots.parentWidget()
    assert _edge(window, rail) == _edge(window, plots)
    assert _handle_height(window) == _in_window(window, plots).y() + rail.HANDLE_CENTRE
    # Over the plots: nothing of them is drawn on top of it.
    siblings = rail.parentWidget().children()
    assert siblings.index(rail) > siblings.index(plots)


def test_the_pointer_near_the_edge_brings_the_tab_out_after_a_pause(window, qapp):
    rail = window.pane_rail
    height = _handle_height(window)
    rail.follow_pointer(_pointer(window, 'edge'), 10.0)
    assert rail.state() == 'rest'                              # only passing by, so far
    rail.follow_pointer(_pointer(window, 'edge'), 10.05)
    assert rail.state() == 'rest'
    rail.follow_pointer(_pointer(window, 'edge'), 10.0 + rail.PEEK_AFTER_MS / 1000 + 0.01)
    assert rail.state() == 'peek'
    assert (rail.width(), rail.height()) == (rail.TAB_WIDTH, rail.TAB_HEIGHT + 2 * rail.FLARE)
    assert rail.width() <= 20                                  # small
    assert not rail.handle.isVisible()
    assert not any(button.isVisible() for button in rail.buttons())
    assert _edge(window, rail) == _edge(window, window.plot_tabs.plots)
    assert _handle_height(window) == height                    # the same height


def test_passing_by_the_edge_does_not_bring_it_out(window):
    rail = window.pane_rail
    rail.follow_pointer(_pointer(window, 'edge'), 10.0)
    rail.follow_pointer(_pointer(window, 'away'), 10.05)
    rail.follow_pointer(_pointer(window, 'edge'), 10.0 + rail.PEEK_AFTER_MS / 1000 + 0.01)
    assert rail.state() == 'rest'                              # the pause starts anew
    rail.follow_pointer(_pointer(window, 'away'), 30.0)
    rail.follow_pointer(_pointer(window, 'away'), 40.0)
    assert rail.state() == 'rest'


def test_the_tab_goes_back_when_the_pointer_leaves(window):
    rail = window.pane_rail
    rail.peek()
    rail.follow_pointer(_pointer(window, 'edge'), 10.0)
    rail.follow_pointer(_pointer(window, 'edge'), 20.0)
    assert rail.state() == 'peek'                              # stays while it is near
    rail.follow_pointer(_pointer(window, 'away'), 20.0)
    rail.follow_pointer(_pointer(window, 'away'), 20.1)
    assert rail.state() == 'peek'                              # a slip does not hide it
    rail.follow_pointer(_pointer(window, 'away'), 20.0 + rail.REST_AFTER_MS / 1000 + 0.01)
    assert rail.state() == 'rest'


def test_the_tab_is_a_plot_tab_stood_on_the_edge(window, qapp):
    rail = window.pane_rail
    rail.peek()
    path = rail.tab_path()
    width, height, flare = rail.width(), rail.height(), rail.FLARE
    inside = path.toFillPolygon()
    from PySide6.QtCore import Qt
    contains = lambda x, y: inside.containsPoint(_point(x, y), Qt.FillRule.OddEvenFill)
    assert contains(width / 2, height / 2)
    # Rounded away at its two free corners...
    assert not contains(1, flare + 1.5) and not contains(1, height - flare - 1.5)
    # ...and flared into the edge above and below it.
    assert contains(width - 1.5, flare - 1) and contains(width - 1.5, height - flare + 1)
    assert not contains(width - flare - 1, 1) and not contains(width - flare - 1, height - 1)


def test_a_click_on_the_tab_opens_the_glass_list(window, qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    rail, plots = window.pane_rail, window.plot_tabs.plots
    width = plots.width()
    rail.peek()
    height = _handle_height(window)
    QTest.mouseClick(rail, Qt.MouseButton.LeftButton,
                     pos=QPoint(rail.width() // 2, rail.height() // 2))
    qapp.processEvents()
    assert rail.state() == 'open'
    assert rail.buttons() == [window.btn_trace, window.btn_table, window.btn_xy]
    assert [button.text() for button in rail.buttons()] == ['Trace', 'Table', 'XY']
    assert all(button.isVisible() for button in rail.buttons())
    assert rail.handle.isVisible() and rail.handle.text() == '»'
    assert (rail.handle.y() < window.btn_trace.y() < window.btn_table.y()
            < window.btn_xy.y())
    assert window.btn_xy.width() == window.btn_table.width() == window.btn_trace.width()
    assert _edge(window, rail) == _edge(window, plots)
    assert _handle_height(window) == height                    # » where « was
    assert plots.width() == width                              # the plot never moves


def test_a_click_on_the_mark_opens_it_too(window, qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    rail = window.pane_rail
    QTest.mouseClick(rail, Qt.MouseButton.LeftButton, pos=QPoint(1, 5))
    assert rail.state() == 'open'


def test_the_list_folds_back_when_the_pointer_leaves_it(window, qapp):
    rail = window.pane_rail
    rail.open()
    qapp.processEvents()
    rail.follow_pointer(_pointer(window, 'rail'), 10.0)
    rail.follow_pointer(_pointer(window, 'rail'), 60.0)
    assert rail.state() == 'open'                              # as long as it is on it
    # The edge below the list is not the list.
    rail.follow_pointer(_pointer(window, 'edge'), 60.0)
    rail.follow_pointer(_pointer(window, 'edge'), 60.1)
    assert rail.state() == 'open'                              # a slip does not fold it
    rail.follow_pointer(_pointer(window, 'rail'), 60.2)
    rail.follow_pointer(_pointer(window, 'away'), 70.0)
    rail.follow_pointer(_pointer(window, 'away'), 70.0 + rail.REST_AFTER_MS / 1000 + 0.01)
    assert rail.state() == 'rest'
    assert not any(button.isVisible() for button in rail.buttons())


def test_the_list_folds_when_the_pointer_is_on_another_window(window, qapp):
    rail = window.pane_rail
    rail.open()
    rail.follow_pointer(None, 10.0)                            # on a menu, a dialog...
    rail.follow_pointer(None, 10.0 + rail.REST_AFTER_MS / 1000 + 0.01)
    assert rail.state() == 'rest'


def test_the_lists_own_button_folds_it_to_the_tab(window, qapp):
    rail = window.pane_rail
    rail.open()
    height = _handle_height(window)
    rail.handle.click()
    assert rail.state() == 'peek'
    assert _handle_height(window) == height


def test_the_rail_keeps_to_the_edge_when_the_window_is_resized(window, qapp):
    rail, plots = window.pane_rail, window.plot_tabs.plots
    for state in (rail.rest, rail.peek, rail.open):
        state()
        window.resize(1300, 700)
        qapp.processEvents()
        assert _edge(window, rail) == _edge(window, plots)
        assert _handle_height(window) == _in_window(window, plots).y() + rail.HANDLE_CENTRE
        window.resize(1700, 800)
        qapp.processEvents()


def test_the_open_list_is_glass_rounded_on_its_free_side(window, qapp):
    rail = window.pane_rail
    rail.open()
    qapp.processEvents()
    path = rail.glass_path()
    width, height = rail.width(), rail.height()
    # Rounded away at the two free corners, square where it meets the edge.
    assert not path.contains(_point(0.5, 0.5)) and not path.contains(_point(0.5, height - 1))
    assert path.contains(_point(width - 1, 1.5)) and path.contains(_point(width - 1, height - 2))
    assert path.contains(_point(width / 2, height / 2))
    # See-through: the plot shows under it.
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget
    image = QImage(rail.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    rail.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)   # no backdrop
    assert 0 < image.pixelColor(width // 2, 3).alpha() < 255
    assert image.pixelColor(0, 0).alpha() == 0


def _point(x, y):
    from PySide6.QtCore import QPointF
    return QPointF(x, y)


def test_the_open_view_is_outlined_and_the_others_are_not(window, qapp):
    sheet = window.pane_rail.styleSheet()
    assert 'QPushButton:checked' in sheet and 'border-color' in sheet
    window.pane_rail.open()
    assert not window.btn_xy.isChecked()
    window.btn_xy.click()
    assert window.btn_xy.isChecked() and not window.btn_table.isChecked()
    assert window.pane_rail.state() == 'open'                  # until the pointer leaves


def test_the_tab_is_out_for_a_moment_when_the_window_opens(qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w.show()
    qapp.processEvents()
    rail = w.pane_rail
    rail._timer.stop()
    assert rail.state() == 'peek'                              # so that it is seen
    hint_ends = rail._hint_until
    rail.follow_pointer(None, hint_ends - 1.0)
    rail.follow_pointer(None, hint_ends - 0.5)
    assert rail.state() == 'peek'                              # the pointer is elsewhere
    rail.follow_pointer(None, hint_ends + 0.01)
    rail.follow_pointer(None, hint_ends + 0.01 + rail.REST_AFTER_MS / 1000 + 0.01)
    assert rail.state() == 'rest'
    w.close()


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


# ── What the rail leaves alone ───────────────────────────────────────────


def test_the_row_has_no_button_for_the_rail(window):
    assert not hasattr(window, 'btn_show_rail')
    assert window.plot_button_row._end_buttons[-1] is window.btn_more
    assert window.btn_table not in window.plot_button_row._buttons
    assert window.btn_xy not in window.plot_button_row._buttons


def test_panes_stay_open_while_the_rail_is_at_rest(window, qapp):
    panel = window.plot_panel
    window.pane_rail.open()
    window.btn_xy.click()
    window.pane_rail.rest()
    qapp.processEvents()
    assert panel.xy_view_active() and panel.xy_view.isVisible()


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
