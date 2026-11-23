# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The XY view: one plotted signal against another beside the time plot,
coloured by a third. It shows the time the time plot shows, marks the
cursors, and a click on a point puts Cursor 1 at its time.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest


def _series(name, ts, vs, unit='u'):
    from core.signal_store import SignalSeries
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit=unit,
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float),
    )


_TIMES = [0.0, 1.0, 2.0, 3.0, 4.0]


def _signals():
    return [
        ('S', 'Speed', _series('Speed', _TIMES, [10, 20, 30, 40, 50], unit='km/h'), '#ff0000'),
        ('T', 'Torque', _series('Torque', _TIMES, [1, 2, 3, 4, 5], unit='Nm'), '#00ff00'),
        ('C', 'Temp', _series('Temp', _TIMES, [60, 70, 80, 90, 100], unit=''), '#0000ff'),
    ]


@pytest.fixture()
def view(qapp):
    from gui.xy_view import XYView
    v = XYView()
    v.resize(500, 400)
    v.set_signals(_signals())
    yield v
    v.close()


def _drawn(view):
    xs, ys = view.scatter.getData()
    return list(xs), list(ys)


def _brush_colors(view):
    return [spot.brush().color().name() for spot in view.scatter.points()]


# ── The view on its own ──────────────────────────────────────────────────


def test_the_first_two_plotted_signals_are_x_and_y(view):
    assert (view.chosen('x'), view.chosen('y'), view.chosen('color')) == ('S', 'T', None)
    assert view.x_box.currentText() == 'Speed [km/h]'
    assert view.y_box.currentText() == 'Torque [Nm]'
    assert view.color_box.currentText() == 'None'
    assert _drawn(view) == ([10, 20, 30, 40, 50], [1, 2, 3, 4, 5])
    assert view.summary.text() == '5 points'


def test_with_one_signal_it_says_what_to_do(qapp):
    from gui.xy_view import XYView
    v = XYView()
    v.set_signals(_signals()[:1])
    assert _drawn(v) == ([], [])
    assert 'two signals' in v.summary.text()


def test_without_a_colour_signal_points_have_the_y_signals_colour(view):
    assert set(_brush_colors(view)) == {'#00ff00'}
    assert view.scale.isHidden()


def test_a_colour_signal_colours_low_blue_and_high_red(view):
    from gui.xy_view import scale_color
    view.choose('color', 'C')
    colors = _brush_colors(view)
    assert colors[0] == scale_color(0.0).name() == '#4662d7'
    assert colors[-1] == scale_color(1.0).name() == '#cb2a04'
    assert len(set(colors)) == 5
    assert not view.scale.isHidden()
    assert view.scale.text() == '60 … 100  Temp'
    view.choose('color', None)
    assert set(_brush_colors(view)) == {'#00ff00'} and view.scale.isHidden()


def test_only_the_time_shown_is_drawn_unless_the_whole_is_asked_for(view):
    view.set_time_range((1.0, 3.0))
    view._redraw()                      # what the timer does once the range rests
    assert _drawn(view) == ([20, 30, 40], [2, 3, 4])
    view.whole_box.setChecked(True)
    assert _drawn(view)[0] == [20, 30, 40]          # not until Update
    view.apply_settings()
    assert len(_drawn(view)[0]) == 5
    view.whole_box.setChecked(False)
    view.apply_settings()
    assert _drawn(view)[0] == [20, 30, 40]


def test_a_range_change_waits_until_the_range_rests(view):
    view.set_time_range((1.0, 3.0))
    assert len(_drawn(view)[0]) == 5 and view._timer.isActive()
    view.set_time_range((1.0, 2.0))
    assert view._timer.isActive()
    view._timer.timeout.emit()
    assert _drawn(view)[0] == [20, 30]


def test_thinning_is_named(view, monkeypatch):
    from core import xy_data
    from gui import xy_view
    monkeypatch.setattr(
        xy_view, 'xy_points',
        lambda x, y, color=None, time_range=None:
        xy_data.xy_points(x, y, color, time_range, max_points=3))
    view._redraw()
    assert view.summary.text() == '3 points, 1 of every 2 samples'


def test_each_cursor_is_marked_where_the_signals_are_then(view):
    assert view.mark_position(1) is None and view.mark_position(2) is None
    view.set_cursors(2.5, None)
    assert view.mark_position(1) == (30.0, 3.0)      # the values held at 2.5
    assert view.mark_position(2) is None
    view.set_cursors(2.5, 4.0)
    assert view.mark_position(2) == (50.0, 5.0)
    view.set_cursors(None, None)
    assert view.mark_position(1) is None and view.mark_position(2) is None


def test_a_click_on_a_point_asks_for_cursor_1_at_its_time(view, qapp):
    view.show()
    qapp.processEvents()
    asked = []
    view.timeClicked.connect(asked.append)
    assert view.pick(30.0, 3.0)
    assert asked == [2.0]
    # Between two points, far from both in pixels: nothing.
    assert not view.pick(35.0, 3.5)
    assert asked == [2.0]


def test_the_choices_are_kept_and_taken_back(view):
    view.choose('x', 'T')
    view.choose('y', 'C')
    view.choose('color', 'S')
    view.whole_box.setChecked(True)
    view.equal_box.setChecked(True)
    view.apply_settings()
    state = view.state()
    assert state == {'x': 'T', 'y': 'C', 'color': 'S',
                     'whole_measurement': True, 'equal_scale': True,
                     'view': 'scatter', 'bins': '', 'show': 'percent', 'gap': '',
                     'y_bins': '', 'log_color': False}

    from gui.xy_view import XYView
    other = XYView()
    other.restore(state)                 # before its signals are plotted
    assert _drawn(other) == ([], [])
    other.set_signals(_signals())
    assert (other.chosen('x'), other.chosen('y'), other.chosen('color')) == ('T', 'C', 'S')
    assert other.x_box.currentText() == 'Torque [Nm]'
    assert other.color_box.currentText() == 'Speed [km/h]'
    assert other.whole_box.isChecked() and other.equal_box.isChecked()
    assert _drawn(other) == ([1, 2, 3, 4, 5], [60, 70, 80, 90, 100])


def test_a_chosen_signal_no_longer_plotted_draws_nothing_until_it_is_back(view):
    view.set_signals([s for s in _signals() if s[0] != 'T'])
    assert _drawn(view) == ([], [])
    assert view.y_box.currentIndex() == -1 and view.chosen('y') == 'T'
    view.set_signals(_signals())
    assert _drawn(view)[1] == [1, 2, 3, 4, 5]


def test_equal_scale_locks_the_two_axes_together(view):
    view_box = view.plot.getPlotItem().vb
    assert not view_box.state['aspectLocked']
    view.equal_box.setChecked(True)
    view.apply_settings()
    assert view_box.state['aspectLocked']
    view.equal_box.setChecked(False)
    view.apply_settings()
    assert not view_box.state['aspectLocked']


# ── In the window ────────────────────────────────────────────────────────


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    panel = w.plot_panel
    panel.add_series('Speed', _series('Speed', _TIMES, [10, 20, 30, 40, 50]))
    panel.add_series('Torque', _series('Torque', _TIMES, [1, 2, 3, 4, 5]))
    panel.add_series('Temp', _series('Temp', _TIMES, [60, 70, 80, 90, 100]))
    panel._rebuild_curves(preserve_selection=True)
    yield w
    w.close()
    qapp.processEvents()


def _left_and_width(panel, widget):
    from PySide6.QtCore import QPoint
    return widget.mapTo(panel, QPoint(0, 0)).x(), widget.width()


def test_the_xy_button_puts_the_xy_plot_right_of_the_plot(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    qapp.processEvents()
    assert not panel.xy_view.isVisible()
    window.plot_panel.set_xy_view(True)
    qapp.processEvents()
    assert panel.xy_view_active()
    assert panel.view_stack.isVisible() and panel.xy_view.isVisible()
    # A panel right of the plot, narrower than it (gui.panel_area).
    plots, xy = _left_and_width(panel, panel.view_stack), _left_and_width(panel, panel.xy_view)
    assert plots[0] < xy[0]
    assert plots[1] > xy[1] > 0
    assert panel.xy_view.x_box.count() == 3
    assert (panel.xy_view.chosen('x'), panel.xy_view.chosen('y')) == ('Speed', 'Torque')
    window.plot_panel.set_xy_view(False)
    qapp.processEvents()
    assert not panel.xy_view_active() and not panel.xy_view.isVisible()
    assert panel.view_stack.isVisible()


def test_the_xy_plot_and_the_table_can_both_be_shown(window, qapp):
    panel = window.plot_panel
    window.resize(1600, 800)
    window.show()
    qapp.processEvents()
    window.btn_table.setChecked(True)
    window.plot_panel.set_xy_view(True)
    qapp.processEvents()
    assert panel.view_stack.isVisible() and panel.tabular.isVisible()
    assert panel.xy_view.isVisible()
    # The table right of the plot, the XY plot right of both, as before panels.
    plots, table, xy = (_left_and_width(panel, widget)
                        for widget in (panel.view_stack, panel.tabular, panel.xy_view))
    assert plots[0] < table[0] < xy[0]
    assert all(width > 0 for _left, width in (plots, table, xy))


def test_the_xy_plot_keeps_the_width_it_was_dragged_to(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    qapp.processEvents()
    window.plot_panel.set_xy_view(True)
    qapp.processEvents()
    splitter = panel.xy_view.parentWidget().parentWidget().parentWidget()   # its place's
    total = sum(splitter.sizes())
    splitter.setSizes([total - 320, 320])
    qapp.processEvents()
    window.plot_panel.set_xy_view(False)
    qapp.processEvents()
    window.plot_panel.set_xy_view(True)
    qapp.processEvents()
    from gui.panel_area import CARD_INSET
    assert abs(panel.xy_view.width() + 2 * CARD_INSET - 320) <= 2      # inside its card


def test_it_follows_the_time_the_time_plot_shows(window, qapp):
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    panel.show_time_range(0.9, 3.1)
    qapp.processEvents()
    panel.xy_view._redraw()
    assert list(panel.xy_view.scatter.getData()[0]) == [20, 30, 40]


def test_plotting_another_signal_offers_it(window, qapp):
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    panel.add_series('Load', _series('Load', _TIMES, [0, 0, 1, 1, 2]))
    panel._rebuild_curves(preserve_selection=True)
    assert panel.xy_view.x_box.count() == 4
    assert panel.xy_view.color_box.count() == 5        # and None


def test_a_click_on_a_point_places_cursor_1(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    window.plot_panel.set_xy_view(True)
    qapp.processEvents()
    assert panel.xy_view.pick(40.0, 4.0)
    assert window.btn_cursor1.isChecked()
    assert panel.v_line.value() == pytest.approx(3.0)
    assert panel.xy_view.mark_position(1) == (40.0, 4.0)


def test_the_cursors_moving_move_their_marks(window):
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    assert panel.xy_view.mark_position(1) is None
    window.btn_cursor1.setChecked(True)
    panel.move_cursor1(1.2)
    assert panel.xy_view.mark_position(1) == (20.0, 2.0)
    window.btn_cursor2.setChecked(True)
    panel.move_cursor2(4.0)
    assert panel.xy_view.mark_position(2) == (50.0, 5.0)
    window.btn_cursor2.setChecked(False)
    assert panel.xy_view.mark_position(2) is None
    window.btn_cursor1.setChecked(False)
    assert panel.xy_view.mark_position(1) is None


def test_the_marks_are_right_when_the_view_opens_with_a_cursor_on(window):
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    panel.move_cursor1(2.0)
    window.plot_panel.set_xy_view(True)
    assert panel.xy_view.mark_position(1) == (30.0, 3.0)


def test_the_background_colour_is_the_plots(window):
    panel = window.plot_panel
    panel.set_background_color('#ffffff')
    assert panel.xy_view.plot.backgroundBrush().color().name() == '#ffffff'
    assert panel.xy_view._foreground != '#ffffff'


def test_each_tab_has_its_own_xy_plots(window, qapp):
    first = window.plot_panel
    window.plot_panel.set_xy_view(True)
    window.plot_tabs.add_tab()
    second = window.plot_panel
    assert second is not first
    assert not second.xy_view_active() and second.xy_views() == {}
    window.plot_tabs.bar.setCurrentIndex(0)
    assert first.xy_view_active() and list(first.xy_views()) == ['xy']


def test_a_tab_is_described_with_its_xy_plot_and_set_up_from_it(window):
    from gui.plot_tabs import describe_tab
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    panel.xy_view.choose('x', 'Torque')
    panel.xy_view.choose('y', 'Temp')
    panel.xy_view.choose('color', 'Speed')
    saved = describe_tab(panel, 'Plot 1')
    assert 'xy' not in saved
    assert saved['xy_views'] == [{'key': 'xy', 'name': 'XY plot',
                                  'x': 'Torque', 'y': 'Temp', 'color': 'Speed',
                                  'whole_measurement': False, 'equal_scale': False,
                                  'view': 'scatter', 'bins': '', 'show': 'percent', 'gap': '',
                                  'y_bins': '', 'log_color': False}]

    window.plot_tabs.add_tab()
    other = window.plot_panel
    window._apply_tab_settings(saved)                 # as a configuration does,
    assert other.xy_view_active() and other.panels.is_open('xy')
    for key in ('Speed', 'Torque', 'Temp'):           # then its signals
        other.add_series(key, panel._items[key].series)
    other._rebuild_curves(preserve_selection=True)
    assert list(other.xy_view.scatter.getData()[0]) == [1, 2, 3, 4, 5]
    assert other.xy_view.color_box.currentText().startswith('Speed')


def test_a_configuration_without_an_xy_plot_leaves_it_as_it_is(window):
    window.plot_panel.set_xy_view(True)
    window._apply_tab_settings({'plot_type': 'stacked'})
    assert window.plot_panel.xy_view_active()
    window._apply_tab_settings({'xy': {'shown': False}})
    assert not window.plot_panel.xy_view_active()


def test_an_older_configurations_unused_xy_plot_makes_no_view(window):
    window._apply_tab_settings({'xy': {'shown': False, 'x': None, 'y': None, 'color': None}})
    assert window.plot_panel.xy_views() == {}
    window._apply_tab_settings({'xy': {'shown': False, 'x': 'Speed', 'y': 'Torque'}})
    assert list(window.plot_panel.xy_views()) == ['xy']             # its choices are kept
    assert not window.plot_panel.xy_view_active()


def test_a_renamed_signal_stays_chosen(window):
    from gui.plot_tabs import SavedTabs
    panel = window.plot_panel
    window.plot_panel.set_xy_view(True)
    panel.rename_series_key('Torque', 'Torque2')
    panel._rebuild_curves(preserve_selection=True)
    assert panel.xy_view.chosen('y') == 'Torque2'
    assert list(panel.xy_view.scatter.getData()[1]) == [1, 2, 3, 4, 5]

    saved = SavedTabs([{'xy': {'x': 'A', 'y': 'B', 'color': 'A'},
                        'xy_views': [{'key': 'xy', 'x': 'B', 'y': 'A'},
                                     {'key': 'histogram', 'x': 'A'}],
                        'signals': []}])
    saved.rename_signal('A', 'A2')
    assert saved.tabs[0]['xy'] == {'x': 'A2', 'y': 'B', 'color': 'A2'}
    assert saved.tabs[0]['xy_views'] == [{'key': 'xy', 'x': 'B', 'y': 'A2'},
                                         {'key': 'histogram', 'x': 'A2'}]


# ── Its settings, behind the gear ────────────────────────────────────────


_SCATTER_SETTINGS = ('x_box', 'y_box', 'color_box', 'whole_box', 'equal_box')


def test_the_settings_are_behind_the_gear_and_the_plot_has_the_room(view, qapp):
    from PySide6.QtCore import QPoint
    view.show()
    qapp.processEvents()
    assert view.settings_button.isVisible() and view.summary.isVisible()
    assert not any(getattr(view, name).isVisible() for name in _SCATTER_SETTINGS)
    assert not view.copy_button.isVisible() and not view.export_button.isVisible()
    # One row above the plot: the name, what it shows and the gear.
    assert view.plot.mapTo(view, QPoint(0, 0)).y() == view.header.height()
    view.open_settings()
    qapp.processEvents()
    assert view.settings_box.isVisible() and view.settings_box.isWindow()
    assert all(getattr(view, name).isVisible() for name in _SCATTER_SETTINGS)
    assert not view.bins_edit.isVisible() and not view.log_box.isVisible()
    view.settings_box.hide()


@pytest.mark.parametrize('kind', ['scatter', 'histogram', 'residency'])
def test_a_click_on_the_gear_opens_the_settings_under_it(qapp, kind):
    # The gear in the view's own row, clicked as the user does: its click
    # passes nothing on to where the box opens (gear item 11).
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from gui import xy_view
    v = xy_view.XYView({'scatter': xy_view.SCATTER, 'histogram': xy_view.HISTOGRAM,
                        'residency': xy_view.RESIDENCY}[kind])
    v.set_signals(_signals())
    v.resize(500, 400)
    v.show()
    qapp.processEvents()
    gear = v.settings_button
    QTest.mouseClick(gear, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    box = v.settings_box
    assert box.isVisible()
    # Under the gear (the screen's edge may nudge it a pixel or two).
    corner = gear.mapToGlobal(QPoint(gear.width(), gear.height()))
    assert abs(box.geometry().top() - corner.y()) <= 4
    v.settings_box.hide()
    v.close()


def test_update_draws_with_the_settings_and_closes_the_box(view, qapp):
    view.show()
    view.open_settings()
    view.y_box.setCurrentIndex(view.y_box.findData('C'))
    view.color_box.setCurrentIndex(view.color_box.findData('S'))
    assert _drawn(view)[1] == [1, 2, 3, 4, 5]               # not yet
    view.update_button.click()
    qapp.processEvents()
    assert not view.settings_box.isVisible()
    assert (view.chosen('y'), view.chosen('color')) == ('C', 'S')
    assert _drawn(view)[1] == [60, 70, 80, 90, 100] and not view.scale.isHidden()


def test_cancel_leaves_the_view_as_it_was_and_the_box_forgets(view, qapp):
    view.show()
    view.open_settings()
    view.x_box.setCurrentIndex(view.x_box.findData('C'))
    view.whole_box.setChecked(True)
    view.cancel_button.click()
    qapp.processEvents()
    assert not view.settings_box.isVisible()
    assert view.chosen('x') == 'S' and not view.option('whole_measurement')
    assert _drawn(view)[0] == [10, 20, 30, 40, 50]
    # Drawn again later, as the time shown changes: still as it was applied.
    view.set_time_range((1.0, 3.0))
    view._redraw()
    assert _drawn(view)[0] == [20, 30, 40]
    view.open_settings()                                 # what is drawn, again
    assert view.x_box.currentData() == 'S' and not view.whole_box.isChecked()
    view.settings_box.hide()


def test_escape_is_cancel(view, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    view.show()
    view.open_settings()
    qapp.processEvents()
    view.y_box.setCurrentIndex(view.y_box.findData('C'))
    QTest.keyClick(view.settings_box, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not view.settings_box.isVisible() and view.chosen('y') == 'T'


def test_enter_in_a_typed_setting_is_update(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from gui.xy_view import HISTOGRAM, XYView
    v = XYView(HISTOGRAM)
    v.set_signals(_signals())
    v.show()
    v.open_settings()
    v.bins_edit.setText('20')
    assert v.option('bins') == ''
    QTest.keyClick(v.bins_edit, Qt.Key.Key_Return)
    qapp.processEvents()
    assert v.option('bins') == '20' and not v.settings_box.isVisible()
    assert v.bars.edges[1] - v.bars.edges[0] == 20.0
    v.close()


def test_what_it_shows_fits_its_row_and_its_tooltip_has_all(qapp):
    from PySide6.QtCore import QPoint
    from gui.xy_view import HISTOGRAM, XYView
    v = XYView(HISTOGRAM)
    v.set_signals(_signals())
    v.resize(300, 300)
    v.show()
    qapp.processEvents()
    text = v.summary.text()
    assert 'counted' in text and 'mean' in text and v.summary.toolTip() == text
    shown = v.summary.shown_text()
    assert shown.endswith('…') and len(shown) < len(text)
    # On the gear's row, however long: the plot starts under that one row.
    assert v.hist_plot.mapTo(v, QPoint(0, 0)).y() == v.header.height()
    v.close()


# ── Several views in a tab ───────────────────────────────────────────────


def _views_menu(window):
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    window._fill_views_menu(menu)
    window._kept_menu = menu                   # alive while its actions are used
    return {action.text().split('\t')[0]: action
            for action in menu.actions() if not action.isSeparator()}


def test_views_of_each_kind_are_added_and_share_the_right_side(window, qapp):
    from PySide6.QtCore import QPoint
    from gui.xy_view import HISTOGRAM, RESIDENCY, SCATTER
    panel = window.plot_panel
    window.resize(1400, 900)
    window.show()
    qapp.processEvents()
    keys = [panel.add_xy_view(kind) for kind in (RESIDENCY, SCATTER, SCATTER, HISTOGRAM)]
    qapp.processEvents()
    assert keys == ['residency', 'xy', 'xy-2', 'histogram']
    views = panel.xy_views()
    assert list(views) == keys
    assert [panel.panels.title(key) for key in keys] == [
        'Residency map', 'XY plot', 'XY plot 2', 'Histogram']
    assert [views[key].view() for key in keys] == [RESIDENCY, SCATTER, SCATTER, HISTOGRAM]
    assert all(view.isVisible() and view.x_box.count() == 3 for view in views.values())
    # Right of the time plot, each under the one before.
    plot = panel.view_stack.mapTo(panel, QPoint(0, 0))
    places = [views[key].mapTo(panel, QPoint(0, 0)) for key in keys]
    assert all(place.x() > plot.x() for place in places)
    assert len({place.x() for place in places}) == 1
    assert [place.y() for place in places] == sorted({place.y() for place in places})
    # Each an equal part of that side, not half of the one before.
    heights = [panel.panels._group_of(key).height() for key in keys]
    assert max(heights) - min(heights) <= 4, heights


def test_the_views_menu_lists_each_view_to_close_and_open_again(window, qapp):
    panel = window.plot_panel
    _views_menu(window)['Add residency map'].trigger()
    _views_menu(window)['Add residency map'].trigger()
    entries = _views_menu(window)
    assert list(entries)[:3] == ['Table', 'Residency map', 'Residency map 2']
    assert entries['Residency map'].isChecked() and entries['Residency map 2'].isChecked()
    entries['Residency map'].trigger()
    assert not panel.panels.is_open('residency') and panel.panels.is_open('residency-2')
    entries = _views_menu(window)
    assert not entries['Residency map'].isChecked()
    assert entries['Reopen Residency map'].isEnabled()
    entries['Residency map'].trigger()
    assert panel.panels.is_open('residency')
    assert panel.xy_views()['residency'].x_box.count() == 3       # given the signals


def test_one_views_shading_lets_the_others_go(window, qapp):
    panel = window.plot_panel
    keys = panel.add_xy_view('histogram'), panel.add_xy_view('residency')
    histogram, residency = (panel.xy_views()[key] for key in keys)
    for view in (histogram, residency):
        view.whole_box.setChecked(True)
        view.apply_settings()
    histogram.pick_bar(next(i for i, held in enumerate(histogram.bars.times) if held > 0))
    assert histogram.picked_spans() and panel.time_spans() == histogram.picked_spans()
    grid = residency.grid
    cell = next((i, j) for i in range(grid.times.shape[0]) for j in range(grid.times.shape[1])
                if grid.times[i, j] > 0)
    residency.pick_cell(cell)
    assert histogram.picked_spans() == []
    assert residency.picked_spans() and panel.time_spans() == residency.picked_spans()


def test_a_tab_keeps_its_views_with_their_names_kinds_and_settings(window, qapp):
    from gui.panel_area import layout_keys
    from gui.plot_tabs import describe_tab
    panel = window.plot_panel
    window.resize(1400, 900)
    window.show()
    for kind in ('residency', 'scatter', 'scatter'):
        panel.add_xy_view(kind)
    panel.panels.set_title('xy-2', 'Gear map')
    views = panel.xy_views()
    views['residency'].y_bins_edit.setText('4')
    views['residency'].apply_settings()
    panel.panels.close_panel('xy', user=True)            # closed, but kept
    qapp.processEvents()
    saved = describe_tab(panel, 'Tab 1')
    assert [(entry['key'], entry['name'], entry['view']) for entry in saved['xy_views']] == [
        ('residency', 'Residency map', 'residency'), ('xy', 'XY plot', 'scatter'),
        ('xy-2', 'Gear map', 'scatter')]
    assert saved['xy_views'][0]['y_bins'] == '4'
    assert sorted(layout_keys(saved['panels'])) == ['plot', 'residency', 'xy-2']

    window.plot_tabs.add_tab()
    other = window.plot_panel
    window._apply_tab_settings(saved)
    qapp.processEvents()
    assert list(other.xy_views()) == ['residency', 'xy', 'xy-2']
    assert other.panels.title('xy-2') == 'Gear map'
    assert other.xy_views()['residency'].option('y_bins') == '4'
    assert [other.panels.is_open(key) for key in other.xy_views()] == [True, False, True]
    again = describe_tab(other, 'Tab 1')
    assert again['xy_views'] == saved['xy_views'] and again['panels'] == saved['panels']

    # A configuration's views replace the tab's: one it does not name goes.
    window._apply_tab_settings({'xy_views': [saved['xy_views'][2]]})
    assert list(other.xy_views()) == ['xy-2'] and other.panels.panel('residency') is None

def test_the_gear_is_where_its_titles_gear_is_never_under_its_close(window, qapp):
    from PySide6.QtCore import QPoint, QRect
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    panel.set_xy_view(True)
    qapp.processEvents()
    view, area = panel.xy_view, panel.panels
    area._timer.stop()                      # the pointer is driven here
    row_gear = view.settings_button
    assert row_gear.isVisible()

    def on_screen(widget):
        return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())

    at_rest = on_screen(row_gear)
    place = area._group_of('xy')
    area.peek(place, hold=60)               # its title, over its own row
    qapp.processEvents()
    title_gear = place.settings_button
    assert place.strip.isVisible() and title_gear.isVisible()
    assert abs(on_screen(title_gear).center().x() - at_rest.center().x()) <= 1
    assert not on_screen(place.close_button).intersects(at_rest)
    assert not on_screen(place.max_button).intersects(at_rest)
    title_gear.click()                      # opens under the title's gear
    qapp.processEvents()
    box = view.settings_box
    assert box.isVisible() and box.y() >= on_screen(title_gear).bottom()
    box.hide()
    area.peek(None)
    # With titles shown as their own row, the title's gear opens the box
    # under itself, not under the row's (hidden) gear below it.
    window._set_panel_titles_shown(True)
    qapp.processEvents()
    assert not row_gear.isVisible() and title_gear.isVisible()
    title_gear.click()
    qapp.processEvents()
    assert box.isVisible() and box.y() == on_screen(title_gear).bottom() + 1
    box.hide()
    window._set_panel_titles_shown(False)


# ── Deleting a view ──────────────────────────────────────────────────────


def _open_views_menu(window, qapp):
    """Views ▾ as a click opens it, shown (not run) so its rows have places.
    Events are handled first: the window showing would close it."""
    qapp.processEvents()
    menu = window.btn_views.menu()
    menu.popup(window.btn_views.mapToGlobal(window.btn_views.rect().bottomLeft()))
    window._kept_menu = menu
    return menu, {action.text().split('\t')[0]: action
                  for action in menu.actions() if not action.isSeparator()}


def _click(menu, pos):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    for kind, held in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                       (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
        QApplication.sendEvent(menu, QMouseEvent(
            kind, QPointF(pos), QPointF(menu.mapToGlobal(pos)), Qt.MouseButton.LeftButton,
            held, Qt.KeyboardModifier.NoModifier))


def test_each_view_in_views_has_a_bin_in_one_column_at_the_right(window, qapp):
    # One column toward the menu's right end, clear of the longest name
    # (feedback item 12), not each bin right after its own name.
    from gui.bin_menu import BinMenu
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    for kind in ('scatter', 'histogram', 'scatter'):
        panel.add_xy_view(kind)
    long_name = 'A view name longer than any other entry here'
    panel.panels.set_title('xy-2', long_name)
    panel.panels.close_panel('histogram')                  # an unticked row
    menu, entries = _open_views_menu(window, qapp)
    assert isinstance(menu, BinMenu)
    rows = [entries[name] for name in ('XY plot', 'Histogram', long_name)]
    assert menu.bin_rows() == rows                       # the views, and nothing else
    # The menu drawn without its bins: where each name ends.
    bins = dict(menu._bins)
    menu._bins.clear()
    plain = menu.grab().toImage()
    menu._bins.update(bins)
    ratio = plain.devicePixelRatio()
    for row in rows:
        area, geometry = menu.bin_area(row), menu.actionGeometry(row)
        # The row's own background: its right end, where a view's row is empty.
        background = plain.pixelColor(int((geometry.right() - 8) * ratio),
                                      int(geometry.center().y() * ratio))
        assert area.right() <= menu.width()              # the menu has room for it
        drawn = [x for x in range(int((geometry.left() + 2) * ratio),
                                  int((geometry.right() - 12) * ratio))
                 for y in range(int((geometry.top() + 2) * ratio),
                                int((geometry.bottom() - 1) * ratio))
                 if plain.pixelColor(x, y) != background]
        # Clear of the name as the style draws it, a whole gap past it.
        assert drawn and area.left() * ratio - max(drawn) >= menu.BIN_GAP * ratio
        # At the row's right end, inside the row and its highlight.
        assert area.right() == geometry.right() - menu.BIN_RIGHT
    # One column: a short name's bin lies where the longest name's does.
    assert len({menu.bin_area(row).left() for row in rows}) == 1
    # Room for the longest name's gap and bin, whatever else is in the menu.
    assert menu.sizeHint().width() >= (menu.text_left() + menu.BIN_GAP + menu.BIN_AREA
                                       + menu.BIN_RIGHT
                                       + menu.fontMetrics().horizontalAdvance(long_name))
    # Its tooltip names what it deletes.
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication, QToolTip
    pos = menu.bin_area(rows[1]).center()
    QApplication.sendEvent(menu, QHelpEvent(QEvent.Type.ToolTip, pos, menu.mapToGlobal(pos)))
    assert QToolTip.text() == 'Delete Histogram'
    QToolTip.hideText()
    menu.hide()


def test_a_bin_deletes_its_view_and_the_menu_stays_open_on_the_rest(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    for kind in ('scatter', 'residency', 'scatter'):
        panel.add_xy_view(kind)
    panel.panels.close_panel('xy-2', user=True)            # closed, and last closed
    view = panel.xy_views()['residency']
    view.pick_cell((0, 0))
    menu, entries = _open_views_menu(window, qapp)
    assert entries['Reopen XY plot 2'].isEnabled()
    _click(menu, menu.bin_area(entries['XY plot 2']).center())
    qapp.processEvents()
    assert list(panel.xy_views()) == ['xy', 'residency']
    assert panel.panels.panel('xy-2') is None and 'xy-2' not in panel.panels.closed_keys()
    assert menu.isVisible() and 'XY plot 2' not in [a.text() for a in menu.actions()]
    reopen = [a for a in menu.actions() if a.text().startswith('Reopen')][0]
    assert reopen.text() == 'Reopen closed panel' and not reopen.isEnabled()
    # An open view goes from the tab, its shading with it.
    _click(menu, menu.bin_area(entries['Residency map']).center())
    qapp.processEvents()
    assert list(panel.xy_views()) == ['xy'] and panel.time_spans() == []
    assert not panel.panels.is_open('residency')
    menu.hide()
    # Its name is free again.
    assert panel.panels.title(panel.add_xy_view('residency')) == 'Residency map'


def test_delete_in_a_views_menu_deletes_it_and_close_keeps_it(window, qapp):
    panel = window.plot_panel
    window.btn_table.setChecked(True)
    key = panel.add_xy_view('histogram')
    texts = [a.text() for a in panel.panels.panel_menu(key).actions() if a.text()]
    assert texts[-2:] == ['Close', 'Delete']
    assert 'Delete' not in [a.text() for a in panel.panels.panel_menu('table').actions()]
    menu = panel.panels.panel_menu(key)
    [a for a in menu.actions() if a.text() == 'Close'][0].trigger()
    assert key in panel.xy_views()
    menu = panel.panels.panel_menu(key)
    [a for a in menu.actions() if a.text() == 'Delete'][0].trigger()
    assert key not in panel.xy_views() and panel.panels.panel(key) is None

def test_its_header_is_a_band_the_title_covers_exactly(window, qapp):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QPalette
    from gui.panel_area import TAB_HEIGHT, HeaderBand
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    panel.set_xy_view(True)
    qapp.processEvents()
    view, area = panel.xy_view, panel.panels
    area._timer.stop()
    header = view.header
    assert isinstance(header, HeaderBand) and header.height() == TAB_HEIGHT
    assert view.name_label.parentWidget() is header and view.settings_button.parentWidget() is header
    # A shade off the card, with a hairline under it.
    image = header.grab().toImage()
    ratio = image.devicePixelRatio()
    window_color = header.palette().color(QPalette.ColorRole.Window)
    inside = image.pixelColor(int(header.width() * ratio) - 3, int(header.height() * ratio) // 2)
    line = image.pixelColor(int(header.width() * ratio) - 3, int(header.height() * ratio) - 1)
    assert inside != window_color and line not in (inside, window_color)

    def on_screen(widget):
        return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())

    place = area._group_of('xy')
    area.peek(place, hold=60)
    qapp.processEvents()
    assert on_screen(place.strip) == on_screen(header)        # the title covers it exactly
    gear, title_gear = view.settings_button, place.settings_button
    assert on_screen(title_gear).center().y() == on_screen(gear).center().y()
    area.peek(None)


def _plotted_area(plot):
    """Where the plot draws its data, in the plot widget."""
    return plot.mapFromScene(plot.getPlotItem().vb.sceneBoundingRect()).boundingRect()


def _point_at(plot, inside: bool) -> None:
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication
    where = QPointF(20, 20)
    QApplication.sendEvent(plot.viewport(), QEnterEvent(where, where, where) if inside
                           else QEvent(QEvent.Type.Leave))


def test_the_colour_scale_is_a_thin_bar_inside_the_plot(view, qapp):
    # It takes no row: the plot reaches the foot of the view, and the bar
    # lies along the plotted area's right edge, high at its top.
    from gui.xy_view import _SCALE_COLORS
    view.resize(600, 400)
    view.show()
    view.choose('color', 'C')
    qapp.processEvents()
    plot, scale = view.plot, view.scale
    assert scale.parentWidget() is plot and scale.isVisible()
    assert plot.geometry().bottom() == view.height() - 1
    area, bar = _plotted_area(plot), scale.geometry()
    assert area.contains(bar)
    assert area.right() - bar.right() <= 8 and bar.top() - area.top() <= 8
    assert bar.height() >= area.height() - 16 and bar.width() < area.width() / 4
    assert scale.ends() == ('100', '60')

    def distance(one, other):
        return sum(abs(a - b) for a, b in zip(one.getRgb()[:3], other.getRgb()[:3]))

    from PySide6.QtGui import QColor
    image = scale.grab().toImage()
    ratio = image.devicePixelRatio()
    middle = int(scale.bar_rect().center().x() * ratio)
    top = image.pixelColor(middle, 1)
    foot = image.pixelColor(middle, int(scale.height() * ratio) - 2)
    assert distance(top, QColor(_SCALE_COLORS[-1])) <= 30
    assert distance(foot, QColor(_SCALE_COLORS[0])) <= 30
    # The points under it are still clicked: it lets the pointer through.
    from PySide6.QtCore import Qt
    assert scale.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

