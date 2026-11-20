# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Drawing a state lane: the bar of labelled stretches, and the axis that
names its signal (core.state_lane cuts the signal into stretches)."""
from __future__ import annotations

from typing import Callable

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPen

from core.state_lane import BIT, Lane, pieces

#: A lane row's plot area, in pixels.
LANE_HEIGHT = 24
_INSET_Y = 2.0         # gap above and below the bar
_GAP_X = 0.5           # gap between neighbouring stretches
_TEXT_PAD = 4          # room around a label
_BUSY_FILL = '#1d4b53'
_BUSY_HATCH = '#3aa8b8'
_EMPTY = '#2a2e35'


def ink_on(color: QColor) -> QColor:
    """Dark writing on a light colour, white on a dark one."""
    light = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue() > 150
    return QColor('#111111') if light else QColor('#ffffff')


class LaneItem(pg.GraphicsObject):
    """One signal's lane, filling its row's height whatever the row's Y
    range. Narrow stretches side by side are hatched as one busy block; a
    label is written where it fits. A click on a stretch calls *on_click*
    with the stretch's index and the clicked time."""

    def __init__(self, lane: Lane,
                 on_click: Callable[[int, float], None] | None = None) -> None:
        super().__init__()
        self.lane = lane
        self.on_click = on_click
        self._written: list[tuple[int, str]] = []
        self._drawn: list[tuple[int, float, float]] = []
        self.setAcceptHoverEvents(True)
        self.setZValue(-10)    # under the cursor lines

    # The item covers what the view shows, so it is always drawn.
    def _view(self):
        view_box = self.getViewBox()
        if view_box is None:
            return None
        return view_box, view_box.viewRange()

    def viewRangeChanged(self) -> None:
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:
        view = self._view()
        if view is None:
            return QRectF()
        _box, ((x0, x1), (y0, y1)) = view
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    def written(self) -> list[tuple[int, str]]:
        """The stretches whose label the last paint wrote: (index, label)."""
        return list(self._written)

    def drawn(self) -> list[tuple[int, float, float]]:
        """What the last paint drew, in device pixels: (index or -1 for a
        busy block, left, right)."""
        return list(self._drawn)

    def paint(self, painter, *_args) -> None:
        view = self._view()
        self._written, self._drawn = [], []
        if view is None or not self.lane.size:
            return
        view_box, ((x0, x1), (y0, y1)) = view
        pixel = float(view_box.viewPixelSize()[0])
        cut = pieces(self.lane, x0, x1, pixel)
        transform = painter.transform()
        row = transform.mapRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        if row.width() <= 0 or x1 <= x0:
            return
        scale = row.width() / (x1 - x0)
        top, height = row.top() + _INSET_Y, max(row.height() - 2 * _INSET_Y, 1.0)
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        font = QFont(painter.font())
        font.setPixelSize(max(9, min(12, int(height * 0.6))))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        busy_brush = QBrush(QColor(_BUSY_HATCH), Qt.BrushStyle.BDiagPattern)
        for start, end, i in zip(cut.starts.tolist(), cut.ends.tolist(), cut.index.tolist()):
            left = row.left() + (max(start, x0) - x0) * scale
            right = row.left() + (min(end, x1) - x0) * scale
            width = max(right - left, 1.0)
            self._drawn.append((i, left, left + width))
            if i < 0:
                box = QRectF(left, top, width, height)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(_BUSY_FILL))
                painter.drawRect(box)
                painter.setBrush(busy_brush)
                painter.drawRect(box)
                continue
            color = QColor(self.lane.color(i))
            inner = max(width - 2 * _GAP_X, 1.0)
            box = QRectF(left + (width - inner) / 2, top, inner, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            radius = min(2.5, inner / 2)
            painter.drawRoundedRect(box, radius, radius)
            if self.lane.kind == BIT:
                continue
            text = self.lane.label(i)
            if box.width() < metrics.horizontalAdvance(text) + 2 * _TEXT_PAD:
                continue
            painter.setPen(ink_on(color))
            painter.drawText(box.adjusted(_TEXT_PAD, 0, -_TEXT_PAD, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            self._written.append((i, text))
        painter.restore()

    def mouseClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.on_click is None:
            event.ignore()
            return
        t = float(event.pos().x())
        i = self.lane.at(t)
        if i is None:
            event.ignore()
            return
        event.accept()
        self.on_click(i, t)

    def hoverEvent(self, event) -> None:
        if event.isExit():
            self.setToolTip('')
            return
        i = self.lane.at(float(event.pos().x()))
        if i is None:
            self.setToolTip('')
            return
        start, end = float(self.lane.starts[i]), float(self.lane.ends[i])
        self.setToolTip(f'{self.lane.label(i)}\n{start:.3f} s to {end:.3f} s '
                        f'({end - start:.3f} s)')


class LaneAxis(pg.AxisItem):
    """A lane row's left axis: no ticks, the signal's name written across,
    right-aligned and cut short with … when it is too long."""

    def __init__(self, name: str, color: str, width: int) -> None:
        super().__init__('left')
        self.name = name
        self.color = color
        self.setStyle(showValues=False, tickLength=0)
        self.setPen(pg.mkPen(None))
        self.setWidth(width)

    def setLabel(self, *_args, **_kwargs) -> None:
        """A lane row has no Y title: its name is written across instead,
        and a sideways title would spill over the rows next to it."""
        self.showLabel(False)

    def shown_name(self) -> str:
        metrics = QFontMetrics(self._name_font())
        return metrics.elidedText(self.name, Qt.TextElideMode.ElideMiddle,
                                  max(int(self.width()) - 10, 10))

    @staticmethod
    def _name_font() -> QFont:
        font = QFont()
        font.setPixelSize(11)
        return font

    def paint(self, painter, *_args) -> None:
        rect = self.boundingRect()
        painter.save()
        painter.setFont(self._name_font())
        painter.setPen(QPen(QColor(self.color)))
        painter.drawText(rect.adjusted(2, 0, -6, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                         self.shown_name())
        painter.restore()


def set_lane_view(plot_item: pg.PlotItem) -> None:
    """A lane row's view: time pans and zooms as the other rows, height
    does not move."""
    view_box = plot_item.vb
    view_box.setMouseEnabled(x=True, y=False)
    view_box.enableAutoRange(y=False)
    view_box.setYRange(0.0, 1.0, padding=0)
    plot_item.hideButtons()

