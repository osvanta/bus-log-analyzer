# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The GPS track view: the drive seen from above, a panel beside the time plot
like the XY views. Its latitude and longitude are found among every decoded
signal (core.gps_track), so they need not be plotted. The route is drawn on
an equal-scale grid, north up, with a scale bar, optionally coloured by a
third signal; where the receiver had no fix the line is dashed.

It is linked to the time plot both ways: the cursors are marks on the route,
a click on the route asks for Cursor 1 at the time the vehicle was there
(*timeClicked*; a second click where the route passes twice goes to the other
pass), and Shift+drag of a box asks for the times the vehicle was inside it
to be shaded (*spansChosen*). The part of the drive outside the time the
time plot shows is dimmed and takes no clicks.

Under the route it can show streets from a map file kept on the PC (a raster
.mbtiles file, gui.map_background), chosen under the gear; nothing is
fetched over the network. The file's credit is shown in the map's corner.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsRectItem, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from core import gps_track as gps
from core import map_tiles
from core.signal_store import SignalSeries
from core.xy_data import color_steps
from gui.map_background import MapBackground
from gui.panel_area import STRIP_BUTTON_SIZE, TITLE_BUTTONS_ROOM, HeaderBand
from gui.plot_icons import plot_icon
from gui.xy_view import _SCALE_COLORS, _ElidedLabel, duration_text, scale_color

TRACK = 'track'
TRACK_NAME = 'GPS track'
#: A click this near the route, in pixels, picks it.
_PICK_PIXELS = 10.0
#: Cursor 1 this near a pass's time, in seconds, is on that pass.
_SAME_PASS_S = 2.0
_REDRAW_MS = 60
_MARK_SIZE = 15
_LINE_WIDTH = 2.5
_LINE_COLOR = '#36aaf9'
_START_COLOR = '#3fa36b'
_END_COLOR = '#d2493f'
_BOX_COLOR = '#faba39'
#: Colour steps of the route: one curve each.
_LEVELS = 24
_OVERLAY_INSET = 4
_MESSAGE_MS = 4000
_DEFAULT_OPTIONS = {'whole_measurement': False, 'gap': '', 'map': ''}
_HINT = ('Click the route to put Cursor 1 there. '
         'Shift+drag a box to shade the times the vehicle was inside it.')
_MAP_FILTER = 'Map files (*.mbtiles);;All files (*)'
#: Over a map the route gets a dark edge, so that it stands out from roads
#: drawn in the same colours.
_CASING_COLOR = QColor(20, 20, 20, 170)
_CASING_WIDTH = _LINE_WIDTH + 3
#: A map, which is mostly light, is faded on a dark background.
_DARK_MAP_OPACITY = 0.6
_ZOOM_IN = 'Zoom in to see the map'
#: Where the last map file was chosen, for the next choice.
_last_map_folder = ''


#: What is drawn over the map lies on the map's background at this opacity.
_BACKDROP_ALPHA = 200


def _backdrop(background: str) -> QColor:
    color = QColor(background)
    color.setAlpha(_BACKDROP_ALPHA)
    return color


class _Readout(_ElidedLabel):
    """The reading over the map's top-left corner: one line on a backdrop."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.backdrop = QColor(255, 255, 255, _BACKDROP_ALPHA)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        text = self.shown_text()
        if text:
            margins = self.contentsMargins()
            width = self.fontMetrics().horizontalAdvance(text) + margins.left() + margins.right()
            painter.fillRect(QRect(0, 0, min(width, self.width()), self.height()), self.backdrop)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.end()


class _ColorScale(QWidget):
    """The colour scale as a thin bar inside the map's right edge: high at
    its top, low at its foot, the values at its ends and, along it, the
    signal the colours show."""

    BAR = 8
    GAP = 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._low = self._high = 0.0
        self._name = ''
        self._ink = QColor('#000000')
        self._backdrop = _backdrop('#ffffff')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_range(self, name: str, low: float, high: float) -> None:
        self._name, self._low, self._high = name, low, high
        self.update()

    def set_colors(self, background: str, foreground: str) -> None:
        self._ink, self._backdrop = QColor(foreground), _backdrop(background)
        self.update()

    def text(self) -> str:
        return f'{self._low:.6g} … {self._high:.6g}  {self._name}'

    def ends(self) -> tuple[str, str]:
        return f'{self._high:.6g}', f'{self._low:.6g}'

    def needed_width(self) -> int:
        metrics = self.fontMetrics()
        words = max(max(metrics.horizontalAdvance(text) for text in self.ends()) + 4,
                    metrics.height())
        return words + self.GAP + self.BAR

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        bar = QRectF(self.width() - self.BAR, 0, self.BAR, self.height())
        gradient = QLinearGradient(0, bar.bottom(), 0, bar.top())
        last = len(_SCALE_COLORS) - 1
        for index, color in enumerate(_SCALE_COLORS):
            gradient.setColorAt(index / last, QColor(color))
        painter.fillRect(bar, gradient)
        metrics = self.fontMetrics()
        line = metrics.height()
        right = bar.left() - self.GAP
        high, low = self.ends()
        painter.setPen(self._ink)
        for text, top in ((high, 0.0), (low, self.height() - line)):
            width = metrics.horizontalAdvance(text) + 4
            box = QRectF(right - width, top, width, line)
            painter.fillRect(box, self._backdrop)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        room = self.height() - 2 * line - 2 * self.GAP
        if room > 3 * line:
            name = metrics.elidedText(self._name, Qt.TextElideMode.ElideRight, int(room))
            width = metrics.horizontalAdvance(name) + 4
            painter.save()
            painter.translate(right - line, self.height() / 2 + width / 2)
            painter.rotate(-90)
            box = QRectF(0, 0, width, line)
            painter.fillRect(box, self._backdrop)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, name)
            painter.restore()
        painter.end()


def _signal_label(series: SignalSeries) -> str:
    unit = f' [{series.unit}]' if series.unit else ''
    return f'{series.message_name}.{series.signal_name}{unit}'


def _nice(length: float) -> float:
    """The largest 1, 2 or 5 times a power of ten not above *length*."""
    if length <= 0 or not math.isfinite(length):
        return 0.0
    power = 10.0 ** math.floor(math.log10(length))
    for step in (5.0, 2.0, 1.0):
        if step * power <= length:
            return step * power
    return power


def distance_text(metres: float) -> str:
    """850 m, 4.32 km, 128 km."""
    if metres < 1000:
        return f'{metres:.0f} m'
    return f'{metres / 1000:.2f} km' if metres < 100_000 else f'{metres / 1000:.0f} km'


class _Grid(pg.GraphicsObject):
    """Grid lines at a round spacing in metres over whatever the view shows."""

    def __init__(self) -> None:
        super().__init__()
        self.color = QColor(128, 128, 128, 50)
        self.spacing = 0.0
        self.setZValue(-20)

    def viewRangeChanged(self) -> None:
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:
        box = self.getViewBox()
        if box is None:
            return QRectF()
        (x0, x1), (y0, y1) = box.viewRange()
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    def paint(self, painter, *_args) -> None:
        box = self.getViewBox()
        if box is None:
            return
        (x0, x1), (y0, y1) = box.viewRange()
        self.spacing = step = _nice(max(x1 - x0, y1 - y0) / 6)
        if step <= 0:
            return
        pen = QPen(self.color)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for x in np.arange(math.ceil(x0 / step) * step, x1, step).tolist():
            painter.drawLine(QPointF(x, y0), QPointF(x, y1))
        for y in np.arange(math.ceil(y0 / step) * step, y1, step).tolist():
            painter.drawLine(QPointF(x0, y), QPointF(x1, y))


class _MapKey(QWidget):
    """North arrow and scale bar, drawn over the plot's bottom-left corner."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.metres_per_pixel = 0.0
        self.color = QColor('#000000')
        self.backdrop = QColor(255, 255, 255, 200)
        self.length = 0.0       # the bar's length in metres

    def text(self) -> str:
        return distance_text(self.length) if self.length else ''

    def set_scale(self, metres_per_pixel: float) -> None:
        self.metres_per_pixel = metres_per_pixel
        self.length = _nice(80 * metres_per_pixel) if metres_per_pixel > 0 else 0.0
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(190, 34)

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(11)
        return font

    def needed_width(self) -> int:
        """The width it draws on: arrow, bar and the bar's length."""
        bar = self.length / self.metres_per_pixel if self.metres_per_pixel > 0 else 0.0
        return int(26 + bar + 8 + QFontMetrics(self._font()).horizontalAdvance(self.text())) + 6

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = self._font()
        painter.setFont(font)
        bar = self.length / self.metres_per_pixel if self.metres_per_pixel > 0 else 0.0
        width = self.needed_width()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.backdrop)
        painter.drawRoundedRect(QRectF(0, 0, min(width, self.width()), self.height()), 3, 3)
        painter.setBrush(self.color)
        # North: an arrow up with an N under it.
        arrow = QPolygonF([QPointF(11, 3), QPointF(17, 17), QPointF(11, 13), QPointF(5, 17)])
        painter.drawPolygon(arrow)
        painter.setPen(QPen(self.color))
        painter.drawText(QRectF(3, 17, 16, 15), Qt.AlignmentFlag.AlignCenter, 'N')
        if bar > 0:
            pen = QPen(self.color, 1.5)
            painter.setPen(pen)
            left, bottom = 26.0, self.height() - 9.0
            painter.drawLine(QPointF(left, bottom - 5), QPointF(left, bottom))
            painter.drawLine(QPointF(left, bottom), QPointF(left + bar, bottom))
            painter.drawLine(QPointF(left + bar, bottom - 5), QPointF(left + bar, bottom))
            painter.drawText(QRectF(left + bar + 6, 0, 80, self.height() - 2),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             self.text())


class _TrackViewBox(pg.ViewBox):
    """Pans with a drag as any plot; Shift+drag draws a box instead."""

    def __init__(self) -> None:
        super().__init__()
        self.on_box = None              # called with the two corners, in metres
        self.box_item = QGraphicsRectItem()
        pen = QPen(QColor(_BOX_COLOR), 1.5, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.box_item.setPen(pen)
        fill = QColor(_BOX_COLOR)
        fill.setAlpha(30)
        self.box_item.setBrush(fill)
        self.box_item.setZValue(30)
        self.box_item.hide()
        self.addItem(self.box_item, ignoreBounds=True)

    def mouseDragEvent(self, event, axis=None) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            event.accept()
            start = self.mapToView(event.buttonDownPos())
            end = self.mapToView(event.pos())
            self.box_item.setRect(QRectF(start, end).normalized())
            self.box_item.show()
            if event.isFinish() and self.on_box is not None:
                self.on_box(start.x(), start.y(), end.x(), end.y())
            return
        super().mouseDragEvent(event, axis)


class GPSView(QWidget):
    """The GPS track, a panel of its own. set_decoded() gives it every
    decoded signal, to find the latitude and longitude among; the choices
    are kept by signal key, None meaning found on their own.

    Its settings are behind the gear in its top row and change the view
    only when Update is pressed."""

    timeClicked = Signal(float)
    #: The stretches of time the vehicle was inside the box, [(start, end),
    #: ...]; empty to clear them.
    spansChosen = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._decoded: dict[str, SignalSeries] = {}
        self._plotted: list[str] = []
        self._chosen: dict[str, str | None] = {'lat': None, 'lon': None, 'color': None}
        self._found: tuple[str, str] | None = None
        self._options: dict = dict(_DEFAULT_OPTIONS)
        self._time_range: tuple[float, float] | None = None
        self._cursors: tuple[float | None, float | None] = (None, None)
        self._foreground = '#000000'
        self.track: gps.Track = gps.EMPTY
        self._track_key: tuple | None = None
        self._color_values: np.ndarray | None = None
        self._color_key: tuple | None = None
        self._fitted: tuple | None = None
        self._spans: list[tuple[float, float]] = []
        self._message = ''
        self._pointer_on = False
        # The map file: open while chosen; the path tried last, and what
        # stops it being shown.
        self._map_file: map_tiles.MapFile | None = None
        self._map_path: str | None = None
        self._map_error = ''
        self._map_problem = ''
        self._pending_map = ''

        # ── Its top row: name while its title is folded away, what it
        # shows, and the gear ─────────────────────────────────────────────
        self.name_label = QLabel(TRACK_NAME)
        self.name_label.setContentsMargins(2, 0, 10, 0)
        self.name_label.hide()
        self.summary = _ElidedLabel()
        self.summary.setContentsMargins(4, 0, 4, 0)
        self.settings_button = QToolButton()
        self.settings_button.setIcon(plot_icon('settings'))
        self.settings_button.setIconSize(QSize(16, 16))
        self.settings_button.setFixedSize(STRIP_BUTTON_SIZE, STRIP_BUTTON_SIZE)
        self.settings_button.setAutoRaise(True)
        self.settings_button.setAccessibleName('Settings')
        self.settings_button.setToolTip(
            'Settings: the latitude, longitude and colour signals, the map and the time shown')
        self.settings_button.clicked.connect(lambda: self.open_settings())
        self.header = HeaderBand()
        top = QHBoxLayout(self.header)
        top.setContentsMargins(4, 0, 0, 0)
        top.setSpacing(2)
        top.addWidget(self.name_label)
        top.addWidget(self.summary, 1)
        top.addWidget(self.settings_button)
        top.addSpacing(TITLE_BUTTONS_ROOM)

        # ── Its settings, in the box the gear opens ──────────────────────
        self.settings_box = QFrame(self, Qt.WindowType.Popup)
        self.settings_box.setFrameShape(QFrame.Shape.StyledPanel)
        self.lat_box, self.lon_box, self.color_box = QComboBox(), QComboBox(), QComboBox()
        for box in (self.lat_box, self.lon_box, self.color_box):
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(24)
            box.setMaxVisibleItems(20)
        self.lat_box.setToolTip('The latitude signal. Automatic finds it by its name, unit '
                                'and values among every decoded signal.')
        self.lon_box.setToolTip('The longitude signal. Automatic finds it by its name, unit '
                                'and values among every decoded signal.')
        self.color_box.setToolTip('A signal as the route\'s colour, blue for low values and '
                                  'red for high.')
        self.gap_edit = QLineEdit()
        self.gap_edit.setPlaceholderText(f'{gps.GAP_SECONDS:g}')
        self.gap_edit.setToolTip('No fix for longer than this many seconds breaks the route '
                                 '(a tunnel, a garage). Empty: 2 s.')
        self.gap_edit.returnPressed.connect(self.apply_settings)
        self.whole_box = QCheckBox('Whole measurement')
        self.whole_box.setToolTip('Off: the part of the drive outside the time the time plot '
                                  'shows is dimmed.')
        self.map_label = _ElidedLabel()
        self.map_label.setMinimumWidth(140)
        self.map_button = QPushButton('Choose…')
        self.map_button.setToolTip(
            'Streets under the route, from a map file kept on this PC: an .mbtiles file of '
            'map images, as QGIS and other tile tools make. Nothing is fetched over the '
            'network.')
        self.map_button.clicked.connect(self.choose_map_file)
        self.map_none_button = QPushButton('None')
        self.map_none_button.setToolTip('No map under the route')
        self.map_none_button.clicked.connect(lambda: self._set_pending_map(''))
        map_row = QHBoxLayout()
        map_row.setSpacing(4)
        map_row.addWidget(self.map_label, 1)
        map_row.addWidget(self.map_button)
        map_row.addWidget(self.map_none_button)
        self.map_note = QLabel()
        self.map_note.setWordWrap(True)
        self.map_note.hide()
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow('Latitude', self.lat_box)
        form.addRow('Longitude', self.lon_box)
        form.addRow('Colour', self.color_box)
        form.addRow('Map', map_row)
        form.addRow(self.map_note)
        form.addRow('Gap s', self.gap_edit)
        form.addRow(self.whole_box)
        self.update_button = QPushButton('Update')
        self.update_button.setToolTip('Draw the view with these settings')
        self.update_button.setDefault(True)
        self.update_button.clicked.connect(self.apply_settings)
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.setToolTip('Close, leaving the view as it was')
        self.cancel_button.clicked.connect(self.settings_box.hide)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.update_button)
        box_layout = QVBoxLayout(self.settings_box)
        box_layout.setContentsMargins(10, 10, 10, 10)
        box_layout.addLayout(form)
        box_layout.addLayout(buttons)

        # ── The map ──────────────────────────────────────────────────────
        self.view_box = _TrackViewBox()
        self.view_box.setDefaultPadding(0.06)
        self.view_box.on_box = self._box_drawn
        self.plot = pg.PlotWidget(viewBox=self.view_box)
        self.plot.setMenuEnabled(False)
        self.plot.hideAxis('left')
        self.plot.hideAxis('bottom')
        self.plot.setAspectLocked(True)
        self.grid = _Grid()
        self.plot.addItem(self.grid, ignoreBounds=True)
        self.map_layer = MapBackground(self.view_box)
        self.map_layer.drawn.connect(self._update_credit)
        self.casing_curve = pg.PlotCurveItem(connect='pairs')
        self.casing_curve.setPen(pg.mkPen(_CASING_COLOR, width=_CASING_WIDTH))
        self.casing_curve.setZValue(-1)
        self.plot.addItem(self.casing_curve)
        self.dim_curve = pg.PlotCurveItem(connect='pairs')
        self.line_curve = pg.PlotCurveItem(connect='pairs')
        self.color_curves = [pg.PlotCurveItem(connect='pairs') for _ in range(_LEVELS)]
        self.gap_curve = pg.PlotCurveItem(connect='pairs')
        for z, curve in enumerate((self.dim_curve, self.gap_curve, self.line_curve,
                                   *self.color_curves)):
            curve.setZValue(z)
            self.plot.addItem(curve)
        for level, curve in enumerate(self.color_curves):
            curve.setPen(pg.mkPen(scale_color(level / (_LEVELS - 1)), width=_LINE_WIDTH))
        self.line_curve.setPen(pg.mkPen(_LINE_COLOR, width=_LINE_WIDTH))
        self.ends = pg.ScatterPlotItem(pxMode=True, size=11)
        self.ends.setZValue(40)
        self.plot.addItem(self.ends)
        self.hover_mark = pg.ScatterPlotItem(pxMode=True, size=9, symbol='o', brush=None)
        self.hover_mark.setZValue(41)
        self.plot.addItem(self.hover_mark, ignoreBounds=True)
        self.mark1 = pg.ScatterPlotItem(pxMode=True, size=_MARK_SIZE, symbol='o', brush=None)
        self.mark2 = pg.ScatterPlotItem(pxMode=True, size=_MARK_SIZE, symbol='d', brush=None)
        for z, mark in enumerate((self.mark2, self.mark1)):
            mark.setZValue(50 + z)
            self.plot.addItem(mark, ignoreBounds=True)
        self.plot.scene().sigMouseClicked.connect(self._on_click)
        self.plot.scene().sigMouseMoved.connect(self._on_hover)
        self.view_box.sigRangeChanged.connect(lambda *_: self._place_overlays())

        # Drawn over the map: the reading in its top-left corner while the
        # pointer is on it, the colour scale inside its right edge, north
        # and the scale in its bottom-left corner.
        self.readout = _Readout(self.plot)
        self.readout.setText(_HINT)
        self.readout.setContentsMargins(4, 1, 4, 1)
        self.readout.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.readout.hide()
        self.scale = _ColorScale(self.plot)
        self.scale.hide()
        self.key = _MapKey(self.plot)
        # The map file's credit, in its bottom-right corner.
        self.credit = _Readout(self.plot)
        credit_font = QFont(self.credit.font())
        credit_font.setPixelSize(10)
        self.credit.setFont(credit_font)
        self.credit.setContentsMargins(3, 0, 3, 0)
        self.credit.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.credit.hide()
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.setInterval(_MESSAGE_MS)
        self._message_timer.timeout.connect(self._message_done)
        self.plot.viewport().installEventFilter(self)
        self.plot.installEventFilter(self)
        self.view_box.sigResized.connect(self._place_overlays)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.plot, 1)
        self.setMinimumWidth(240)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._redraw)
        self.set_colors('#ffffff', '#000000')
        self._redraw()

    # ── What it is given ──────────────────────────────────────────────────

    def set_signals(self, signals: list[tuple[str, str, SignalSeries, str]]) -> None:
        """The plotted signals: offered first as the colour."""
        self._plotted = [key for key, _name, _series, _color in signals]

    def set_decoded(self, signals: list[tuple[str, SignalSeries]]) -> None:
        """Every decoded signal, (key, series) each: where the latitude and
        longitude are found. An empty list lets go of the measurement."""
        decoded = dict(signals)
        if decoded.keys() != self._decoded.keys() or any(
                decoded[key] is not self._decoded[key] for key in decoded):
            self._decoded = decoded
            self._found = gps.find_position_signals(decoded.items()) if decoded else None
            self._track_key = self._color_key = None
            self._redraw()

    def set_time_range(self, time_range: tuple[float, float] | None) -> None:
        """The time the time plot shows; drawn once it stops changing."""
        if time_range == self._time_range:
            return
        self._time_range = time_range
        if not self._options['whole_measurement']:
            self._timer.start(_REDRAW_MS)

    def set_cursors(self, cursor1: float | None, cursor2: float | None) -> None:
        """Each cursor's time, None for one that is off."""
        self._cursors = (cursor1, cursor2)
        self._draw_marks()
        self.summary.setText(self._summary_text())

    def set_colors(self, background: str, foreground: str) -> None:
        """The map's background, and the colour for its marks and writing."""
        self._foreground = foreground
        self.plot.setBackground(background)
        fg = QColor(foreground)
        self.grid.color = QColor(fg.red(), fg.green(), fg.blue(), 45)
        self.grid.update()
        dim = QColor(fg.red(), fg.green(), fg.blue(), 70)
        self.dim_curve.setPen(pg.mkPen(dim, width=_LINE_WIDTH))
        gap = QColor(fg.red(), fg.green(), fg.blue(), 170)
        self.gap_curve.setPen(pg.mkPen(gap, width=1.5, style=Qt.PenStyle.DashLine))
        self.hover_mark.setPen(pg.mkPen(_BOX_COLOR, width=2))
        self.scale.set_colors(background, foreground)
        self.key.color = fg
        self.key.backdrop = _backdrop(background)
        self.key.update()
        for label in (self.readout, self.credit):
            label.backdrop = _backdrop(background)
            palette = label.palette()
            palette.setColor(label.foregroundRole(), fg)
            label.setPalette(palette)
        dark = QColor(background).lightness() < 128
        self.map_layer.set_opacity(_DARK_MAP_OPACITY if dark else 1.0)
        self._draw_marks()

    # ── Its settings ──────────────────────────────────────────────────────

    def open_settings(self, anchor: QWidget | None = None) -> None:
        """Open the settings box under the gear, or under *anchor* (its
        panel title's gear), showing the settings the view is drawn with."""
        self._load_settings()
        self._settings_anchor = anchor
        self._show_settings_box()
        self.lat_box.setFocus()

    def _show_settings_box(self) -> None:
        box = self.settings_box
        box.adjustSize()
        anchor = getattr(self, '_settings_anchor', None)
        gear = anchor if anchor is not None else self.settings_button
        corner = gear.mapToGlobal(QPoint(gear.width(), gear.height()))
        rect = QRect(QPoint(corner.x() - box.width(), corner.y()), box.size())
        screen = QGuiApplication.screenAt(corner) or QGuiApplication.primaryScreen()
        if screen is not None:
            room = screen.availableGeometry()
            rect.moveLeft(max(room.left(), min(rect.left(), room.right() - rect.width())))
            if rect.bottom() > room.bottom():
                rect.moveBottom(gear.mapToGlobal(QPoint(0, 0)).y())
            rect.moveTop(max(room.top(), rect.top()))
        box.move(rect.topLeft())
        box.show()

    def apply_settings(self) -> None:
        """Draw the view with what the settings box holds (its Update), and
        close the box."""
        chosen = {role: box.currentData() if box.currentIndex() >= 0 else self._chosen[role]
                  for role, box in (('lat', self.lat_box), ('lon', self.lon_box),
                                    ('color', self.color_box))}
        options = {'whole_measurement': self.whole_box.isChecked(),
                   'gap': self.gap_edit.text().strip(),
                   'map': self._pending_map}
        self.settings_box.hide()
        self._chosen, self._options = chosen, options
        self._map_path = None       # opened again: the file may have changed
        self._redraw()

    def choose_map_file(self) -> None:
        """Ask for a map file (the settings box's Choose…), check that it
        can be shown, and hold it for Update."""
        global _last_map_folder
        reopen = self.settings_box.isVisible()
        # The box is a popup, which a file dialog would close anyway.
        self.settings_box.hide()
        start = os.path.dirname(self._pending_map) if self._pending_map else _last_map_folder
        path = self._ask_map_file(start)
        if path:
            _last_map_folder = os.path.dirname(path)
            try:
                map_tiles.MapFile(path).close()
            except map_tiles.MapFileError as error:
                self.map_note.setText(f'{os.path.basename(path)} cannot be shown: {error}.')
                self.map_note.show()
            else:
                self.map_note.hide()
                self._set_pending_map(path)
        if reopen:
            self._show_settings_box()

    def _ask_map_file(self, start: str) -> str:
        path, _ = QFileDialog.getOpenFileName(self, 'Map file', start, _MAP_FILTER)
        return path

    def _set_pending_map(self, path: str) -> None:
        """The map file the settings box holds, shown by its name."""
        self._pending_map = path
        self.map_label.setText(os.path.basename(path) if path else 'None')
        if path:
            self.map_label.setToolTip(path)
        self.map_none_button.setEnabled(bool(path))
        if not path:
            self.map_note.hide()

    def _load_settings(self) -> None:
        """Fill the settings box with the choices and the settings the view
        is drawn with. Filled here, not before: a measurement may decode
        thousands of signals."""
        found = self._found
        ordered = sorted(self._decoded, key=lambda key: _signal_label(self._decoded[key]).lower())
        for role, box in (('lat', self.lat_box), ('lon', self.lon_box)):
            box.blockSignals(True)
            box.clear()
            auto = found[0 if role == 'lat' else 1] if found else None
            box.addItem('Automatic' + (f' ({_signal_label(self._decoded[auto])})' if auto
                                       else ' (none found)'), None)
            fit = gps.LAT if role == 'lat' else gps.LON
            first = [key for key in ordered if gps.position_role(self._decoded[key]) == fit]
            for key in first + [key for key in ordered if key not in first]:
                box.addItem(_signal_label(self._decoded[key]), key)
            box.setCurrentIndex(max(box.findData(self._chosen[role]), 0)
                                if self._chosen[role] else 0)
            box.blockSignals(False)
        box = self.color_box
        box.blockSignals(True)
        box.clear()
        box.addItem('None', None)
        plotted = [key for key in self._plotted if key in self._decoded]
        for key in plotted + [key for key in ordered if key not in plotted]:
            box.addItem(_signal_label(self._decoded[key]), key)
        chosen = self._chosen['color']
        box.setCurrentIndex(max(box.findData(chosen), 0) if chosen else 0)
        box.blockSignals(False)
        self.gap_edit.setText(self._options['gap'])
        self.whole_box.setChecked(bool(self._options['whole_measurement']))
        self.map_note.hide()
        self._set_pending_map(self._options['map'])

    # ── What it keeps ─────────────────────────────────────────────────────

    def state(self) -> dict:
        """The choices, as a configuration keeps them."""
        return {**self._chosen, **self._options, 'view': TRACK}

    def restore(self, state: dict) -> None:
        """Take saved choices; a signal not decoded yet is used once it is."""
        for role in self._chosen:
            if role in state:
                self._chosen[role] = str(state[role]) if state[role] else None
        if 'whole_measurement' in state:
            self._options['whole_measurement'] = bool(state['whole_measurement'])
        if 'gap' in state:
            self._options['gap'] = str(state['gap'] or '').strip()
        if 'map' in state:
            self._options['map'] = str(state['map'] or '')
            self._map_path = None
        self.pick_bar(None)
        self._redraw()

    def rename_signal(self, old_key: str, new_key: str) -> None:
        for role, key in self._chosen.items():
            if key == old_key:
                self._chosen[role] = new_key
        if old_key in self._plotted:
            self._plotted[self._plotted.index(old_key)] = new_key

    def chosen(self, role: str) -> str | None:
        """The key chosen as 'lat', 'lon' or 'color'; None for automatic."""
        return self._chosen[role]

    def choose(self, role: str, key: str | None) -> None:
        """Choose a signal as 'lat', 'lon' or 'color' (None: automatic, or no
        colour), at once."""
        self._chosen[role] = key
        self._redraw()

    def used(self) -> tuple[str | None, str | None]:
        """The latitude and longitude keys the track is drawn from."""
        found = self._found or (None, None)
        lat = self._chosen['lat'] or found[0]
        lon = self._chosen['lon'] or found[1]
        return (lat if lat in self._decoded else None, lon if lon in self._decoded else None)

    def option(self, name: str):
        return self._options[name]

    def view(self) -> str:
        return TRACK

    kind = view

    # ── The track ─────────────────────────────────────────────────────────

    def _gap(self) -> float:
        try:
            gap = float(self._options['gap'])
        except ValueError:
            return gps.GAP_SECONDS
        return gap if gap > 0 else gps.GAP_SECONDS

    def _range(self) -> tuple[float, float] | None:
        return None if self._options['whole_measurement'] else self._time_range

    def _update_track(self) -> None:
        lat_key, lon_key = self.used()
        if lat_key is None or lon_key is None:
            self.track, self._track_key = gps.EMPTY, None
            return
        lat, lon = self._decoded[lat_key], self._decoded[lon_key]
        key = (lat_key, lon_key, id(lat), id(lon), self._gap())
        if key != self._track_key:
            self.track = gps.build_track(lat, lon, self._gap())
            self._track_key = key
            self._color_key = None

    def _update_colors(self) -> None:
        color_key = self._chosen['color']
        series = self._decoded.get(color_key or '')
        if series is None or not self.track.size:
            self._color_values, self._color_key = None, None
            return
        key = (color_key, id(series), self._track_key)
        if key != self._color_key:
            self._color_values = gps.held_at(series, self.track.t)
            self._color_key = key

    def _open_map(self) -> None:
        """Open the chosen map file, once; why it cannot be is kept for the
        summary."""
        path = self._options['map']
        if path == self._map_path:
            return
        self.map_layer.set_map(None, None)
        if self._map_file is not None:
            self._map_file.close()
        self._map_file, self._map_path, self._map_error = None, path, ''
        if path:
            try:
                self._map_file = map_tiles.MapFile(path)
            except map_tiles.MapFileError as error:
                self._map_error = f'map file {os.path.basename(path)}: {error}'

    def _update_map(self) -> None:
        self._open_map()
        track, map_file = self.track, self._map_file
        shown = map_file is not None and track.size > 0
        self.map_layer.set_map(map_file if shown else None, track.origin if shown else None)
        self.grid.setVisible(not shown)
        self._map_problem = self._map_error
        if shown and not map_file.covers(float(track.lon.min()), float(track.lat.min()),
                                         float(track.lon.max()), float(track.lat.max())):
            self._map_problem = 'the map file does not cover this drive'
        self._update_credit()

    def _update_credit(self) -> None:
        """The map's corner: the file's credit, or that the view is zoomed
        out too far for it."""
        layer = self.map_layer
        if not layer.shown():
            self.credit.hide()
            return
        self.credit.setText(_ZOOM_IN if layer.too_many else self._map_file.credit())
        self.credit.show()
        self._place_overlays()

    def map_file(self) -> map_tiles.MapFile | None:
        """The map file open under the route, if any."""
        return self._map_file

    def _redraw(self) -> None:
        self._timer.stop()
        self._update_track()
        self._update_colors()
        self._update_map()
        track = self.track
        empty = np.empty(0)
        if not track.size:
            for curve in (self.casing_curve, self.dim_curve, self.line_curve, self.gap_curve,
                          *self.color_curves):
                curve.setData(empty, empty)
            self.ends.setData(x=[], y=[])
            self.scale.hide()
            self.summary.setText(self._summary_text())
            self._draw_marks()
            self._place_overlays()
            return
        joined = np.flatnonzero(track.joined)
        inside = track.in_range(self._range())
        bright = inside[joined] & inside[joined - 1]
        self.dim_curve.setData(*self._pairs(joined[~bright]), connect='pairs')
        shown = joined[bright]
        if self.map_layer.shown():
            self.casing_curve.setData(*self._pairs(shown), connect='pairs')
        else:
            self.casing_curve.setData(empty, empty)
        values = self._color_values
        if values is not None:
            colored = shown[np.isfinite(values[shown])]
            steps, low, high = color_steps(values[colored], _LEVELS)
            for level, curve in enumerate(self.color_curves):
                curve.setData(*self._pairs(colored[steps == level]), connect='pairs')
            self.line_curve.setData(*self._pairs(np.setdiff1d(shown, colored)), connect='pairs')
            self.scale.set_range(_signal_label(self._decoded[self._chosen['color']]), low, high)
            self.scale.setVisible(colored.size > 0)
        else:
            for curve in self.color_curves:
                curve.setData(empty, empty)
            self.line_curve.setData(*self._pairs(shown), connect='pairs')
            self.scale.hide()
        gaps = track.gaps()
        if gaps:
            before = np.array([a for a, _b in gaps])
            after = np.array([b for _a, b in gaps])
            xs, ys = np.empty(2 * len(gaps)), np.empty(2 * len(gaps))
            xs[0::2], xs[1::2] = track.e[before], track.e[after]
            ys[0::2], ys[1::2] = track.n[before], track.n[after]
            self.gap_curve.setData(xs, ys, connect='pairs')
        else:
            self.gap_curve.setData(empty, empty)
        self.ends.setData(
            x=[track.e[0], track.e[-1]], y=[track.n[0], track.n[-1]],
            symbol=['t1', 's'], brush=[pg.mkBrush(_START_COLOR), pg.mkBrush(_END_COLOR)],
            pen=pg.mkPen(self._foreground, width=1))
        if self._track_key != self._fitted:
            # A new track is shown whole; a zoom is kept otherwise.
            self._fitted = self._track_key
            self.fit()
        self.summary.setText(self._summary_text())
        self._draw_marks()
        self._place_overlays()

    def _pairs(self, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The line pieces ending at fixes *ends*, each from the fix before,
        as connect='pairs' wants them."""
        track = self.track
        xs, ys = np.empty(2 * ends.size), np.empty(2 * ends.size)
        xs[0::2], xs[1::2] = track.e[ends - 1], track.e[ends]
        ys[0::2], ys[1::2] = track.n[ends - 1], track.n[ends]
        return xs, ys

    def fit(self) -> None:
        """Show the whole route, and keep it whole as the panel is resized,
        until the map is zoomed or panned."""
        self.view_box.enableAutoRange()

    def _summary_text(self) -> str:
        track = self.track
        lat_key, lon_key = self.used()
        if not self._decoded:
            return 'Load a measurement to see its GPS track.'
        if lat_key is None or lon_key is None:
            return 'No latitude and longitude found. Choose them under the gear.'
        if not track.size:
            return 'No valid GPS fixes in the chosen latitude and longitude.'
        text = (f'{distance_text(track.distance)} · {duration_text(track.duration)} · '
                f'{track.size:,} fixes')
        gaps = track.gaps()
        if gaps:
            text += (f' · {len(gaps)} gap{"s" if len(gaps) > 1 else ""}, '
                     f'{duration_text(track.no_fix_seconds())} no fix, '
                     f'{distance_text(track.gap_distance)} across')
        if self._map_problem:
            text += f' · {self._map_problem}'
        cursor1, cursor2 = self._cursors
        if cursor1 is not None and cursor2 is not None:
            low, high = sorted((cursor1, cursor2))
            driven = track.distance_at(high) - track.distance_at(low)
            text += f' · C1 to C2 {distance_text(driven)} in {duration_text(high - low)}'
        return text

    # ── The cursors ───────────────────────────────────────────────────────

    def _draw_marks(self) -> None:
        track = self.track
        for mark, time in zip((self.mark1, self.mark2), self._cursors):
            index, has = (None, False) if time is None else track.fix_at(time)
            if index is None:
                mark.setData(x=[], y=[])
                continue
            # A cursor at a time without a fix: its mark at the last place
            # known, dotted.
            style = Qt.PenStyle.SolidLine if has else Qt.PenStyle.DotLine
            mark.setData(x=[track.e[index]], y=[track.n[index]],
                         pen=pg.mkPen(self._foreground, width=2, style=style))

    def mark_position(self, cursor: int) -> tuple[float, float] | None:
        """Where Cursor 1 or 2 is marked, in metres; None when it is not."""
        xs, ys = (self.mark1, self.mark2)[cursor - 1].getData()
        return (float(xs[0]), float(ys[0])) if len(xs) else None

    def mark_has_fix(self, cursor: int) -> bool:
        """Whether that cursor's mark is drawn solid: a fix at its time."""
        time = self._cursors[cursor - 1]
        return time is not None and self.track.fix_at(time)[1]

    # ── A click picks a pass; a box shades its times ──────────────────────

    def _scene_to_map(self, scene_pos) -> QPointF | None:
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return None
        return self.view_box.mapSceneToView(scene_pos)

    def _on_click(self, event) -> None:
        if (event.button() != Qt.MouseButton.LeftButton
                or event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            return
        at = self._scene_to_map(event.scenePos())
        if at is not None:
            self.pick(at.x(), at.y())

    def pick(self, e: float, n: float) -> bool:
        """Ask for Cursor 1 at the time the vehicle was nearest (*e*, *n*),
        in metres, if the route passes within a few pixels. Where it passes
        more than once, the pass after the one Cursor 1 is on. A click away
        from the route clears a box's shading."""
        track = self.track
        radius = _PICK_PIXELS * float(self.view_box.viewPixelSize()[0])
        passes = track.passes_near(e, n, radius, self._range())
        if not passes:
            if self._spans:
                self.pick_bar(None)
                self._tell('Shading cleared.')
            return False
        # The pass Cursor 1 is on: a click by it again goes on to the next.
        cursor1 = self._cursors[0]
        current = [k for k, index in enumerate(passes)
                   if cursor1 is not None and abs(track.t[index] - cursor1) <= _SAME_PASS_S]
        if current and len(passes) > 1:
            k = (current[0] + 1) % len(passes)
        else:
            k = int(np.argmin([math.hypot(track.e[i] - e, track.n[i] - n) for i in passes]))
        index = passes[k]
        text = self.fix_text(index)
        if len(passes) > 1:
            text += f' · pass {k + 1} of {len(passes)} here, click again for the next'
        self._tell(text)
        self.timeClicked.emit(float(track.t[index]))
        return True

    def _box_drawn(self, e0: float, n0: float, e1: float, n1: float) -> None:
        self.choose_box(e0, n0, e1, n1)

    def choose_box(self, e0: float, n0: float, e1: float, n1: float) -> list:
        """Shade the times the vehicle was inside the box and send Cursor 1
        to the first; the stretches, [(start, end), ...]."""
        self.view_box.box_item.setRect(QRectF(QPointF(e0, n0), QPointF(e1, n1)).normalized())
        self.view_box.box_item.show()
        spans = self.track.spans_in_box(e0, e1, n0, n1, self._range())
        self._spans = spans
        self.spansChosen.emit(list(spans))
        if spans:
            total = sum(end - start for start, end in spans)
            self._tell(f'{len(spans)} pass{"es" if len(spans) > 1 else ""} through the box, '
                       f'{duration_text(total)} in all, shaded on the plot')
            self.timeClicked.emit(float(spans[0][0]))
        else:
            self._tell('The route does not pass through the box in the time shown.')
        return spans

    def pick_bar(self, index) -> None:
        """Let go of the box and its shading (*index* None, as the plot asks
        of every view when another one shades)."""
        had = bool(self._spans) or self.view_box.box_item.isVisible()
        self._spans = []
        self.view_box.box_item.hide()
        if had:
            self.spansChosen.emit([])

    def picked_spans(self) -> list[tuple[float, float]]:
        return list(self._spans)

    # ── Reading under the pointer ─────────────────────────────────────────

    def fix_text(self, index: int) -> str:
        """A fix as the readout shows it: time, position, colour value."""
        track = self.track
        text = (f'{track.t[index]:.3f} s · {track.lat[index]:.6f}°, '
                f'{track.lon[index]:.6f}°')
        values, key = self._color_values, self._chosen['color']
        if values is not None and key in self._decoded and np.isfinite(values[index]):
            series = self._decoded[key]
            unit = f' {series.unit}' if series.unit else ''
            text += f' · {series.signal_name} {values[index]:.6g}{unit}'
        return text

    def _on_hover(self, scene_pos) -> None:
        at = self._scene_to_map(scene_pos)
        index = None
        if at is not None and self.track.size:
            radius = _PICK_PIXELS * float(self.view_box.viewPixelSize()[0])
            near = self.track.passes_near(at.x(), at.y(), radius, self._range())
            if near:
                index = min(near, key=lambda i: math.hypot(self.track.e[i] - at.x(),
                                                           self.track.n[i] - at.y()))
        if index is None:
            self.hover_mark.setData(x=[], y=[])
            if not self._message_timer.isActive():
                self.readout.setText(_HINT)
        else:
            self.hover_mark.setData(x=[self.track.e[index]], y=[self.track.n[index]])
            if not self._message_timer.isActive():
                self.readout.setText(self.fix_text(index))
        self._show_readout()

    def _tell(self, text: str) -> None:
        self.readout.setText(text)
        self._message_timer.start()
        self._show_readout()

    def _message_done(self) -> None:
        self.readout.setText(_HINT)
        self._show_readout()

    def _show_readout(self) -> None:
        on = self._pointer_on or self._message_timer.isActive()
        if on:
            self._place_overlays()
        self.readout.setVisible(on)

    def eventFilter(self, watched, event) -> bool:
        plot = getattr(self, 'plot', None)
        if plot is not None:
            if watched is plot.viewport() and event.type() in (QEvent.Type.Enter,
                                                               QEvent.Type.Leave):
                self._pointer_on = event.type() == QEvent.Type.Enter
                if not self._pointer_on:
                    self.hover_mark.setData(x=[], y=[])
                self._show_readout()
            elif watched is plot and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                self._place_overlays()
        return super().eventFilter(watched, event)

    def _metres_per_pixel(self) -> float:
        """From the range itself: pyqtgraph's viewPixelSize() can still hold
        the range before a setRange() while the range-changed signal runs."""
        (x0, x1), _y = self.view_box.viewRange()
        width = self.view_box.width()
        return (x1 - x0) / width if width > 0 else 0.0

    def _place_overlays(self) -> None:
        plot = getattr(self, 'plot', None)
        if plot is None:
            return
        area = plot.mapFromScene(self.view_box.sceneBoundingRect()).boundingRect()
        inner = area.adjusted(_OVERLAY_INSET, _OVERLAY_INSET, -_OVERLAY_INSET, -_OVERLAY_INSET)
        if inner.width() < 2 or inner.height() < 2:
            return
        scale = self.scale
        width = scale.needed_width()
        scale.setGeometry(inner.right() + 1 - width, inner.top(), width, inner.height())
        right = inner.right() + 1 - (0 if scale.isHidden() else width + _OVERLAY_INSET)
        self.readout.setGeometry(inner.left(), inner.top(), max(right - inner.left(), 1),
                                 self.readout.sizeHint().height())
        hint = self.key.sizeHint()
        self.key.setGeometry(inner.left(), inner.bottom() + 1 - hint.height(),
                             min(hint.width(), max(right - inner.left(), 1)), hint.height())
        self.key.set_scale(self._metres_per_pixel())
        # The credit in the bottom-right corner, beside north and the scale,
        # or over them where the map is too narrow for both.
        credit = self.credit
        margins = credit.contentsMargins()
        wanted = (credit.fontMetrics().horizontalAdvance(credit.text())
                  + margins.left() + margins.right() + 1)
        height = credit.fontMetrics().height() + 2
        bottom = inner.bottom() + 1
        if wanted > right - (inner.left() + self.key.needed_width() + _OVERLAY_INSET):
            bottom -= hint.height() + 2
        width = max(min(wanted, right - inner.left()), 1)
        credit.setGeometry(right - width, bottom - height, width, height)
        scale.raise_()
        self.key.raise_()
        credit.raise_()
        self.readout.raise_()
