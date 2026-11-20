# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The Histogram view of the XY pane: how long one plotted signal spent at each
value. A click on a bar shades the times it covers on the plot and sends
Cursor 1 to the first of them, then the next.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest


def _series(name, ts, vs, unit='u', labels=None):
    from core.signal_store import SignalSeries
    series = SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit=unit,
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float))
    if labels is not None:
        series.raw_values = list(labels)
        series.has_labels = True
    return series


_TIMES = [0.0, 1.0, 2.0, 3.0, 4.0]


def _signals():
    return [
        ('S', 'Speed', _series('Speed', _TIMES, [10, 20, 30, 40, 50], unit='km/h'), '#ff0000'),
        ('T', 'Torque', _series('Torque', _TIMES, [1, 2, 3, 4, 5], unit='Nm'), '#00ff00'),
    ]


@pytest.fixture()
def view(qapp):
    from gui.xy_view import HISTOGRAM, XYView
    v = XYView()
    v.resize(500, 400)
    v.set_signals(_signals())
    v.bins_edit.setText('10')
    v.set_view(HISTOGRAM)
    yield v
    v.close()


def _heights(view):
    return [float(h) for h in view.bar_item.opts['height']]


# ── The view on its own ──────────────────────────────────────────────────


def test_it_opens_in_scatter_and_the_switch_shows_the_histogram(qapp):
    from gui.xy_view import HISTOGRAM, SCATTER, XYView
    v = XYView()
    v.set_signals(_signals())
    v.show()
    qapp.processEvents()
    assert v.view() == SCATTER and v.scatter_button.isChecked()
    assert v.plots.currentWidget() is v.plot and not v.histogram_options.isVisible()
    v.histogram_button.click()
    qapp.processEvents()
    assert v.view() == HISTOGRAM and v.histogram_button.isChecked()
    assert v.plots.currentWidget() is v.hist_plot
    assert v.histogram_options.isVisible() and v.readout.isVisible()
    # One signal only: Y, Colour and Equal scale go, X is the Signal.
    assert not v.y_box.isVisible() and not v.color_box.isVisible()
    assert not v.equal_box.isVisible() and v.whole_box.isVisible()
    assert v._role_labels['x'].text() == 'Signal'
    v.scatter_button.click()
    qapp.processEvents()
    assert v.view() == SCATTER and v.y_box.isVisible() and v._role_labels['x'].text() == 'X'
    v.close()


def test_the_bars_are_the_share_of_time_at_each_value(view):
    # Each value holds a second; the last sample ends the recording.
    assert list(view.bars.edges) == [10, 20, 30, 40, 50, 60]
    assert _heights(view) == [25.0, 25.0, 25.0, 25.0, 0.0]
    assert view.hist_plot.getPlotItem().getAxis('left').labelText == '% of time'
    assert view.hist_plot.getPlotItem().getAxis('bottom').labelText == 'Speed [km/h]'
    assert view.summary.text().startswith('4.00 s of 4.00 s counted (gap limit 10 s)')
    assert 'mean 25' in view.summary.text()


def test_the_show_box_gives_seconds_or_samples(view):
    from gui.xy_view import SAMPLES, SECONDS
    view.show_box.setCurrentIndex(view.show_box.findData(SECONDS))
    view._redraw()
    assert _heights(view) == [1.0, 1.0, 1.0, 1.0, 0.0]
    assert view.hist_plot.getPlotItem().getAxis('left').labelText == 'Time [s]'
    view.show_box.setCurrentIndex(view.show_box.findData(SAMPLES))
    view._redraw()
    assert _heights(view) == [1.0, 1.0, 1.0, 1.0, 1.0]


def test_bins_typed_as_edges_and_a_mistake_is_said(view):
    view.bins_edit.setText('0, 25, 35')
    view._redraw()
    assert list(view.bars.edges) == [0, 25, 35]          # 40 held a second above them
    assert '25.0 % above the edges' in view.summary.text()
    view.bins_edit.setText('ten')
    view._redraw()
    assert view.summary.text().startswith('Bins not understood')
    assert view.bars.edges[1] - view.bars.edges[0] == 2.0       # automatic


def test_a_typed_gap_limit_is_used(view):
    series = _series('Gappy', [0.0, 1.0, 20.0, 21.0], [10, 10, 20, 20])
    view.set_signals([('G', 'Gappy', series, '#ffffff')])
    view.choose('x', 'G')
    assert 'gap limit 10 s' in view.summary.text()
    assert view.bars.counted == 2.0                             # 19 s silence left out
    view.gap_edit.setText('30')
    view._redraw()
    assert view.bars.counted == 21.0 and 'gap limit 30 s' in view.summary.text()


def test_it_follows_the_time_shown_unless_the_whole_is_asked_for(view):
    view.set_time_range((1.0, 3.0))
    view._redraw()
    # 20 from 1 s to 2 s, 30 from 2 s to 3 s; 40 begins as the range ends.
    assert view.bars.edges[0] == 20.0
    assert _heights(view) == [50.0, 50.0, 0.0]
    view.whole_box.setChecked(True)
    assert _heights(view)[:4] == [25.0, 25.0, 25.0, 25.0]


def test_pointing_at_a_bar_reads_it(view):
    assert view.bar_at_value(25.0) == 1 and view.bar_at_value(5.0) is None
    assert view.bar_text(1) == '20 to 30 km/h · 25.0 % of time · 1.00 s · 1 samples'


def test_clicking_a_bar_shades_its_times_and_steps_cursor_1_through_them(qapp):
    from gui.xy_view import HISTOGRAM, XYView
    v = XYView()
    series = _series('Speed', [0, 1, 2, 3, 4, 5], [12, 30, 15, 33, 18, 18])
    v.set_signals([('S', 'Speed', series, '#ff0000')])
    v.bins_edit.setText('10')
    v.set_view(HISTOGRAM)
    shaded, asked = [], []
    v.spansChosen.connect(shaded.append)
    v.timeClicked.connect(asked.append)
    v.pick_bar(0)                                        # 10 to 20
    assert shaded == [[(0.0, 1.0), (2.0, 3.0), (4.0, 5.0)]]
    assert asked == [0.0]
    assert 'stretch 1 of 3' in v.readout.text()
    assert v.picked_item.opts['x'] == [15.0]
    v.pick_bar(0)
    v.pick_bar(0)
    v.pick_bar(0)                                        # round to the first again
    assert asked == [0.0, 2.0, 4.0, 0.0] and len(shaded) == 1
    v.pick_bar(None)                                     # a click beside the bars
    assert shaded[-1] == [] and v.picked_spans() == []
    assert len(v.picked_item.opts['x']) == 0
    v.close()


def test_a_bar_with_no_time_shades_nothing(view):
    asked = []
    view.timeClicked.connect(asked.append)
    view.pick_bar(4)                                     # 50 only ends the recording
    assert view.picked_spans() == [] and asked == []


def test_choosing_another_signal_or_scatter_clears_the_shading(view):
    from gui.xy_view import SCATTER
    shaded = []
    view.spansChosen.connect(shaded.append)
    view.pick_bar(1)
    view.choose('x', 'T')
    assert shaded[-1] == []
    view.choose('x', 'S')
    view.pick_bar(1)
    view.set_view(SCATTER)
    assert shaded[-1] == [] and view.picked_spans() == []


def test_the_cursors_are_lines_at_the_values_held_then(view):
    assert view.cursor_line_value(1) is None and view.cursor_line_value(2) is None
    view.set_cursors(2.5, 4.0)
    assert view.cursor_line_value(1) == 30.0
    assert view.cursor_line_value(2) == 50.0
    view.set_cursors(None, None)
    assert view.cursor_line_value(1) is None


def test_a_signal_with_labels_has_a_named_bar_per_state(qapp):
    from gui.xy_view import HISTOGRAM, XYView
    v = XYView()
    series = _series('Mode', [0, 1, 3, 4], [0, 2, 0, 0], labels=['Off', 'Drive', 'Off', 'Off'])
    v.set_signals([('M', 'Mode', series, '#ff0000')])
    v.set_view(HISTOGRAM)
    assert v.bars.labels == ('Off', 'Drive')
    ticks = v.hist_plot.getPlotItem().getAxis('bottom')._tickLevels
    assert ticks == [[(0, 'Off'), (1, 'Drive')]]
    assert [float(h) for h in v.bar_item.opts['height']] == [50.0, 50.0]
    assert v.bar_text(1).startswith('Drive · 50.0 % of time')
    v.set_cursors(2.0, None)
    assert v.cursor_line_value(1) == 1.0                 # on the Drive bar
    shaded = []
    v.spansChosen.connect(shaded.append)
    v.pick_bar(0)
    assert shaded == [[(0.0, 1.0), (3.0, 4.0)]]
    v.close()


def test_without_a_signal_it_says_what_to_do(qapp):
    from gui.xy_view import HISTOGRAM, XYView
    v = XYView()
    v.set_view(HISTOGRAM)
    assert 'Plot a signal' in v.summary.text()
    v.pick_bar(0)                                        # nothing to pick: no error
    v.close()


def test_the_histogram_choices_are_kept_and_taken_back(view):
    from gui.xy_view import HISTOGRAM, SECONDS, XYView
    view.show_box.setCurrentIndex(view.show_box.findData(SECONDS))
    view.gap_edit.setText('5')
    state = view.state()
    assert (state['view'], state['bins'], state['show'], state['gap']) == (
        'histogram', '10', 'seconds', '5')
    other = XYView()
    other.restore(state)
    other.set_signals(_signals())
    assert other.view() == HISTOGRAM and other.histogram_button.isChecked()
    assert other.bins_edit.text() == '10' and other.gap_edit.text() == '5'
    assert other.show_box.currentData() == SECONDS
    assert [float(h) for h in other.bar_item.opts['height']] == [1.0, 1.0, 1.0, 1.0, 0.0]
    # A configuration from before the histogram opens in Scatter.
    older = XYView()
    older.restore({'x': 'S', 'y': 'T'})
    assert older.view() == 'scatter'


def test_the_histogram_has_the_plots_background(view):
    view.set_colors('#123456', '#fedcba')
    assert view.hist_plot.backgroundBrush().color().name() == '#123456'
    assert view.cursor_lines[0].pen.color().name() == '#fedcba'


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
    panel._rebuild_curves(preserve_selection=True)
    w._set_plot_type('normal')                   # one plot; stacked has its own test
    w.btn_xy.setChecked(True)
    view = panel.xy_view
    view.bins_edit.setText('10')
    view.whole_box.setChecked(True)
    view.histogram_button.click()
    yield w
    w.close()
    qapp.processEvents()


def _span_items(panel):
    return [item for _plot, item in panel._span_items]


def test_a_clicked_bar_is_shaded_on_the_plot_and_cursor_1_goes_there(window, qapp):
    panel = window.plot_panel
    panel.xy_view.pick_bar(2)                            # 30 to 40: held from 2 s to 3 s
    assert panel.time_spans() == [(2.0, 3.0)]
    items = _span_items(panel)
    assert len(items) == 1 and items[0].spans() == [(2.0, 3.0)]
    assert items[0].scene() is panel.plot.plotItem.scene()
    assert window.btn_cursor1.isChecked() and panel.v_line.value() == pytest.approx(2.0)
    assert panel.xy_view.cursor_line_value(1) == 30.0
    panel.xy_view.pick_bar(None)
    assert panel.time_spans() == [] and _span_items(panel) == []


def test_the_shading_is_drawn_under_the_curves_across_the_plot(window, qapp):
    from PySide6.QtCore import QPointF
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    panel.show_time_range(0.0, 4.0)
    qapp.processEvents()
    plot_item = panel.plot.plotItem
    before = panel.plot.grab().toImage()
    panel.xy_view.pick_bar(2)
    qapp.processEvents()
    after = panel.plot.grab().toImage()
    (item,) = _span_items(panel)
    assert item.zValue() < 0
    view_box = plot_item.vb
    (_x0, _x1), (y0, y1) = view_box.viewRange()
    ratio = after.devicePixelRatio()
    changed = []
    for t in (1.5, 2.5, 3.5):
        for fraction in (0.1, 0.9):
            scene = view_box.mapViewToScene(QPointF(t, y0 + (y1 - y0) * fraction))
            point = panel.plot.mapFromScene(scene)
            x, y = int(point.x() * ratio), int(point.y() * ratio)
            changed.append(before.pixelColor(x, y) != after.pixelColor(x, y))
    # Shaded top to bottom inside 2 s to 3 s, and nowhere outside it.
    assert changed == [False, False, True, True, False, False]


def test_stretches_apart_are_shaded_apart_and_thousands_draw(window, qapp):
    from PySide6.QtCore import QPointF
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    panel.show_time_range(0.0, 4.0)
    qapp.processEvents()
    view_box = panel.plot.plotItem.vb
    (_x0, _x1), (y0, y1) = view_box.viewRange()
    before = panel.plot.grab().toImage()
    panel.set_time_spans([(0.5, 1.0), (3.0, 3.5)])
    qapp.processEvents()
    after = panel.plot.grab().toImage()
    ratio = after.devicePixelRatio()

    def changed(t):
        # High up, clear of the curves (Torque runs along the bottom, Speed
        # is at 60 % of the height at 2 s).
        point = panel.plot.mapFromScene(view_box.mapViewToScene(QPointF(t, y0 + (y1 - y0) * 0.85)))
        x, y = int(point.x() * ratio), int(point.y() * ratio)
        return before.pixelColor(x, y) != after.pixelColor(x, y)

    assert [changed(t) for t in (0.75, 2.0, 3.25)] == [True, False, True]
    # Ten thousand 0.1 ms stretches: drawn, each at least a pixel wide.
    starts = np.arange(10_000) * 0.0004
    panel.set_time_spans([(s, s + 0.0001) for s in starts.tolist()])
    qapp.processEvents()
    panel.plot.grab()
    assert len(panel._span_items[0][1].spans()) == 10_000


def test_hiding_the_xy_pane_takes_the_shading_away(window):
    panel = window.plot_panel
    panel.xy_view.pick_bar(1)
    assert panel.time_spans()
    window.btn_xy.setChecked(False)
    assert panel.time_spans() == [] and _span_items(panel) == []


def test_the_shading_is_on_every_row_of_a_stacked_plot(window, qapp):
    panel = window.plot_panel
    panel.xy_view.pick_bar(1)
    assert len(panel._span_items) == 1
    window._set_plot_type('stacked')
    qapp.processEvents()
    assert panel._stacked_mode and len(panel._stacked_plots) == 2
    plots = [plot for plot, _item in panel._span_items]
    assert plots == list(panel._stacked_plots)
    assert all(item.spans() == [(1.0, 2.0)] for item in _span_items(panel))


def test_a_tab_keeps_its_histogram(window):
    from gui.plot_tabs import describe_tab
    panel = window.plot_panel
    saved = describe_tab(panel, 'Plot 1')
    assert saved['xy']['view'] == 'histogram' and saved['xy']['bins'] == '10'
    window.plot_tabs.add_tab()
    other = window.plot_panel
    window._apply_tab_settings(saved)
    assert other.xy_view_active() and other.xy_view.view() == 'histogram'
