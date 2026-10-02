# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A cursor contrasts with the plot background: black on a light background and
white on a dark one. That holds for both cursors, every row of the stacked
layout and the C1/C2 labels, also when the background changes after plotting.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPointF, Qt

BLACK = '#000000'
WHITE = '#ffffff'
RED = '#ff0000'


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
    from core.signal_store import SignalSeries

    for name in names:
        ts = array.array('d', (i * 0.01 for i in range(1001)))
        vs = array.array('d', (i * 0.1 for i in range(1001)))
        window.plot_panel.add_series(
            name, SignalSeries(None, 'Msg', 1, name, 'km/h', ts, vs))
    window.plot_panel.fit_to_window()
    qapp.processEvents()


def _cursors_on(qapp, window, c1=3.0, c2=7.0):
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)
    qapp.processEvents()
    panel.move_cursor1(c1)
    for line in (panel.v_line2, *panel._stacked_c2_lines):
        line.setPos(c2)
    qapp.processEvents()


def _hover(qapp, panel, t, row=0):
    """Move the mouse to time t, half way up one plot row (see test_cursor_hover)."""
    import pyqtgraph as pg
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    QTest.qWait(int(2000 / pg.getConfigOption('mouseRateLimit')) + 1)
    if panel._stacked_mode:
        view, vb = panel.glw, panel._stacked_plots[row].vb
    else:
        view, vb = panel.plot, panel.plot.plotItem.vb
    y = sum(vb.viewRange()[1]) / 2
    local = QPointF(view.mapFromScene(vb.mapViewToScene(QPointF(t, y))))
    viewport = view.viewport()
    move = QMouseEvent(QEvent.Type.MouseMove, local,
                       QPointF(viewport.mapToGlobal(local.toPoint())),
                       Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, move)
    qapp.processEvents()


def _cursor_lines(panel):
    """Every cursor line the current layout draws, Cursor 1's first."""
    if panel._stacked_mode:
        return [*panel._stacked_c1_lines, *panel._stacked_c2_lines]
    return [panel.v_line, panel.v_line2]


def _drawn_colors(lines):
    return [line.currentPen.color().name() for line in lines]


def _assert_cursors(panel, color):
    lines = _cursor_lines(panel)
    assert lines and all(line.isVisible() for line in lines)
    assert _drawn_colors(lines) == [color] * len(lines)
    assert [line.pen.color().name() for line in lines] == [color] * len(lines)
    assert [line.label.color.name() for line in lines] == [color] * len(lines)
    # The colour is all that changes: width and Cursor 2's dashes stay.
    styles = {line.pen.style() for line in lines}
    assert styles == {Qt.PenStyle.SolidLine, Qt.PenStyle.DashLine}
    assert {line.pen.widthF() for line in lines} == {1.5}


@pytest.mark.parametrize('stacked', [True, False], ids=['stacked', 'single-plot'])
def test_cursors_are_white_on_the_default_black_background(qapp, window, stacked):
    window.btn_stacked.setChecked(stacked)
    _plot(qapp, window, 'Speed', 'Torque')
    _cursors_on(qapp, window)

    assert window.plot_panel.background_color() == BLACK
    _assert_cursors(window.plot_panel, WHITE)


@pytest.mark.parametrize('background, cursor', [
    ('#ffffff', BLACK),
    ('#000000', WHITE),
    ('#d3d3d3', BLACK),   # light grey
    ('#ffffcc', BLACK),   # pale yellow
    ('#404040', WHITE),   # dark grey
    ('#000080', WHITE),   # navy
], ids=['white', 'black', 'light-grey', 'pale-yellow', 'dark-grey', 'navy'])
def test_choosing_a_background_recolours_the_cursors_on_screen(
        qapp, window, background, cursor):
    _plot(qapp, window, 'Speed', 'Torque', 'Temp')
    _cursors_on(qapp, window)
    panel = window.plot_panel

    panel.set_background_color('#808000' if background == '#000000' else '#000000')
    panel.set_background_color(background)
    qapp.processEvents()

    _assert_cursors(panel, cursor)
    # The table's checkboxes agree on which backgrounds are dark.
    assert panel._theme_colors()['check_bg'] == ('#2a2a2a' if cursor == WHITE else '#ffffff')


@pytest.mark.parametrize('stacked', [True, False], ids=['stacked', 'single-plot'])
def test_white_background_gives_black_cursors_in_either_layout(qapp, window, stacked):
    window.btn_stacked.setChecked(stacked)
    _plot(qapp, window, 'Speed', 'Torque')
    _cursors_on(qapp, window)

    window.plot_panel.set_background_color(WHITE)
    qapp.processEvents()

    _assert_cursors(window.plot_panel, BLACK)


def test_cursors_drawn_after_the_change_take_the_new_colour(qapp, window):
    panel = window.plot_panel
    panel.set_background_color(WHITE)      # before anything is plotted
    _plot(qapp, window, 'Speed', 'Torque')
    _cursors_on(qapp, window)              # stacked rows and Cursor 2 built now
    _assert_cursors(panel, BLACK)

    _plot(qapp, window, 'Temp')            # rows rebuilt
    _assert_cursors(panel, BLACK)

    window.btn_stacked.setChecked(False)   # single-plot cursors rebuilt
    qapp.processEvents()
    _assert_cursors(panel, BLACK)


def test_cursor2_switched_on_later_matches_the_background(qapp, window):
    window.btn_stacked.setChecked(False)
    _plot(qapp, window, 'Speed')
    window.btn_cursor1.setChecked(True)
    window.plot_panel.set_background_color(WHITE)   # while Cursor 2 is off

    window.btn_cursor2.setChecked(True)
    qapp.processEvents()

    _assert_cursors(window.plot_panel, BLACK)


def test_back_to_a_dark_background_turns_the_cursors_white_again(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque')
    _cursors_on(qapp, window)
    panel = window.plot_panel

    panel.set_background_color(WHITE)
    panel.set_background_color('#1e1e1e')
    qapp.processEvents()

    _assert_cursors(panel, WHITE)


@pytest.mark.parametrize('stacked', [True, False], ids=['stacked', 'single-plot'])
def test_hover_turns_red_then_returns_to_black(qapp, window, stacked):
    window.btn_stacked.setChecked(stacked)
    _plot(qapp, window, 'Speed', 'Torque')
    _cursors_on(qapp, window)
    panel = window.plot_panel
    panel.set_background_color(WHITE)
    qapp.processEvents()
    c1 = _cursor_lines(panel)[0]

    _hover(qapp, panel, 3.0)
    assert c1.currentPen.color().name() == RED
    assert c1.currentPen.widthF() >= 2 * 1.5

    _hover(qapp, panel, 5.0)
    assert c1.currentPen.color().name() == BLACK


def test_recolouring_under_the_mouse_keeps_the_whole_cursor_lit(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque', 'Temp')
    _cursors_on(qapp, window)
    panel = window.plot_panel
    _hover(qapp, panel, 3.0, row=1)

    panel.set_background_color(WHITE)

    assert _drawn_colors(panel._stacked_c1_lines) == [RED] * 3
    _hover(qapp, panel, 5.0, row=1)
    assert _drawn_colors(panel._stacked_c1_lines) == [BLACK] * 3


def test_a_saved_white_background_loads_with_black_cursors(qapp, window, tmp_path, monkeypatch):
    import json
    from PySide6.QtWidgets import QFileDialog

    config = tmp_path / 'white.json'
    config.write_text(json.dumps({'plot_background_color': WHITE, 'cursor1': True,
                                  'cursor2': True}), encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName',
                        lambda *a, **k: (str(config), 'JSON Files (*.json)'))
    _plot(qapp, window, 'Speed', 'Torque')

    window.load_configuration()
    qapp.processEvents()

    _assert_cursors(window.plot_panel, BLACK)
