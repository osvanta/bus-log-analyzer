# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The logo at the right end of the status bar: one drawing in the colours of the
light or the dark theme, following a switch between them, with the product's
name and version in its tooltip.
"""
from __future__ import annotations

import pytest

from gui.brand_logo import DARK_COLOURS, LIGHT_COLOURS, BrandLogo, logo_svg


def _palette(window_colour):
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(window_colour))
    return palette


def _drawn_colours(qapp, logo):
    """The colours the logo has painted, as #rrggbb, at its own pixels."""
    image = logo.grab().toImage()
    return {image.pixelColor(x, y).name().upper()
            for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)}


@pytest.mark.parametrize('colours', [DARK_COLOURS, LIGHT_COLOURS])
def test_each_theme_draws_the_whole_logo(qapp, colours):
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    svg = logo_svg(colours)
    renderer = QSvgRenderer(QByteArray(svg.encode()))

    assert renderer.isValid()
    assert renderer.viewBox().size().toTuple() == (440, 76)
    other = LIGHT_COLOURS if colours is DARK_COLOURS else DARK_COLOURS
    for role, colour in colours.items():
        assert colour in svg, role
        assert other[role] not in svg, role


def test_the_logo_keeps_its_proportions_at_any_height(qapp):
    for height in (20, 28, 36):
        logo = BrandLogo(height)
        drawn = logo.width() - 12   # its margins
        assert logo.height() == height
        assert abs(drawn / height - 440 / 76) < 0.05


@pytest.mark.parametrize('window_colour, expected', [
    ('#202020', DARK_COLOURS),
    ('#F3F3F3', LIGHT_COLOURS),
])
def test_the_theme_picks_the_colours(qapp, window_colour, expected):
    logo = BrandLogo(28)
    logo.setPalette(_palette(window_colour))

    assert logo.colours() is expected


def test_a_switch_of_theme_redraws_the_logo_in_the_other_colours(qapp):
    logo = BrandLogo(28)
    logo.setPalette(_palette('#202020'))
    dark = _drawn_colours(qapp, logo)

    logo.setPalette(_palette('#F3F3F3'))
    light = _drawn_colours(qapp, logo)

    # The name's ink is the logo's largest area in either theme.
    assert DARK_COLOURS['ink'] in dark and LIGHT_COLOURS['ink'] not in dark
    assert LIGHT_COLOURS['ink'] in light and DARK_COLOURS['ink'] not in light


def test_the_logo_sits_at_the_right_end_of_the_status_bar(qapp):
    from gui.main_window import BRAND_LOGO_HEIGHT, MainWindow

    window = MainWindow('Osvanta Bus Log Analyzer', 'v00.00.99')
    try:
        window.resize(1200, 700)
        window.show()
        qapp.processEvents()
        bar = window.statusBar()
        logo = window.brand_logo

        assert logo.parentWidget() is bar and logo.isVisible()
        # Below 28 pixels OSVANTA, under the name, can no longer be read.
        assert logo.height() == BRAND_LOGO_HEIGHT >= 28
        assert logo.x() > window.status_next_step_label.geometry().right()
        assert bar.height() >= BRAND_LOGO_HEIGHT
        assert logo.toolTip() == 'Osvanta Bus Log Analyzer v00.00.99'
    finally:
        window.close()
        qapp.processEvents()


def test_no_line_is_drawn_beside_the_logo(qapp):
    # Windows draws each status bar item's line at its right edge: the
    # logo's own, and the one of the item just before it, touch the logo.
    from PySide6.QtWidgets import QCommonStyle, QStyle, QStyleOption
    from gui.brand_logo import StatusBarStyle
    from gui.main_window import MainWindow

    framed = []

    class Recording(QCommonStyle):
        def drawPrimitive(self, element, option, painter, widget=None):
            if element == QStyle.PrimitiveElement.PE_FrameStatusBarItem:
                framed.append(widget)

    window = MainWindow('Osvanta Bus Log Analyzer', 'v00.00.99')
    try:
        window.resize(1200, 700)
        window.show()
        qapp.processEvents()
        style = StatusBarStyle(Recording())
        items = (window.status_state_label, window.status_next_step_label, window.brand_logo)
        for item in items:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_FrameStatusBarItem,
                                QStyleOption(), None, item)

        assert framed == [window.status_state_label]
        # The status bar has that style; its items keep the application's.
        assert isinstance(window.statusBar().style(), StatusBarStyle)
        assert not isinstance(window.status_state_label.style(), StatusBarStyle)
        assert not isinstance(window.brand_logo.style(), StatusBarStyle)
    finally:
        window.close()
        qapp.processEvents()


def test_a_long_status_message_never_widens_the_window(qapp):
    # The messages are cut short instead: a wider window moved the plot
    # under the mouse in the middle of a drag.
    from gui.main_window import MainWindow

    window = MainWindow('Osvanta Bus Log Analyzer', 'v00.00.99')
    try:
        window.resize(900, 700)
        window.show()
        qapp.processEvents()

        window._update_status('Rectangle Zoom on ' * 5, 'Drag to zoom in, Shift+drag to zoom out ' * 5)
        qapp.processEvents()

        assert window.width() == 900
        assert window.brand_logo.isVisible()
    finally:
        window.close()
        qapp.processEvents()
