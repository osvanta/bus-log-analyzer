# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The XY view: one plotted signal against another, a point per sample of the X
signal, optionally coloured by a third signal. Its Histogram view instead
shows how long the X signal spent at each value (core.histogram), and its
Residency view how long X and Y spent together in each cell of a grid
(core.residency). A click on a bar or a cell asks for the times it covers
to be shaded (*spansChosen*).

It shows the time range the time plot shows (or the whole measurement), marks
where each cursor is, and a click on a point asks for Cursor 1 at that
point's time (*timeClicked*). The points themselves come from core.xy_data.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from core import histogram as hist
from core import residency as res

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
SCATTER, HISTOGRAM, RESIDENCY = 'scatter', 'histogram', 'residency'
_VIEWS = (SCATTER, HISTOGRAM, RESIDENCY)
#: What the bars' heights show, as the Show box offers it.
PERCENT, SECONDS, SAMPLES = 'percent', 'seconds', 'samples'
_SHOW_CHOICES = ((PERCENT, '% of time'), (SECONDS, 'Seconds'), (SAMPLES, 'Samples'))
#: The bar a click picked.
_PICKED_COLOR = '#faba39'


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


def duration_text(seconds: float) -> str:
    """12.3 s, 4:05 or 1:02:03."""
    if seconds < 60:
        return f'{seconds:.2f} s' if seconds < 10 else f'{seconds:.1f} s'
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'


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


class _CellGrid(pg.GraphicsObject):
    """The residency map's cells: each filled in its colour, empty ones left
    out, with its number written in it where the cell is large enough to
    hold it, and the picked cell outlined."""

    def __init__(self) -> None:
        super().__init__()
        self.x_edges = np.empty(0)
        self.y_edges = np.empty(0)
        self.colors: list[list[QColor | None]] = []
        self.texts: list[list[str]] = []
        self.picked: tuple[int, int] | None = None

    def set_cells(self, x_edges, y_edges, colors, texts) -> None:
        self.prepareGeometryChange()
        self.x_edges, self.y_edges = np.asarray(x_edges), np.asarray(y_edges)
        self.colors, self.texts = colors, texts
        self.update()

    def set_picked(self, cell: tuple[int, int] | None) -> None:
        self.picked = cell
        self.update()

    def boundingRect(self) -> QRectF:
        if self.x_edges.size < 2 or self.y_edges.size < 2:
            return QRectF()
        return QRectF(self.x_edges[0], self.y_edges[0], self.x_edges[-1] - self.x_edges[0],
                      self.y_edges[-1] - self.y_edges[0])

    def _rect(self, i: int, j: int) -> QRectF:
        return QRectF(self.x_edges[i], self.y_edges[j], self.x_edges[i + 1] - self.x_edges[i],
                      self.y_edges[j + 1] - self.y_edges[j])

    def written(self) -> list[tuple[int, int, str]]:
        """The cells whose number fits, as the last paint wrote them: (i, j,
        text, its colour)."""
        return list(getattr(self, '_written', []))

    def paint(self, painter, *_args) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        for i, column in enumerate(self.colors):
            for j, color in enumerate(column):
                if color is not None:
                    painter.setBrush(color)
                    painter.drawRect(self._rect(i, j))
        if self.picked is not None and self.colors:
            pen = QPen(QColor(_PICKED_COLOR), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._rect(*self.picked))
        # Numbers in device pixels, upright, where they fit.
        transform = painter.transform()
        metrics = painter.fontMetrics()
        written = []
        painter.save()
        painter.resetTransform()
        for i, column in enumerate(self.texts):
            for j, text in enumerate(column):
                color = self.colors[i][j]
                if not text or color is None:
                    continue
                box = transform.mapRect(self._rect(i, j))
                if box.width() < metrics.horizontalAdvance(text) + 4 \
                        or box.height() < metrics.height():
                    continue
                light = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue() > 150
                ink = QColor('#111111') if light else QColor('#ffffff')
                painter.setPen(ink)
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
                written.append((i, j, text, ink.name()))
        painter.restore()
        self._written = written


class XYView(QWidget):
    """The XY plot with its signal choices. set_signals() gives it the
    plotted signals to choose from; the choices are kept by signal key, so
    one named before its signal is plotted takes effect once it is."""

    timeClicked = Signal(float)
    #: The stretches of time a clicked bar covers, [(start, end), ...];
    #: empty to clear them.
    spansChosen = Signal(list)

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
        self._view = SCATTER
        self.bars: hist.Histogram | None = None
        # The bar a click picked, by its edges, its stretches, and which one
        # Cursor 1 was last sent to.
        self._picked: tuple[float, float] | None = None
        self._spans: list[tuple[float, float]] = []
        self._span_index = -1

        self.scatter_button = QPushButton('Scatter')
        self.histogram_button = QPushButton('Histogram')
        self.residency_button = QPushButton('Residency')
        self.residency_button.setToolTip('How long X and Y spent together in each cell of '
                                         'a grid: a load collective.')
        self.scatter_button.setToolTip('One signal against another, a point per sample.')
        self.histogram_button.setToolTip('How long one signal spent at each value.')
        views = QHBoxLayout()
        views.setContentsMargins(4, 2, 4, 0)
        views.setSpacing(0)
        self._view_group = QButtonGroup(self)
        for view, button in ((SCATTER, self.scatter_button), (HISTOGRAM, self.histogram_button),
                             (RESIDENCY, self.residency_button)):
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self._view_group.addButton(button)
            button.clicked.connect(lambda _on, view=view: self.set_view(view))
            views.addWidget(button)
        views.addStretch(1)
        self.scatter_button.setChecked(True)

        self.x_box, self.y_box, self.color_box = QComboBox(), QComboBox(), QComboBox()
        choices = QHBoxLayout()
        choices.setContentsMargins(4, 2, 4, 0)
        self._role_labels: dict[str, QLabel] = {}
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
            self._role_labels[role] = label = QLabel(text)
            choices.addWidget(label)
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

        # The histogram's own choices, shown in its view only.
        self.bins_edit = QLineEdit()
        self.bins_edit.setPlaceholderText('Auto')
        self.bins_edit.setToolTip('Empty for automatic bars, one number for the bar width '
                                  '(2 for 2 °C), or edges such as 0, 25, 45, 60.')
        self.bins_edit.editingFinished.connect(self._redraw)
        self.show_box = QComboBox()
        for key, text in _SHOW_CHOICES:
            self.show_box.addItem(text, key)
        self.show_box.setToolTip('What the bar heights show. Time counts how long each '
                                 'value held; Samples counts the samples taken.')
        self.show_box.activated.connect(lambda _index: self._redraw())
        self.gap_edit = QLineEdit()
        self.gap_edit.setPlaceholderText('Auto')
        self.gap_edit.setToolTip('A value held longer than this many seconds (a logger '
                                 'pause, a silent bus) is not counted. Auto: ten sample '
                                 'periods, at least 1 s.')
        self.gap_edit.editingFinished.connect(self._redraw)
        for edit in (self.bins_edit, self.gap_edit):
            edit.setMinimumWidth(40)
            edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.histogram_options = QWidget()
        bar_options = QHBoxLayout(self.histogram_options)
        bar_options.setContentsMargins(4, 0, 4, 0)
        self._bins_label = QLabel('Bins')
        for label, widget, stretch in ((self._bins_label, self.bins_edit, 2),
                                       (QLabel('Show'), self.show_box, 0),
                                       (QLabel('Gap s'), self.gap_edit, 1)):
            bar_options.addWidget(label)
            bar_options.addWidget(widget, stretch)
        self.histogram_options.hide()

        # The residency map's own choices, below those it shares.
        self.y_bins_edit = QLineEdit()
        self.y_bins_edit.setPlaceholderText('Auto')
        self.y_bins_edit.setToolTip('As for X: empty for automatic, a cell height, or edges.')
        self.y_bins_edit.editingFinished.connect(self._redraw)
        self.y_bins_edit.setMinimumWidth(40)
        self.y_bins_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.log_box = QCheckBox('Log')
        self.log_box.setToolTip('Log colour: colour by the logarithm of the time, so that a cell '
                                'holding most of it (idle, say) does not wash out the rest.')
        self.log_box.toggled.connect(lambda _on: self._redraw())
        self.copy_button = QPushButton('Copy')
        self.copy_button.setToolTip('Copy the grid as a table, to paste into a spreadsheet.')
        self.copy_button.clicked.connect(self.copy_table)
        self.export_button = QPushButton('CSV…')
        self.export_button.setToolTip('Export the grid as a table to a CSV file.')
        self.export_button.clicked.connect(self._export_clicked)
        self.residency_options = QWidget()
        cell_options = QHBoxLayout(self.residency_options)
        cell_options.setContentsMargins(4, 0, 4, 0)
        cell_options.addWidget(QLabel('Y bins'))
        cell_options.addWidget(self.y_bins_edit, 1)
        cell_options.addWidget(self.log_box)
        cell_options.addWidget(self.copy_button)
        cell_options.addWidget(self.export_button)
        self.residency_options.hide()
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

        self.hist_plot = pg.PlotWidget()
        self.hist_plot.setMenuEnabled(False)
        self.hist_plot.showGrid(x=False, y=True, alpha=0.25)
        self.hist_plot.getPlotItem().vb.setMouseEnabled(x=False, y=False)
        self.bar_item = pg.BarGraphItem(x=[], height=[], width=1.0)
        self.picked_item = pg.BarGraphItem(x=[], height=[], width=1.0,
                                           brush=_PICKED_COLOR, pen=None)
        self.hist_plot.addItem(self.bar_item)
        self.hist_plot.addItem(self.picked_item)
        self.cursor_lines = []
        for style in (Qt.PenStyle.DashLine, Qt.PenStyle.DotLine):
            line = pg.InfiniteLine(angle=90, movable=False)
            line.setZValue(5)
            line.hide()
            line.pen_style = style
            self.hist_plot.addItem(line, ignoreBounds=True)
            self.cursor_lines.append(line)
        self.hist_plot.scene().sigMouseMoved.connect(self._on_bar_hover)
        self.hist_plot.scene().sigMouseClicked.connect(self._on_bar_click)
        self.readout = QLabel('Point at a bar to read it. Click it to shade its times on the plot.')
        self.readout.setWordWrap(True)
        self.readout.setMinimumWidth(1)
        self.readout.setContentsMargins(4, 2, 4, 2)
        self.readout.hide()

        self.res_plot = pg.PlotWidget()
        self.res_plot.setMenuEnabled(False)
        self.res_plot.getPlotItem().vb.setMouseEnabled(x=False, y=False)
        self.cells = _CellGrid()
        self.res_plot.addItem(self.cells)
        self.res_marks = []
        for symbol in ('o', 'd'):
            mark = pg.ScatterPlotItem(pxMode=True, size=_MARK_SIZE, symbol=symbol, brush=None)
            mark.setZValue(5)
            self.res_plot.addItem(mark)
            self.res_marks.append(mark)
        self.res_plot.scene().sigMouseMoved.connect(self._on_cell_hover)
        self.res_plot.scene().sigMouseClicked.connect(self._on_cell_click)
        self.grid: res.Residency | None = None

        self.plots = QStackedWidget()
        self.plots.addWidget(self.plot)
        self.plots.addWidget(self.hist_plot)
        self.plots.addWidget(self.res_plot)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(views)
        layout.addLayout(choices)
        layout.addLayout(options)
        layout.addWidget(self.histogram_options)
        layout.addWidget(self.residency_options)
        layout.addWidget(self.summary)
        layout.addWidget(self.plots, 1)
        layout.addWidget(self.readout)
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
        # The first plotted signal is X (all a histogram needs), the next Y.
        if self._chosen['x'] is None and self._chosen['y'] is None and keys:
            self._chosen['x'] = keys[0]
        if self._chosen['y'] is None and len(keys) >= 2:
            self._chosen['y'] = next(key for key in keys if key != self._chosen['x'])
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
        for plot in (self.plot, self.hist_plot, self.res_plot):
            plot.setBackground(background)
            for name in ('bottom', 'left'):
                axis = plot.getAxis(name)
                axis.setPen(foreground)
                axis.setTextPen(foreground)
        pen = pg.mkPen(foreground, width=2)
        self.mark1.setPen(pen)
        self.mark2.setPen(pen)
        for line in self.cursor_lines:
            line.setPen(pg.mkPen(foreground, width=1.5, style=line.pen_style))
        for mark in self.res_marks:
            mark.setPen(pen)
        self._draw_marks()

    # ── What it keeps ─────────────────────────────────────────────────────

    def state(self) -> dict:
        """The choices, as a configuration keeps them."""
        return {**self._chosen,
                'whole_measurement': self.whole_box.isChecked(),
                'equal_scale': self.equal_box.isChecked(),
                'view': self._view,
                'bins': self.bins_edit.text().strip(),
                'show': self.show_box.currentData(),
                'gap': self.gap_edit.text().strip(),
                'y_bins': self.y_bins_edit.text().strip(),
                'log_color': self.log_box.isChecked()}

    def restore(self, state: dict) -> None:
        """Take saved choices; a signal not plotted yet is chosen once it is."""
        for role in self._chosen:
            if role in state:
                self._chosen[role] = str(state[role]) if state[role] else None
        if 'whole_measurement' in state:
            self.whole_box.setChecked(bool(state['whole_measurement']))
        if 'equal_scale' in state:
            self.equal_box.setChecked(bool(state['equal_scale']))
        if 'bins' in state:
            self.bins_edit.setText(str(state['bins'] or ''))
        if 'gap' in state:
            self.gap_edit.setText(str(state['gap'] or ''))
        if 'y_bins' in state:
            self.y_bins_edit.setText(str(state['y_bins'] or ''))
        if 'log_color' in state:
            self.log_box.setChecked(bool(state['log_color']))
        index = self.show_box.findData(state.get('show'))
        if index >= 0:
            self.show_box.setCurrentIndex(index)
        # An older configuration has no view: it opens in Scatter.
        self._set_view(state.get('view') if state.get('view') in _VIEWS else SCATTER)
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

    def view(self) -> str:
        """SCATTER or HISTOGRAM."""
        return self._view

    def set_view(self, view: str) -> None:
        """Show the scatter plot, the histogram of the X signal or the
        residency map of X and Y."""
        if view != self._view:
            self._set_view(view)
            self._redraw()

    def _set_view(self, view: str) -> None:
        self._view = view = view if view in _VIEWS else SCATTER
        bars, cells = view == HISTOGRAM, view == RESIDENCY
        {SCATTER: self.scatter_button, HISTOGRAM: self.histogram_button,
         RESIDENCY: self.residency_button}[view].setChecked(True)
        for role, box in (('y', self.y_box), ('color', self.color_box)):
            self._role_labels[role].setVisible(not bars)
            box.setVisible(not bars)
        self._role_labels['x'].setText('Signal' if bars else 'X')
        self._role_labels['color'].setText('Mean of' if cells else 'Colour')
        self.color_box.setToolTip(
            'A third signal whose average each cell shows instead of the time.' if cells
            else 'A third signal as each point\'s colour, blue for low values and red for high.')
        self._bins_label.setText('X bins' if cells else 'Bins')
        self.equal_box.setVisible(view == SCATTER)
        self.histogram_options.setVisible(bars or cells)
        self.residency_options.setVisible(cells)
        self.readout.setVisible(bars or cells)
        self.readout.setText(
            'Point at a cell to read it. Click it to shade its times on the plot.' if cells
            else 'Point at a bar to read it. Click it to shade its times on the plot.')
        self.plots.setCurrentWidget({SCATTER: self.plot, HISTOGRAM: self.hist_plot,
                                     RESIDENCY: self.res_plot}[view])
        if view != SCATTER:
            self.scale.hide()
        self._clear_pick()

    # ── Drawing ───────────────────────────────────────────────────────────

    def _choose(self, role: str, key: str | None) -> None:
        self._chosen[role] = key
        if role in ('x', 'y'):
            self._clear_pick()
        self._redraw()

    def _series(self, role: str) -> SignalSeries | None:
        entry = self._signals.get(self._chosen[role] or '')
        return entry[1] if entry else None

    def _set_equal_scale(self, on: bool) -> None:
        self.plot.setAspectLocked(bool(on))
        self.plot.enableAutoRange()

    def _redraw(self) -> None:
        self._timer.stop()
        if self._view == HISTOGRAM:
            self._redraw_histogram()
            return
        if self._view == RESIDENCY:
            self._redraw_residency()
            return
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
        if self._view == HISTOGRAM:
            self._draw_cursor_lines()
            return
        if self._view == RESIDENCY:
            self._draw_res_marks()
            return
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

    # ── Histogram ─────────────────────────────────────────────────────────

    def _histogram_range(self) -> tuple[float, float] | None:
        return None if self.whole_box.isChecked() else self._time_range

    def _gap(self) -> float | None:
        try:
            gap = float(self.gap_edit.text().strip())
        except ValueError:
            return None
        return gap if gap > 0 else None

    def _redraw_histogram(self) -> None:
        series = self._series('x')
        plot = self.hist_plot.getPlotItem()
        if series is None:
            self.bars = None
            self.bar_item.setOpts(x=[], height=[], width=1.0)
            self.picked_item.setOpts(x=[], height=[], width=1.0)
            plot.setLabel('bottom', '')
            plot.setLabel('left', '')
            self.summary.setText('Plot a signal to see how long it spent at each value.')
            self._draw_cursor_lines()
            return
        problem = ''
        try:
            bins = hist.parse_bins(self.bins_edit.text())
        except ValueError:
            bins = None
            problem = ('Bins not understood, shown automatic: type a width such as 2, '
                       'or edges such as 0, 25, 45. ')
        show = self.show_box.currentData()
        self.bars = bars = hist.histogram(
            series, self._histogram_range(), bins,
            hist.SAMPLES if show == SAMPLES else hist.TIME, self._gap())
        heights = self._heights(bars)
        centres = (bars.edges[:-1] + bars.edges[1:]) / 2 if bars.edges.size else bars.edges
        widths = np.diff(bars.edges) * (0.8 if bars.labels else 0.94)
        self.bar_item.setOpts(x=centres, height=heights, width=widths,
                              brush=pg.mkBrush(self._signals[self._chosen['x']][2]), pen=None)
        axis = plot.getAxis('bottom')
        axis.setTicks([list(enumerate(bars.labels))] if bars.labels else None)
        plot.setLabel('bottom', self._signals[self._chosen['x']][0])
        plot.setLabel('left', {PERCENT: '% of time', SECONDS: 'Time [s]',
                               SAMPLES: 'Samples'}[show])
        plot.enableAutoRange()
        self.summary.setText(problem + self._histogram_summary(bars))
        self._draw_picked()
        self._draw_cursor_lines()

    def _heights(self, bars: hist.Histogram) -> np.ndarray:
        if self.show_box.currentData() == PERCENT:
            total = bars.total
            return bars.weights * (100.0 / total) if total > 0 else bars.weights * 0.0
        return bars.weights

    def _histogram_summary(self, bars: hist.Histogram) -> str:
        if not bars.weights.size:
            return ('No samples of this signal.' if self.whole_box.isChecked()
                    else 'No samples of this signal in the time shown.')
        text = (f'{duration_text(bars.counted)} of {duration_text(bars.covered)} counted '
                f'(gap limit {bars.gap_limit:.3g} s)')
        stats = bars.stats
        if stats and not bars.labels:
            text += (f' · min {stats["min"]:.6g} · max {stats["max"]:.6g} · mean '
                     f'{stats["mean"]:.6g} · P5 {stats["p5"]:.6g} · P50 {stats["p50"]:.6g}'
                     f' · P95 {stats["p95"]:.6g}')
        total = bars.total
        for name, part in (('below', bars.below), ('above', bars.above)):
            if part > 0 and total > 0:
                text += f' · {100 * part / total:.1f} % {name} the edges'
        return text

    def bar_text(self, index: int) -> str:
        """What a bar holds, as the readout shows it."""
        bars = self.bars
        series = self._series('x')
        unit = f' {series.unit}' if series is not None and series.unit else ''
        if bars.labels:
            where = bars.labels[index]
        else:
            where = f'{bars.edges[index]:.6g} to {bars.edges[index + 1]:.6g}{unit}'
        total_time = float(bars.times.sum())
        share = 100 * bars.times[index] / total_time if total_time > 0 else 0.0
        return (f'{where} · {share:.1f} % of time · {duration_text(float(bars.times[index]))}'
                f' · {int(bars.counts[index]):,} samples')

    def _bar_at(self, scene_pos) -> int | None:
        view_box = self.hist_plot.getPlotItem().vb
        if not view_box.sceneBoundingRect().contains(scene_pos):
            return None
        return self.bar_at_value(view_box.mapSceneToView(scene_pos).x())

    def bar_at_value(self, x: float) -> int | None:
        """The bar drawn at *x* along the bottom."""
        bars = self.bars
        if bars is None or not bars.edges.size or x < bars.edges[0] or x > bars.edges[-1]:
            return None
        return int(min(np.searchsorted(bars.edges, x, side='right') - 1, bars.weights.size - 1))

    def _on_bar_hover(self, scene_pos) -> None:
        if self._view != HISTOGRAM:
            return
        index = self._bar_at(scene_pos)
        if index is not None:
            self.readout.setText(self.bar_text(index) + self._stretch_text(
                (float(self.bars.edges[index]), float(self.bars.edges[index + 1]))))

    def _on_bar_click(self, event) -> None:
        if self._view != HISTOGRAM or event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.hist_plot.getPlotItem().vb.sceneBoundingRect().contains(event.scenePos()):
            return
        self.pick_bar(self._bar_at(event.scenePos()))

    def pick_bar(self, index: int | None) -> None:
        """Shade the times bar *index* covers and send Cursor 1 to the first;
        the same bar again sends it to the next. None clears the shading."""
        bars, series = self.bars, self._series('x')
        if index is None or bars is None or series is None:
            self._clear_pick()
            return
        edges = (float(bars.edges[index]), float(bars.edges[index + 1]))
        if edges == self._picked and self._spans:
            self._span_index = (self._span_index + 1) % len(self._spans)
        else:
            self._picked = edges
            if bars.labels:
                value = bars.values[index]
                self._spans = hist.spans(series, value, value, self._histogram_range(),
                                         self._gap(), last=True)
            else:
                self._spans = hist.spans(series, edges[0], edges[1], self._histogram_range(),
                                         self._gap(), last=index == bars.weights.size - 1)
            self._span_index = 0
            self.spansChosen.emit(list(self._spans))
        self._draw_picked()
        self.readout.setText(self.bar_text(index) + self._stretch_text(edges))
        if self._spans:
            self.timeClicked.emit(float(self._spans[self._span_index][0]))

    def picked_spans(self) -> list[tuple[float, float]]:
        return list(self._spans)

    def _stretch_text(self, key: tuple) -> str:
        if not self._spans or self._picked != key:
            return ''
        return (f' · stretch {self._span_index + 1} of {len(self._spans)}, '
                f'click again for the next')

    def _clear_pick(self) -> None:
        had = self._picked is not None
        self._picked, self._spans, self._span_index = None, [], -1
        self.picked_item.setOpts(x=[], height=[], width=1.0)
        self.cells.set_picked(None)
        if had:
            self.spansChosen.emit([])

    def _draw_picked(self) -> None:
        bars = self.bars
        index = None
        if self._picked is not None and len(self._picked) == 2 and bars is not None \
                and bars.edges.size:
            for i in range(bars.weights.size):
                if (float(bars.edges[i]), float(bars.edges[i + 1])) == self._picked:
                    index = i
                    break
        if index is None:
            self.picked_item.setOpts(x=[], height=[], width=1.0)
            return
        heights = self._heights(bars)
        width = (bars.edges[index + 1] - bars.edges[index]) * (0.8 if bars.labels else 0.94)
        self.picked_item.setOpts(x=[(bars.edges[index] + bars.edges[index + 1]) / 2],
                                 height=[heights[index]], width=width)

    def _draw_cursor_lines(self) -> None:
        series, bars = self._series('x'), self.bars
        for line, t in zip(self.cursor_lines, self._cursors):
            value = hist.value_at(series, t) if t is not None and series is not None else None
            if value is not None and bars is not None and bars.labels:
                matches = [i for i, v in enumerate(bars.values) if v == value]
                value = float(matches[0]) if matches else None
            if value is None or self._view != HISTOGRAM:
                line.hide()
            else:
                line.setValue(value)
                line.show()

    def cursor_line_value(self, cursor: int) -> float | None:
        """Where Cursor 1 or 2's line is drawn; None when it is not."""
        line = self.cursor_lines[cursor - 1]
        return float(line.value()) if line.isVisible() else None

    # ── Residency map ─────────────────────────────────────────────────────

    def _cell_values(self, grid: res.Residency) -> np.ndarray:
        """What each cell shows: the third signal's mean, or its time or
        samples as the Show box asks."""
        if grid.mean is not None:
            return grid.mean
        show = self.show_box.currentData()
        if show == PERCENT:
            total = grid.total
            return grid.weights * (100.0 / total) if total > 0 else grid.weights * 0.0
        return grid.weights

    def _redraw_residency(self) -> None:
        x, y, z = self._series('x'), self._series('y'), self._series('color')
        plot = self.res_plot.getPlotItem()
        if x is None or y is None:
            self.grid = None
            self.cells.set_cells([], [], [], [])
            plot.setLabel('bottom', '')
            plot.setLabel('left', '')
            self.scale.hide()
            self.summary.setText('Plot two signals to see how long they spent together '
                                 'in each cell.' if len(self._signals) < 2
                                 else 'Choose the X and Y signals.')
            self._draw_res_marks()
            return
        problems = []
        bins = []
        for name, edit in (('X bins', self.bins_edit), ('Y bins', self.y_bins_edit)):
            try:
                bins.append(hist.parse_bins(edit.text()))
            except ValueError:
                bins.append(None)
                problems.append(name)
        gap = self._gap()
        show = self.show_box.currentData()
        self.grid = grid = res.residency(
            x, y, self._histogram_range(), bins[0], bins[1],
            hist.SAMPLES if show == SAMPLES else hist.TIME, gap, gap, z)
        values = self._cell_values(grid)
        filled = (grid.times > 0) | (grid.counts > 0)
        if grid.mean is not None:
            filled &= np.isfinite(grid.mean)
        else:
            filled &= grid.weights > 0
        colors, texts = [], []
        shown = values[filled]
        low = float(shown.min()) if shown.size else 0.0
        high = float(shown.max()) if shown.size else 1.0
        use_log = self.log_box.isChecked()      # for time and samples, not a mean
        self.log_box.setEnabled(grid.mean is None)
        for i in range(values.shape[0]):
            column_colors, column_texts = [], []
            for j in range(values.shape[1]):
                if not filled[i, j]:
                    column_colors.append(None)
                    column_texts.append('')
                    continue
                value = float(values[i, j])
                if grid.mean is not None:
                    fraction = (value - low) / (high - low) if high > low else 0.5
                elif use_log:
                    fraction = float(np.log10(1 + 999 * value / high) / 3) if high > 0 else 0.0
                else:
                    fraction = value / high if high > 0 else 0.0
                column_colors.append(scale_color(fraction))
                column_texts.append(self._cell_number(value, show, grid.mean is not None))
            colors.append(column_colors)
            texts.append(column_texts)
        self.cells.set_cells(grid.x_edges, grid.y_edges, colors, texts)
        self._draw_picked_cell()
        plot.setLabel('bottom', self._signals[self._chosen['x']][0])
        plot.setLabel('left', self._signals[self._chosen['y']][0])
        plot.enableAutoRange()
        if shown.size:
            name = (self._signals[self._chosen['color']][0] + ', mean' if grid.mean is not None
                    else {PERCENT: '% of time', SECONDS: 'seconds', SAMPLES: 'samples'}[show]
                    + (' (log colour)' if use_log else ''))
            self.scale.set_range(name, 0.0 if grid.mean is None else low, high)
            self.scale.show()
        else:
            self.scale.hide()
        text = ''
        if problems:
            text = (f'{" and ".join(problems)} not understood, shown automatic: type a width '
                    f'such as 500, or edges such as 0, 1000, 3000. ')
        self.summary.setText(text + self._residency_summary(grid))
        self._draw_res_marks()

    @staticmethod
    def _cell_number(value: float, show: str, mean: bool) -> str:
        if mean:
            return f'{value:.3g}'
        if show == PERCENT:
            return f'{value:.0f}' if value >= 1 else f'{value:.1f}'
        if show == SAMPLES:
            return f'{int(round(value))}'
        return f'{value:.3g}'

    def _residency_summary(self, grid: res.Residency) -> str:
        if not grid.weights.size:
            return ('No time with both signals.' if self.whole_box.isChecked()
                    else 'No time with both signals in the time shown.')
        used = int(np.count_nonzero((grid.times > 0) | (grid.counts > 0)))
        text = (f'{duration_text(grid.counted)} of {duration_text(grid.covered)} counted '
                f'(gap limit {grid.x_limit:.3g} s / {grid.y_limit:.3g} s) · {used} of '
                f'{grid.weights.size} cells used')
        total = grid.total
        if grid.outside > 0 and total > 0:
            text += f' · {100 * grid.outside / total:.1f} % outside the grid'
        return text

    def cell_text(self, cell: tuple[int, int]) -> str:
        """What a cell holds, as the readout shows it."""
        grid = self.grid
        i, j = cell
        x, y = self._series('x'), self._series('y')
        x_unit = f' {x.unit}' if x is not None and x.unit else ''
        y_unit = f' {y.unit}' if y is not None and y.unit else ''
        total_time = float(grid.times.sum())
        share = 100 * grid.times[i, j] / total_time if total_time > 0 else 0.0
        text = (f'{grid.x_edges[i]:.6g} to {grid.x_edges[i + 1]:.6g}{x_unit} · '
                f'{grid.y_edges[j]:.6g} to {grid.y_edges[j + 1]:.6g}{y_unit} · '
                f'{share:.1f} % of time · {duration_text(float(grid.times[i, j]))} · '
                f'{int(grid.counts[i, j]):,} samples')
        if grid.mean is not None and np.isfinite(grid.mean[i, j]):
            text += f' · mean {self._signals[self._chosen["color"]][0]} {grid.mean[i, j]:.6g}'
        return text

    def cell_at_value(self, x: float, y: float) -> tuple[int, int] | None:
        """The cell drawn at (*x*, *y*)."""
        return self.grid.cell_of(x, y) if self.grid is not None else None

    def _cell_at(self, scene_pos) -> tuple[int, int] | None:
        view_box = self.res_plot.getPlotItem().vb
        if not view_box.sceneBoundingRect().contains(scene_pos):
            return None
        at = view_box.mapSceneToView(scene_pos)
        return self.cell_at_value(at.x(), at.y())

    def _cell_key(self, cell: tuple[int, int]) -> tuple:
        grid = self.grid
        i, j = cell
        return (float(grid.x_edges[i]), float(grid.x_edges[i + 1]),
                float(grid.y_edges[j]), float(grid.y_edges[j + 1]))

    def _on_cell_hover(self, scene_pos) -> None:
        if self._view != RESIDENCY:
            return
        cell = self._cell_at(scene_pos)
        if cell is not None:
            self.readout.setText(self.cell_text(cell) + self._stretch_text(self._cell_key(cell)))

    def _on_cell_click(self, event) -> None:
        if self._view != RESIDENCY or event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.res_plot.getPlotItem().vb.sceneBoundingRect().contains(event.scenePos()):
            return
        self.pick_cell(self._cell_at(event.scenePos()))

    def pick_cell(self, cell: tuple[int, int] | None) -> None:
        """Shade the times cell (i, j) covers and send Cursor 1 to the first;
        the same cell again sends it to the next. None clears the shading."""
        grid, x, y = self.grid, self._series('x'), self._series('y')
        if cell is None or grid is None or x is None or y is None:
            self._clear_pick()
            return
        key = self._cell_key(cell)
        if key == self._picked and self._spans:
            self._span_index = (self._span_index + 1) % len(self._spans)
        else:
            self._picked = key
            gap = self._gap()
            self._spans = res.cell_spans(
                x, y, key[:2], key[2:], self._histogram_range(), gap, gap,
                x_last=cell[0] == grid.x_edges.size - 2, y_last=cell[1] == grid.y_edges.size - 2)
            self._span_index = 0
            self.spansChosen.emit(list(self._spans))
        self._draw_picked_cell()
        self.readout.setText(self.cell_text(cell) + self._stretch_text(key))
        if self._spans:
            self.timeClicked.emit(float(self._spans[self._span_index][0]))

    def _draw_picked_cell(self) -> None:
        grid, picked = self.grid, None
        if self._picked is not None and len(self._picked) == 4 and grid is not None:
            for i in range(grid.x_edges.size - 1):
                for j in range(grid.y_edges.size - 1):
                    if self._cell_key((i, j)) == self._picked:
                        picked = (i, j)
        self.cells.set_picked(picked)

    def _draw_res_marks(self) -> None:
        x, y = self._series('x'), self._series('y')
        for mark, t in zip(self.res_marks, self._cursors):
            at = xy_at(x, y, t) if t is not None and x is not None and y is not None else None
            if at is None:
                mark.setData(x=[], y=[])
            else:
                mark.setData(x=[at[0]], y=[at[1]])

    def res_mark_position(self, cursor: int) -> tuple[float, float] | None:
        """Where Cursor 1 or 2's ring is on the residency map; None when it is not."""
        xs, ys = self.res_marks[cursor - 1].getData()
        return (float(xs[0]), float(ys[0])) if len(xs) else None

    def table(self, separator: str = ',') -> str:
        """The residency map as a table of what the cells show."""
        grid = self.grid
        if grid is None or not grid.weights.size:
            return ''
        return res.table_text(grid, self._cell_values(grid), self._signals[self._chosen['x']][0],
                              self._signals[self._chosen['y']][0], separator)

    def copy_table(self) -> None:
        """Put the table on the clipboard, tab-separated for a spreadsheet."""
        text = self.table('\t')
        if text:
            QApplication.clipboard().setText(text)
            self.readout.setText('Table copied: paste it into a spreadsheet.')

    def export_csv(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8', newline='') as file:
            file.write(self.table(','))

    def _export_clicked(self) -> None:
        if not self.table():
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, 'Export residency map', 'residency_map.csv', 'CSV files (*.csv)')
        if path:
            try:
                self.export_csv(path)
            except OSError as error:
                self.readout.setText(f'Not saved: {error.strerror or error}.')
                return
            self.readout.setText(f'Saved {path}.')
