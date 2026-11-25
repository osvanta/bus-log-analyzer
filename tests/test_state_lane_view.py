# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""State lanes in the window: their rows in Stacked and MultiStack, the
strip under the Multi-Axis plot, clicks, the menu and configurations."""
from unittest.mock import Mock

import numpy as np
import pytest

from core.signal_store import SignalSeries

GEAR = {0: 'P', 1: 'R', 2: 'N', 3: 'D'}
T = np.arange(0.0, 600.0, 1.0)
GEAR_V = np.select([T < 20, T < 95, T < 103, T < 240, T < 256, T < 540],
                   [0, 3, 2, 3, 1, 3], 0).astype(float)
SPEED_V = np.where(GEAR_V == 3, 3000 + 1000 * np.sin(T / 9), 0.0)
BRAKE_V = ((T % 37) < 3).astype(float)


def _series(name, vs, labels=None, unit=''):
    series = SignalSeries(channel=1, message_name='Msg', message_id=0x100,
                          signal_name=name, unit=unit,
                          timestamps=T.copy(), values=np.asarray(vs, dtype=float))
    if labels is not None:
        series.raw_values = [labels[int(v)] for v in vs]
        series.has_labels = True
    return series


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w.resize(1500, 850)
    w.show()
    panel = w.plot_panel
    panel.add_series('Speed', _series('Speed', SPEED_V, unit='rpm'))
    panel.add_series('Gear', _series('Gear', GEAR_V, GEAR))
    panel.add_series('Brake', _series('Brake', BRAKE_V))
    panel.add_series('Torque', _series('Torque', SPEED_V / 100, unit='Nm'))
    w.btn_cursor1.setChecked(True)
    yield w
    w.close()
    qapp.processEvents()


def _settle(qapp, rounds=15):
    for _ in range(rounds):
        qapp.processEvents()


def _mode(window, qapp, mode):
    window._set_plot_type(mode)
    window.plot_panel.fit_to_window()
    _settle(qapp)
    return window.plot_panel


# ── Stacked and MultiStack ───────────────────────────────────────────────


def test_in_stacked_a_state_signal_is_a_short_row_of_its_own_in_list_order(window, qapp):
    from gui.state_lane import LANE_HEIGHT
    panel = _mode(window, qapp, 'stacked')
    assert panel._stacked_row_keys == [['Speed'], ['Gear'], ['Brake'], ['Torque']]
    assert panel._stacked_lane_rows == [None, 'Gear', 'Brake', None]
    assert panel.lane_keys() == ['Gear', 'Brake']
    heights = [plot.vb.height() for plot in panel._stacked_plots]
    assert heights[1] == pytest.approx(LANE_HEIGHT, abs=1)
    assert heights[2] == pytest.approx(LANE_HEIGHT, abs=1)
    # The curves share the rest alike.
    assert heights[0] == pytest.approx(heights[3], rel=0.05)
    assert heights[0] > 4 * LANE_HEIGHT
    assert panel._items['Gear'].curve is None and panel._items['Speed'].curve is not None


def test_a_lane_moves_with_its_row_in_the_table(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    order = ['Gear', 'Speed', 'Brake', 'Torque']
    panel._items = {key: panel._items[key] for key in order}
    panel._rebuild_curves(preserve_selection=False)
    _settle(qapp)
    assert panel._stacked_lane_rows == ['Gear', None, 'Brake', None]


def test_a_plot_without_lanes_lays_out_as_before_lanes_came_and_went(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    panel.set_series_show_as(['Gear', 'Brake'], 'line')
    _settle(qapp)
    heights = [round(plot.vb.height(), 1) for plot in panel._stacked_plots]
    assert panel.lane_keys() == []
    # Every curve row as tall as the next, as without any lane, and none
    # asked for a height of its own.
    assert heights[0] == heights[1] == heights[2]
    assert heights[3] <= heights[0]
    assert all(plot.preferredHeight() < 1000 for plot in panel._stacked_plots)


def test_in_multistack_a_stacks_lanes_sit_under_its_curves_in_one_section(window, qapp):
    panel = _mode(window, qapp, 'multistack')
    items = panel._items
    for key in ('Speed', 'Gear', 'Brake'):
        items[key].multistack_id = 0
    items['Torque'].multistack_id = 1
    panel._rebuild_curves(preserve_selection=False)
    _settle(qapp)
    assert panel._stacked_row_keys == [['Speed'], ['Gear'], ['Brake'], ['Torque']]
    assert panel._stacked_stack_ids == [0, 0, 0, 1]
    assert panel._stacked_lane_rows == [None, 'Gear', 'Brake', None]
    panel._place_stack_sections()
    sections = panel.stack_sections().sections()
    assert len(sections) == 2               # one per stack, its lanes included


def _cursor_names(panel):
    """Where each cursor's name is written in Stacked: (row, shown whole)."""
    names = {}
    for name, lines in (('C1', panel._stacked_c1_lines), ('C2', panel._stacked_c2_lines)):
        for row, line in enumerate(lines):
            label = getattr(line, 'label', None)
            if label is not None and label.toPlainText():
                view = panel._stacked_plots[row].vb.sceneBoundingRect()
                names.setdefault(name, []).append(
                    (row, view.contains(label.sceneBoundingRect())))
    return names


def _ending_with_lanes(window, qapp, mode):
    panel = _mode(window, qapp, mode)
    if mode == 'multistack':
        # One stack: its lanes sit under its curves.
        for plotted in panel._items.values():
            plotted.multistack_id = 0
    else:
        panel._items = {key: panel._items[key] for key in ('Speed', 'Torque', 'Gear', 'Brake')}
    panel._rebuild_curves(preserve_selection=False)
    window.btn_cursor2.setChecked(True)
    _settle(qapp)
    return panel


@pytest.mark.parametrize('mode', ['stacked', 'multistack'])
def test_the_cursors_names_go_on_the_lowest_row_of_curves_not_on_a_lane(window, qapp, mode):
    # A lane is too short for the name: written there, it was cut off.
    panel = _ending_with_lanes(window, qapp, mode)
    assert panel._stacked_lane_rows[-2:] == ['Gear', 'Brake']
    curves = max(row for row, lane in enumerate(panel._stacked_lane_rows) if lane is None)
    assert _cursor_names(panel) == {'C1': [(curves, True)], 'C2': [(curves, True)]}


def test_with_only_lanes_no_row_writes_a_name_it_would_cut_off(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    for key in ('Speed', 'Torque'):
        panel._items[key].visible = False
    panel._rebuild_curves(preserve_selection=False)
    window.btn_cursor2.setChecked(True)
    _settle(qapp)
    assert panel._stacked_lane_rows == ['Gear', 'Brake']
    assert _cursor_names(panel) == {}
    # The lines still run through every lane, and the band above the plot
    # names both cursors.
    assert len(panel._stacked_c1_lines) == len(panel._stacked_c2_lines) == 2
    assert all(line.isVisible() for line in panel._stacked_c1_lines)
    assert panel.cursor_label.text().startswith('C1')
    assert 'C2' in panel.cursor_label.text()


def test_without_lanes_the_cursors_names_stay_on_the_bottom_row(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    panel.set_series_show_as(['Gear', 'Brake'], 'line')
    window.btn_cursor2.setChecked(True)
    _settle(qapp)
    last = len(panel._stacked_plots) - 1
    assert {name: [row for row, _whole in rows]
            for name, rows in _cursor_names(panel).items()} == {'C1': [last], 'C2': [last]}


def test_a_lanes_row_keeps_no_y_range_for_its_stack(window, qapp):
    panel = _mode(window, qapp, 'multistack')
    for key in ('Speed', 'Gear'):
        panel._items[key].multistack_id = 0
    panel._rebuild_curves(preserve_selection=False)
    _settle(qapp)
    speed_plot = panel._stacked_plots[0]
    speed_plot.setYRange(1000.0, 2000.0, padding=0)
    panel._rebuild_curves(preserve_selection=False)
    _settle(qapp)
    low, high = panel._stacked_plots[0].vb.viewRange()[1]
    assert (low, high) == pytest.approx((1000.0, 2000.0))


# ── Under the Multi-Axis plot ────────────────────────────────────────────


@pytest.mark.parametrize('mode', ['multi_axis', 'normal'])
def test_lanes_sit_in_a_strip_under_the_plot_lined_up_with_its_time(window, qapp, mode):
    panel = _mode(window, qapp, mode)
    assert panel.lane_strip.isVisible()
    assert len(panel._strip_plots) == 2
    main = panel.plot.plotItem.vb
    main_left = panel.plot.mapToGlobal(panel.plot.mapFromScene(main.sceneBoundingRect().topLeft()))
    for plot in panel._strip_plots:
        left = panel.lane_strip.mapToGlobal(
            panel.lane_strip.mapFromScene(plot.vb.sceneBoundingRect().topLeft()))
        assert abs(left.x() - main_left.x()) <= 1
        assert plot.vb.viewRange()[0] == pytest.approx(main.viewRange()[0])
    # The time axis is under the lanes, not under the plot.
    assert not panel.plot.getAxis('bottom').style['showValues']
    assert panel._strip_plots[-1].getAxis('bottom').style['showValues']
    assert not panel._strip_plots[0].getAxis('bottom').style['showValues']
    # Zooming the plot moves the lanes with it.
    panel.show_time_range(100.0, 200.0)
    _settle(qapp)
    for plot in panel._strip_plots:
        assert plot.vb.viewRange()[0] == pytest.approx([100.0, 200.0], abs=1e-6)


def test_without_lanes_the_strip_goes_and_the_plot_has_its_time_axis_back(window, qapp):
    panel = _mode(window, qapp, 'multi_axis')
    panel.set_series_show_as(['Gear', 'Brake'], 'line')
    _settle(qapp)
    assert not panel.lane_strip.isVisible() and panel._strip_plots == []
    assert panel.plot.getAxis('bottom').style['showValues']
    assert panel._items['Gear'].curve is not None


def test_lanes_take_no_y_axis_and_no_part_in_the_curves_range(window, qapp):
    panel = _mode(window, qapp, 'multi_axis')
    # Speed (rpm) and Torque (Nm): two axes; the lanes add none.
    assert len(panel._extra_axes) == 1
    # One axis for all: a curve far from 0 is fitted alone, not stretched
    # down to the gear's 0 to 3.
    panel.add_series('Volt', _series('Volt', 300 + 100 * np.sin(T / 30), unit='V'))
    panel.remove_series_many(['Speed', 'Torque'])
    panel = _mode(window, qapp, 'normal')
    low, high = panel.plot.plotItem.vb.viewRange()[1]
    assert low > 150 and high < 450
    panel.show_time_range(0.0, 100.0)
    panel.fit_vertical()
    low, high = panel.plot.plotItem.vb.viewRange()[1]
    assert low > 150 and high < 450


@pytest.mark.parametrize('mode', ['multi_axis', 'normal'])
def test_a_lane_gives_no_axis_title_and_names_no_plot(window, qapp, mode):
    panel = window.plot_panel
    panel._current_key = 'Gear'                      # a lane is the current signal
    panel = _mode(window, qapp, mode)
    panel._set_axis_label('Gear')
    _settle(qapp)
    for plot in panel._strip_plots:
        assert not plot.getAxis('left').label.isVisible()
    title = panel.plot.getAxis('left').labelText
    assert 'Gear' not in title and 'Brake' not in title
    if mode == 'normal':
        assert 'Speed' in title                      # the first curve names it


def test_a_lane_axis_takes_no_title_whoever_gives_it_one(qapp):
    from gui.state_lane import LaneAxis
    axis = LaneAxis('Gear', '#ffffff', 85)
    axis.setLabel('Gear (no unit)', color='#ff0000')
    assert not axis.label.isVisible()
    assert axis.shown_name() == 'Gear'


def test_the_cursors_run_through_the_strip(window, qapp):
    panel = _mode(window, qapp, 'multi_axis')
    panel.move_cursor1(123.0)
    window.btn_cursor2.setChecked(True)
    panel.move_cursor2(321.0)
    _settle(qapp)
    for c1, c2 in panel._strip_lines:
        assert c1.isVisible() and c1.value() == pytest.approx(123.0)
        assert c2.isVisible() and c2.value() == pytest.approx(321.0)
    window.btn_cursor2.setChecked(False)
    _settle(qapp)
    assert not any(c2.isVisible() for _c1, c2 in panel._strip_lines)


# ── Drawing ──────────────────────────────────────────────────────────────


def test_each_stretch_is_drawn_in_its_values_colour_with_its_label(window, qapp):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor
    from core.state_lane import PALETTE
    panel = _mode(window, qapp, 'stacked')
    image = panel.glw.grab().toImage()
    item = panel._items['Gear'].lane
    written = dict(item.written())
    assert written.get(1) == 'D' and written.get(0) == 'P'
    # Below the label in the first D (20 s to 95 s): the colour for 3.
    plot = panel._stacked_plots[1]
    (_x0, _x1), (y0, y1) = plot.vb.viewRange()
    scene = plot.vb.mapViewToScene(QPointF(80.0, y0 + (y1 - y0) * 0.5))
    point = panel.glw.mapFromScene(scene)
    ratio = image.devicePixelRatio()
    color = image.pixelColor(int(point.x() * ratio), int(point.y() * ratio))
    expected = QColor(PALETTE[3])
    assert abs(color.red() - expected.red()) < 8 and abs(color.green() - expected.green()) < 8


def test_a_label_too_wide_for_its_stretch_is_left_out(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    panel.show_time_range(-2700.0, 3300.0)          # N's 8 s is about a pixel wide
    _settle(qapp)
    window.grab()
    lane = panel.lane_of('Gear')
    written = dict(panel._items['Gear'].lane.written())
    assert lane.at(99.0) not in written
    assert written.get(lane.at(300.0)) == 'D'


def test_a_lanes_height_does_not_move_with_the_mouse(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    assert panel._stacked_plots[1].vb.state['mouseEnabled'] == [True, False]


def test_a_click_beside_every_stretch_is_left_to_the_plot(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    item = panel._items['Gear'].lane
    clicked = []
    item.on_click = lambda i, t: clicked.append((i, t))
    from PySide6.QtCore import QPointF, Qt
    for t, hit in ((-50.0, False), (150.0, True)):
        event = Mock()
        event.button.return_value = Qt.MouseButton.LeftButton
        event.pos.return_value = QPointF(t, 0.5)
        item.mouseClickEvent(event)
        assert event.accept.called is hit and event.ignore.called is not hit
    assert clicked == [(item.lane.at(150.0), 150.0)]
    right = Mock()
    right.button.return_value = Qt.MouseButton.RightButton
    item.mouseClickEvent(right)
    assert right.ignore.called and len(clicked) == 1


def test_a_bit_lane_draws_only_its_ones(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    panel.show_time_range(0.0, 40.0)                        # a press is wide here
    _settle(qapp)
    window.grab()
    assert panel._items['Gear'].lane.written()              # room for labels
    assert panel._items['Brake'].lane.written() == []       # but no 0 or 1 written
    panel.show_time_range(-10.0, 610.0)
    _settle(qapp)
    window.grab()
    drawn = panel._items['Brake'].lane.drawn()
    lane = panel.lane_of('Brake')
    assert drawn and all(index >= 0 and lane.values[index] == 1.0 for index, _l, _r in drawn)
    assert len(drawn) == int(np.sum(np.diff(np.concatenate(([0.0], BRAKE_V))) == 1))


def test_zoomed_out_fast_changes_are_one_hatched_block(window, qapp):
    panel = window.plot_panel
    ts = np.arange(0.0, 600.0, 0.01)
    fast = ((ts > 100) & (ts < 110) & (np.arange(ts.size) % 2 == 1)).astype(float)
    series = SignalSeries(channel=1, message_name='Msg', message_id=0x100, signal_name='Fast',
                          unit='', timestamps=ts, values=fast)
    panel.add_series('Fast', series)
    panel = _mode(window, qapp, 'stacked')
    window.grab()
    drawn = panel._items['Fast'].lane.drawn()
    assert [index for index, _l, _r in drawn] == [-1]
    panel.show_time_range(100.0, 100.5)
    _settle(qapp)
    window.grab()
    assert all(index >= 0 for index, _l, _r in panel._items['Fast'].lane.drawn())


# ── Clicking a stretch ───────────────────────────────────────────────────


def test_a_clicked_stretch_shades_its_state_and_steps_cursor_1_through_it(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    lane = panel.lane_of('Gear')
    second_d = lane.at(150.0)
    panel._on_lane_clicked('Gear', second_d, 150.0)
    assert panel.time_spans() == [(20.0, 95.0), (103.0, 240.0), (256.0, 540.0)]
    assert panel.lane_pick() == ('Gear', 3.0)
    assert panel.v_line.value() == pytest.approx(103.0)      # the clicked one's start
    panel._on_lane_clicked('Gear', second_d, 150.0)
    assert panel.v_line.value() == pytest.approx(256.0)      # then the next
    panel._on_lane_clicked('Gear', second_d, 150.0)
    assert panel.v_line.value() == pytest.approx(20.0)       # and round again
    panel._on_lane_clicked('Gear', lane.at(250.0), 250.0)    # R: a new state
    assert panel.time_spans() == [(240.0, 256.0)] and panel.v_line.value() == pytest.approx(240.0)
    # Every row is shaded.
    assert len(panel._span_items) == len(panel._stacked_plots)


def test_a_lane_click_moves_cursor_1_once(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    panel._on_lane_clicked('Gear', panel.lane_of('Gear').at(150.0), 150.0)
    assert panel._lane_click_claimed
    emitted = []
    panel.plotAreaClicked.connect(emitted.append)
    event = Mock()
    panel._emit_plot_area_click(event, [panel._stacked_plots[1].vb])
    assert emitted == [] and not panel._lane_click_claimed
    assert panel.v_line.value() == pytest.approx(103.0)


def test_shading_from_elsewhere_ends_the_lane_pick(window, qapp):
    panel = _mode(window, qapp, 'normal')
    panel._on_lane_clicked('Gear', panel.lane_of('Gear').at(150.0), 150.0)
    assert len(panel._span_items) == 1 + len(panel._strip_plots)   # plot and lanes
    panel.set_time_spans([])
    assert panel.lane_pick() is None and panel._span_items == []


def test_a_real_click_on_a_lane_reaches_it(window, qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    panel = _mode(window, qapp, 'stacked')
    plot = panel._stacked_plots[1]
    scene = plot.vb.mapViewToScene(QPointF(150.0, 0.5))
    point = panel.glw.mapFromScene(scene)
    QTest.mouseClick(panel.glw.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(point.x(), point.y()))
    _settle(qapp)
    assert panel.lane_pick() == ('Gear', 3.0)
    assert panel.v_line.value() == pytest.approx(103.0)


# ── The menu and configurations ──────────────────────────────────────────


def test_show_as_in_the_signal_menu_switches_lane_and_line(window, qapp):
    from PySide6.QtWidgets import QMenu
    panel = _mode(window, qapp, 'stacked')
    menu = QMenu()
    show = panel._add_show_as_menu(menu, ['Gear'])
    labels = [action.text() for action in show.actions()]
    assert labels == ['Automatic', 'State lane', 'Line']
    assert [action.isChecked() for action in show.actions()] == [True, False, False]
    show.actions()[2].trigger()
    _settle(qapp)
    assert panel._items['Gear'].show_as == 'line' and 'Gear' not in panel.lane_keys()
    menu = QMenu()
    show = panel._add_show_as_menu(menu, ['Speed'])
    show.actions()[1].trigger()
    _settle(qapp)
    assert 'Speed' in panel.lane_keys()
    # No signal chosen: nothing to switch.
    menu = QMenu()
    assert not panel._add_show_as_menu(menu, []).isEnabled()


def test_clear_shaded_time_is_offered_while_time_is_shaded(window, qapp):
    from PySide6.QtWidgets import QMenu
    panel = _mode(window, qapp, 'stacked')
    menu = QMenu()
    panel._add_show_as_menu(menu, ['Gear'])
    assert 'Clear shaded time' not in [a.text() for a in menu.actions()]
    panel._on_lane_clicked('Gear', 1, 50.0)
    menu = QMenu()
    panel._add_show_as_menu(menu, ['Gear'])
    (clear,) = [a for a in menu.actions() if a.text() == 'Clear shaded time']
    clear.trigger()
    assert panel.time_spans() == []


def test_the_choice_is_saved_with_the_tab_and_restored(window, qapp):
    from gui.plot_tabs import describe_tab
    panel = _mode(window, qapp, 'stacked')
    panel.set_series_show_as(['Gear'], 'line')
    panel.set_series_show_as(['Speed'], 'lane')
    tab = describe_tab(panel, 'Tab 1')
    shown = {entry['key']: entry['show_as'] for entry in tab['signals']}
    assert shown == {'Speed': 'lane', 'Gear': 'line', 'Brake': '', 'Torque': ''}
    for key in ('Speed', 'Gear'):
        panel._items[key].show_as = ''
    window._plot_saved_signals(tab)
    _settle(qapp)
    assert panel._items['Speed'].show_as == 'lane' and panel._items['Gear'].show_as == 'line'
    assert set(panel.lane_keys()) == {'Speed', 'Brake'}


def test_a_signal_moved_to_another_tab_keeps_its_choice(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    panel.set_series_show_as(['Gear'], 'line')
    from gui.plot_widget import PlotPanel
    other = PlotPanel()
    try:
        other.take_series([panel._items['Gear']])
        assert other._items['Gear'].show_as == 'line'
    finally:
        other.deleteLater()


def test_the_table_shows_the_state_at_cursor_1(window, qapp):
    panel = _mode(window, qapp, 'stacked')
    panel.move_cursor1(250.0)
    row = panel._row_lookup['Gear']
    assert panel.table.item(row, 2).text() == 'R'


def test_the_background_colour_reaches_the_strip(window, qapp):
    panel = _mode(window, qapp, 'normal')
    panel.set_background_color('#ffffff')
    assert panel.lane_strip.backgroundBrush().color().name() == '#ffffff'
    c1, _c2 = panel._strip_lines[0]
    assert c1.pen.color().name() == '#000000'
