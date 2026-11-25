# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
How long a signal spent at each value: the bars of a histogram.

Time, not samples, is counted: each sample holds its value until the
signal's next sample (as the plot and the XY view draw it, never
interpolated). A signal sent every 10 ms in one phase and every 100 ms in
another would otherwise look ten times as common in the first.

A hold longer than the gap limit is not counted at all: a logger pause or a
silent bus would otherwise add minutes to the last value seen. The limit is
ten times the signal's typical sample period, and at least a second.

Values outside typed bin edges are counted as below or above, never
dropped, so the bars and those two add up to all the time counted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from core.signal_store import SignalSeries

#: Bars aimed at when the bins are chosen automatically.
AUTO_BINS = 30
#: The gap limit is this many typical sample periods ...
GAP_PERIODS = 10
#: ... and never shorter than this, in seconds.
MIN_GAP_S = 1.0
#: A signal with labels gets a bar per value up to this many values, and
#: numeric bins past it.
MAX_LABEL_BARS = 64

TIME, SAMPLES = 'time', 'samples'


@dataclass(frozen=True)
class Held:
    """A signal's samples in a time range, each with how long it held."""

    starts: np.ndarray      # when each held value began (cut to the range)
    values: np.ndarray
    durations: np.ndarray   # seconds counted for each; 0 for a hold past the gap limit
    samples: np.ndarray     # bool: a sample taken inside the range (not the value held into it)
    gap_limit: float
    covered: float          # the range's length, seconds

    @property
    def counted(self) -> float:
        return float(self.durations.sum())


@dataclass(frozen=True)
class Histogram:
    """The bars: *edges* has one more entry than *weights*. For a signal with
    labels, each bar is one value and *labels* names it (edges then are the
    values' midpoints, one bar per value)."""

    edges: np.ndarray
    weights: np.ndarray     # seconds, or samples when counted by samples
    times: np.ndarray       # seconds in each bar, whatever is counted
    counts: np.ndarray      # samples in each bar
    mode: str
    counted: float          # seconds counted, below and above included
    covered: float
    gap_limit: float
    below: float = 0.0      # weight below the first edge
    above: float = 0.0      # weight above the last edge
    values: tuple = ()      # for labels: the value of each bar
    labels: tuple = ()
    stats: dict = field(default_factory=dict)

    @property
    def total(self) -> float:
        """All weight counted: the bars, below and above."""
        return float(self.weights.sum()) + self.below + self.above

    def bar_of(self, value: float) -> int | None:
        """The bar *value* falls in; None outside them all."""
        if self.labels:
            matches = np.flatnonzero(np.asarray(self.values) == value)
            return int(matches[0]) if matches.size else None
        if not self.edges.size or value < self.edges[0] or value > self.edges[-1]:
            return None
        return int(min(np.searchsorted(self.edges, value, side='right') - 1,
                       self.weights.size - 1))


def gap_limit(timestamps: np.ndarray) -> float:
    """Ten typical sample periods, at least a second."""
    if timestamps.size < 2:
        return MIN_GAP_S
    period = float(np.median(np.diff(timestamps)))
    return max(GAP_PERIODS * period, MIN_GAP_S)


def held(series: SignalSeries, time_range: tuple[float, float] | None = None,
         limit: float | None = None) -> Held:
    """Each value of *series* in *time_range* (the whole recording when None)
    and how long it held there. The value held when the range starts counts
    from its start; the last one holds to its end. *limit*: the gap limit,
    automatic when None."""
    stamps = np.asarray(series.numpy_timestamps(), dtype=np.float64)
    values = np.asarray(series.numpy_values(), dtype=np.float64)
    size = min(stamps.size, values.size)
    stamps, values = stamps[:size], values[:size]
    limit = gap_limit(stamps) if limit is None else float(limit)
    empty = np.empty(0, dtype=np.float64)
    if not size:
        return Held(empty, empty, empty, np.empty(0, dtype=bool), limit, 0.0)
    if time_range is None:
        low, high = float(stamps[0]), float(stamps[-1])
    else:
        low, high = float(time_range[0]), float(time_range[1])
    if high < low:
        low, high = high, low
    # The sample before the range holds into it; samples after it do not count.
    first = max(int(np.searchsorted(stamps, low, side='right')) - 1, 0)
    last = int(np.searchsorted(stamps, high, side='right'))
    if last <= first or stamps[first] > high:
        return Held(empty, empty, empty, np.empty(0, dtype=bool), limit, high - low)
    starts = stamps[first:last]
    ends = np.empty_like(starts)
    ends[:-1] = stamps[first + 1:last]
    ends[-1] = stamps[last] if last < size else high
    # A hold past the gap limit counts for nothing, measured uncut.
    whole = ends - starts
    cut_starts = np.maximum(starts, low)
    durations = np.clip(np.minimum(ends, high) - cut_starts, 0.0, None)
    durations[whole > limit] = 0.0
    finite = np.isfinite(values[first:last])
    return Held(cut_starts[finite], values[first:last][finite], durations[finite],
                (starts >= low)[finite], limit, high - low)


def nice_width(low: float, high: float, bins: int = AUTO_BINS) -> float:
    """A round bar width (1, 2, 2.5 or 5 times a power of ten) giving about
    *bins* bars between *low* and *high*."""
    span = high - low
    if not math.isfinite(span) or span <= 0:
        return 1.0
    raw = span / bins
    power = 10.0 ** math.floor(math.log10(raw))
    for step in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= step * power:
            return step * power
    return 10.0 * power


def parse_bins(text: str) -> float | list[float] | None:
    """What the user typed for the bins: nothing (automatic), one width, or a
    list of edges. Raises ValueError for anything else."""
    text = (text or '').strip()
    if not text or text.lower() == 'auto':
        return None
    parts = [part for part in text.replace(';', ',').split(',') if part.strip()]
    numbers = [float(part) for part in parts]
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError('not a number')
    if len(numbers) == 1:
        if numbers[0] <= 0:
            raise ValueError('a width must be more than 0')
        return numbers[0]
    edges = sorted(set(numbers))
    if len(edges) < 2:
        raise ValueError('edges need two different values')
    return edges


def edges_for(values: np.ndarray, bins: float | list[float] | None,
              target: int = AUTO_BINS) -> np.ndarray:
    """Bin edges covering *values*: automatic (None, about *target* bins),
    a width, or as listed."""
    return _edges(values, bins, target)


def _edges(values: np.ndarray, bins: float | list[float] | None,
           target: int = AUTO_BINS) -> np.ndarray:
    if isinstance(bins, list):
        return np.asarray(bins, dtype=np.float64)
    low, high = float(values.min()), float(values.max())
    width = float(bins) if bins else nice_width(low, high, target)
    start = math.floor(low / width) * width
    count = max(int(math.floor((high - start) / width)) + 1, 1)
    # Too many bars would draw as a smear and cost memory; widen instead.
    while count > 10_000:
        width *= 2
        start = math.floor(low / width) * width
        count = max(int(math.floor((high - start) / width)) + 1, 1)
    return start + width * np.arange(count + 1, dtype=np.float64)


def _weighted_stats(values: np.ndarray, weights: np.ndarray) -> dict:
    keep = weights > 0
    values, weights = values[keep], weights[keep]
    if not values.size:
        return {}
    order = np.argsort(values, kind='stable')
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights)
    total = cumulative[-1]

    def percentile(q: float) -> float:
        return float(values[min(int(np.searchsorted(cumulative, q * total, side='left')),
                                values.size - 1)])

    return {'min': float(values[0]), 'max': float(values[-1]),
            'mean': float(np.dot(values, weights) / total),
            'p5': percentile(0.05), 'p50': percentile(0.50), 'p95': percentile(0.95)}


def _labels_of(series: SignalSeries, values: np.ndarray) -> tuple:
    """The display label of each distinct value, read from its first sample."""
    all_values = np.asarray(series.numpy_values(), dtype=np.float64)
    labels = []
    for value in values:
        index = np.flatnonzero(all_values == value)
        label = series.display_value_at(int(index[0])) if index.size else value
        labels.append(str(label))
    return tuple(labels)


def histogram(series: SignalSeries, time_range: tuple[float, float] | None = None,
              bins: float | list[float] | None = None, mode: str = TIME,
              limit: float | None = None) -> Histogram:
    """The bars of *series* in *time_range*. *bins*: None for automatic, a
    width, or a list of edges. *mode*: TIME (seconds held) or SAMPLES
    (samples taken in the range). A signal with labels gets one bar per
    value, whatever *bins* says."""
    pieces = held(series, time_range, limit)
    empty = np.empty(0, dtype=np.float64)
    if not pieces.values.size:
        return Histogram(empty, empty, empty, empty, mode, 0.0, pieces.covered,
                         pieces.gap_limit)
    by_samples = mode == SAMPLES
    weight = pieces.samples.astype(np.float64) if by_samples else pieces.durations
    used = (weight > 0) if by_samples else np.ones(pieces.values.size, dtype=bool)
    stats = _weighted_stats(pieces.values, weight)
    distinct = np.unique(pieces.values[used] if used.any() else pieces.values)
    if bool(getattr(series, 'has_labels', False)) and distinct.size <= MAX_LABEL_BARS:
        position = np.searchsorted(distinct, pieces.values)
        position = np.clip(position, 0, distinct.size - 1)
        weights = np.bincount(position, weights=weight, minlength=distinct.size)
        times = np.bincount(position, weights=pieces.durations, minlength=distinct.size)
        counts = np.bincount(position, weights=pieces.samples.astype(np.float64),
                             minlength=distinct.size)
        index = np.arange(distinct.size + 1, dtype=np.float64) - 0.5
        return Histogram(index, weights, times, counts, mode, pieces.counted,
                         pieces.covered, pieces.gap_limit,
                         values=tuple(float(v) for v in distinct),
                         labels=_labels_of(series, distinct), stats=stats)
    edges = _edges(pieces.values[used] if used.any() else pieces.values, bins)
    values = pieces.values
    weights, _ = np.histogram(values, bins=edges, weights=weight)
    times, _ = np.histogram(values, bins=edges, weights=pieces.durations)
    counts, _ = np.histogram(values, bins=edges, weights=pieces.samples.astype(np.float64))
    below = float(weight[values < edges[0]].sum())
    above = float(weight[values > edges[-1]].sum())
    return Histogram(edges, weights.astype(np.float64), times.astype(np.float64),
                     counts.astype(np.float64), mode, pieces.counted, pieces.covered,
                     pieces.gap_limit, below=below, above=above, stats=stats)


def value_at(series: SignalSeries, t: float) -> float | None:
    """The value *series* last had at time *t*; None before its first sample."""
    stamps = np.asarray(series.numpy_timestamps(), dtype=np.float64)
    values = np.asarray(series.numpy_values(), dtype=np.float64)
    size = min(stamps.size, values.size)
    index = int(np.searchsorted(stamps[:size], t, side='right')) - 1
    if index < 0:
        return None
    value = float(values[index])
    return value if math.isfinite(value) else None


def spans(series: SignalSeries, low: float, high: float,
          time_range: tuple[float, float] | None = None,
          limit: float | None = None, last: bool = False) -> list[tuple[float, float]]:
    """The stretches of time *series* held a value from *low* up to *high*
    (*high* itself only for the *last* bar, which ends at it), joined where
    one follows straight on from the next. Holds past the gap limit are
    left out, as the bars leave them out."""
    pieces = held(series, time_range, limit)
    inside = (pieces.values >= low) & ((pieces.values <= high) if last else (pieces.values < high))
    inside &= pieces.durations > 0
    result: list[tuple[float, float]] = []
    for start, duration in zip(pieces.starts[inside].tolist(), pieces.durations[inside].tolist()):
        end = start + duration
        if result and abs(result[-1][1] - start) <= 1e-9:
            result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result
