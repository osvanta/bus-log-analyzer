# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
One signal against another: the points of an XY plot.

Signals from different frames have different sample times, so the points sit
on the X signal's sample times, and Y and the colour signal give the value
each last had at that time (held, never interpolated: no point shows a value
the bus did not carry). A time before a signal's first sample gives no point.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.signal_store import SignalSeries

#: Points drawn at most; past it every k-th sample is taken.
MAX_POINTS = 50_000
#: Colour steps a colour signal's range is divided into.
COLOR_LEVELS = 64


@dataclass(frozen=True)
class XYPoints:
    """The points, in time order. *color* is None without a colour signal."""

    t: np.ndarray
    x: np.ndarray
    y: np.ndarray
    color: np.ndarray | None
    #: 1 of every *stride* samples in the time range is a point.
    stride: int = 1
    #: Samples in the time range, before thinning.
    total: int = 0

    @property
    def count(self) -> int:
        return int(self.t.size)


_NONE = np.empty(0, dtype=np.float64)
EMPTY = XYPoints(_NONE, _NONE, _NONE, None)


def _held(series: SignalSeries, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The series' latest value at each of *times*, and where it has one."""
    stamps = np.asarray(series.numpy_timestamps(), dtype=np.float64)
    values = np.asarray(series.numpy_values(), dtype=np.float64)
    size = min(stamps.size, values.size)
    if not size:
        return np.full(times.size, np.nan), np.zeros(times.size, dtype=bool)
    index = np.searchsorted(stamps[:size], times, side='right') - 1
    held = values[np.clip(index, 0, size - 1)]
    return held, (index >= 0) & np.isfinite(held)


def xy_points(
    x: SignalSeries,
    y: SignalSeries,
    color: SignalSeries | None = None,
    time_range: tuple[float, float] | None = None,
    max_points: int = MAX_POINTS,
) -> XYPoints:
    """The points of *y* against *x* in *time_range* (the whole recording
    when None), thinned evenly to *max_points*."""
    times = np.asarray(x.numpy_timestamps(), dtype=np.float64)
    x_values = np.asarray(x.numpy_values(), dtype=np.float64)
    size = min(times.size, x_values.size)
    first, last = 0, size
    if time_range is not None:
        first = int(np.searchsorted(times[:size], time_range[0], side='left'))
        last = int(np.searchsorted(times[:size], time_range[1], side='right'))
    total = max(last - first, 0)
    if not total:
        return EMPTY
    stride = max(-(-total // max(int(max_points), 1)), 1)
    times = times[first:last:stride]
    x_values = x_values[first:last:stride]
    y_values, keep = _held(y, times)
    keep &= np.isfinite(x_values)
    color_values = None
    if color is not None:
        color_values, has_color = _held(color, times)
        keep &= has_color
        color_values = color_values[keep]
    return XYPoints(times[keep], x_values[keep], y_values[keep], color_values,
                    stride=stride, total=total)


def color_steps(values: np.ndarray, levels: int = COLOR_LEVELS) -> tuple[np.ndarray, float, float]:
    """Each value's colour step, 0 to *levels* - 1, and the range the steps
    span. A constant signal is all step 0."""
    if not values.size:
        return np.empty(0, dtype=np.intp), 0.0, 1.0
    low, high = float(values.min()), float(values.max())
    if high <= low:
        return np.zeros(values.size, dtype=np.intp), low, low + 1.0
    steps = ((values - low) * (levels / (high - low))).astype(np.intp)
    return np.clip(steps, 0, levels - 1), low, high


def xy_at(x: SignalSeries, y: SignalSeries, t: float) -> tuple[float, float] | None:
    """Where the two signals are at time *t*: the value each last had. None
    while either has none yet. Exact, whatever thinning left out."""
    at = np.array([t], dtype=np.float64)
    (x_value,), (has_x,) = _held(x, at)
    (y_value,), (has_y,) = _held(y, at)
    return (float(x_value), float(y_value)) if has_x and has_y else None


def nearest_point(points: XYPoints, x: float, y: float,
                  x_unit: float = 1.0, y_unit: float = 1.0) -> tuple[int, float] | None:
    """The point nearest (*x*, *y*) and its distance, both measured in
    *x_unit* by *y_unit* steps (a pixel's size, for a distance in pixels).
    Of points equally near, the earliest."""
    if not points.count or x_unit <= 0 or y_unit <= 0:
        return None
    distance = np.hypot((points.x - x) / x_unit, (points.y - y) / y_unit)
    index = int(np.argmin(distance))
    return index, float(distance[index])
