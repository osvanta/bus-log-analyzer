# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A signal group's row in the signal table follows the application's light or
dark theme, like the table's header: the window colour a shade toward its
text, with the window's text colour for the arrow and the name. It was a
fixed navy with a light blue name, which suited neither theme, and on a
white plot the arrow was dark on navy. The group's box suits the row's
colour, as a signal's box suits the plot's.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import numpy as np
import pytest

from PySide6.QtCore import Qt

LIGHT = {'window': '#f3f3f3', 'text': '#000000'}
DARK = {'window': '#1e1e1e', 'text': '#ffffff'}
GROUP = 'Powertrain'


@pytest.fixture()
def theme(qapp):
    """Set the application palette to a theme; restored after the test."""
    from PySide6.QtGui import QColor, QPalette

    saved = QPalette(qapp.palette())

    def apply(colors):
        palette = QPalette(saved)
        palette.setColor(QPalette.ColorRole.Window, QColor(colors['window']))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors['text']))
        qapp.setPalette(palette)
        qapp.processEvents()

    yield apply
    qapp.setPalette(saved)
    qapp.processEvents()


def _make_series(name: str):
    from core.signal_store import SignalSeries
    ts = np.linspace(0.0, 10.0, 200)
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100,
        signal_name=name, unit='u',
        timestamps=ts, values=np.sin(ts), raw_values=[], has_labels=False,
    )


def _grouped_panel(qapp):
    from gui.plot_widget import PlotPanel

    p = PlotPanel()
    for key in ('Speed', 'Temp', 'Pressure'):
        p.add_series(key, _make_series(key))
    p._items['Speed'].group = GROUP
    p._items['Temp'].group = GROUP
    p._rebuild_curves(preserve_selection=True)
    p.table_panel.resize(500, 200)
    p.table_panel.show()
    qapp.processEvents()
    return p


@pytest.fixture()
def grouped(qapp):
    p = _grouped_panel(qapp)
    yield p
    p.table_panel.close()
    p.close()
    qapp.processEvents()


def _shade(colors) -> str:
    from gui.edge_tab import _mix
    from gui.plot_widget import _GROUP_ROW_SHADE
    from PySide6.QtGui import QColor
    return _mix(QColor(colors['window']), QColor(colors['text']), _GROUP_ROW_SHADE).name()


def _header_row(panel) -> int:
    for row in range(panel.table.rowCount()):
        if panel.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == f'__group__{GROUP}':
            return row
    raise AssertionError('no group header row')


def _pixel(qapp, panel, row: int, col: int, x_from_left: int | None = None) -> str:
    """The colour on screen halfway down a cell: near its left, or at
    *x_from_left*."""
    qapp.processEvents()
    table = panel.table
    cell = table.visualRect(table.model().index(row, col))
    image = table.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    x = cell.left() + (2 if x_from_left is None else x_from_left)
    y = cell.center().y()
    return image.pixelColor(round(x * ratio), round(y * ratio)).name()


def _assert_row_colours(qapp, panel, colors):
    row = _header_row(panel)
    expected = _shade(colors)
    for col in range(panel.table.columnCount()):
        assert panel.table.item(row, col).background().color().name() == expected
    for col in (0, 1):  # the arrow and the name
        assert panel.table.item(row, col).foreground().color().name() == colors['text']
    # Past the name's text, in the Signal column, and in an empty cell.
    name = panel.table.visualRect(panel.table.model().index(row, 1))
    assert _pixel(qapp, panel, row, 1, x_from_left=name.width() - 4) == expected
    assert _pixel(qapp, panel, row, 4) == expected


@pytest.mark.parametrize('colors', [LIGHT, DARK], ids=['light', 'dark'])
def test_the_group_row_takes_the_theme_colours(qapp, theme, colors):
    theme(colors)
    p = _grouped_panel(qapp)
    try:
        _assert_row_colours(qapp, p, colors)
        assert p.table.item(_header_row(p), 1).font().bold()
    finally:
        p.table_panel.close()
        p.close()


@pytest.mark.parametrize('colors', [LIGHT, DARK], ids=['light', 'dark'])
def test_the_group_row_is_a_shade_apart_from_the_header(colors):
    assert _shade(colors) != colors['window']


def test_the_group_row_follows_a_switch_between_light_and_dark(qapp, theme, grouped):
    theme(LIGHT)
    _assert_row_colours(qapp, grouped, LIGHT)

    theme(DARK)

    _assert_row_colours(qapp, grouped, DARK)


@pytest.mark.parametrize('background', ['#000000', '#ffffff'])
def test_the_plot_background_does_not_change_the_group_row(qapp, theme, grouped, background):
    theme(LIGHT)

    grouped.set_background_color(background)
    grouped._rebuild_curves(preserve_selection=True)

    _assert_row_colours(qapp, grouped, LIGHT)


@pytest.mark.parametrize('colors, background, group_box, signal_box', [
    (LIGHT, '#000000', '#ffffff', '#2a2a2a'),
    (DARK, '#ffffff', '#2a2a2a', '#ffffff'),
], ids=['light-on-black-plot', 'dark-on-white-plot'])
def test_the_group_box_suits_its_row_and_a_signal_box_the_plot(
        qapp, theme, grouped, colors, background, group_box, signal_box):
    from gui.plot_widget import _CheckDelegate

    theme(colors)
    grouped.set_background_color(background)
    for key in ('Speed', 'Temp', 'Pressure'):
        grouped._items[key].visible = False  # empty boxes, nothing drawn inside
    grouped._rebuild_curves(preserve_selection=True)
    grouped.table.clearSelection()

    def box_centre(row):
        index = grouped.table.model().index(row, 0)
        cell = grouped.table.visualRect(index)
        centre = _CheckDelegate.box_rect(cell, index).center()
        return centre.x() - cell.left()

    group_row = _header_row(grouped)
    signal_row = grouped._row_lookup['Pressure']
    assert _pixel(qapp, grouped, group_row, 0, x_from_left=box_centre(group_row)) == group_box
    assert _pixel(qapp, grouped, signal_row, 0, x_from_left=box_centre(signal_row)) == signal_box
