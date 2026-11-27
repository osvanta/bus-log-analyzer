# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Painting a plot once, and only what changed.

Three things made every zoom, resize and cursor move slow, none of them the
amount of data. Each is put right here, for every plot of the application,
and none changes a pixel of what is drawn:

* **Two paints for one change.** pyqtgraph puts off a view's new transform
  until its paint event has begun. Changing it there marks the plot as
  changed while it is being painted, so the whole plot was painted a second
  time. :class:`PreparedViewBox` applies the transform before the paint
  event is made up. A plot resized by a layout was painted twice for
  another reason, which :class:`PlotView` and :class:`LayoutView` remove.
* **Grid lines drawn a pixel at a time.** Qt draws a thin line that is not
  fully opaque pixel by pixel, each with its own blend: the 20 lines across
  one plot took 5 ms. :class:`GridAxis` fills the very same pixels as
  rectangles, which takes a hundredth of that (:func:`fill_thin_line`).
* **A whole curve drawn for a sliver.** A cursor moving across the plot
  uncovers two thin columns, and pyqtgraph drew every curve under them from
  end to end, some 6 ms each. :func:`paint_exposed_part` draws only the
  segments that reach into what is being repainted.
"""

from __future__ import annotations

import types
import weakref

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QCoreApplication, QEvent, QLineF, QObject, QRectF, Qt
from PySide6.QtGui import QPainter, QPen, QPicture, QTransform

try:                                    # PySide6 names it shiboken6
    from shiboken6 import isValid as _is_valid
    from shiboken6 import wrapInstance as _wrap_instance
except ImportError:                     # pragma: no cover - another binding
    def _is_valid(_item) -> bool:
        return True

    def _wrap_instance(_address, _kind):
        raise TypeError('no way to hand Qt an array of lines')


# ── One paint for one change of the view ─────────────────────────────────

#: Qt queues a paint at low priority; this is handled first. (A number: the
#: binding takes no Qt.EventPriority here.)
_AHEAD_OF_PAINT = int(Qt.EventPriority.HighEventPriority.value)


class _ViewPreparer(QObject):
    """Applies the transform of the view boxes that changed, once the code
    that changed them has finished and before anything is painted."""

    _PREPARE = QEvent.Type(QEvent.registerEventType())

    def __init__(self) -> None:
        super().__init__()
        self._waiting: dict[int, weakref.ref] = {}

    def ask(self, view_box: pg.ViewBox) -> None:
        if not self._waiting:
            QCoreApplication.postEvent(self, QEvent(self._PREPARE), _AHEAD_OF_PAINT)
        self._waiting.setdefault(id(view_box), weakref.ref(view_box))

    def prepare(self) -> None:
        """Apply the transform of every view box that asked."""
        waiting, self._waiting = self._waiting, {}
        for ref in waiting.values():
            view_box = ref()
            if view_box is not None:
                prepare_now(view_box)

    def event(self, event: QEvent) -> bool:
        if event.type() != self._PREPARE:
            return super().event(event)
        self.prepare()
        return True


_preparer: _ViewPreparer | None = None


def prepare_now(view_box: pg.ViewBox) -> None:
    """Do for *view_box* now what pyqtgraph does as its paint begins: give it
    the transform of the range it shows. A range still to be worked out from
    its data stays with the paint, as it always was."""
    try:
        if not _is_valid(view_box) or view_box.scene() is None:
            return
        if getattr(view_box, '_autoRangeNeedsUpdate', False) and any(view_box.state['autoRange']):
            return
        view_box.prepareForPaint()
    except RuntimeError:                # deleted meanwhile
        pass


def prepare_before_paint(view_box: pg.ViewBox, *, now: bool = False) -> None:
    """*view_box* changed what it shows: apply it before the next paint.

    Usually once the code changing it has finished, so several changes cost
    one transform. *now* is for a change of size: a window being resized is
    painted before any event is handled, so every view box waiting is
    prepared at once (the rows of a stacked plot are resized one by one, and
    each sets the range of those linked to it).
    """
    global _preparer
    if not getattr(view_box, '_matrixNeedsUpdate', False):
        return
    if _preparer is None:
        _preparer = _ViewPreparer()
    _preparer.ask(view_box)
    if now:
        _preparer.prepare()


class PreparedViewBox(pg.ViewBox):
    """A view box painted once per change of its range or size."""

    def update(self, *args) -> None:
        super().update(*args)
        prepare_before_paint(self)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        prepare_before_paint(self, now=True)

    def itemBoundsChanged(self, item) -> None:
        """An item moved or changed size. pyqtgraph then fits the range
        again where a range is fitted, and repaints the whole box to do so.
        An item left out of the fitting (a cursor's mark, added with
        ``ignoreBounds``) cannot change the fit: nothing is repainted but
        the mark itself."""
        if item is not None and not self._counts_for_range(item):
            self._itemBoundsCache.pop(item, None)
            return
        super().itemBoundsChanged(item)

    def _counts_for_range(self, item) -> bool:
        """Whether *item*, or the item it is part of, is one the range is
        fitted to."""
        added = self.addedItems
        while item is not None and item is not self.childGroup:
            if item in added:
                return True
            item = item.parentItem()
        return False


class _ResizedOnce:
    """A pyqtgraph view painted once when it is resized.

    Resizing moves every item, and Qt notes each moved item to repaint its
    place later. A resized view is repainted whole in any case; when that
    paint came first (a panel hidden or shown beside the plot resizes it
    from a queued layout pass), the notes were worked off after it and the
    whole plot was painted again. Asking the scene for a full redraw first
    is what Qt is about to do anyway, and with it Qt keeps no such notes.
    """

    def resizeEvent(self, ev) -> None:
        scene = self.scene()
        if scene is not None:
            scene.update()
        super().resizeEvent(ev)

    #: The rectangles being repainted, in the view's own pixels, while its
    #: paint event runs (:func:`paint_exposed_part` and :class:`GridAxis`
    #: read them), and the rectangle around them all.
    exposed: list | None = None
    exposed_box: QRectF | None = None

    def paintEvent(self, ev) -> None:
        region = ev.region()
        self.exposed = list(region)
        self.exposed_box = QRectF(region.boundingRect())
        try:
            super().paintEvent(ev)
        finally:
            self.exposed = self.exposed_box = None


class PlotView(_ResizedOnce, pg.PlotWidget):
    """A plot widget painted once per resize."""


class LayoutView(_ResizedOnce, pg.GraphicsLayoutWidget):
    """A widget of several plots painted once per resize."""


# ── Grid lines ───────────────────────────────────────────────────────────

#: Farther out than any screen: where a line's ends are cut off before its
#: pixels are handed to Qt as a rectangle.
_FAR = float(1 << 24)
_TX_SCALE = QTransform.TransformationType.TxScale.value


def is_thin_solid(pen: QPen) -> bool:
    """Whether Qt draws a straight line of *pen*, not antialiased, as one row
    or column of device pixels whatever the scale: a solid cosmetic pen no
    wider than 1, with the square cap pens have unless told otherwise."""
    return (pen.isCosmetic() and pen.widthF() <= 1.0
            and pen.style() == Qt.PenStyle.SolidLine
            and pen.capStyle() == Qt.PenCapStyle.SquareCap
            and pen.brush().style() == Qt.BrushStyle.SolidPattern)


def _pixel(value: float) -> int:
    """The device pixel a coordinate of a thin line falls in, as Qt works it
    out: cut to 1/64 pixel, then to the pixel."""
    return int(value * 64.0) >> 6


def thin_line_pixels(device: QTransform, x1: float, y1: float, x2: float, y2: float):
    """The device pixels Qt fills for a thin solid line (:func:`is_thin_solid`)
    from (x1, y1) to (x2, y2), drawn without antialiasing under the transform
    *device*: ``(left, top, width, height)``, or None when the line does not
    run along a row or a column there (the caller then draws it as Qt does).

    Such a line is one device pixel wide and takes the pixels both its ends
    fall in. A test draws lines both ways at every scale factor and compares.
    """
    if device.type().value > _TX_SCALE:
        return None
    ax, ay = device.map(x1, y1)
    bx, by = device.map(x2, y2)
    ax, bx = max(-_FAR, min(_FAR, ax)), max(-_FAR, min(_FAR, bx))
    ay, by = max(-_FAR, min(_FAR, ay)), max(-_FAR, min(_FAR, by))
    same_x, same_y = int(ax * 64.0) == int(bx * 64.0), int(ay * 64.0) == int(by * 64.0)
    if same_x and same_y:
        return None                                 # too short to have a direction
    if same_y:                                      # along a row
        left, right = sorted((_pixel(ax), _pixel(bx)))
        return left, _pixel(ay), right - left + 1, 1
    if same_x:                                      # along a column
        top, bottom = sorted((_pixel(ay), _pixel(by)))
        return _pixel(ax), top, 1, bottom - top + 1
    return None


def fill_thin_line(painter: QPainter, pen: QPen, x1: float, y1: float,
                   x2: float, y2: float) -> bool:
    """Draw the line from (x1, y1) to (x2, y2) with *pen* by filling the
    pixels Qt would draw one by one; False (nothing drawn) when *pen* or the
    painter's transform is not one this holds for. The painter must not be
    antialiasing."""
    if not is_thin_solid(pen):
        return False
    device = painter.deviceTransform()
    pixels = thin_line_pixels(device, x1, y1, x2, y2)
    if pixels is None:
        return False
    inverse, invertible = device.inverted()
    if not invertible:
        return False
    painter.fillRect(inverse.mapRect(QRectF(*pixels)), pen.color())
    return True


class GridAxis(pg.AxisItem):
    """An axis whose tick and grid lines are filled instead of stroked.

    pyqtgraph records an axis (its line, its ticks, its tick texts) in one
    picture and plays that on every paint. Here the picture is kept in two
    parts, the line and the texts, played exactly as before; between them
    the ticks are filled (:func:`fill_thin_line`). What is drawn, and in
    what order, is unchanged. Where only a part of the plot is repainted (a
    cursor moving), what lies outside that part is not drawn at all.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._line_picture: QPicture | None = None
        self._text_picture: QPicture | None = None
        #: Where the line and the texts are, with room to spare.
        self._written = QRectF()
        self._ticks: list = []
        # The ticks as device pixels, for the transform they were worked out
        # under: a cursor moving over the plot repaints under the same one.
        self._tick_fills: tuple | None = None

    def _record(self) -> None:
        """Work out the axis again, as AxisItem.paint does when its picture
        is gone, and record it in parts."""
        self._ticks, self._tick_fills = [], None
        line, text = QPicture(), QPicture()
        painter = QPainter(line)
        specs = None
        try:
            if self.style['tickFont']:
                painter.setFont(self.style['tickFont'])
            specs = self.generateDrawSpecs(painter)
            if specs is not None:
                self.drawPicture(painter, specs[0], [], [])
        finally:
            painter.end()
        self._written = QRectF()
        if specs is not None:
            axis_spec, self._ticks, text_specs = specs
            written = QRectF(axis_spec[1], axis_spec[2]).normalized()
            for rect, _flags, _text in text_specs:
                written = written.united(QRectF(rect))
            self._written = written.adjusted(-4, -4, 4, 4)
            painter = QPainter(text)
            try:
                if self.style['tickFont']:
                    painter.setFont(self.style['tickFont'])
                # The line is in the other part: a pen that draws nothing.
                unseen = (QPen(Qt.PenStyle.NoPen), axis_spec[1], axis_spec[2])
                self.drawPicture(painter, unseen, [], text_specs)
            finally:
                painter.end()
        self._line_picture, self._text_picture = line, text
        self.picture = text             # pyqtgraph's sign that the axis is worked out

    def paint(self, p, opt, widget) -> None:
        if self.picture is None or self._line_picture is None:
            self._record()
        # The part being repainted, in the axis's own units, with a pixel
        # to spare; None: all of it, or a view that does not say.
        view = widget.parent() if widget is not None else None
        exposed = getattr(view, 'exposed_box', None)
        if exposed is not None:
            back, invertible = p.worldTransform().inverted()
            exposed = back.mapRect(exposed.adjusted(-2, -2, 2, 2)) if invertible else None
        written = exposed is None or exposed.intersects(self._written)
        if written:
            # A picture leaves the painter as its last command left it: this
            # one ends by clipping to the axis, which the ticks, drawn before
            # that in pyqtgraph's one picture, must not be.
            p.save()
            try:
                self._line_picture.play(p)
            finally:
                p.restore()
        if self._ticks:
            p.save()
            try:
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                self._paint_ticks(p, exposed)
            finally:
                p.restore()
        if written:
            self._text_picture.play(p)

    def _paint_ticks(self, p: QPainter, exposed: QRectF | None) -> None:
        device = p.deviceTransform()
        key = (device.m11(), device.m12(), device.m21(), device.m22(), device.dx(), device.dy())
        if self._tick_fills is None or self._tick_fills[0] != key:
            self._tick_fills = (key, self._tick_work(device))
        for rect, pen, lines in self._tick_fills[1]:
            if lines is None:
                if exposed is None or rect.intersects(exposed):
                    p.fillRect(rect, pen)
            else:                       # as pyqtgraph draws them
                if exposed is not None:
                    lines = [line for line, place in lines if place.intersects(exposed)]
                else:
                    lines = [line for line, _place in lines]
                if lines:
                    p.setPen(pen)
                    p.drawLines(lines)

    def _tick_work(self, device: QTransform) -> list:
        """How each tick is drawn under *device*, in order: ``(rectangle,
        colour, None)`` to fill, or ``(None, pen, lines)`` for a run of ticks
        of one pen drawn as lines, each with the place it takes up.

        Only a tick along a row of pixels is filled: its pixels are blended
        as one run. One down a column is a run per pixel either way, and
        Qt's own line is as quick, so those stay lines.
        """
        inverse, invertible = device.inverted()
        work: list = []
        thin: dict[int, bool] = {}
        for pen, p1, p2 in self._ticks:
            fill = thin.get(id(pen))
            if fill is None:
                fill = thin[id(pen)] = invertible and is_thin_solid(pen)
            pixels = thin_line_pixels(device, p1.x(), p1.y(), p2.x(), p2.y()) if fill else None
            if pixels is not None and pixels[3] == 1:
                work.append((inverse.mapRect(QRectF(*pixels)), pen.color(), None))
                continue
            line = QLineF(p1, p2)
            place = QRectF(p1, p2).normalized().adjusted(-2, -2, 2, 2)
            if work and work[-1][2] is not None and work[-1][1] is pen:
                work[-1][2].append((line, place))
            else:
                work.append((None, pen, [(line, place)]))
        return work


def grid_axes() -> dict:
    """The left and bottom axes of a plot, for ``axisItems``."""
    return {'left': GridAxis('left'), 'bottom': GridAxis('bottom')}


# ── Curves: only what is being repainted ─────────────────────────────────

#: Above this share of a curve's segments, the whole curve is drawn.
_PART_WORTH = 0.5


def runs_left_to_right(ends: np.ndarray) -> bool:
    """Whether the segments of *ends* (a row ``x1, y1, x2, y2`` each) follow
    one another from left to right, none going back: only then can those in
    a part of the plot be found by their place."""
    if len(ends) < 2:
        return False
    first_x, last_x = ends[:, 0], ends[:, 2]
    return bool(np.all(last_x >= first_x) and np.all(first_x[1:] >= first_x[:-1])
                and np.all(last_x[1:] >= last_x[:-1]))


def exposed_segments(ends: np.ndarray, boxes) -> np.ndarray | None:
    """The line segments of *ends* that reach into one of *boxes*.

    *ends* holds a row ``x1, y1, x2, y2`` per segment, running left to right
    (:func:`runs_left_to_right`); each box is ``(x_low, x_high, y_low,
    y_high)``, already widened by the pen. Returns the rows to draw (a
    segment in two boxes comes twice: the pen is opaque, so drawing it twice
    changes nothing); None when so many are wanted that drawing the whole
    curve is as quick.
    """
    count = len(ends)
    first_x, last_x = ends[:, 0], ends[:, 2]
    picked = []
    wanted = 0
    for x_low, x_high, y_low, y_high in boxes:
        # One segment more on each side than reach in: none can be missed.
        start = max(0, int(last_x.searchsorted(x_low, side='left')) - 1)
        stop = min(count, int(first_x.searchsorted(x_high, side='right')) + 1)
        if stop <= start:
            continue
        part = ends[start:stop]
        y1, y2 = part[:, 1], part[:, 3]
        inside = (np.maximum(y1, y2) >= y_low) & (np.minimum(y1, y2) <= y_high)
        chosen = part[inside]
        wanted += len(chosen)
        if wanted > count * _PART_WORTH:
            return None
        picked.append(chosen)
    if not picked:
        return np.empty((0, 4), dtype=np.float64)
    return np.ascontiguousarray(np.concatenate(picked), dtype=np.float64)


def _paint_part(curve: pg.PlotCurveItem, painter: QPainter, widget) -> bool:
    """Draw the part of *curve* inside what its view is repainting, exactly
    as PlotCurveItem.paint draws the whole; False when the whole must be
    drawn (nothing has been drawn then)."""
    view = widget.parent() if widget is not None else None
    exposed = getattr(view, 'exposed', None)
    if not exposed:
        return False
    opts = curve.opts
    pen = opts['pen']
    shadow = opts['shadowPen']
    brush = opts['brush']
    if (curve.xData is None or len(curve.xData) == 0
            or getattr(curve, '_exportOpts', False) is not False
            or opts['compositionMode'] is not None
            or (shadow is not None and shadow.style() != Qt.PenStyle.NoPen)
            or (opts['fillLevel'] is not None and brush is not None
                and brush.style() != Qt.BrushStyle.NoBrush)
            or pen is None or pen.style() == Qt.PenStyle.NoPen
            or not pen.isCosmetic()
            or not curve._shouldUseDrawLineSegments(pen)):
        return False
    # What PlotCurveItem.paint hands to drawLines: with this binding the
    # first line of an array and how many follow, with an older one a list.
    known = getattr(curve, '_lineSegmentsRendered', False)   # False: made anew now
    drawn = curve._getLineSegments()
    ends = curve._lineSegments.ndarray()
    if not known or not hasattr(curve, '_left_to_right'):
        curve._left_to_right = runs_left_to_right(ends)
    if not curve._left_to_right:
        return False
    listed = len(drawn) == 1 and isinstance(drawn[0], list)
    if listed:
        count = len(drawn[0])
    elif len(drawn) == 2 and isinstance(drawn[1], (int, np.integer)):
        count = int(drawn[1])
    else:
        return False
    if len(ends) != count or ends.shape[1:] != (4,):
        return False
    to_view = painter.worldTransform()
    from_view, invertible = to_view.inverted()
    if not invertible or to_view.type().value > _TX_SCALE or to_view.m11() == 0:
        return False
    # Each rectangle being repainted, in the curve's own units, widened by
    # the pen and two pixels to spare; only those the curve can reach into.
    # The pen's width is in pixels of the screen, the rectangles in the
    # view's own, which a scaled-up display makes larger.
    scale = max(1.0, abs(painter.deviceTransform().m11() / to_view.m11()))
    margin = (pen.widthF() + 2.0) / scale + 1.0
    reach = to_view.mapRect(curve.boundingRect()).adjusted(-margin, -margin, margin, margin)
    boxes = []
    for rect in exposed:
        if not reach.intersects(rect):
            continue
        x_a, y_a = from_view.map(rect.left() - margin, rect.top() - margin)
        x_b, y_b = from_view.map(rect.right() + 1 + margin, rect.bottom() + 1 + margin)
        boxes.append((min(x_a, x_b), max(x_a, x_b), min(y_a, y_b), max(y_a, y_b)))
    part = exposed_segments(ends, boxes) if boxes else np.empty((0, 4))
    if part is None:
        return False
    if len(part):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, opts['antialias'])
        painter.setPen(pen)
        if listed:
            painter.drawLines([QLineF(*row) for row in part.tolist()])
        else:
            painter.drawLines(_wrap_instance(part.ctypes.data, QLineF), len(part))
    return True


def _paint_exposed(curve: pg.PlotCurveItem, painter: QPainter, opt, widget) -> None:
    try:
        done = _paint_part(curve, painter, widget)
    except (AttributeError, KeyError, TypeError):
        done = False                    # pyqtgraph changed inside: draw it all
    if not done:
        pg.PlotCurveItem.paint(curve, painter, opt, widget)


def paint_exposed_part(curve: pg.PlotCurveItem) -> None:
    """From now on *curve* draws only the segments that reach into what its
    view (a :class:`PlotView` or :class:`LayoutView`) is repainting, when
    that is a small part of it; otherwise, and in any other view, it is drawn
    whole as before. The pixels painted are the same."""
    curve.paint = types.MethodType(_paint_exposed, curve)
