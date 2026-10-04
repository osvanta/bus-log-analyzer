# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A small flat handle on the edge of a side panel that shows or hides it.

It is painted from the window's own colours whenever it is drawn, so it
follows a switch between light and dark mode, and it is thin enough to sit
in the gap beside the panel without covering what lies around it. A chevron
points the way the panel will move.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget


def _mix(base: QColor, toward: QColor, amount: float) -> QColor:
    """base moved amount (0..1) of the way toward toward."""
    return QColor(
        round(base.red() + (toward.red() - base.red()) * amount),
        round(base.green() + (toward.green() - base.green()) * amount),
        round(base.blue() + (toward.blue() - base.blue()) * amount),
    )


class EdgeTab(QAbstractButton):
    """A handle that lies along a panel's edge: horizontal along a top or
    bottom edge, vertical along a left or right edge."""

    THICKNESS = 10   # across the edge
    LENGTH = 40      # along the edge

    # How far each colour lies from the window colour toward its text colour.
    _FILL = 0.06
    _FILL_HOVER = 0.14
    _FILL_DOWN = 0.22
    _BORDER = 0.28
    _CHEVRON = 0.7

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._arrow = (Qt.ArrowType.DownArrow if orientation == Qt.Orientation.Horizontal
                       else Qt.ArrowType.LeftArrow)
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedSize(self.LENGTH, self.THICKNESS)
        else:
            self.setFixedSize(self.THICKNESS, self.LENGTH)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def arrow(self) -> Qt.ArrowType:
        return self._arrow

    def set_arrow(self, arrow: Qt.ArrowType) -> None:
        """Point the chevron the way the panel will move on a click."""
        self._arrow = arrow
        self.update()

    def colors(self) -> tuple[QColor, QColor, QColor]:
        """Fill, border and chevron, from the current palette and state."""
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        if self.isDown():
            fill = self._FILL_DOWN
        elif self.underMouse():
            fill = self._FILL_HOVER
        else:
            fill = self._FILL
        chevron = 1.0 if self.underMouse() else self._CHEVRON
        return _mix(window, text, fill), _mix(window, text, self._BORDER), _mix(window, text, chevron)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        fill, border, chevron = self.colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(body.width(), body.height()) / 2
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(body, radius, radius)

        pen = QPen(chevron, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        c = body.center()
        # Four wide and two deep either side of the centre.
        tips = {
            Qt.ArrowType.DownArrow: ((-4, -2), (0, 2), (4, -2)),
            Qt.ArrowType.UpArrow: ((-4, 2), (0, -2), (4, 2)),
            Qt.ArrowType.LeftArrow: ((2, -4), (-2, 0), (2, 4)),
            Qt.ArrowType.RightArrow: ((-2, -4), (2, 0), (-2, 4)),
        }[self._arrow]
        painter.drawPolyline([QPointF(c.x() + dx, c.y() + dy) for dx, dy in tips])
        painter.end()
