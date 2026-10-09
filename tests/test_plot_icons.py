# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The plot buttons' icons: drawn in the colour of button text in the current
palette, so white in dark mode and black in light mode, and sharp at the
display's scaling.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def palette(qapp):
    """The application palette, restored after the test."""
    from PySide6.QtGui import QPalette

    saved = QPalette(qapp.palette())
    yield QPalette(saved)
    qapp.setPalette(saved)


def _set(qapp, palette, role, color, group=None):
    from PySide6.QtGui import QColor, QPalette

    if group is None:
        palette.setColor(role, QColor(color))
    else:
        palette.setColor(group, role, QColor(color))
    qapp.setPalette(palette)
    return QPalette(palette)


def _ink(pixmap) -> str:
    """The colour of the drawing's solid strokes."""
    image = pixmap.toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 255:
                return color.name()
    raise AssertionError('nothing drawn')


def _pixmap(icon, mode=None, state=None, scale=1.0):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    return icon.pixmap(QSize(18, 18), scale, mode or QIcon.Mode.Normal,
                       state or QIcon.State.Off)


@pytest.mark.parametrize('name', ['fit_window', 'fit_vertical', 'zoom_rect', 'cursor1',
                                  'cursor2', 'points', 'hide_line'])
def test_every_icon_draws_in_the_button_text_colour(qapp, palette, name):
    from PySide6.QtGui import QPalette
    from gui.plot_icons import plot_icon

    _set(qapp, palette, QPalette.ColorRole.ButtonText, '#ff0000')

    assert _ink(_pixmap(plot_icon(name))) == '#ff0000'


def test_an_icon_follows_a_switch_between_light_and_dark(qapp, palette):
    from PySide6.QtGui import QPalette
    from gui.plot_icons import plot_icon

    icon = plot_icon('cursor1')
    _set(qapp, palette, QPalette.ColorRole.ButtonText, '#000000')
    assert _ink(_pixmap(icon)) == '#000000'

    _set(qapp, palette, QPalette.ColorRole.ButtonText, '#ffffff')
    assert _ink(_pixmap(icon)) == '#ffffff'


def test_a_disabled_icon_takes_the_disabled_text_colour(qapp, palette):
    from PySide6.QtGui import QIcon, QPalette
    from gui.plot_icons import plot_icon

    _set(qapp, palette, QPalette.ColorRole.ButtonText, '#787878',
         group=QPalette.ColorGroup.Disabled)

    assert _ink(_pixmap(plot_icon('hide_line'), QIcon.Mode.Disabled)) == '#787878'


@pytest.mark.parametrize('window, ink', [('#202020', '#000000'), ('#f3f3f3', '#ffffff')])
def test_a_checked_icon_on_the_windows_11_accent_fill(qapp, palette, monkeypatch, window, ink):
    # Black on dark mode's light accent, white on light mode's dark accent.
    from PySide6.QtGui import QIcon, QPalette
    from gui import plot_icons

    monkeypatch.setattr(plot_icons, '_checked_buttons_are_filled_with_accent', lambda: True)
    _set(qapp, palette, QPalette.ColorRole.Window, window)

    assert _ink(_pixmap(plot_icons.plot_icon('cursor1'), state=QIcon.State.On)) == ink


def test_a_checked_icon_in_other_styles_keeps_the_text_colour(qapp, palette, monkeypatch):
    from PySide6.QtGui import QIcon, QPalette
    from gui import plot_icons

    monkeypatch.setattr(plot_icons, '_checked_buttons_are_filled_with_accent', lambda: False)
    _set(qapp, palette, QPalette.ColorRole.ButtonText, '#123456')

    assert _ink(_pixmap(plot_icons.plot_icon('cursor1'), state=QIcon.State.On)) == '#123456'


def test_an_icon_is_drawn_at_the_display_scaling(qapp):
    from gui.plot_icons import plot_icon

    pixmap = _pixmap(plot_icon('fit_window'), scale=2.25)

    # 18 px at 225 %: drawn on 40 device pixels, not scaled up from 18.
    assert pixmap.width() == 40
    assert pixmap.devicePixelRatio() > 2


def test_an_icon_button_shows_its_icon_and_keeps_its_name(qapp):
    from gui.plot_icons import ICON_SIZE, icon_button

    button = icon_button('cursor2', 'Cursor 2', 'Cursor 2, or Shift+click the plot')

    assert button.text() == ''
    assert button.accessibleName() == 'Cursor 2'
    assert button.toolTip() == 'Cursor 2, or Shift+click the plot'
    assert button.iconSize() == ICON_SIZE
    assert not button.icon().isNull()
