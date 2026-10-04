# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The signal table's header follows the application's light or dark theme:
the window colour with black text in light mode, white text in dark mode.
It was a fixed dark grey with black text, which suited neither. The table's
lines take the header's border colour, so they match it in either theme.
The table has no frame and the header no line above or left of it, so the
header joins the window around it; a frame was painted in the plot
background, a dark ring around a light header on a dark plot.
"""
from __future__ import annotations

import pytest

LIGHT = {'window': '#f3f3f3', 'text': '#000000'}
DARK = {'window': '#1e1e1e', 'text': '#ffffff'}


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


@pytest.fixture()
def panel(qapp):
    from gui.plot_widget import PlotPanel

    p = PlotPanel()
    p.table_panel.resize(300, 200)
    p.table_panel.show()
    qapp.processEvents()
    yield p
    p.table_panel.close()
    p.close()
    qapp.processEvents()


def _header_fill(qapp, panel) -> str:
    """The colour inside the Signal column's header section."""
    header = panel.table.horizontalHeader()
    qapp.processEvents()
    image = header.grab().toImage()
    x = round((header.sectionViewportPosition(1) + 4) * image.devicePixelRatio())
    return image.pixelColor(x, round(4 * image.devicePixelRatio())).name()


def _section_style(panel) -> str:
    sheet = panel.table_panel.styleSheet()
    return sheet[sheet.index('QHeaderView::section'):]


def _assert_lines_match_the_header(panel):
    border = panel._header_colors()[2]
    assert f'border-right: 1px solid {border}' in _section_style(panel)
    assert f'border-bottom: 1px solid {border}' in _section_style(panel)
    assert f'gridline-color: {border}' in panel.table_panel.styleSheet()


@pytest.mark.parametrize('colors', [LIGHT, DARK], ids=['light', 'dark'])
def test_the_header_takes_the_window_colour_and_its_text_colour(qapp, theme, colors):
    from gui.plot_widget import PlotPanel

    theme(colors)
    p = PlotPanel()
    p.table_panel.resize(300, 200)
    p.table_panel.show()
    try:
        assert f"background-color: {colors['window']}" in _section_style(p)
        assert f"color: {colors['text']}" in _section_style(p)
        assert _header_fill(qapp, p) == colors['window']
        _assert_lines_match_the_header(p)
    finally:
        p.table_panel.close()
        p.close()


def test_the_header_follows_a_switch_between_light_and_dark(qapp, theme, panel):
    theme(LIGHT)
    assert _header_fill(qapp, panel) == LIGHT['window']

    theme(DARK)

    assert f"color: {DARK['text']}" in _section_style(panel)
    assert _header_fill(qapp, panel) == DARK['window']
    _assert_lines_match_the_header(panel)


def test_the_plot_background_does_not_change_the_header(qapp, theme, panel):
    theme(LIGHT)

    panel.set_background_color('#000000')

    assert f"background-color: {LIGHT['window']}" in _section_style(panel)
    assert _header_fill(qapp, panel) == LIGHT['window']


def test_no_plot_background_rings_the_header(qapp, theme, panel):
    # Light theme on a black plot: nothing black above, left or right of
    # the header, in the panel's top row.
    from PySide6.QtCore import QPoint

    theme(LIGHT)
    panel.set_background_color('#000000')
    panel.table_panel.resize(500, 200)
    qapp.processEvents()
    header = panel.table.horizontalHeader()

    assert panel.table.frameWidth() == 0
    assert header.mapTo(panel.table_panel, QPoint(0, 0)) == QPoint(0, 0)
    image = panel.table_panel.grab().toImage()
    ratio = image.devicePixelRatio()
    row = round(header.height() / 2 * ratio)
    beyond_the_columns = round((header.length() + 10) * ratio)
    for x in (0, beyond_the_columns, image.width() - 1):
        assert image.pixelColor(x, row).name() == LIGHT['window']
    assert image.pixelColor(round(4 * ratio), 0).name() == LIGHT['window']
