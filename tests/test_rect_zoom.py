# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Rectangle Zoom: with its button on, a left drag on the plot draws a box, and
letting go zooms the plot to the box: the time range to its width, the
height to its height. The button sits right after Fit Vertical.

The drags are real mouse events sent through Qt to pyqtgraph's scene, in the
stacked layout (the default), the single-plot layout and Multi-Axis.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPointF, Qt


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


def _plot(qapp, window, *signals):
    """0–10 s at 10 ms; each signal is its scale times ten times the time."""
    from core.signal_store import SignalSeries

    for name, unit, scale in signals:
        ts = array.array('d', (i * 0.01 for i in range(1001)))
        vs = array.array('d', (i * 0.1 * scale for i in range(1001)))
        window.plot_panel.add_series(name, SignalSeries(None, 'Msg', 1, name, unit, ts, vs))
    window.plot_panel.fit_to_window()
    qapp.processEvents()


SPEED = ('Speed', 'km/h', 1.0)      # 0–100
TORQUE = ('Torque', 'km/h', 1.0)    # 0–100, Speed's unit: one axis with it
TEMP = ('Temp', 'degC', 0.5)        # 0–50, an axis of its own in Multi-Axis


def _view_and_box(panel, row=0):
    if panel._stacked_mode:
        return panel.glw, panel._stacked_plots[row].vb
    return panel.plot, panel.plot.plotItem.vb


def _at(panel, point, row=0) -> QPointF:
    """A (time, value) of one plot row, in the plot's viewport pixels."""
    view, vb = _view_and_box(panel, row)
    return QPointF(view.mapFromScene(vb.mapViewToScene(QPointF(*point))))


def _move(qapp, viewport, local, buttons):
    """Move the mouse straight to the plot. QTest.mouseMove moves the global
    cursor instead, so a window an earlier test left open can take it."""
    import pyqtgraph as pg
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    # pyqtgraph drops mouse moves that follow the previous one too closely.
    QTest.qWait(int(2000 / pg.getConfigOption('mouseRateLimit')) + 1)
    move = QMouseEvent(QEvent.Type.MouseMove, local,
                       QPointF(viewport.mapToGlobal(local.toPoint())),
                       Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, move)
    qapp.processEvents()


def _press_and_move(qapp, panel, start, end, row=0, button=Qt.MouseButton.LeftButton):
    """Press at start and move to end, in steps, each a (time, value) in one
    plot row; the button is still down."""
    from PySide6.QtTest import QTest

    view, _ = _view_and_box(panel, row)
    viewport = view.viewport()
    p0, p1 = _at(panel, start, row), _at(panel, end, row)
    QTest.mousePress(viewport, button, Qt.KeyboardModifier.NoModifier, p0.toPoint())
    for f in (0.25, 0.5, 0.75, 1.0):
        _move(qapp, viewport, p0 + (p1 - p0) * f, button)
    return viewport, p1


def _drag(qapp, panel, start, end, row=0, button=Qt.MouseButton.LeftButton):
    from PySide6.QtTest import QTest

    viewport, p1 = _press_and_move(qapp, panel, start, end, row, button)
    QTest.mouseRelease(viewport, button, Qt.KeyboardModifier.NoModifier, p1.toPoint())
    qapp.processEvents()


def _ranges(vb):
    (x0, x1), (y0, y1) = vb.viewRange()
    return [x0, x1], [y0, y1]


def _pixels(vb):
    """A view's size of two pixels, in its time and its values."""
    (x0, x1), (y0, y1) = vb.viewRange()
    return 2 * (x1 - x0) / vb.width(), 2 * (y1 - y0) / vb.height()


def test_the_button_is_a_switch_that_starts_off(window):
    # Its place, right after Fit Vertical, is checked in test_overflow_row.py.
    assert window.btn_zoom_rect.isCheckable()
    assert not window.btn_zoom_rect.isChecked()
    assert not window.plot_panel._rect_zoom


def test_a_box_in_a_stacked_row_zooms_the_time_and_that_row_only(qapp, window):
    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel
    row0, row1 = (p.vb for p in panel._stacked_plots)
    _, row1_y = _ranges(row1)
    tx, ty = _pixels(row0)
    window.btn_zoom_rect.setChecked(True)

    _drag(qapp, panel, (2.0, 60.0), (4.0, 20.0), row=0)

    x, y = _ranges(row0)
    assert x == pytest.approx([2.0, 4.0], abs=tx)
    assert y == pytest.approx([20.0, 60.0], abs=ty)
    # The rows share one time range; the other row keeps its height.
    assert _ranges(row1)[0] == pytest.approx(x)
    assert _ranges(row1)[1] == pytest.approx(row1_y)


def test_a_box_drawn_from_its_bottom_right_corner_zooms_the_same(qapp, window):
    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel
    row1 = panel._stacked_plots[1].vb
    tx, ty = _pixels(row1)
    window.btn_zoom_rect.setChecked(True)

    _drag(qapp, panel, (7.0, 30.0), (5.0, 70.0), row=1)

    x, y = _ranges(row1)
    assert x == pytest.approx([5.0, 7.0], abs=tx)
    assert y == pytest.approx([30.0, 70.0], abs=ty)


def test_a_box_zooms_the_single_plot(qapp, window):
    window.btn_stacked.setChecked(False)
    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel
    vb = panel.plot.plotItem.vb
    tx, ty = _pixels(vb)
    window.btn_zoom_rect.setChecked(True)

    _drag(qapp, panel, (2.0, 60.0), (4.0, 20.0))

    x, y = _ranges(vb)
    assert x == pytest.approx([2.0, 4.0], abs=tx)
    assert y == pytest.approx([20.0, 60.0], abs=ty)


def test_a_box_zooms_every_axis_in_multi_axis(qapp, window):
    window.btn_multi_axis.setChecked(True)
    _plot(qapp, window, SPEED, TEMP)
    panel = window.plot_panel
    main = panel.plot.plotItem.vb
    [(_, temp)] = panel._extra_axes
    tx, ty = _pixels(main)
    # Temp's axis zooms to its own values at the box's top and bottom.
    top, bottom = (temp.mapSceneToView(main.mapViewToScene(QPointF(2.0, v))).y()
                   for v in (60.0, 20.0))
    temp_ty = _pixels(temp)[1]
    window.btn_zoom_rect.setChecked(True)

    _drag(qapp, panel, (2.0, 60.0), (4.0, 20.0))

    x, y = _ranges(main)
    assert x == pytest.approx([2.0, 4.0], abs=tx)
    assert y == pytest.approx([20.0, 60.0], abs=ty)
    assert _ranges(temp)[0] == pytest.approx(x)
    assert _ranges(temp)[1] == pytest.approx([bottom, top], abs=temp_ty)
    assert bottom == pytest.approx(10.0, abs=temp_ty)   # half Speed's values
    assert top == pytest.approx(30.0, abs=temp_ty)


def test_the_box_shows_while_dragging_inside_its_row(qapp, window):
    from PySide6.QtTest import QTest

    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel
    row0 = panel._stacked_plots[0].vb
    _, (bottom, _) = _ranges(row0)
    window.btn_zoom_rect.setChecked(True)

    # Down into the next row: the box stops at the bottom of its own.
    viewport, end = _press_and_move(qapp, panel, (2.0, 60.0), (4.0, -60.0), row=0)

    box = row0._zoom_box
    assert box.isVisible()
    assert box.rect().bottom() == pytest.approx(row0.boundingRect().bottom())
    assert box.pen().color().name() == '#ffffff'   # the cursor's colour on black
    assert box.pen().style() == Qt.PenStyle.DashLine

    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, end.toPoint())
    qapp.processEvents()
    assert not box.isVisible()
    # Zoomed to the box as it stopped: down to the row's bottom.
    ty = _pixels(row0)[1]
    assert _ranges(row0)[1] == pytest.approx([bottom, 60.0], abs=ty)


def test_with_the_button_off_a_drag_pans(qapp, window):
    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel
    row0 = panel._stacked_plots[0].vb
    (x0, x1), _ = _ranges(row0)

    _drag(qapp, panel, (2.0, 60.0), (4.0, 20.0), row=0)

    (nx0, nx1), _ = _ranges(row0)
    assert nx1 - nx0 == pytest.approx(x1 - x0)   # the same span, moved
    assert nx0 == pytest.approx(x0 - 2.0, abs=_pixels(row0)[0])
    assert not row0._zoom_box.isVisible()


def test_turning_the_button_off_brings_the_pan_back(qapp, window):
    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel
    row0 = panel._stacked_plots[0].vb
    window.btn_zoom_rect.setChecked(True)
    window.btn_zoom_rect.setChecked(False)
    (x0, x1), _ = _ranges(row0)

    _drag(qapp, panel, (2.0, 60.0), (4.0, 20.0), row=0)

    (nx0, nx1), _ = _ranges(row0)
    assert nx1 - nx0 == pytest.approx(x1 - x0)


def test_a_box_too_flat_to_mean_anything_zooms_nowhere(qapp, window):
    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel
    row0 = panel._stacked_plots[0].vb
    before = _ranges(row0)
    window.btn_zoom_rect.setChecked(True)
    # Two pixels high: a slip of the mouse along the time axis.
    flat = row0.mapSceneToView(row0.mapViewToScene(QPointF(4.0, 50.0)) + QPointF(0, 2))

    _drag(qapp, panel, (2.0, 50.0), (4.0, flat.y()), row=0)

    assert _ranges(row0) == pytest.approx(before)


def test_a_click_still_places_cursor1(qapp, window):
    from PySide6.QtTest import QTest

    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel
    window.btn_zoom_rect.setChecked(True)
    before = _ranges(panel._stacked_plots[0].vb)

    QTest.mouseClick(panel.glw.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, _at(panel, (6.0, 50.0)).toPoint())
    qapp.processEvents()

    assert window.btn_cursor1.isChecked()
    assert panel.v_line.value() == pytest.approx(6.0, abs=_pixels(panel._stacked_plots[0].vb)[0])
    assert _ranges(panel._stacked_plots[0].vb) == pytest.approx(before)


def _all_view_boxes(panel):
    return [panel.plot.plotItem.vb, *(vb for _, vb in panel._extra_axes),
            *(p.vb for p in panel._stacked_plots)]


def _shows_cross(vb):
    return vb.hasCursor() and vb.cursor().shape() == Qt.CursorShape.CrossCursor


def test_the_plot_shows_a_cross_while_the_button_is_on(qapp, window):
    _plot(qapp, window, SPEED, TORQUE)
    panel = window.plot_panel

    window.btn_zoom_rect.setChecked(True)
    assert all(_shows_cross(vb) for vb in _all_view_boxes(panel))

    # Rows and axes made afterwards show it too.
    window.btn_multi_axis.setChecked(True)
    _plot(qapp, window, TEMP)
    assert panel._extra_axes
    assert all(_shows_cross(vb) for vb in _all_view_boxes(panel))

    window.btn_zoom_rect.setChecked(False)
    assert not any(vb.hasCursor() for vb in _all_view_boxes(panel))


def _shortcut(window, key):
    from PySide6.QtGui import QKeySequence, QShortcut

    [shortcut] = [s for s in window.findChildren(QShortcut)
                  if s.key().toString(QKeySequence.SequenceFormat.PortableText) == key]
    return shortcut


def test_z_turns_rectangle_zoom_on_and_off(qapp, window):
    z = _shortcut(window, 'Z')

    z.activated.emit()
    assert window.btn_zoom_rect.isChecked()
    assert window.plot_panel._rect_zoom

    z.activated.emit()
    assert not window.btn_zoom_rect.isChecked()
    assert not window.plot_panel._rect_zoom


def test_esc_leaves_rectangle_zoom_and_is_free_otherwise(qapp, window):
    esc = _shortcut(window, 'Esc')
    assert not esc.isEnabled()   # left to a table cell being edited

    window.btn_zoom_rect.setChecked(True)
    assert esc.isEnabled()

    esc.activated.emit()
    assert not window.btn_zoom_rect.isChecked()
    assert not esc.isEnabled()


def test_the_shortcuts_list_names_z_and_esc(window):
    keys = dict(window._SHORTCUTS)

    assert 'Rectangle Zoom' in keys['Z']
    assert 'Rectangle Zoom' in keys['Esc']
