# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The handles that show and hide the signal panel and the log panel.

Each is a small flat handle in the window's own colours, lying in the gap
between its panel and the plot area, so it follows the light or dark theme
and covers neither the plot area's cursor line and table nor the panel. Its
chevron points the way the panel moves on a click. They were fixed navy
buttons laid over the window, on top of the cursor line. They are the only
way to hide and show the panels: the panels' title rows have no button.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest


def _palette(window: str, text: str):
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(window))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(text))
    return palette


@pytest.mark.parametrize('window, text', [('#f3f3f3', '#000000'), ('#202020', '#ffffff')],
                         ids=['light', 'dark'])
def test_handle_is_drawn_in_the_window_colours(qapp, window, text):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from gui.edge_tab import EdgeTab

    tab = EdgeTab(Qt.Orientation.Horizontal)
    tab.setPalette(_palette(window, text))
    fill, border, chevron = tab.colors()

    def distance_from_window(color):
        return abs(color.lightness() - QColor(window).lightness())

    # Close to the window colour, the border further out and the chevron
    # nearly the text colour.
    assert distance_from_window(fill) < distance_from_window(border) < distance_from_window(chevron)
    assert distance_from_window(fill) < 25

    image = tab.grab().toImage()
    body = image.pixelColor(image.width() // 5, image.height() // 2)
    assert abs(body.lightness() - fill.lightness()) <= 2


def _chevron_stroke_width(tab) -> float:
    """The chevron's ink inside the outline, divided by its length: about
    the width of its stroke, in pixels. Its round caps and soft edges add a
    little: a 1.5 px pen reads 1.7, a 2.2 px pen 2.5."""
    import math

    image = tab.grab().toImage()
    fill, _border, chevron = tab.colors()
    low, high = fill.lightnessF(), chevron.lightnessF()
    w, h = image.width(), image.height()
    end = min(w, h) // 2 + 1      # past the rounded ends, where the outline curves
    xs = range(end, w - end) if w > h else range(2, w - 2)
    ys = range(2, h - 2) if w > h else range(end, h - end)
    ink = sum(max(0.0, min(1.0, (image.pixelColor(x, y).lightnessF() - low) / (high - low)))
              for y in ys for x in xs)
    return ink / (2 * math.hypot(4, 4))   # two strokes, each four across and four along


@pytest.mark.parametrize('window, text', [('#f3f3f3', '#000000'), ('#202020', '#ffffff')],
                         ids=['light', 'dark'])
@pytest.mark.parametrize('orientation, arrows', [
    ('Horizontal', ('DownArrow', 'UpArrow')),
    ('Vertical', ('LeftArrow', 'RightArrow')),
], ids=['under-the-plot', 'beside-the-panel'])
def test_chevron_is_bold_in_a_clear_outline(qapp, window, text, orientation, arrows):
    """The owner found the handle under the plot too faint: its chevron was
    a 1.5 px line at 70 % of the text colour, in an outline at 28 %."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from gui.edge_tab import EdgeTab

    tab = EdgeTab(getattr(Qt.Orientation, orientation))
    tab.setPalette(_palette(window, text))
    _fill, border, chevron = tab.colors()

    def share_of_text(color):
        return (abs(color.lightness() - QColor(window).lightness())
                / abs(QColor(text).lightness() - QColor(window).lightness()))

    assert share_of_text(chevron) >= 0.8
    assert share_of_text(border) >= 0.35
    # The outline is drawn in that colour.
    image = tab.grab().toImage()
    along = (image.width() // 2, 0) if orientation == 'Horizontal' else (0, image.height() // 2)
    assert abs(image.pixelColor(*along).lightness() - border.lightness()) <= 3

    for arrow in arrows:
        tab.set_arrow(getattr(Qt.ArrowType, arrow))
        assert _chevron_stroke_width(tab) >= 2.0, arrow


def test_handle_lies_along_its_edge_and_points_where_it_is_told(qapp):
    from PySide6.QtCore import Qt
    from gui.edge_tab import EdgeTab

    along_bottom = EdgeTab(Qt.Orientation.Horizontal)
    along_side = EdgeTab(Qt.Orientation.Vertical)
    assert along_bottom.width() > along_bottom.height() == EdgeTab.THICKNESS
    assert along_side.height() > along_side.width() == EdgeTab.THICKNESS

    along_bottom.set_arrow(Qt.ArrowType.UpArrow)
    assert along_bottom.arrow() == Qt.ArrowType.UpArrow


# ── In the main window ───────────────────────────────────────────────────


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from core.signal_store import SignalSeries
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    w.resize(1400, 800)
    w.show()
    ts = array.array('d', (i * 0.1 for i in range(100)))
    w.plot_panel.add_series('Speed', SignalSeries(None, 'Msg', 1, 'Speed', 'km/h', ts, ts))
    w.plot_panel.set_measurement_file('C:/logs/drive_cycle_03.blf')
    _settle(qapp)
    yield w
    w.close()
    qapp.processEvents()


def _settle(qapp):
    from PySide6.QtTest import QTest

    for _ in range(3):
        qapp.processEvents()
        QTest.qWait(20)


def _click(qapp, tab):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(tab, Qt.MouseButton.LeftButton)
    _settle(qapp)


def _in_window(window, widget):
    from PySide6.QtCore import QRect

    return QRect(widget.mapTo(window, widget.rect().topLeft()), widget.size())


def test_handles_are_not_navy_buttons_laid_over_the_window(window):
    from gui.edge_tab import EdgeTab

    for tab in (window.left_edge_btn, window.bottom_edge_btn):
        assert isinstance(tab, EdgeTab)
        assert tab.styleSheet() == ''


@pytest.mark.parametrize('log_shown', [True, False], ids=['log-shown', 'log-hidden'])
def test_log_handle_lies_between_the_cursor_line_and_the_panel(qapp, window, log_shown):
    from PySide6.QtCore import Qt

    if not log_shown:
        _click(qapp, window.bottom_edge_btn)
    tab = _in_window(window, window.bottom_edge_btn)
    cursor_line = _in_window(window, window.plot_panel.cursor_label)
    edge = (window.bottom_dock.geometry().top() if log_shown
            else _in_window(window, window.statusBar()).top())

    assert window.bottom_dock.isVisible() == log_shown
    assert tab.top() > cursor_line.bottom()
    assert tab.bottom() + 1 == edge
    assert abs(tab.center().x() - window.rect().center().x()) <= 1
    assert window.bottom_edge_btn.arrow() == (
        Qt.ArrowType.DownArrow if log_shown else Qt.ArrowType.UpArrow)
    assert window.bottom_edge_btn.toolTip() == (
        'Hide the log panel' if log_shown else 'Show the log panel')


@pytest.mark.parametrize('signals_shown', [True, False], ids=['signals-shown', 'signals-hidden'])
def test_signal_panel_handle_lies_between_the_panel_and_the_table(qapp, window, signals_shown):
    from PySide6.QtCore import Qt

    if not signals_shown:
        _click(qapp, window.left_edge_btn)
    tab = _in_window(window, window.left_edge_btn)
    table = _in_window(window, window.plot_panel.table)

    assert window.left_dock.isVisible() == signals_shown
    assert tab.right() < table.left()
    if signals_shown:
        assert tab.left() == window.left_dock.geometry().right() + 1
    else:
        assert tab.left() == window.centralWidget().geometry().left()
    assert window.left_edge_btn.arrow() == (
        Qt.ArrowType.LeftArrow if signals_shown else Qt.ArrowType.RightArrow)


def test_handles_bring_their_panels_back(qapp, window):
    _click(qapp, window.bottom_edge_btn)
    _click(qapp, window.left_edge_btn)
    assert not window.bottom_dock.isVisible() and not window.left_dock.isVisible()

    _click(qapp, window.bottom_edge_btn)
    _click(qapp, window.left_edge_btn)
    assert window.bottom_dock.isVisible() and window.left_dock.isVisible()


def test_panel_title_rows_carry_only_the_panel_name(window):
    """The handles are the one way to hide and show a panel: the "◀" and "▼"
    buttons at the right end of the title rows did the same and were removed."""
    from PySide6.QtWidgets import QAbstractButton, QLabel

    for dock, name in ((window.left_dock, 'Decoded Signals'),
                       (window.bottom_dock, 'Log / Diagnostics / Measurement')):
        title = dock.titleBarWidget()
        assert [label.text() for label in title.findChildren(QLabel)] == [name]
        assert title.findChildren(QAbstractButton) == []
