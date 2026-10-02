# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A left click on the plot places Cursor 1 at the clicked time, switching it on
first if it is off, so reading values needs no trip to the Cursor 1 button.

The clicks are real mouse events sent through Qt to pyqtgraph's scene, in the
stacked layout (the default) and the single-plot layout.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest


@pytest.fixture()
def window(qapp, monkeypatch):
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


def _plot_ramp(qapp, window):
    """0–10 s at 10 ms; the value is ten times the time."""
    from core.signal_store import SignalSeries

    ts = array.array('d', (i * 0.01 for i in range(1001)))
    vs = array.array('d', (i * 0.1 for i in range(1001)))
    window.plot_panel.add_series(
        'Speed', SignalSeries(None, 'Msg', 1, 'Speed', 'km/h', ts, vs))
    window.plot_panel.fit_to_window()
    qapp.processEvents()


def _view_and_box(panel):
    if panel._stacked_mode:
        return panel.glw, panel._stacked_plots[0].vb
    return panel.plot, panel.plot.plotItem.vb


def _click(qapp, panel, t, button=None, x_offset_px=0):
    """Click the plot at time t, half way up the view."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    view, vb = _view_and_box(panel)
    y = sum(vb.viewRange()[1]) / 2
    point = view.mapFromScene(vb.mapViewToScene(QPointF(t, y)))
    point.setX(point.x() + x_offset_px)
    QTest.mouseClick(view.viewport(), button or Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, point)
    qapp.processEvents()


def _one_pixel_in_seconds(panel):
    _, vb = _view_and_box(panel)
    x0, x1 = vb.viewRange()[0]
    return (x1 - x0) / vb.width()


def _cursor1_cell(panel):
    return panel.table.item(panel._row_lookup['Speed'], 2).text()


def _assert_cursor1_at(panel, t):
    tolerance = 2 * _one_pixel_in_seconds(panel)
    assert panel.v_line.value() == pytest.approx(t, abs=tolerance)
    for line in panel._stacked_c1_lines:
        assert line.value() == panel.v_line.value()
    # The table shows the sample nearest the cursor: value = 10 × time.
    nearest = round(panel.v_line.value() / 0.01) * 0.01
    assert _cursor1_cell(panel) == f'{nearest * 10:.3f}'


def test_click_switches_cursor1_on_and_places_it(qapp, window):
    _plot_ramp(qapp, window)
    panel = window.plot_panel
    assert panel._stacked_mode
    assert not window.btn_cursor1.isChecked()

    _click(qapp, panel, 6.0)

    assert window.btn_cursor1.isChecked()
    assert window.btn_cursor1.text() == 'Cursor 1: ON'
    _assert_cursor1_at(panel, 6.0)


def test_click_moves_cursor1_that_is_already_on(qapp, window):
    _plot_ramp(qapp, window)
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)

    _click(qapp, panel, 2.0)

    assert window.btn_cursor1.isChecked()
    _assert_cursor1_at(panel, 2.0)


def test_click_places_cursor1_in_the_single_plot_layout(qapp, window):
    _plot_ramp(qapp, window)
    window.btn_stacked.setChecked(False)
    qapp.processEvents()
    panel = window.plot_panel
    assert not panel._stacked_mode

    _click(qapp, panel, 7.5)

    assert window.btn_cursor1.isChecked()
    assert panel.v_line.scene() is panel.plot.scene()
    _assert_cursor1_at(panel, 7.5)


def test_click_on_cursor2_line_does_not_pull_cursor1_onto_it(qapp, window):
    _plot_ramp(qapp, window)
    panel = window.plot_panel
    _click(qapp, panel, 2.0)
    window.btn_cursor2.setChecked(True)
    qapp.processEvents()
    cursor1 = panel.v_line.value()
    cursor2 = panel.v_line2.value()
    assert abs(cursor2 - cursor1) > 50 * _one_pixel_in_seconds(panel)

    _click(qapp, panel, cursor2)

    assert panel.v_line.value() == cursor1


def test_right_click_does_not_place_cursor1(qapp, window, monkeypatch):
    from PySide6.QtCore import Qt

    _plot_ramp(qapp, window)
    panel = window.plot_panel
    monkeypatch.setattr(panel, '_show_signal_menu', Mock())  # modal menu

    _click(qapp, panel, 6.0, button=Qt.MouseButton.RightButton)

    assert not window.btn_cursor1.isChecked()


def test_click_beside_the_plot_area_is_ignored(qapp, window):
    _plot_ramp(qapp, window)
    panel = window.plot_panel
    _, vb = _view_and_box(panel)
    x0 = vb.viewRange()[0][0]

    # 20 px left of the plot's left edge lands on the Y axis.
    _click(qapp, panel, x0, x_offset_px=-20)

    assert not window.btn_cursor1.isChecked()


def test_click_with_nothing_plotted_leaves_cursor1_off(qapp, window):
    window.btn_stacked.setChecked(False)
    qapp.processEvents()

    _click(qapp, window.plot_panel, 0.5)

    assert not window.btn_cursor1.isChecked()
