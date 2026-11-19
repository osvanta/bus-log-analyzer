# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The XY view: one plotted signal against another, a point per sample of the X
signal, optionally coloured by a third signal.

It shows the time range the time plot shows (or the whole measurement), marks
where each cursor is, and a click on a point asks for Cursor 1 at that
point's time (*timeClicked*). The points themselves come from core.xy_data.
"""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from core.signal_store import SignalSeries
from core.xy_data import (
    COLOR_LEVELS, EMPTY, XYPoints, color_steps, nearest_point, xy_at, xy_points,
)

#: Blue for low to red for high (the turbo scale without its two dark ends,
#: which a black plot background would hide).
_SCALE_COLORS = ('#4662d7', '#36aaf9', '#1ae4b6', '#72fe5e',
                 '#c7ef34', '#faba39', '#f66b19', '#cb2a04')
#: A click this near a point, in pixels, picks it.
_PICK_PIXELS = 10.0
#: Range changes while the time plot is dragged are drawn once it rests.
_REDRAW_MS = 60
_POINT_SIZE = 5
_MARK_SIZE = 15


def scale_color(fraction: float) -> QColor:
    """The colour scale's colour *fraction* of the way along it, 0 to 1."""
    position = min(max(fraction, 0.0), 1.0) * (len(_SCALE_COLORS) - 1)
    index = min(int(position), len(_SCALE_COLORS) - 2)
    low, high = QColor(_SCALE_COLORS[index]), QColor(_SCALE_COLORS[index + 1])
    part = position - index
    return QColor(*(round(a + (b - a) * part) for a, b in (
        (low.red(), high.red()), (low.green(), high.green()), (low.blue(), high.blue()))))


def _label(series: SignalSeries, name: str) -> str:
    return f'{name} [{series.unit}]' if series.unit else name


class _ColorScale(QWidget):
    """The colour scale as a strip, with the values at its two ends."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._low = self._high = 0.0
        self._name = ''
        self.setFixedHeight(self.fontMetrics().height() + 14)

    def set_range(self, name: str, low: float, high: float) -> None:
        self._name, self._low, self._high = name, low, high
        self.update()

    def text(self) -> str:
        return f'{self._low:.6g} … {self._high:.6g}  {self._name}'

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        strip = QRectF(6, 2, max(self.width() - 12, 1), 8)
        gradient = QLinearGradient(strip.left(), 0, strip.right(), 0)
        last = len(_SCALE_COLORS) - 1
        for index, color in enumerate(_SCALE_COLORS):
            gradient.setColorAt(index / last, QColor(color))
        painter.fillRect(strip, gradient)
        painter.setPen(self.palette().text().color())
        text = QRectF(6, 11, strip.width(), self.height() - 11)
        painter.drawText(text, Qt.AlignmentFlag.AlignLeft, f'{self._low:.6g}')
        painter.drawText(text, Qt.AlignmentFlag.AlignRight, f'{self._high:.6g}')
        painter.drawText(text, Qt.AlignmentFlag.AlignHCenter, self._name)
        painter.end()


class XYView(QWidget):
    """The XY plot with its signal choices. set_signals() gives it the
    plotted signals to choose from; the choices are kept by signal key, so
    one named before its signal is plotted takes effect once it is."""

    timeClicked = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # key -> (label, series, plot colour), in plotted order.
        self._signals: dict[str, tuple[str, SignalSeries, str]] = {}
        self._chosen: dict[str, str | None] = {'x': None, 'y': None, 'color': None}
        self._time_range: tuple[float, float] | None = None
        self._cursors: tuple[float | None, float | None] = (None, None)
        self._foreground = '#000000'
        self.points: XYPoints = EMPTY
        self._fitted: tuple[str | None, str | None] = (None, None)

        self.x_box, self.y_box, self.color_box = QComboBox(), QComboBox(), QComboBox()
        choices = QHBoxLayout()
        choices.setContentsMargins(4, 2, 4, 0)
        for role, text, box in (('x', 'X', self.x_box), ('y', 'Y', self.y_box),
                                ('color', 'Colour', self.color_box)):
            # A narrow pane beside the plot is not held wider by a long name.
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(4)
            box.setMinimumWidth(50)
            box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            box.activated.connect(lambda _index, role=role, box=box:
                                  self._choose(role, box.currentData()))
            choices.addWidget(QLabel(text))
            choices.addWidget(box, 1)
        self.x_box.setToolTip('The signal along the bottom. Each of its samples is a point.')
        self.y_box.setToolTip('The signal up the side: the value it had at each point\'s time.')
        self.color_box.setToolTip('A third signal as each point\'s colour, blue for low '
                                  'values and red for high.')

        self.whole_box = QCheckBox('Whole measurement')
        self.whole_box.setToolTip('Off: only the time range the time plot shows.')
        self.whole_box.toggled.connect(lambda _on: self._redraw())
        self.equal_box = QCheckBox('Equal scale')
        self.equal_box.setToolTip('One unit of X as long as one unit of Y, as for a '
                                  'track of latitude against longitude.')
        self.equal_box.toggled.connect(self._set_equal_scale)
        options = QHBoxLayout()
        options.setContentsMargins(4, 0, 4, 0)
        options.addWidget(self.whole_box)
        options.addWidget(self.equal_box)
        options.addStretch(1)
        # On its own line and wrapping, so it never holds the pane wider.
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setMinimumWidth(1)
        self.summary.setContentsMargins(4, 0, 4, 2)

        self.plot = pg.PlotWidget()
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.scatter = pg.ScatterPlotItem(pxMode=True, size=_POINT_SIZE, pen=None)
        self.mark1 = pg.ScatterPlotItem(pxMode=True, size=_MARK_SIZE, symbol='o', brush=None)
        self.mark2 = pg.ScatterPlotItem(pxMode=True, size=_MARK_SIZE, symbol='d', brush=None)
        for z, item in enumerate((self.scatter, self.mark1, self.mark2)):
            item.setZValue(z)
            self.plot.addItem(item)
        self.plot.scene().sigMouseClicked.connect(self._on_click)
        self.scale = _ColorScale()
        self.scale.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(choices)
        layout.addLayout(options)
        layout.addWidget(self.summary)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.scale)
        # Its own, not the widest row's: the pane may be dragged narrow.
        self.setMinimumWidth(240)

        self._brushes = [pg.mkBrush(scale_color(step / (COLOR_LEVELS - 1)))
                         for step in range(COLOR_LEVELS)]
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._redraw)
        self.set_colors('#ffffff', '#000000')
        self._redraw()

    # ── What it is given ──────────────────────────────────────────────────

    def set_signals(self, signals: list[tuple[str, str, SignalSeries, str]]) -> None:
        """The plotted signals: (key, name, series, plot colour) each. With
        nothing chosen yet, the first two become X and Y."""
        self._signals = {key: (_label(series, name), series, color)
                         for key, name, series, color in signals}
        keys = list(self._signals)
        if self._chosen['x'] is None and self._chosen['y'] is None and len(keys) >= 2:
            self._chosen['x'], self._chosen['y'] = keys[0], keys[1]
        for role, box in (('x', self.x_box), ('y', self.y_box), ('color', self.color_box)):
            box.blockSignals(True)
            box.clear()
            if role == 'color':
                box.addItem('None', None)
            for key, (label, _series, _color) in self._signals.items():
                box.addItem(label, key)
                box.setItemData(box.count() - 1, key, Qt.ItemDataRole.ToolTipRole)
            chosen = self._chosen[role]
            # A chosen signal that is not plotted leaves the box empty.
            box.setCurrentIndex(box.findData(chosen) if chosen in self._signals
                                else 0 if role == 'color' and chosen is None else -1)
            box.blockSignals(False)
        self._redraw()

    def set_time_range(self, time_range: tuple[float, float] | None) -> None:
        """The time the time plot shows; drawn once it stops changing."""
        if time_range == self._time_range:
            return
        self._time_range = time_range
        if not self.whole_box.isChecked():
            self._timer.start(_REDRAW_MS)

    def set_cursors(self, cursor1: float | None, cursor2: float | None) -> None:
        """Each cursor's time, None for one that is off."""
        self._cursors = (cursor1, cursor2)
        self._draw_marks()

    def set_colors(self, background: str, foreground: str) -> None:
        """The plot's background, and the colour for its axes and cursor marks."""
        self._foreground = foreground
        self.plot.setBackground(background)
        for name in ('bottom', 'left'):
            axis = self.plot.getAxis(name)
            axis.setPen(foreground)
            axis.setTextPen(foreground)
        pen = pg.mkPen(foreground, width=2)
        self.mark1.setPen(pen)
        self.mark2.setPen(pen)
        self._draw_marks()

    # ── What it keeps ─────────────────────────────────────────────────────

    def state(self) -> dict:
        """The choices, as a configuration keeps them."""
        return {**self._chosen,
                'whole_measurement': self.whole_box.isChecked(),
                'equal_scale': self.equal_box.isChecked()}

    def restore(self, state: dict) -> None:
        """Take saved choices; a signal not plotted yet is chosen once it is."""
        for role in self._chosen:
            if role in state:
                self._chosen[role] = str(state[role]) if state[role] else None
        if 'whole_measurement' in state:
            self.whole_box.setChecked(bool(state['whole_measurement']))
        if 'equal_scale' in state:
            self.equal_box.setChecked(bool(state['equal_scale']))
        self.set_signals([(key, label, series, color)
                          for key, (label, series, color) in self._signals.items()])

    def rename_signal(self, old_key: str, new_key: str) -> None:
        for role, key in self._chosen.items():
            if key == old_key:
                self._chosen[role] = new_key

    def chosen(self, role: str) -> str | None:
        """The key chosen as 'x', 'y' or 'color'."""
        return self._chosen[role]

    def choose(self, role: str, key: str | None) -> None:
        """Choose a plotted signal as 'x', 'y' or 'color' (None: no colour)."""
        self._choose(role, key)
        box = {'x': self.x_box, 'y': self.y_box, 'color': self.color_box}[role]
        box.setCurrentIndex(box.findData(key))

    # ── Drawing ───────────────────────────────────────────────────────────

    def _choose(self, role: str, key: str | None) -> None:
        self._chosen[role] = key
        self._redraw()

    def _series(self, role: str) -> SignalSeries | None:
        entry = self._signals.get(self._chosen[role] or '')
        return entry[1] if entry else None

    def _set_equal_scale(self, on: bool) -> None:
        self.plot.setAspectLocked(bool(on))
        self.plot.enableAutoRange()

    def _redraw(self) -> None:
        self._timer.stop()
        x, y, color = self._series('x'), self._series('y'), self._series('color')
        if x is None or y is None:
            self.points = EMPTY
            self.scatter.setData(x=[], y=[])
            self.scale.hide()
            self.plot.setLabel('bottom', '')
            self.plot.setLabel('left', '')
            self.summary.setText('Plot two signals to compare them here.'
                                 if len(self._signals) < 2 else 'Choose the X and Y signals.')
            self._draw_marks()
            return
        whole = self.whole_box.isChecked()
        self.points = points = xy_points(
            x, y, color, None if whole else self._time_range)
        if points.color is not None:
            steps, low, high = color_steps(points.color)
            brushes = self._brushes
            self.scatter.setData(x=points.x, y=points.y,
                                 brush=[brushes[step] for step in steps.tolist()])
            self.scale.set_range(self._signals[self._chosen['color']][0], low, high)
        else:
            self.scatter.setData(x=points.x, y=points.y,
                                 brush=pg.mkBrush(self._signals[self._chosen['y']][2]))
        self.scale.setVisible(points.color is not None and points.count > 0)
        self.plot.setLabel('bottom', self._signals[self._chosen['x']][0])
        self.plot.setLabel('left', self._signals[self._chosen['y']][0])
        pair = (self._chosen['x'], self._chosen['y'])
        if pair != self._fitted:
            # A new pair of signals has a new range; a zoom is kept otherwise.
            self._fitted = pair
            self.plot.enableAutoRange()
        self.summary.setText(self._summary(points, whole))
        self._draw_marks()

    @staticmethod
    def _summary(points: XYPoints, whole: bool) -> str:
        if not points.count:
            return ('No samples of all the chosen signals.' if whole
                    else 'No samples of all the chosen signals in the time shown.')
        text = f'{points.count:,} points'
        if points.stride > 1:
            text += f', 1 of every {points.stride} samples'
        return text

    def _draw_marks(self) -> None:
        x, y = self._series('x'), self._series('y')
        for mark, t in zip((self.mark1, self.mark2), self._cursors):
            at = xy_at(x, y, t) if t is not None and x is not None and y is not None else None
            if at is None:
                mark.setData(x=[], y=[])
            else:
                mark.setData(x=[at[0]], y=[at[1]])

    def mark_position(self, cursor: int) -> tuple[float, float] | None:
        """Where the mark of Cursor 1 or 2 is drawn; None when it is not."""
        xs, ys = (self.mark1, self.mark2)[cursor - 1].getData()
        return (float(xs[0]), float(ys[0])) if len(xs) else None

    # ── A click picks a point ─────────────────────────────────────────────

    def _on_click(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        view_box = self.plot.getPlotItem().vb
        if not view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        at = view_box.mapSceneToView(event.scenePos())
        self.pick(at.x(), at.y())

    def pick(self, x: float, y: float) -> bool:
        """Ask for Cursor 1 at the time of the point at (*x*, *y*), if one is
        within a few pixels of it."""
        pixel_x, pixel_y = self.plot.getPlotItem().vb.viewPixelSize()
        found = nearest_point(self.points, x, y, pixel_x, pixel_y)
        if found is None or found[1] > _PICK_PIXELS:
            return False
        self.timeClicked.emit(float(self.points.t[found[0]]))
        return True
