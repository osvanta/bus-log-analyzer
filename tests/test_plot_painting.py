# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A plot is painted once per change, and only what changed (gui.plot_painting).

Two kinds of test, neither of which times anything:

* what is painted is, pixel for pixel, what pyqtgraph and Qt paint: grid
  lines filled instead of stroked, an axis drawn in parts, a curve drawn
  only where it is being repainted;
* how much is painted: one paint for a zoom, a resize or a panel shown,
  no whole curve for a cursor move, no layout change for a Trace step.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRegion, QTransform
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

SCALES = (1.0, 1.25, 1.5, 2.0, 2.25, 3.0)       # display scale factors


def _image(width: int, height: int, scale: float = 1.0, fill=QColor(13, 17, 23)) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(scale)
    image.fill(fill)
    return image


def _pixels(image: QImage) -> np.ndarray:
    return np.frombuffer(image.constBits(), dtype=np.uint32).reshape(
        image.height(), image.width()).copy()


def _pen(width: float, alpha: int = 255, color=(200, 180, 90), **changes) -> QPen:
    pen = QPen(QColor(*color, alpha))
    pen.setWidthF(width)
    pen.setCosmetic(changes.get('cosmetic', True))
    if 'style' in changes:
        pen.setStyle(changes['style'])
    if 'cap' in changes:
        pen.setCapStyle(changes['cap'])
    return pen


# ── Grid lines: the pixels Qt draws, filled ──────────────────────────────

def _line_both_ways(scale, transform, pen, p1, p2, clip=None):
    """The line drawn by Qt, and filled by fill_thin_line: (pixels, pixels, filled)."""
    from gui.plot_painting import fill_thin_line

    out = []
    filled = False
    for fill in (False, True):
        image = _image(40, 40, scale)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if clip is not None:
            painter.setClipRegion(clip)
        painter.setTransform(transform)
        if fill:
            filled = fill_thin_line(painter, pen, p1[0], p1[1], p2[0], p2[1])
        if not fill or not filled:
            painter.setPen(pen)
            painter.drawLine(QPointF(*p1), QPointF(*p2))
        painter.end()
        out.append(_pixels(image))
    return out[0], out[1], filled


def test_a_filled_grid_line_takes_the_very_pixels_qt_draws(qapp):
    # Along rows and down columns, both ways, at every scale factor, with the
    # line's place and its ends at every sixteenth of a pixel and at the
    # values where a rounding rule would show (just under a pixel, a half).
    fractions = [k / 16 for k in range(16)] + [0.999999, 127 / 128, 1 / 128, 0.5 + 1e-9]
    pens = [_pen(1.0, 64), _pen(0.0, 255), _pen(1.0, 255), _pen(0.0, 64)]
    compared = filled = 0
    for scale in SCALES:
        for transform in (QTransform(), QTransform.fromTranslate(3.37, 1.61),
                          QTransform(0.83, 0, 0, 1.31, 2.2, -0.7),
                          QTransform(1.0, 0, 0, -1.0, 0.5, 40 / scale - 0.25)):
            back = (QTransform(transform) * QTransform.fromScale(scale, scale)).inverted()[0]
            for number, fraction in enumerate(fractions):
                pen = pens[number % len(pens)]
                lines = []
                a, b = back.map(QPointF(3.3, 7 + fraction)), back.map(QPointF(31.6, 7 + fraction))
                lines += [((a.x(), a.y()), (b.x(), a.y())), ((b.x(), a.y()), (a.x(), a.y()))]
                a, b = back.map(QPointF(7 + fraction, 2.2)), back.map(QPointF(7 + fraction, 33.9))
                lines += [((a.x(), a.y()), (a.x(), b.y())), ((a.x(), b.y()), (a.x(), a.y()))]
                a, b = back.map(QPointF(5 + fraction, 9.4)), back.map(QPointF(20 + fraction, 9.4))
                lines += [((a.x(), a.y()), (b.x(), a.y())), ((b.x(), a.y()), (a.x(), a.y()))]
                a, b = back.map(QPointF(-3 - fraction, 9.4)), back.map(QPointF(45 + fraction, 9.4))
                lines += [((a.x(), a.y()), (b.x(), a.y()))]
                for p1, p2 in lines:
                    drawn, as_filled, was_filled = _line_both_ways(scale, transform, pen, p1, p2)
                    compared += 1
                    filled += was_filled
                    assert np.array_equal(drawn, as_filled), (scale, fraction, p1, p2)
    assert filled == compared > 2000


def test_filled_lines_of_any_length_under_a_clip(qapp):
    rng = np.random.default_rng(3)
    clip = QRegion(QRect(2, 2, 10, 30)) + QRegion(QRect(15, 5, 9, 9))
    pens = [_pen(1.0, 64), _pen(0.0, 255)]
    for scale in SCALES:
        for number in range(250):
            transform = QTransform(rng.uniform(0.3, 3), 0, 0,
                                   rng.uniform(0.3, 3) * rng.choice([-1, 1]),
                                   rng.uniform(-5, 5), rng.uniform(-5, 20))
            back = (QTransform(transform) * QTransform.fromScale(scale, scale)).inverted()[0]
            # Ends anywhere, on and off the picture; every fourth line short.
            v = rng.uniform(-5, 45, 3)
            if number % 4 == 0:
                v[1] = v[0] + rng.uniform(-2, 2)
            if number % 2:
                a, b = back.map(QPointF(v[0], v[2])), back.map(QPointF(v[1], v[2]))
                p1, p2 = (a.x(), a.y()), (b.x(), a.y())
            else:
                a, b = back.map(QPointF(v[2], v[0])), back.map(QPointF(v[2], v[1]))
                p1, p2 = (a.x(), a.y()), (a.x(), b.y())
            drawn, as_filled, _ = _line_both_ways(scale, transform, pens[number % 2], p1, p2,
                                                  clip if number % 3 else None)
            assert np.array_equal(drawn, as_filled), (scale, p1, p2)


def test_only_a_thin_solid_line_along_a_row_or_column_is_filled(qapp):
    from gui.plot_painting import fill_thin_line, is_thin_solid, thin_line_pixels

    assert is_thin_solid(_pen(1.0, 64)) and is_thin_solid(_pen(0.0))
    assert not is_thin_solid(_pen(2.0))                             # wider than a pixel
    assert not is_thin_solid(_pen(1.0, cosmetic=False))             # scaled with the view
    assert not is_thin_solid(_pen(1.0, style=Qt.PenStyle.DashLine))
    assert not is_thin_solid(_pen(1.0, cap=Qt.PenCapStyle.FlatCap))  # ends differently
    plain = QTransform()
    assert thin_line_pixels(plain, 2.5, 3.5, 9.5, 3.5) == (2, 3, 8, 1)
    assert thin_line_pixels(plain, 9.5, 3.5, 2.5, 3.5) == (2, 3, 8, 1)
    assert thin_line_pixels(plain, 4.2, 1.0, 4.2, 6.9) == (4, 1, 1, 6)
    assert thin_line_pixels(plain, 2.5, 3.5, 9.5, 4.5) is None      # slanted
    assert thin_line_pixels(plain, 2.5, 3.5, 2.501, 3.5) is None    # no length to speak of
    assert thin_line_pixels(QTransform().rotate(30), 2.5, 3.5, 9.5, 3.5) is None
    assert thin_line_pixels(QTransform().rotate(90), 2.5, 3.5, 9.5, 3.5) is None    # not tried
    image = _image(20, 20)
    painter = QPainter(image)
    assert not fill_thin_line(painter, _pen(2.0), 2.5, 3.5, 9.5, 3.5)
    assert not fill_thin_line(painter, _pen(1.0), 2.5, 3.5, 9.5, 4.5)
    painter.end()
    assert len(set(_pixels(image).ravel().tolist())) == 1           # nothing was drawn


class _View:
    """Stands in for the view while it paints: the rectangles repainted, and
    the rectangle around them."""

    def __init__(self, exposed, box=None):
        self.exposed = exposed
        self.exposed_box = box


class _Viewport:
    def __init__(self, exposed, box=None):
        self._view = _View(exposed, box)

    def parent(self):
        return self._view


# ── The axis: drawn in parts, the same picture ───────────────────────────

def _plot(axis_kind, *, grid: bool, width: int = 331, height: int = 187):
    """A plot 331 x 187 with a curve, its left and bottom axes of *axis_kind*."""
    widget = pg.PlotWidget(axisItems={'left': axis_kind('left'), 'bottom': axis_kind('bottom')})
    widget.resize(width, height)
    x = np.linspace(0.0, 50.0, 400)
    widget.plot(x, np.sin(x) * 37.0 + 11.0, pen=pg.mkPen('#58a6ff', width=1))
    widget.setXRange(3.3, 41.7, padding=0)
    widget.setYRange(-30.2, 52.9, padding=0)
    if grid:
        widget.showGrid(x=True, y=True, alpha=0.25)
    return widget


def _render(widget, scale: float) -> np.ndarray:
    image = _image(int(widget.width() * scale), int(widget.height() * scale), scale)
    painter = QPainter(image)
    widget.render(painter, QRectF(0, 0, widget.width(), widget.height()),
                  QRect(0, 0, widget.width(), widget.height()))
    painter.end()
    return _pixels(image)


@pytest.mark.parametrize('grid', [True, False])
def test_an_axis_is_drawn_pixel_for_pixel_as_pyqtgraph_draws_it(qapp, grid):
    from gui.plot_painting import GridAxis

    usual, ours = _plot(pg.AxisItem, grid=grid), _plot(GridAxis, grid=grid)
    try:
        for widget in (usual, ours):
            widget.show()
        qapp.processEvents()
        # Odd sizes put the axes at every kind of place between pixels.
        for width, height in ((331, 187), (402, 233), (517, 301)):
            for widget in (usual, ours):
                widget.resize(width, height)
            qapp.processEvents()
            for scale in SCALES:
                assert np.array_equal(_render(usual, scale), _render(ours, scale)), (width, scale)
    finally:
        usual.close()
        ours.close()


def test_ticks_past_the_axis_keep_their_last_pixel(qapp):
    # A tick reaches a few pixels past the strip its axis lies in. Drawn
    # after the axis's line, it was cut where the line's picture had left a
    # clip to that strip.
    from gui.plot_painting import GridAxis

    def axis_alone(kind, x: float):
        view = pg.GraphicsView()
        view.resize(160, 220)
        axis = kind('left')
        box = pg.ViewBox()
        view.scene().addItem(box)
        view.scene().addItem(axis)
        axis.linkToView(box)
        box.setGeometry(QRectF(x + 50, 10, 90, 200))
        axis.setGeometry(QRectF(x, 10, 50, 200))
        box.setYRange(-3.7, 61.3, padding=0)
        return view

    for x in (2.0, 2.25, 2.5, 3.75):
        usual, ours = axis_alone(pg.AxisItem, x), axis_alone(GridAxis, x)
        try:
            usual.show()
            ours.show()
            qapp.processEvents()
            for scale in SCALES:
                assert np.array_equal(_render(usual, scale), _render(ours, scale)), (x, scale)
        finally:
            usual.close()
            ours.close()


def _axis_painted(axis, exposed: QRectF, scale: float, *, told: bool) -> np.ndarray:
    """The pixels of *axis* inside *exposed*, painted with the painter
    clipped to it: *told* by its view that only that part is repainted, or
    not."""
    bounds = axis.boundingRect()
    image = _image(int((bounds.width() + 20) * scale), int((bounds.height() + 20) * scale), scale)
    painter = QPainter(image)
    painter.translate(10 - bounds.left(), 10 - bounds.top())
    painter.setClipRect(exposed)
    viewport = None
    if told:                            # in the view's own pixels
        viewport = _Viewport([], painter.worldTransform().mapRect(exposed))
    axis.paint(painter, None, viewport)
    inside = painter.deviceTransform().mapRect(exposed)
    painter.end()
    # On screen nothing widens the part being repainted; here the axis's
    # texts set a clip of their own, so only what lies inside is compared.
    left, top = int(np.ceil(inside.left())) + 1, int(np.ceil(inside.top())) + 1
    right, bottom = int(np.floor(inside.right())) - 1, int(np.floor(inside.bottom())) - 1
    return _pixels(image)[top:bottom, left:right]


def test_a_part_of_an_axis_is_painted_as_the_whole_is(qapp):
    from gui.plot_painting import GridAxis

    widget = _plot(GridAxis, grid=True)
    try:
        widget.show()
        qapp.processEvents()
        drawn = 0
        for name in ('left', 'bottom'):
            axis = widget.getPlotItem().getAxis(name)
            bounds = axis.boundingRect()
            for part in (QRectF(bounds.left() + 140.3, bounds.top(), 9.0, bounds.height()),
                         QRectF(bounds.left(), bounds.top() + 60.6, bounds.width(), 8.0),
                         QRectF(bounds.left() + 1, bounds.top() + 1, 45, 30),
                         QRectF(bounds.right() - 40, bounds.bottom() - 35, 39, 34),
                         bounds):
                for scale in (1.0, 2.25):
                    whole = _axis_painted(axis, part, scale, told=False)
                    assert np.array_equal(whole, _axis_painted(axis, part, scale, told=True)), (
                        name, part, scale)
                    drawn += len(set(whole.ravel().tolist())) > 1
        assert drawn >= 12                  # most parts hold some of the axis
    finally:
        widget.close()


def test_grid_lines_across_are_filled_and_those_down_stay_lines(qapp):
    from gui.plot_painting import GridAxis

    widget = _plot(GridAxis, grid=True)
    try:
        widget.show()
        qapp.processEvents()
        widget.grab()
        left = widget.getPlotItem().getAxis('left')
        bottom = widget.getPlotItem().getAxis('bottom')
        across = left._tick_fills[1]
        assert len(across) == len(left._ticks) > 5
        assert all(lines is None for _rect, _colour, lines in across)       # every one filled
        down = bottom._tick_fills[1]
        assert all(lines is not None for _rect, _pen, lines in down)        # none filled
        # One call to Qt per pen (per level of ticks), not one per tick.
        assert sum(len(lines) for _rect, _pen, lines in down) == len(bottom._ticks) > 5
        assert len(down) <= 4
    finally:
        widget.close()


def test_ticks_are_worked_out_once_while_nothing_moves(qapp, monkeypatch):
    from gui.plot_painting import GridAxis

    worked = []
    tick_work = GridAxis._tick_work
    monkeypatch.setattr(GridAxis, '_tick_work',
                        lambda self, device: (worked.append(self.orientation),
                                              tick_work(self, device))[1])
    widget = _plot(GridAxis, grid=True)
    try:
        widget.show()
        qapp.processEvents()
        widget.grab()
        worked.clear()
        for _ in range(3):
            widget.grab()                   # the same picture again
        assert worked == []
        widget.setXRange(5.0, 20.0, padding=0)
        widget.grab()
        widget.grab()
        assert worked == ['bottom']         # the time axis, once; the other did not change
        worked.clear()
        widget.resize(420, 260)
        qapp.processEvents()
        widget.grab()
        widget.grab()
        assert sorted(worked) == ['bottom', 'left']     # once each at the new size
    finally:
        widget.close()


# ── Curves: only what is being repainted ─────────────────────────────────

def _curve(x, y, *, width=2.8, connect='all', color='#58a6ff', **pen_changes):
    from gui.plot_painting import paint_exposed_part

    pen = pg.mkPen(color, width=width, **pen_changes)
    item = pg.PlotCurveItem(x, y, pen=pen, connect=connect)
    paint_exposed_part(item)
    return item


def _curve_painted(item, to_view: QTransform, size, scale, rects, *, part: bool):
    """*item* painted under *to_view* with only *rects* (view pixels) let
    through: its whole, or (*part*) as the view tells it what is repainted."""
    image = _image(int(size[0] * scale), int(size[1] * scale), scale)
    painter = QPainter(image)
    region = QRegion()
    for rect in rects:
        region += QRegion(rect)
    painter.setClipRegion(region)
    painter.setWorldTransform(to_view)
    if part:
        item.paint(painter, None, _Viewport(list(rects)))
    else:
        pg.PlotCurveItem.paint(item, painter, None, None)
    painter.end()
    return _pixels(image)


def _signals():
    rng = np.random.default_rng(5)
    x = np.linspace(0.0, 100.0, 6000)
    steps = np.repeat(rng.integers(-20, 20, 600).cumsum().astype(float), 10)
    step_x = np.empty(2 * len(x) - 1)
    step_x[0::2], step_x[1::2] = x, x[1:]
    step_y = np.empty(2 * len(x) - 1)
    step_y[0::2], step_y[1::2] = steps, steps[:-1]
    gaps = np.ones(len(x), dtype=bool)
    gaps[rng.integers(0, len(x), 40)] = False
    return {
        'noise': (x, rng.normal(0.0, 30.0, len(x)), 'all'),
        'sine': (x, np.sin(x / 3.0) * 80.0, 'all'),
        'steps': (step_x, step_y, 'all'),
        'gaps': (x, np.cos(x / 5.0) * 60.0, gaps),
    }


@pytest.mark.parametrize('name', ['noise', 'sine', 'steps', 'gaps'])
def test_a_part_of_a_curve_paints_the_pixels_the_whole_curve_does(qapp, name):
    from gui import plot_painting

    x, y, connect = _signals()[name]
    size = (640, 200)
    # 100 s over 640 pixels, values over 200 pixels, up the screen.
    to_view = QTransform(6.4, 0, 0, -0.7, 0.0, 100.0)
    slivers = [
        [QRect(300, 0, 3, 200)],                                    # a cursor's column
        [QRect(120, 0, 3, 200), QRect(480, 0, 3, 200)],             # where it was, where it is
        [QRect(120, 0, 3, 200), QRect(124, 4, 34, 16)],             # with its label
        [QRect(0, 0, 2, 200), QRect(637, 0, 3, 200)],               # at both ends
        [QRect(200, 90, 40, 30), QRect(205, 20, 3, 5), QRect(400, 150, 9, 50)],
    ]
    parts = []
    whole = plot_painting._paint_part
    plot_painting._paint_part = lambda *args: (parts.append(whole(*args)), parts[-1])[1]
    try:
        for width in (1.2, 2.8, 5.0):
            item = _curve(x, y, width=width, connect=connect)
            for scale in (1.0, 2.25):
                drawn = 0
                for rects in slivers:
                    expected = _curve_painted(item, to_view, size, scale, rects, part=False)
                    got = _curve_painted(item, to_view, size, scale, rects, part=True)
                    assert np.array_equal(expected, got), (width, scale, rects)
                    drawn += len(set(expected.ravel().tolist())) > 1
                assert drawn >= 3           # the curve passes through most of them
    finally:
        plot_painting._paint_part = whole
    assert parts and all(parts)         # each time only a part was drawn


def test_how_much_of_a_curve_a_cursor_column_takes(qapp):
    # 6,000 samples over 640 pixels: the column of a cursor needs a few
    # dozen of the segments, not all of them.
    from gui import plot_painting

    x, y, connect = _signals()['sine']
    item = _curve(x, y)
    drawn = []
    segments = plot_painting.exposed_segments
    plot_painting.exposed_segments = lambda ends, boxes: (
        drawn.append((len(ends), segments(ends, boxes))), drawn[-1][1])[1]
    try:
        _curve_painted(item, QTransform(6.4, 0, 0, -0.7, 0.0, 100.0), (640, 200), 2.25,
                       [QRect(300, 0, 3, 200)], part=True)
    finally:
        plot_painting.exposed_segments = segments
    (count, part), = drawn
    assert count == len(x) - 1
    assert 0 < len(part) < count * 0.03


def test_which_segments_reach_into_a_box():
    from gui.plot_painting import exposed_segments, runs_left_to_right

    x = np.arange(0.0, 11.0)
    y = np.array([0, 5, 0, 5, 0, 50, 0, 5, 0, 5, 0], dtype=float)
    ends = np.column_stack([x[:-1], y[:-1], x[1:], y[1:]])
    assert runs_left_to_right(ends)
    # x 2.2 to 2.8 lies in the segment from 2 to 3; one more is taken on each side.
    part = exposed_segments(ends, [(2.2, 2.8, -1.0, 6.0)])
    assert part[:, 0].tolist() == [1.0, 2.0, 3.0]
    # Above y = 20 only the two segments of the peak reach, wherever x is.
    part = exposed_segments(ends, [(0.0, 10.0, 20.0, 60.0)])
    assert part[:, 0].tolist() == [4.0, 5.0]
    # More than half of the curve: as quick to draw it whole.
    assert exposed_segments(ends, [(0.0, 10.0, -1.0, 60.0)]) is None
    assert exposed_segments(ends, [(0.0, 3.5, -1.0, 60.0), (6.5, 10.0, -1.0, 60.0)]) is None
    # Nothing there: nothing to draw, which is not "draw it whole".
    assert len(exposed_segments(ends, [(20.0, 30.0, 100.0, 200.0)])) == 0
    assert len(exposed_segments(ends, [])) == 0
    # A curve that goes back cannot be searched by place.
    back = ends.copy()
    back[4, 0], back[4, 2] = 9.0, 9.5
    assert not runs_left_to_right(back)
    assert not runs_left_to_right(ends[:1])


def test_a_curve_that_cannot_be_drawn_in_part_is_drawn_whole(qapp):
    from gui import plot_painting

    x = np.linspace(0.0, 100.0, 4000)
    y = np.sin(x / 3.0) * 80.0
    to_view = QTransform(6.4, 0, 0, -0.7, 0.0, 100.0)
    rects = [QRect(300, 0, 3, 200)]
    back = x.copy()
    back[2000:2100] -= 30.0                 # time goes back
    cases = {
        'goes back': _curve(back, y),
        'dashed': _curve(x, y, style=Qt.PenStyle.DashLine),
        'see-through': _curve(x, y, color=(88, 166, 255, 120)),
        'one pixel': _curve(x, y, width=1.0),
    }
    for name, item in cases.items():
        image = _image(640, 200)
        painter = QPainter(image)
        painter.setWorldTransform(to_view)
        partly = plot_painting._paint_part(item, painter, _Viewport(rects))
        painter.end()
        assert not partly, name
        # And drawn whole it is what pyqtgraph draws.
        expected = _curve_painted(item, to_view, (640, 200), 1.0, rects, part=False)
        assert np.array_equal(expected, _curve_painted(item, to_view, (640, 200), 1.0, rects,
                                                       part=True)), name
    # Outside a view of ours (an export, another widget) nothing is left out.
    item = _curve(x, y)
    image = _image(640, 200)
    painter = QPainter(image)
    painter.setWorldTransform(to_view)
    partly = [plot_painting._paint_part(item, painter, None),
              plot_painting._paint_part(item, painter, _Viewport(None))]
    painter.end()
    assert partly == [False, False]


# ── How often a plot is painted ──────────────────────────────────────────

class _Paints(QObject):
    """Counts the paint events of a widget, and how much each repaints."""

    def __init__(self, widget) -> None:
        super().__init__()
        self.widget = widget
        self.shares: list[float] = []
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Paint:
            area = sum(rect.width() * rect.height() for rect in event.region())
            self.shares.append(area / max(1, watched.width() * watched.height()))
        return False


def _settle(qapp, passes: int = 12) -> None:
    for _ in range(passes):
        qapp.processEvents()


@pytest.fixture()
def stacked(qapp):
    """Three signals in three stacked rows, shown 900 x 600."""
    from core.signal_store import SignalSeries
    from gui.plot_widget import PlotPanel

    panel = PlotPanel()
    panel.resize(900, 600)
    ts = np.arange(0.0, 100.0, 0.01)
    for number, name in enumerate('ABC'):
        panel.add_series(name, SignalSeries(1, 'Msg', 0x100 + number, name, 'u', ts,
                                            np.sin(ts * (number + 1)) * 10.0))
    panel.set_stacked(True)
    panel.show()
    _settle(qapp, 30)
    yield panel
    panel.close()


def test_a_zoom_paints_the_plot_once(stacked, qapp):
    paints = _Paints(stacked.glw.viewport())
    for span in ((20.0, 60.0), (30.0, 35.0), (0.0, 100.0)):
        paints.shares.clear()
        stacked.zoom_to_time(*span, margin=0.0)
        _settle(qapp)
        assert len(paints.shares) == 1, (span, paints.shares)


def test_a_plot_resized_by_a_layout_is_painted_once(qapp):
    # A panel hidden beside the plot: the layout pass that widens the plot
    # is queued, and so is the paint.
    from gui.plot_painting import LayoutView, PreparedViewBox

    host = QWidget()
    layout = QHBoxLayout(host)
    side = QLabel('side panel')
    side.setFixedWidth(200)
    view = LayoutView()
    layout.addWidget(side)
    layout.addWidget(view, 1)
    x = np.linspace(0, 100, 2000)
    first = None
    for row in range(3):
        plot = view.addPlot(row=row, col=0, viewBox=PreparedViewBox())
        plot.plot(x, np.sin(x + row))
        plot.setXRange(0, 100, padding=0)
        plot.setYRange(-1, 1, padding=0)
        if first is None:
            first = plot
        else:
            plot.setXLink(first)
    host.resize(900, 500)
    host.show()
    try:
        _settle(qapp, 30)
        paints = _Paints(view.viewport())
        for change in (side.hide, side.show, lambda: host.resize(860, 480),
                       lambda: host.resize(940, 520)):
            paints.shares.clear()
            change()
            _settle(qapp)
            assert len(paints.shares) == 1, paints.shares
    finally:
        host.close()


def test_a_view_box_takes_its_transform_before_the_paint(qapp):
    # pyqtgraph gives a view box its new transform as the paint begins,
    # which marks the plot as changed while it is painted.
    from gui.plot_painting import PlotView, PreparedViewBox

    widget = PlotView(viewBox=PreparedViewBox())
    widget.resize(400, 300)
    widget.plot(np.arange(100.0), np.arange(100.0))
    widget.show()
    try:
        _settle(qapp, 20)
        box = widget.getPlotItem().vb
        box.setRange(xRange=(10, 20), yRange=(5, 9), padding=0)
        box.setRange(xRange=(12, 30), padding=0)        # two changes, one transform
        assert box._matrixNeedsUpdate
        changed = []
        box.sigTransformChanged.connect(lambda *_: changed.append(1))
        qapp.sendPostedEvents()                         # before any paint event
        assert not box._matrixNeedsUpdate and len(changed) == 1
        left, right = box.viewRange()[0]
        assert (left, right) == pytest.approx((12, 30))
        assert box.mapViewToScene(QPointF(12, 5)).x() == pytest.approx(
            box.sceneBoundingRect().left(), abs=0.01)
    finally:
        widget.close()


def test_a_range_still_to_be_fitted_is_left_to_the_paint(qapp):
    from gui.plot_painting import PlotView, PreparedViewBox, prepare_now

    widget = PlotView(viewBox=PreparedViewBox())
    widget.resize(400, 300)
    widget.show()
    try:
        _settle(qapp, 20)
        box = widget.getPlotItem().vb
        box.enableAutoRange(x=True, y=True)
        widget.plot(np.arange(50.0), np.arange(50.0) * 3)
        assert box._autoRangeNeedsUpdate and box.viewRange()[1][1] < 100
        prepare_now(box)                    # must not fit the range early
        assert box._autoRangeNeedsUpdate and box.viewRange()[1][1] < 100
        _settle(qapp)
        assert box.viewRange()[1][1] > 100  # fitted by the paint, as always
    finally:
        widget.close()


def test_a_cursor_move_repaints_a_sliver_and_no_whole_curve(stacked, qapp, monkeypatch):
    from gui import plot_painting

    stacked.set_cursor1_enabled(True)
    stacked.move_cursor1(20.0)
    _settle(qapp)
    parts = []                  # for each curve painted: only a part of it?
    paint_part = plot_painting._paint_part
    monkeypatch.setattr(plot_painting, '_paint_part',
                        lambda *args: (parts.append(paint_part(*args)), parts[-1])[1])
    paints = _Paints(stacked.glw.viewport())
    for t in (61.0, 61.4, 12.5):
        paints.shares.clear()
        parts.clear()
        stacked.move_cursor1(t)
        _settle(qapp)
        assert len(paints.shares) == 1 and paints.shares[0] < 0.2, paints.shares
        # Each of the three curves was painted, and none from end to end.
        assert parts == [True, True, True]


def test_the_plot_after_cursor_moves_is_the_plot_painted_afresh(stacked, qapp):
    # Whatever was left out while the cursor moved, the picture on screen is
    # the one a full repaint gives.
    stacked.set_cursor1_enabled(True)
    for t in (20.0, 61.0, 61.4, 12.5, 88.8):
        stacked.move_cursor1(t)
        _settle(qapp)
    view = stacked.glw
    image = _image(view.width(), view.height())
    painter = QPainter(image)
    backing = view.viewport().backingStore().paintDevice()
    origin = view.viewport().mapTo(view.window(), view.viewport().rect().topLeft())
    painter.drawImage(0, 0, backing, origin.x(), origin.y(), view.width(), view.height())
    painter.end()
    on_screen = _pixels(image)
    view.viewport().update()
    _settle(qapp)
    painter = QPainter(image)
    painter.drawImage(0, 0, backing, origin.x(), origin.y(), view.width(), view.height())
    painter.end()
    assert np.array_equal(on_screen, _pixels(image))


def test_a_cursor_move_repaints_its_cells_of_the_signal_table_only(stacked, qapp):
    # Updates switched off and on again round the new values repainted the
    # whole table with every move.
    stacked.table_panel.resize(500, 400)    # in the application the window shows it
    stacked.table_panel.show()
    stacked.set_cursor1_enabled(True)
    stacked.move_cursor1(20.0)
    _settle(qapp, 30)
    paints = _Paints(stacked.table.viewport())
    for t in (61.03, 61.47, 12.51):
        paints.shares.clear()
        stacked.move_cursor1(t)
        _settle(qapp)
        assert paints.shares and max(paints.shares) < 0.25, paints.shares
        cells = [stacked.table.item(row, 2).text() for row in range(stacked.table.rowCount())
                 if stacked.table.item(row, 2) is not None]
        assert f'{np.sin(round(t, 2)) * 10.0:.3f}' in cells      # and the values are the new ones
    stacked.table_panel.close()


def test_a_mark_left_out_of_the_range_repaints_only_itself(qapp):
    # A view that fits its range to its data did so again, and repainted
    # itself whole, whenever a cursor's mark moved, though the mark is not
    # one of the items the range is fitted to.
    from gui.plot_painting import PlotView, PreparedViewBox

    widget = PlotView(viewBox=PreparedViewBox())
    widget.resize(400, 300)
    x = np.linspace(0.0, 10.0, 500)
    data = widget.plot(x, np.sin(x))
    mark = pg.ScatterPlotItem(pxMode=True, size=9)
    widget.addItem(mark, ignoreBounds=True)
    counted = pg.ScatterPlotItem(pxMode=True, size=9)
    widget.addItem(counted)
    widget.enableAutoRange()
    widget.show()
    try:
        _settle(qapp, 20)
        box = widget.getPlotItem().vb
        paints = _Paints(widget.viewport())
        mark.setData(x=[3.0], y=[0.2])
        _settle(qapp)
        paints.shares.clear()
        mark.setData(x=[4.0], y=[-0.5])
        assert not box._autoRangeNeedsUpdate
        _settle(qapp)
        assert len(paints.shares) == 1 and paints.shares[0] < 0.05, paints.shares
        # An item the range is fitted to still has it fitted again: one added
        # as itself, and the curve inside a plotted signal.
        counted.setData(x=[5.0], y=[7.0])
        assert box._autoRangeNeedsUpdate
        _settle(qapp)
        assert box.viewRange()[1][1] >= 7.0
        data.setData(x, np.sin(x) * 20.0)
        assert box._autoRangeNeedsUpdate
        _settle(qapp)
        assert box.viewRange()[1][1] >= 20.0
        # The curve inside the plotted signal reports for itself too.
        assert not box._autoRangeNeedsUpdate
        box.itemBoundsChanged(mark)
        assert not box._autoRangeNeedsUpdate
        box.itemBoundsChanged(data.curve)
        assert box._autoRangeNeedsUpdate
    finally:
        widget.close()


# ── The Trace beside the plot ────────────────────────────────────────────

def test_a_trace_step_does_not_change_the_room_the_trace_needs(qapp, tmp_path):
    # Its least width was that of the status line's text, a few pixels more
    # or less with every other frame number: each step of the Trace moved
    # the panels beside it in a full window, and every plot was drawn again.
    from gui.raw_frame_dialog import RawFrameDialog
    from tests.test_trace_differences import _store

    count = 123_456
    store = _store(np.arange(count) * 0.0131, [1] * count, [0x100 + n % 7 for n in range(count)])
    dialog = RawFrameDialog(store, settings_path=tmp_path / 'settings.json')
    try:
        dialog.show()
        qapp.processEvents()
        needs = set()
        texts = set()
        for start in (0, 4_999, 9_999, 99_999, 111_111, count - 5_000):
            dialog._set_window(start)
            qapp.processEvents()
            texts.add(dialog.status_label.text())
            needs.add((dialog.status_label.minimumWidth(), dialog.minimumSizeHint().width()))
            # The whole text has room.
            assert (dialog.status_label.fontMetrics().horizontalAdvance(
                dialog.status_label.text()) <= dialog.status_label.minimumWidth())
        assert len(texts) == 6 and len(needs) == 1, needs
    finally:
        dialog.close()
        dialog.deleteLater()
        store.close()


# ── The signal panel shown or hidden ─────────────────────────────────────

def test_a_panel_shown_or_hidden_sizes_the_plots_at_once(qapp, monkeypatch, tmp_path):
    # The plot area's room and the gap beside it both change. Left to the
    # queued layout passes, the plots were painted at a size in between.
    from core.signal_store import SignalSeries
    from gui import app_data
    from gui.main_window import MainWindow

    monkeypatch.setenv(app_data.DATA_DIR_ENV_VAR, str(tmp_path))
    monkeypatch.setenv('OSVANTA_SESSION_RESTORE', '0')
    window = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        window._closing_confirmed = True
        window.resize(1100, 700)
        window.show()
        panel = window.plot_panel
        ts = np.arange(0.0, 50.0, 0.01)
        for number, name in enumerate('AB'):
            panel.add_series(name, SignalSeries(1, 'Msg', 0x100 + number, name, 'u', ts,
                                                np.sin(ts * (number + 1))))
        _settle(qapp, 40)
        view = panel.glw if panel._stacked_mode else panel.plot
        paints = _Paints(view.viewport())
        widths = []
        for _ in range(2):                  # hidden, then shown again
            paints.shares.clear()
            before = view.width()
            window._toggle_left_panel()
            at_once = view.width()          # before any event is handled
            _settle(qapp, 20)
            assert view.width() == at_once != before
            assert len(paints.shares) == 1, paints.shares
            widths.append(at_once)
        assert widths[0] > widths[1]
    finally:
        window.close()
        window.deleteLater()
