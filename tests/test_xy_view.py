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
    assert len(_drawn(view)[0]) == 5
    view.whole_box.setChecked(False)
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
    state = view.state()
    assert state == {'x': 'T', 'y': 'C', 'color': 'S',
                     'whole_measurement': True, 'equal_scale': True}

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
    assert view_box.state['aspectLocked']
    view.equal_box.setChecked(False)
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


def test_the_xy_button_puts_the_xy_plot_right_of_the_plot(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    qapp.processEvents()
    assert not panel.xy_view.isVisible()
    window.btn_xy.setChecked(True)
    qapp.processEvents()
    assert panel.xy_view_active()
    assert panel.view_stack.isVisible() and panel.xy_view.isVisible()
    plots, xy = panel.side_stack.sizes()
    assert plots > xy > 0
    assert panel.xy_view.x_box.count() == 3
    assert (panel.xy_view.chosen('x'), panel.xy_view.chosen('y')) == ('Speed', 'Torque')
    window.btn_xy.setChecked(False)
    qapp.processEvents()
    assert not panel.xy_view_active() and not panel.xy_view.isVisible()
    assert panel.view_stack.isVisible()


def test_the_xy_plot_and_the_table_can_both_be_shown(window, qapp):
    panel = window.plot_panel
    window.resize(1600, 800)
    window.show()
    qapp.processEvents()
    window.btn_table.setChecked(True)
    window.btn_xy.setChecked(True)
    qapp.processEvents()
    assert panel.view_stack.isVisible() and panel.tabular.isVisible()
    assert panel.xy_view.isVisible()
    assert all(size > 0 for size in panel.area_stack.sizes() + panel.side_stack.sizes())


def test_the_xy_plot_keeps_the_width_it_was_dragged_to(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    qapp.processEvents()
    window.btn_xy.setChecked(True)
    qapp.processEvents()
    total = sum(panel.side_stack.sizes())
    panel.side_stack.setSizes([total - 320, 320])
    window.btn_xy.setChecked(False)
    window.btn_xy.setChecked(True)
    qapp.processEvents()
    assert panel.side_stack.sizes()[1] == 320


def test_it_follows_the_time_the_time_plot_shows(window, qapp):
    panel = window.plot_panel
    window.btn_xy.setChecked(True)
    panel.show_time_range(0.9, 3.1)
    qapp.processEvents()
    panel.xy_view._redraw()
    assert list(panel.xy_view.scatter.getData()[0]) == [20, 30, 40]


def test_plotting_another_signal_offers_it(window, qapp):
    panel = window.plot_panel
    window.btn_xy.setChecked(True)
    panel.add_series('Load', _series('Load', _TIMES, [0, 0, 1, 1, 2]))
    panel._rebuild_curves(preserve_selection=True)
    assert panel.xy_view.x_box.count() == 4
    assert panel.xy_view.color_box.count() == 5        # and None


def test_a_click_on_a_point_places_cursor_1(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    window.btn_xy.setChecked(True)
    qapp.processEvents()
    assert panel.xy_view.pick(40.0, 4.0)
    assert window.btn_cursor1.isChecked()
    assert panel.v_line.value() == pytest.approx(3.0)
    assert panel.xy_view.mark_position(1) == (40.0, 4.0)


def test_the_cursors_moving_move_their_marks(window):
    panel = window.plot_panel
    window.btn_xy.setChecked(True)
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
    window.btn_xy.setChecked(True)
    assert panel.xy_view.mark_position(1) == (30.0, 3.0)


def test_the_background_colour_is_the_plots(window):
    panel = window.plot_panel
    panel.set_background_color('#ffffff')
    assert panel.xy_view.plot.backgroundBrush().color().name() == '#ffffff'
    assert panel.xy_view._foreground != '#ffffff'


def test_each_tab_has_its_own_xy_plot_and_the_button_follows(window, qapp):
    first = window.plot_panel
    window.btn_xy.setChecked(True)
    window.plot_tabs.add_tab()
    second = window.plot_panel
    assert second is not first
    assert not window.btn_xy.isChecked() and not second.xy_view_active()
    window.plot_tabs.bar.setCurrentIndex(0)
    assert window.btn_xy.isChecked() and first.xy_view_active()


def test_a_tab_is_described_with_its_xy_plot_and_set_up_from_it(window):
    from gui.plot_tabs import describe_tab
    panel = window.plot_panel
    window.btn_xy.setChecked(True)
    panel.xy_view.choose('x', 'Torque')
    panel.xy_view.choose('y', 'Temp')
    panel.xy_view.choose('color', 'Speed')
    saved = describe_tab(panel, 'Plot 1')
    assert saved['xy'] == {'shown': True, 'x': 'Torque', 'y': 'Temp', 'color': 'Speed',
                           'whole_measurement': False, 'equal_scale': False}

    window.plot_tabs.add_tab()
    other = window.plot_panel
    window._apply_tab_settings(saved)                 # as a configuration does,
    assert window.btn_xy.isChecked() and other.xy_view_active()
    for key in ('Speed', 'Torque', 'Temp'):           # then its signals
        other.add_series(key, panel._items[key].series)
    other._rebuild_curves(preserve_selection=True)
    assert list(other.xy_view.scatter.getData()[0]) == [1, 2, 3, 4, 5]
    assert other.xy_view.color_box.currentText().startswith('Speed')


def test_a_configuration_without_an_xy_plot_leaves_it_as_it_is(window):
    window.btn_xy.setChecked(True)
    window._apply_tab_settings({'plot_type': 'stacked'})
    assert window.plot_panel.xy_view_active()
    window._apply_tab_settings({'xy': {'shown': False}})
    assert not window.plot_panel.xy_view_active() and not window.btn_xy.isChecked()


def test_a_renamed_signal_stays_chosen(window):
    from gui.plot_tabs import SavedTabs
    panel = window.plot_panel
    window.btn_xy.setChecked(True)
    panel.rename_series_key('Torque', 'Torque2')
    panel._rebuild_curves(preserve_selection=True)
    assert panel.xy_view.chosen('y') == 'Torque2'
    assert list(panel.xy_view.scatter.getData()[1]) == [1, 2, 3, 4, 5]

    saved = SavedTabs([{'xy': {'x': 'A', 'y': 'B', 'color': 'A'}, 'signals': []}])
    saved.rename_signal('A', 'A2')
    assert saved.tabs[0]['xy'] == {'x': 'A2', 'y': 'B', 'color': 'A2'}
