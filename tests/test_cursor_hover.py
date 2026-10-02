# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Under the mouse a cursor line turns red and thicker, and Cursor 2 keeps its
dashes; a cursor that is switched off does not light up at all.

The mouse moves are real events sent through Qt to pyqtgraph's scene, in the
stacked layout (the default) and the single-plot layout.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPointF, Qt

WHITE = '#ffffff'   # a cursor on the default black background
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
    """Move the mouse to time t, half way up one plot row.

    The move goes straight to the plot. QTest.mouseMove moves the global
    cursor instead, so a window an earlier test left open can take it.
    """
    import pyqtgraph as pg
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    # pyqtgraph drops mouse moves that follow the previous one too closely.
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


def _shown_lines(panel):
    if panel._stacked_mode:
        return {'C1': panel._stacked_c1_lines, 'C2': panel._stacked_c2_lines}
    return {'C1': [panel.v_line], 'C2': [panel.v_line2]}


def _drawn_with(line):
    pen = line.currentPen
    return pen.color().name(), pen.widthF(), pen.style()


@pytest.mark.parametrize('stacked', [True, False], ids=['stacked', 'single-plot'])
@pytest.mark.parametrize('cursor, t, style', [
    ('C1', 3.0, Qt.PenStyle.SolidLine),
    ('C2', 7.0, Qt.PenStyle.DashLine),
], ids=['cursor1', 'cursor2'])
def test_cursor_under_the_mouse_turns_red_and_thicker(
        qapp, window, stacked, cursor, t, style):
    window.btn_stacked.setChecked(stacked)
    _plot(qapp, window, 'Speed', 'Torque')
    _cursors_on(qapp, window)
    panel = window.plot_panel
    line = _shown_lines(panel)[cursor][0]
    color, width, _ = _drawn_with(line)
    assert color == WHITE

    _hover(qapp, panel, t)

    hover_color, hover_width, hover_style = _drawn_with(line)
    assert hover_color == RED
    # pyqtgraph's own hover pen is red too, but only rounds the width up.
    assert hover_width >= 2 * width
    assert hover_style == style   # Cursor 2 stays dashed

    _hover(qapp, panel, 5.0)   # away from both cursors
    assert _drawn_with(line)[0] == WHITE


def test_hovering_one_row_lights_the_whole_stacked_cursor(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque', 'Temp')
    _cursors_on(qapp, window)
    panel = window.plot_panel

    _hover(qapp, panel, 3.0, row=1)

    assert [_drawn_with(l)[0] for l in panel._stacked_c1_lines] == [RED] * 3
    assert [_drawn_with(l)[0] for l in panel._stacked_c2_lines] == [WHITE] * 3

    _hover(qapp, panel, 5.0, row=1)

    assert [_drawn_with(l)[0] for l in panel._stacked_c1_lines] == [WHITE] * 3


def test_moving_between_rows_along_the_cursor_keeps_it_lit(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque', 'Temp')
    _cursors_on(qapp, window)
    panel = window.plot_panel

    for row in (0, 1, 2, 1):
        _hover(qapp, panel, 3.0, row=row)
        assert panel._stacked_c1_lines[row].mouseHovering
        assert [_drawn_with(l)[0] for l in panel._stacked_c1_lines] == [RED] * 3


def test_every_cursor_line_carries_the_red_hover_pen(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque', 'Temp')
    _cursors_on(qapp, window)
    panel = window.plot_panel

    lines = [panel.v_line, panel.v_line2, *panel._stacked_c1_lines, *panel._stacked_c2_lines]
    assert len(lines) == 2 + 2 * 3
    for line in lines:
        assert line.hoverPen.color().name() == RED
        assert line.hoverPen.widthF() >= 2 * line.pen.widthF()


def test_cursor2_that_is_off_does_not_light_up(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque')
    _cursors_on(qapp, window)
    panel = window.plot_panel
    window.btn_cursor2.setChecked(False)
    qapp.processEvents()
    lines = panel._stacked_c2_lines
    assert lines and not any(line.isVisible() for line in lines)

    _hover(qapp, panel, 7.0)

    for line in lines:
        assert not line.mouseHovering
        vb = line.getViewBox()
        at_line = vb.mapViewToScene(QPointF(7.0, sum(vb.viewRange()[1]) / 2))
        assert line not in vb.scene().items(at_line)


def test_cursor2_turned_back_on_reappears_where_expected(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque')
    _cursors_on(qapp, window)
    panel = window.plot_panel
    window.btn_cursor2.setChecked(False)
    window.btn_cursor2.setChecked(True)
    qapp.processEvents()

    lines = panel._stacked_c2_lines
    assert lines and all(line.isVisible() for line in lines)
    assert all(line.value() == panel.v_line2.value() for line in lines)
    assert _drawn_with(lines[0]) == (WHITE, 1.5, Qt.PenStyle.DashLine)
