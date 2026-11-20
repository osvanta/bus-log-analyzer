# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""State lanes: a signal with a few named values drawn as one bar of
labelled stretches instead of a step line.

A lane is the signal cut into stretches, each one state held from the
sample that starts it until the next sample that changes it. Which signals
become lanes on their own, their colours, and what to draw at a zoom level
are worked out here, apart from any drawing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from core.signal_store import SignalSeries

#: A signal with a value table becomes a lane when it takes this many
#: distinct values or fewer in the measurement.
MAX_LANE_VALUES = 16
#: Kinds of lane.
LABELS = 'labels'   # named values, each stretch shows its label
BIT = 'bit'         # 0 and 1 only: 1 filled, 0 left empty
#: How a signal is shown: '' lets the signal decide, or the user's choice.
AUTO, LANE, LINE = '', 'lane', 'line'
SHOW_AS = (AUTO, LANE, LINE)

#: Colours by raw value, so a value has the same colour in every tab and
#: measurement. Readable with dark and light writing on the app's plot.
PALETTE = ('#6f7783', '#e0a33a', '#4b7bc4', '#3fa36b',
           '#9a6fd0', '#3aa8b8', '#c77b4a', '#8fa83c')
FAULT_COLOR = '#d2493f'
BIT_COLOR = '#3aa8b8'
_FAULT_WORDS = re.compile(r'fault|error|fail|abort|critical|derat', re.IGNORECASE)

#: A stretch narrower than this many pixels is too narrow to tell from its
#: neighbours; two or more of them side by side are drawn as one busy block.
MIN_STRETCH_PX = 2.0
_CHUNK = 65536


def lane_kind(series: SignalSeries) -> str | None:
    """LABELS, BIT, or None when the signal should stay a line.

    LABELS: a value table and at most MAX_LANE_VALUES distinct values.
    BIT: no value table, and both 0 and 1 and nothing else."""
    # Read a chunk at a time and stop at the first value that rules a lane
    # out, so a long ordinary signal costs one small chunk, not a copy.
    values = np.asarray(series.numpy_values(), dtype=np.float64)
    labelled = bool(getattr(series, 'has_labels', False))
    distinct: set[float] = set()
    zeros = ones = False
    for start in range(0, values.size, _CHUNK):
        chunk = values[start:start + _CHUNK]
        chunk = chunk[np.isfinite(chunk)]
        if labelled:
            distinct.update(np.unique(chunk).tolist())
            if len(distinct) > MAX_LANE_VALUES:
                return None
            continue
        is_one = chunk == 1.0
        if not np.all(is_one | (chunk == 0.0)):
            return None
        ones = ones or bool(is_one.any())
        zeros = zeros or not bool(is_one.all())
    if labelled:
        return LABELS if distinct else None
    return BIT if ones and zeros else None


def shown_as_lane(series: SignalSeries, show_as: str) -> str | None:
    """The kind of lane *series* is drawn as, or None to draw it as a line:
    the user's choice if they made one, otherwise the signal's own kind. A
    signal made a lane by hand that has no kind of its own shows its
    values as labels."""
    if show_as == LINE:
        return None
    kind = lane_kind(series)
    if show_as == LANE:
        return kind or LABELS
    return kind


@dataclass(frozen=True)
class Lane:
    """A signal as stretches: stretch i holds values[i] from starts[i] to
    ends[i]. The last stretch ends at the last sample."""

    starts: np.ndarray
    ends: np.ndarray
    values: np.ndarray
    labels: dict
    kind: str

    @property
    def size(self) -> int:
        return int(self.starts.size)

    def label(self, i: int) -> str:
        return self.labels.get(float(self.values[i]), _number(self.values[i]))

    def color(self, i: int) -> str:
        return color_of(float(self.values[i]), self.label(i), self.kind)

    def at(self, t: float) -> int | None:
        """The stretch holding time *t*, or None outside every stretch."""
        i = int(np.searchsorted(self.starts, t, side='right')) - 1
        if i < 0 or t > self.ends[i]:
            return None
        return i

    def spans_of(self, value: float) -> list[tuple[float, float]]:
        """Every stretch with *value*, as (start, end)."""
        same = self.values == value
        return list(zip(self.starts[same].tolist(), self.ends[same].tolist()))


def _number(value: float) -> str:
    return f'{float(value):g}'


def build_lane(series: SignalSeries, kind: str) -> Lane:
    """*series* cut into stretches. Samples with no value (NaN) end the
    stretch before them and start none."""
    stamps = np.asarray(series.numpy_timestamps(), dtype=np.float64)
    values = np.asarray(series.numpy_values(), dtype=np.float64)
    size = min(stamps.size, values.size)
    stamps, values = stamps[:size], values[:size]
    empty = np.empty(0, dtype=np.float64)
    if not size:
        return Lane(empty, empty, empty, {}, kind)
    # NaN never equals itself, so each missing sample starts a stretch of
    # its own; those are dropped below.
    first = np.concatenate(([0], np.flatnonzero(values[1:] != values[:-1]) + 1))
    starts = stamps[first]
    ends = np.concatenate((starts[1:], stamps[-1:]))
    held = values[first]
    keep = ~np.isnan(held)
    starts, ends, held = starts[keep], ends[keep], held[keep]
    return Lane(starts, ends, held, _labels(series, values, np.unique(held)), kind)


def _labels(series: SignalSeries, values: np.ndarray, distinct: np.ndarray) -> dict:
    """The label of each distinct value, from its first sample."""
    labels = {}
    for value in distinct.tolist():
        index = np.flatnonzero(values == value)
        label = series.display_value_at(int(index[0])) if index.size else value
        labels[float(value)] = label if isinstance(label, str) and label else _number(value)
    return labels


def color_of(value: float, label: str, kind: str = LABELS) -> str:
    """A state's colour: red for a fault-like word, otherwise one picked by
    raw value, the same wherever the value appears."""
    if kind == BIT:
        return BIT_COLOR
    if _FAULT_WORDS.search(label or ''):
        return FAULT_COLOR
    if np.isfinite(value) and float(value).is_integer():
        return PALETTE[int(value) % len(PALETTE)]
    return PALETTE[hash(round(float(value), 9)) % len(PALETTE)]


@dataclass(frozen=True)
class Pieces:
    """What to draw between two times at one zoom: piece k runs from
    starts[k] to ends[k]; index[k] is its stretch, or -1 for a busy block of
    stretches too narrow to draw one by one."""

    starts: np.ndarray
    ends: np.ndarray
    index: np.ndarray

    @property
    def busy(self) -> np.ndarray:
        return self.index < 0


def pieces(lane: Lane, low: float, high: float, pixel: float,
           min_px: float = MIN_STRETCH_PX) -> Pieces:
    """The stretches of *lane* between *low* and *high*, *pixel* seconds a
    pixel wide. Two or more neighbouring stretches each narrower than
    *min_px* pixels become one busy block; a narrow one alone stays itself.
    For a bit lane only the 1s are drawn, so 0s neither show nor join."""
    empty = np.empty(0, dtype=np.float64)
    nothing = Pieces(empty, empty, np.empty(0, dtype=np.int64))
    if not lane.size or high <= low:
        return nothing
    first = int(np.searchsorted(lane.ends, low, side='left'))
    last = int(np.searchsorted(lane.starts, high, side='right'))
    if last <= first:
        return nothing
    index = np.arange(first, last)
    starts, ends = lane.starts[first:last], lane.ends[first:last]
    shown = np.minimum(ends, high) - np.maximum(starts, low)
    narrow = shown < min_px * max(pixel, 0.0)
    # Runs of narrow stretches that touch, in the list and in time: a gap
    # of a pixel or more (no value there) ends a run.
    gap = (starts[1:] - ends[:-1]) >= max(pixel, 0.0)
    after_break = np.concatenate(([True], ~narrow[:-1] | gap))
    before_break = np.concatenate((~narrow[1:] | gap, [True]))
    run_starts = np.flatnonzero(narrow & after_break)
    run_ends = np.flatnonzero(narrow & before_break) + 1   # one past the run
    busy_runs = (run_ends - run_starts) >= 2
    if lane.kind == BIT:
        # A run is busy only if it holds a 1; a stretch of narrow 0s alone
        # draws nothing.
        has_one = np.array([bool((lane.values[index[a:b]] == 1.0).any())
                            for a, b in zip(run_starts.tolist(), run_ends.tolist())],
                           dtype=bool)
        busy_runs &= has_one
    in_busy = np.zeros(index.size, dtype=bool)
    for a, b in zip(run_starts[busy_runs].tolist(), run_ends[busy_runs].tolist()):
        in_busy[a:b] = True
    single = ~in_busy
    if lane.kind == BIT:
        single &= lane.values[index] == 1.0
    out_starts = np.concatenate((starts[single], starts[run_starts[busy_runs]]))
    out_ends = np.concatenate((ends[single], ends[run_ends[busy_runs] - 1]))
    out_index = np.concatenate((index[single],
                                np.full(int(busy_runs.sum()), -1, dtype=np.int64)))
    order = np.argsort(out_starts, kind='stable')
    return Pieces(out_starts[order], out_ends[order], out_index[order].astype(np.int64))
