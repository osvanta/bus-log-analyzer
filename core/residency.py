# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
How long two signals spent together in each cell of a grid: a residency map
(a load collective, time-at-level matrix).

The time is cut into pieces at every sample of either signal; in each piece
both hold the value of their last sample (never interpolated), and the piece
counts for its length. A piece in which either signal's hold runs past its
gap limit counts for nothing, as in core.histogram, and so does one before
either signal has a value.

Optionally a third signal's time-weighted mean is kept per cell (the mean
fuel rate at each speed and torque, say). Time outside typed edges is
counted as outside the grid, never dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.histogram import SAMPLES, TIME, edges_for, gap_limit
from core.signal_store import SignalSeries

#: Cells aimed at along each axis when the grid is chosen automatically.
AUTO_CELLS = 20


def _arrays(series: SignalSeries) -> tuple[np.ndarray, np.ndarray]:
    stamps = np.asarray(series.numpy_timestamps(), dtype=np.float64)
    values = np.asarray(series.numpy_values(), dtype=np.float64)
    size = min(stamps.size, values.size)
    return stamps[:size], values[:size]


@dataclass(frozen=True)
class Pieces:
    """Stretches in which both signals held one value each."""

    starts: np.ndarray
    durations: np.ndarray     # 0 for a piece past a gap limit
    x: np.ndarray
    y: np.ndarray
    samples: np.ndarray       # bool: the piece starts at a sample taken in the range
    covered: float
    x_limit: float
    y_limit: float

    @property
    def counted(self) -> float:
        return float(self.durations.sum())


def _hold(stamps: np.ndarray, values: np.ndarray, times: np.ndarray, high: float,
          limit: float) -> tuple[np.ndarray, np.ndarray]:
    """Each time's held value, and whether it counts: there is one, and the
    sample it comes from holds no longer than *limit* (to its next sample,
    or to *high* for the last)."""
    index = np.searchsorted(stamps, times, side='right') - 1
    has = index >= 0
    index = np.clip(index, 0, max(stamps.size - 1, 0))
    following = np.where(index + 1 < stamps.size,
                         stamps[np.minimum(index + 1, stamps.size - 1)], high)
    whole = following - stamps[index]
    held = values[index]
    return held, has & (whole <= limit) & np.isfinite(held)


def pieces(x: SignalSeries, y: SignalSeries,
           time_range: tuple[float, float] | None = None,
           x_limit: float | None = None, y_limit: float | None = None,
           cuts: np.ndarray | None = None) -> Pieces:
    """The pieces of *time_range* (the whole recording when None) in which
    *x* and *y* each held one value. *cuts*: more times to cut at (a third
    signal's samples, so that it too holds one value in each piece)."""
    x_stamps, x_values = _arrays(x)
    y_stamps, y_values = _arrays(y)
    x_limit = gap_limit(x_stamps) if x_limit is None else float(x_limit)
    y_limit = gap_limit(y_stamps) if y_limit is None else float(y_limit)
    empty = np.empty(0, dtype=np.float64)
    if not x_stamps.size or not y_stamps.size:
        return Pieces(empty, empty, empty, empty, np.empty(0, dtype=bool), 0.0,
                      x_limit, y_limit)
    if time_range is None:
        low = float(min(x_stamps[0], y_stamps[0]))
        high = float(max(x_stamps[-1], y_stamps[-1]))
    else:
        low, high = sorted((float(time_range[0]), float(time_range[1])))
    inside = [stamps[(stamps > low) & (stamps < high)] for stamps in (x_stamps, y_stamps)]
    starts = np.union1d(np.union1d(inside[0], inside[1]), [low])
    if cuts is not None and cuts.size:
        starts = np.union1d(starts, cuts[(cuts > low) & (cuts < high)])
    ends = np.append(starts[1:], high)
    durations = np.clip(ends - starts, 0.0, None)
    xs, x_ok = _hold(x_stamps, x_values, starts, high, x_limit)
    ys, y_ok = _hold(y_stamps, y_values, starts, high, y_limit)
    keep = x_ok & y_ok
    sampled = np.isin(starts, x_stamps) | np.isin(starts, y_stamps)
    return Pieces(starts[keep], durations[keep], xs[keep], ys[keep], sampled[keep],
                  high - low, x_limit, y_limit)


@dataclass(frozen=True)
class Residency:
    """The grid: *weights*[i, j] is cell i along X, j along Y."""

    x_edges: np.ndarray
    y_edges: np.ndarray
    weights: np.ndarray       # seconds, or samples when counted by samples
    times: np.ndarray         # seconds per cell
    counts: np.ndarray        # samples per cell
    mean: np.ndarray | None   # the third signal's time-weighted mean; NaN where unknown
    mode: str
    counted: float
    covered: float
    outside: float            # weight outside the grid's edges
    x_limit: float
    y_limit: float

    @property
    def total(self) -> float:
        return float(self.weights.sum()) + self.outside

    def cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        """The cell (*x*, *y*) falls in; None outside the grid."""
        found = []
        for value, edges in ((x, self.x_edges), (y, self.y_edges)):
            if not edges.size or value < edges[0] or value > edges[-1]:
                return None
            found.append(int(min(np.searchsorted(edges, value, side='right') - 1,
                                 edges.size - 2)))
        return found[0], found[1]


def residency(x: SignalSeries, y: SignalSeries,
              time_range: tuple[float, float] | None = None,
              x_bins: float | list[float] | None = None,
              y_bins: float | list[float] | None = None,
              mode: str = TIME, x_limit: float | None = None,
              y_limit: float | None = None, z: SignalSeries | None = None) -> Residency:
    """The grid of *x* against *y*. Bins as for core.histogram: None for
    automatic, a width, or a list of edges. *z*: a third signal whose
    time-weighted mean each cell keeps."""
    z_stamps, z_values = _arrays(z) if z is not None else (None, None)
    parts = pieces(x, y, time_range, x_limit, y_limit, z_stamps)
    empty = np.empty(0, dtype=np.float64)
    if not parts.x.size:
        return Residency(empty, empty, np.zeros((0, 0)), np.zeros((0, 0)), np.zeros((0, 0)),
                         None, mode, 0.0, parts.covered, 0.0, parts.x_limit, parts.y_limit)
    weight = parts.samples.astype(np.float64) if mode == SAMPLES else parts.durations
    used = weight > 0
    basis_x = parts.x[used] if used.any() else parts.x
    basis_y = parts.y[used] if used.any() else parts.y
    x_edges = edges_for(basis_x, x_bins, AUTO_CELLS)
    y_edges = edges_for(basis_y, y_bins, AUTO_CELLS)
    grid = (x_edges, y_edges)
    weights, _, _ = np.histogram2d(parts.x, parts.y, bins=grid, weights=weight)
    times, _, _ = np.histogram2d(parts.x, parts.y, bins=grid, weights=parts.durations)
    counts, _, _ = np.histogram2d(parts.x, parts.y, bins=grid,
                                  weights=parts.samples.astype(np.float64))
    in_grid = ((parts.x >= x_edges[0]) & (parts.x <= x_edges[-1])
               & (parts.y >= y_edges[0]) & (parts.y <= y_edges[-1]))
    outside = float(weight[~in_grid].sum())
    mean = None
    if z is not None:
        index = np.searchsorted(z_stamps, parts.starts, side='right') - 1
        has = (index >= 0) & bool(z_stamps.size)
        held = z_values[np.clip(index, 0, max(z_stamps.size - 1, 0))] if z_stamps.size \
            else np.full(parts.starts.size, np.nan)
        has &= np.isfinite(held)
        known_time, _, _ = np.histogram2d(parts.x[has], parts.y[has], bins=grid,
                                          weights=parts.durations[has])
        weighted, _, _ = np.histogram2d(parts.x[has], parts.y[has], bins=grid,
                                        weights=held[has] * parts.durations[has])
        with np.errstate(invalid='ignore', divide='ignore'):
            mean = np.where(known_time > 0, weighted / known_time, np.nan)
    return Residency(x_edges, y_edges, weights, times, counts, mean, mode, parts.counted,
                     parts.covered, outside, parts.x_limit, parts.y_limit)


def cell_spans(x: SignalSeries, y: SignalSeries, x_range: tuple[float, float],
               y_range: tuple[float, float], time_range: tuple[float, float] | None = None,
               x_limit: float | None = None, y_limit: float | None = None,
               x_last: bool = False, y_last: bool = False) -> list[tuple[float, float]]:
    """The stretches of time both signals were in the cell *x_range* by
    *y_range* (upper edges included only for the last cell along an axis),
    joined where one follows straight on from the next."""
    parts = pieces(x, y, time_range, x_limit, y_limit)

    def within(values, low_high, last):
        low, high = low_high
        return (values >= low) & ((values <= high) if last else (values < high))

    # Pieces past a gap limit are not among them.
    inside = within(parts.x, x_range, x_last) & within(parts.y, y_range, y_last)
    result: list[tuple[float, float]] = []
    for start, duration in zip(parts.starts[inside].tolist(), parts.durations[inside].tolist()):
        end = start + duration
        if result and abs(result[-1][1] - start) <= 1e-9:
            result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def table_text(grid: Residency, values: np.ndarray, x_name: str, y_name: str,
               separator: str = ',') -> str:
    """The grid as a table: a row per Y cell (highest first, as drawn),
    a column per X cell, each headed by its range; empty cells blank."""
    def head(edges, i):
        return f'{edges[i]:.6g} to {edges[i + 1]:.6g}'

    def quote(text):
        return f'"{text}"' if separator in text or '"' in text else text

    lines = [separator.join([quote(f'{y_name} \\ {x_name}')]
                            + [quote(head(grid.x_edges, i)) for i in range(grid.x_edges.size - 1)])]
    for j in reversed(range(grid.y_edges.size - 1)):
        row = [quote(head(grid.y_edges, j))]
        for i in range(grid.x_edges.size - 1):
            value = values[i, j]
            row.append('' if not np.isfinite(value) or grid.times[i, j] <= 0 and
                       grid.counts[i, j] <= 0 else f'{value:.6g}')
        lines.append(separator.join(row))
    return '\n'.join(lines) + '\n'
