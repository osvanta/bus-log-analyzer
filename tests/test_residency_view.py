# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The Residency view of the XY pane: how long X and Y spent together in each
cell of a grid, or a third signal's mean there. A click on a cell shades
the times it covers on the plot and sends Cursor 1 through them; the grid
copies and exports as a table.

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
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float))


_TIMES = [0.0, 1.0, 2.0, 3.0, 4.0]


def _signals():
    return [
        ('S', 'Speed', _series('Speed', _TIMES, [10, 10, 30, 30, 30], unit='rpm'), '#ff0000'),
        ('T', 'Torque', _series('Torque', [0.0, 2.0, 4.0], [1, 5, 5], unit='Nm'), '#00ff00'),
        ('F', 'Fuel', _series('Fuel', _TIMES, [100, 200, 300, 400, 400], unit='g/h'), '#0000ff'),
    ]


@pytest.fixture()
def view(qapp):
    from gui.xy_view import RESIDENCY, XYView
    v = XYView()
    v.resize(600, 500)
    v.set_signals(_signals())
    v.bins_edit.setText('20')
    v.y_bins_edit.setText('4')
    v.set_view(RESIDENCY)
    yield v
    v.close()


def _colors(view):
    return {(i, j): color.name() for i, column in enumerate(view.cells.colors)
            for j, color in enumerate(column) if color is not None}


def _texts(view):
    return {(i, j): text for i, column in enumerate(view.cells.texts)
            for j, text in enumerate(column) if text}


# ── The view on its own ──────────────────────────────────────────────────


def test_the_switch_shows_the_residency_map_with_its_choices(qapp):
    from gui.xy_view import RESIDENCY, XYView
    v = XYView()
    v.set_signals(_signals())
    v.show()
    qapp.processEvents()
    v.residency_button.click()
    qapp.processEvents()
    assert v.view() == RESIDENCY and v.residency_button.isChecked()
    assert v.plots.currentWidget() is v.res_plot
    assert v.y_box.isVisible() and v.color_box.isVisible()
    assert v._role_labels['color'].text() == 'Mean of' and v._role_labels['x'].text() == 'X'
    assert v._bins_label.text() == 'X bins'
    assert v.histogram_options.isVisible() and v.residency_options.isVisible()
    assert not v.equal_box.isVisible() and v.readout.isVisible()
    v.histogram_button.click()
    qapp.processEvents()
    assert not v.residency_options.isVisible() and v._bins_label.text() == 'Bins'
    assert v._role_labels['color'].text() == 'Colour'
    v.close()


def test_each_cell_shows_its_share_of_time(view):
    from gui.xy_view import scale_color
    assert list(view.grid.x_edges) == [0, 20, 40] and list(view.grid.y_edges) == [0, 4, 8]
    assert _texts(view) == {(0, 0): '50', (1, 1): '50'}
    assert _colors(view) == {(0, 0): scale_color(1.0).name(), (1, 1): scale_color(1.0).name()}
    assert view.res_plot.getPlotItem().getAxis('bottom').labelText == 'Speed [rpm]'
    assert view.res_plot.getPlotItem().getAxis('left').labelText == 'Torque [Nm]'
    assert view.summary.text().startswith('4.00 s of 4.00 s counted (gap limit 10 s / 20 s)')
    assert '2 of 4 cells used' in view.summary.text()
    assert not view.scale.isHidden() and view.scale.text() == '0 … 50  % of time'


def test_seconds_samples_and_a_log_colour(view):
    from gui.xy_view import SAMPLES, SECONDS, scale_color
    view.show_box.setCurrentIndex(view.show_box.findData(SECONDS))
    view.bins_edit.setText('10')                   # 10 for 2 s; 30 for 2 s
    view.set_signals([('S', 'Speed', _series('Speed', [0, 3, 4], [10, 30, 30]), '#f00'),
                      _signals()[1]])
    view.choose('x', 'S')
    view.choose('y', 'T')
    # (10, 1) from 0 s to 2 s, (10, 5) to 3 s, (30, 5) to 4 s.
    assert _texts(view) == {(0, 0): '2', (0, 1): '1', (2, 1): '1'}
    linear = _colors(view)[(0, 1)]
    assert linear == scale_color(0.5).name()
    view.log_box.setChecked(True)
    assert _colors(view)[(0, 1)] == scale_color(np.log10(1 + 999 * 0.5) / 3).name()
    assert 'log colour' in view.scale.text()
    view.show_box.setCurrentIndex(view.show_box.findData(SAMPLES))
    view._redraw()
    # Each of the three pieces starts at a sample of one or the other.
    assert _texts(view) == {(0, 0): '1', (0, 1): '1', (2, 1): '1'}


def test_a_third_signal_shows_its_mean_in_each_cell(view):
    from gui.xy_view import scale_color
    view.log_box.setChecked(True)
    view.choose('color', 'F')
    assert _texts(view) == {(0, 0): '150', (1, 1): '350'}
    assert _colors(view) == {(0, 0): scale_color(0.0).name(), (1, 1): scale_color(1.0).name()}
    assert view.scale.text() == '150 … 350  Fuel [g/h], mean'
    assert not view.log_box.isEnabled()                         # no log for a mean
    assert view.cell_text((1, 1)).endswith('mean Fuel [g/h] 350')
    view.choose('color', None)
    assert view.log_box.isEnabled() and _texts(view)[(0, 0)] == '50'


def test_numbers_are_written_only_where_they_fit(view, qapp):
    view.show()
    qapp.processEvents()
    view.res_plot.grab()
    assert sorted(cell[:3] for cell in view.cells.written()) == [(0, 0, '50'), (1, 1, '50')]
    view.resize(260, 200)
    view.bins_edit.setText('0.5')                               # 80 narrow columns
    view._redraw()
    qapp.processEvents()
    view.res_plot.grab()
    assert view.cells.written() == []


def test_numbers_are_dark_on_light_cells_and_light_on_dark(view, qapp):
    from gui.xy_view import SECONDS
    view.show_box.setCurrentIndex(view.show_box.findData(SECONDS))
    view.bins_edit.setText('10')
    view.set_signals([('S', 'Speed', _series('Speed', [0, 3, 4], [10, 30, 30]), '#f00'),
                      _signals()[1]])
    view.choose('x', 'S')
    view.show()
    qapp.processEvents()
    view.res_plot.grab()
    ink = {(i, j): color for i, j, _text, color in view.cells.written()}
    assert ink[(0, 0)] == '#ffffff'          # the deepest red
    assert ink[(0, 1)] == '#111111'          # half way: a light green-yellow


def test_in_samples_a_cell_with_time_but_no_sample_is_left_empty(view):
    from gui.xy_view import SAMPLES
    view.show_box.setCurrentIndex(view.show_box.findData(SAMPLES))
    view.bins_edit.setText('0, 20, 40')
    view.y_bins_edit.setText('0, 4, 8')
    view.set_time_range((1.5, 4.0))          # (10, 1) is held into it, not sampled
    view._redraw()
    assert view.grid.times[0, 0] == 0.5 and view.grid.counts[0, 0] == 0
    assert (0, 0) not in _colors(view) and (0, 0) not in _texts(view)
    assert _texts(view) == {(1, 1): '2'}


def test_a_value_on_the_last_typed_edge_is_in_the_last_cell(qapp):
    from gui.xy_view import RESIDENCY, XYView
    v = XYView()
    v.set_signals([('S', 'Speed', _series('Speed', [0, 1, 2], [10, 30, 30]), '#f00'),
                   ('T', 'Torque', _series('Torque', [0, 2], [1, 1]), '#0f0')])
    v.bins_edit.setText('0, 20, 30')
    v.y_bins_edit.setText('0, 4')
    v.set_view(RESIDENCY)
    shaded = []
    v.spansChosen.connect(shaded.append)
    v.pick_cell((1, 0))
    assert shaded == [[(1.0, 2.0)]]
    v.close()


def test_the_outline_goes_when_the_picked_cell_is_no_longer_drawn(view):
    view.pick_cell((1, 1))
    assert view.cells.picked == (1, 1)
    view.bins_edit.setText('5')
    view._redraw()
    assert view.cells.picked is None
    view.bins_edit.setText('20')
    view._redraw()
    assert view.cells.picked == (1, 1)       # the same cell again


def test_typed_edges_and_a_mistake_is_said(view):
    view.bins_edit.setText('0, 20')
    view.y_bins_edit.setText('nonsense')
    view._redraw()
    assert list(view.grid.x_edges) == [0, 20]
    assert view.summary.text().startswith('Y bins not understood, shown automatic')
    assert '50.0 % outside the grid' in view.summary.text()


def test_pointing_at_a_cell_reads_it(view):
    assert view.cell_at_value(25, 6) == (1, 1) and view.cell_at_value(50, 6) is None
    assert view.cell_text((1, 1)) == (
        '20 to 40 rpm · 4 to 8 Nm · 50.0 % of time · 2.00 s · 2 samples')


def test_clicking_a_cell_shades_its_times_and_steps_cursor_1_through_them(qapp):
    from gui.xy_view import RESIDENCY, XYView
    v = XYView()
    v.set_signals([('S', 'Speed', _series('Speed', [0, 1, 2, 3, 4, 5], [10, 30, 10, 30, 10, 10]),
                    '#f00'),
                   ('T', 'Torque', _series('Torque', [0, 5], [1, 1]), '#0f0')])
    v.bins_edit.setText('20')
    v.y_bins_edit.setText('4')
    v.set_view(RESIDENCY)
    shaded, asked = [], []
    v.spansChosen.connect(shaded.append)
    v.timeClicked.connect(asked.append)
    v.pick_cell((0, 0))
    assert shaded == [[(0.0, 1.0), (2.0, 3.0), (4.0, 5.0)]] and asked == [0.0]
    assert v.cells.picked == (0, 0)
    assert 'stretch 1 of 3' in v.readout.text()
    v.pick_cell((0, 0))
    v.pick_cell((0, 0))
    assert asked == [0.0, 2.0, 4.0] and len(shaded) == 1
    v.pick_cell((1, 0))                                         # the last column: 30 itself
    assert shaded[-1] == [(1.0, 2.0), (3.0, 4.0)]
    v.pick_cell(None)
    assert shaded[-1] == [] and v.cells.picked is None
    v.close()


def test_choosing_another_y_clears_the_shading(view):
    shaded = []
    view.spansChosen.connect(shaded.append)
    view.pick_cell((0, 0))
    view.choose('y', 'F')
    assert shaded[-1] == [] and view.picked_spans() == []


def test_the_cursors_are_rings_where_the_signals_are(view):
    assert view.res_mark_position(1) is None
    view.set_cursors(2.5, 0.5)
    assert view.res_mark_position(1) == (30.0, 5.0)
    assert view.res_mark_position(2) == (10.0, 1.0)
    view.set_cursors(None, None)
    assert view.res_mark_position(1) is None and view.res_mark_position(2) is None


def test_the_table_copies_and_exports(view, qapp, tmp_path):
    from PySide6.QtWidgets import QApplication
    view.copy_table()
    assert QApplication.clipboard().text().splitlines() == [
        'Torque [Nm] \\ Speed [rpm]\t0 to 20\t20 to 40',
        '4 to 8\t\t50',
        '0 to 4\t50\t',
    ]
    path = tmp_path / 'grid.csv'
    view.export_csv(str(path))
    assert path.read_text(encoding='utf-8').splitlines()[1] == '4 to 8,,50'


def test_export_asks_where_and_says_where_it_saved(view, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    path = tmp_path / 'map.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(path), '')))
    view._export_clicked()
    assert path.exists() and view.readout.text() == f'Saved {path}.'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(tmp_path / 'no' / 'x.csv'), '')))
    view._export_clicked()
    assert view.readout.text().startswith('Not saved:')


def test_with_one_signal_it_says_what_to_do(qapp):
    from gui.xy_view import RESIDENCY, XYView
    v = XYView()
    v.set_signals(_signals()[:1])
    v.set_view(RESIDENCY)
    assert 'Plot two signals' in v.summary.text() and v.scale.isHidden()
    assert v.table() == ''
    v.pick_cell((0, 0))                                         # nothing to pick: no error
    v.close()


def test_the_residency_choices_are_kept_and_taken_back(view):
    from gui.xy_view import RESIDENCY, XYView
    view.log_box.setChecked(True)
    state = view.state()
    assert (state['view'], state['bins'], state['y_bins'], state['log_color']) == (
        'residency', '20', '4', True)
    other = XYView()
    other.restore(state)
    other.set_signals(_signals())
    assert other.view() == RESIDENCY and other.residency_button.isChecked()
    assert other.y_bins_edit.text() == '4' and other.log_box.isChecked()
    assert _texts(other) == {(0, 0): '50', (1, 1): '50'}


def test_the_map_has_the_plots_background(view):
    view.set_colors('#123456', '#fedcba')
    assert view.res_plot.backgroundBrush().color().name() == '#123456'
    assert view.res_marks[0].opts['pen'].color().name() == '#fedcba'


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
    for key, _name, series, _color in _signals()[:2]:
        panel.add_series(key, series)
    panel._rebuild_curves(preserve_selection=True)
    w._set_plot_type('normal')
    w.btn_xy.setChecked(True)
    view = panel.xy_view
    view.bins_edit.setText('20')
    view.y_bins_edit.setText('4')
    view.whole_box.setChecked(True)
    view.residency_button.click()
    yield w
    w.close()
    qapp.processEvents()


def test_a_clicked_cell_is_shaded_on_the_plot_and_cursor_1_goes_there(window):
    panel = window.plot_panel
    assert panel.xy_view.cell_text((1, 1)).startswith('20 to 40')
    panel.xy_view.pick_cell((1, 1))
    assert panel.time_spans() == [(2.0, 4.0)]
    assert window.btn_cursor1.isChecked() and panel.v_line.value() == pytest.approx(2.0)
    assert panel.xy_view.res_mark_position(1) == (30.0, 5.0)
    window.btn_xy.setChecked(False)
    assert panel.time_spans() == []


def test_a_tab_keeps_its_residency_map(window):
    from gui.plot_tabs import describe_tab
    saved = describe_tab(window.plot_panel, 'Plot 1')
    assert saved['xy']['view'] == 'residency' and saved['xy']['y_bins'] == '4'
    window.plot_tabs.add_tab()
    other = window.plot_panel
    window._apply_tab_settings(saved)
    assert other.xy_view_active() and other.xy_view.view() == 'residency'
