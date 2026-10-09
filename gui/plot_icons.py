# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Monochrome icons for the plot buttons, drawn from small SVG drawings.

An icon is drawn whenever Qt asks for it, in the colour of button text in the
current palette: white in dark mode, black in light mode, following a switch
while the application runs, and sharp at any display scaling. There are no
image files, so the build needs nothing added.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QIconEngine,
    QImage,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QPushButton

ICON_SIZE = QSize(18, 18)

_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
         'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
         'stroke-linejoin="round">')
_SAMPLES = ('<g fill="currentColor" stroke="none">'
            '<circle cx="4" cy="17" r="2"/><circle cx="9" cy="11" r="2"/>'
            '<circle cx="14" cy="15" r="2"/><circle cx="20" cy="7" r="2"/></g>')

# Drawn on a 24 x 24 grid.
DRAWINGS: dict[str, str] = {
    # Four corners with a trace inside: all the data fits the window.
    'fit_window': ('<path d="M3.5 8.5v-5h5M15.5 3.5h5v5M20.5 15.5v5h-5M8.5 20.5h-5v-5"/>'
                   '<path d="M7 15l3-4 3 3 4-5"/>'),
    # Arrows between two bars: the height only.
    'fit_vertical': ('<path d="M5 3.5h14M5 20.5h14"/>'
                     '<path d="M12 7v10M9 9.5l3-3 3 3M9 14.5l3 3 3-3"/>'),
    # A dashed box with a lens over its lower right corner.
    'zoom_rect': ('<path stroke-linecap="butt" d="M2.5 6.5v-2a2 2 0 0 1 2-2h2M9.5 2.5h2.5'
                  'M14 2.5h1a2 2 0 0 1 2 2v1.5M2.5 9.5v2.5M2.5 14v.5a2 2 0 0 0 2 2h2"/>'
                  '<circle cx="13.8" cy="13.8" r="5"/><path d="M17.4 17.4l3.8 3.8"/>'),
    # As on the plot, Cursor 1 solid and Cursor 2 dashed, each with its number.
    'cursor1': '<path d="M8 3v18"/><path d="M13.5 10l3-2.5V17"/>',
    'cursor2': ('<path d="M8 3v18" stroke-dasharray="3 3"/>'
                '<path d="M13.5 9.6c0-1.3 1.1-2.1 2.4-2.1 1.4 0 2.4.9 2.4 2.1 0 1-.6 1.7-1.5 2.5'
                'L13.5 17h5"/>'),
    # A trace with its samples marked.
    'points': '<path d="M4 17l5-6 5 4 6-8"/>' + _SAMPLES,
    # The samples alone: the line is hidden.
    'hide_line': _SAMPLES,
}


def plot_icon(name: str) -> QIcon:
    """The icon drawn by DRAWINGS[name]."""
    return QIcon(_PlotIconEngine(name))


def icon_button(name: str, label: str, tooltip: str) -> QPushButton:
    """A plot button that shows the icon called name instead of its label.

    The label stays its accessible name, which the "»" menu shows when the
    button does not fit the row.
    """
    button = QPushButton()
    button.setIcon(plot_icon(name))
    button.setIconSize(ICON_SIZE)
    button.setAccessibleName(label)
    button.setToolTip(tooltip)
    return button


def _color(mode: QIcon.Mode, state: QIcon.State) -> QColor:
    palette = QGuiApplication.palette()
    if mode == QIcon.Mode.Disabled:
        return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText)
    if state == QIcon.State.On and _checked_buttons_are_filled_with_accent():
        # On the accent fill, Windows 11 writes black in dark mode, where the
        # accent is light, and white in light mode, where it is dark.
        dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        return QColor(Qt.GlobalColor.black if dark else Qt.GlobalColor.white)
    return palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText)


def _checked_buttons_are_filled_with_accent() -> bool:
    # Other styles, such as Windows 10's, draw a checked button pressed in,
    # under ordinary button text.
    style = QApplication.style()
    return style is not None and style.name().lower() == 'windows11'


class _PlotIconEngine(QIconEngine):
    """Renders one drawing in the colour for each mode and state."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name
        self._svg = _HEAD + DRAWINGS[name] + '</svg>'
        self._rendered: dict[tuple[int, int, float], QPixmap] = {}

    def clone(self) -> QIconEngine:
        return _PlotIconEngine(self._name)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State,
                     scale: float) -> QPixmap:
        side = max(1, round(min(size.width(), size.height()) * scale))
        color = _color(mode, state)
        key = (side, color.rgba(), scale)
        pixmap = self._rendered.get(key)
        if pixmap is None:
            image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            svg = self._svg.replace('currentColor', color.name())
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, side, side))
            painter.end()
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(scale)
            self._rendered[key] = pixmap
        return pixmap

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode,
              state: QIcon.State) -> None:
        device = painter.device()
        scale = device.devicePixelRatioF() if device is not None else 1.0
        painter.drawPixmap(rect, self.scaledPixmap(rect.size(), mode, state, scale))
