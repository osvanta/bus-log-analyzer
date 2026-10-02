# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Cursor 1 is on the plot exactly when its button says so.

Bug reproduced: the Cursor 1 button starts off, but its state was set before
the button's toggled signal was connected, so the plot panel kept Cursor 1 on.
A C1 line sat at t=0 and the Cursor 1 column showed values while the button
read off. In the single-plot layout every rebuild re-added the line even after
the user switched Cursor 1 off, and in the stacked layout an "off" line was
only drawn with a transparent pen: it still turned red under the mouse and
could be dragged.
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


def _plot(qapp, window, *names):
    """0–10 s at 10 ms; each value is ten times the time."""
    from core.signal_store import SignalSeries

    for name in names:
        ts = array.array('d', (i * 0.01 for i in range(1001)))
        vs = array.array('d', (i * 0.1 for i in range(1001)))
        window.plot_panel.add_series(
            name, SignalSeries(None, 'Msg', 1, name, 'km/h', ts, vs))
    window.plot_panel.fit_to_window()
    qapp.processEvents()


def _cursor1_lines(panel):
    return [panel.v_line, *panel._stacked_c1_lines]


def _assert_cursor1_off(panel):
    assert not any(line.isVisible() for line in _cursor1_lines(panel))
    assert panel.table.isColumnHidden(2)
    assert not panel.cursor_label.text().startswith('C1')


def _assert_cursor1_on(panel):
    assert panel._stacked_c1_lines if panel._stacked_mode else True
    shown = panel._stacked_c1_lines if panel._stacked_mode else [panel.v_line]
    assert all(line.isVisible() for line in shown)
    assert not panel.table.isColumnHidden(2)
    assert panel.cursor_label.text().startswith('C1')


def test_cursor1_is_off_at_startup_like_its_button(qapp, window):
    assert not window.btn_cursor1.isChecked()

    _plot(qapp, window, 'Speed', 'Torque')

    assert window.plot_panel._stacked_mode
    _assert_cursor1_off(window.plot_panel)
    assert window.plot_panel.cursor_label.text() == 'Click the plot to place Cursor 1'


def test_single_plot_rebuilds_keep_cursor1_off(qapp, window):
    window.btn_stacked.setChecked(False)
    _plot(qapp, window, 'Speed')
    _plot(qapp, window, 'Torque')   # another rebuild

    assert not window.plot_panel._stacked_mode
    _assert_cursor1_off(window.plot_panel)


@pytest.mark.parametrize('stacked', [True, False], ids=['stacked', 'single-plot'])
def test_button_switches_cursor1_on_and_off(qapp, window, stacked):
    window.btn_stacked.setChecked(stacked)
    _plot(qapp, window, 'Speed', 'Torque')
    panel = window.plot_panel

    window.btn_cursor1.setChecked(True)
    qapp.processEvents()
    _assert_cursor1_on(panel)
    row = panel._row_lookup['Speed']
    assert panel.table.item(row, 2).text()

    window.btn_cursor1.setChecked(False)
    qapp.processEvents()
    _assert_cursor1_off(panel)


def test_switching_layout_keeps_cursor1_state(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque')
    window.btn_stacked.setChecked(False)
    qapp.processEvents()
    _assert_cursor1_off(window.plot_panel)

    window.btn_cursor1.setChecked(True)
    window.btn_stacked.setChecked(True)
    qapp.processEvents()
    _assert_cursor1_on(window.plot_panel)


def test_cursor1_that_is_off_cannot_be_hovered_or_dragged(qapp, window):
    from PySide6.QtCore import QPointF

    _plot(qapp, window, 'Speed', 'Torque')
    panel = window.plot_panel
    panel.move_cursor1(5.0)   # mid-view, where the mouse can reach it

    def lines_under_mouse():
        found = []
        for line in panel._stacked_c1_lines:
            vb = line.getViewBox()
            at_line = vb.mapViewToScene(QPointF(5.0, sum(vb.viewRange()[1]) / 2))
            found += [item for item in vb.scene().items(at_line) if item is line]
        return found

    # The scene offers only visible items to hover and drag.
    assert lines_under_mouse() == []

    window.btn_cursor1.setChecked(True)
    panel.move_cursor1(5.0)
    qapp.processEvents()
    assert len(lines_under_mouse()) == len(panel._stacked_c1_lines)


def test_readout_without_cursor1_shows_no_time_delta(qapp, window):
    _plot(qapp, window, 'Speed')
    panel = window.plot_panel

    window.btn_cursor2.setChecked(True)
    qapp.processEvents()
    assert panel.cursor_label.text().startswith('C2: t=')
    assert 'ΔT' not in panel.cursor_label.text()

    window.btn_cursor1.setChecked(True)
    qapp.processEvents()
    assert 'ΔT' in panel.cursor_label.text()


def test_both_cursors_give_one_readout_line(qapp, window):
    # The time delta is in the cursor line; no second line repeats it.
    from PySide6.QtWidgets import QLabel

    _plot(qapp, window, 'Speed')
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)
    qapp.processEvents()

    readouts = [label for label in panel.findChildren(QLabel)
                if label.isVisible() and 'C2' in label.text()]
    assert readouts == [panel.cursor_label]
    assert panel.cursor_label.text().startswith('C1: t=')
    assert 'C2: t=' in panel.cursor_label.text()
    assert 'ΔT=' in panel.cursor_label.text()


def test_click_on_the_plot_brings_the_cursor1_column_back(qapp, window):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    _plot(qapp, window, 'Speed')
    panel = window.plot_panel
    vb = panel._stacked_plots[0].vb
    point = panel.glw.mapFromScene(vb.mapViewToScene(QPointF(4.0, sum(vb.viewRange()[1]) / 2)))

    QTest.mouseClick(panel.glw.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, point)
    qapp.processEvents()

    _assert_cursor1_on(panel)


def test_loading_a_config_with_cursor1_on_shows_it(qapp, window, tmp_path, monkeypatch):
    import json
    from PySide6.QtWidgets import QFileDialog

    config = tmp_path / 'cursor.json'
    config.write_text(json.dumps({'cursor1': True}), encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName',
                        lambda *a, **k: (str(config), 'JSON Files (*.json)'))
    _plot(qapp, window, 'Speed')

    window.load_configuration()
    qapp.processEvents()

    assert window.btn_cursor1.isChecked()
    assert not window.plot_panel.table.isColumnHidden(2)
