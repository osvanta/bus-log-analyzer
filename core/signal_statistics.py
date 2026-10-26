# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Statistics of a signal over a time range: minimum, maximum, mean, RMS and the
number of samples, as the signal table shows them for the visible range or
between the cursors.

Mean and RMS are over the samples, not weighted by how long each value held:
the same as asammdf and as a spreadsheet would give. Read a chunk at a time,
NaN skipped, so a signal of millions of samples needs no temporary array
longer than one chunk, and need not be in time order.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Samples looked at per step, bounding the temporary arrays.
CHUNK = 1 << 20


@dataclass(frozen=True)
class RangeStatistics:
    minimum: float
    maximum: float
    mean: float
    rms: float
    samples: int


def range_statistics(timestamps, values, start: float, end: float, *,
                     hold: bool = False) -> RangeStatistics | None:
    """Statistics of the samples with ``start <= t <= end``; None when there
    are none (or all are NaN).

    With *hold*, a range with no sample of its own takes the value held from
    the last sample before it, as a step plot shows it: minimum, maximum, mean
    and RMS are that value, and ``samples`` is 0.
    """
    ts = np.asarray(timestamps, dtype=np.float64)
    vs = np.asarray(values, dtype=np.float64)
    count = 0
    total = 0.0
    squares = 0.0
    low = np.inf
    high = -np.inf
    held_t = -np.inf          # the last sample before the range: its time …
    held_v = np.nan           # … and value
    for lo in range(0, min(len(ts), len(vs)), CHUNK):
        t = ts[lo:lo + CHUNK]
        v = vs[lo:lo + CHUNK]
        if hold:
            before = np.flatnonzero((t < start) & ~np.isnan(v))
            if before.size:
                last = before[np.argmax(t[before])]
                if t[last] > held_t:
                    held_t, held_v = float(t[last]), float(v[last])
        v = v[(t >= start) & (t <= end) & ~np.isnan(v)]
        if not v.size:
            continue
        count += int(v.size)
        total += float(v.sum())
        squares += float(np.dot(v, v))
        low = min(low, float(v.min()))
        high = max(high, float(v.max()))
    if not count:
        if hold and held_v == held_v:
            return RangeStatistics(held_v, held_v, held_v, abs(held_v), 0)
        return None
    return RangeStatistics(low, high, total / count, float(np.sqrt(squares / count)), count)
