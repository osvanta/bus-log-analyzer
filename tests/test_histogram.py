# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
How long a signal spent at each value (core.histogram): time is counted by
how long each sample held, holds past the gap limit are left out, and what
falls outside typed edges is counted as below or above.
"""
from __future__ import annotations

import numpy as np
import pytest

from core import histogram as hist
from core.signal_store import SignalSeries


def _series(ts, vs, labels=None):
    series = SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name='Sig', unit='°C',
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float))
    if labels is not None:
        series.raw_values = list(labels)
        series.has_labels = True
    return series


# 10 for three seconds, then 20 for one: the last sample ends the recording.
_STEP = _series([0.0, 3.0, 4.0], [10.0, 20.0, 20.0])


def test_time_is_counted_by_how_long_each_value_held():
    bars = hist.histogram(_STEP, bins=10.0)
    assert list(bars.edges) == [10.0, 20.0, 30.0]
    assert list(bars.weights) == [3.0, 1.0]
    assert bars.counted == 4.0 and bars.covered == 4.0
    assert list(bars.weights * 100 / bars.total) == [75.0, 25.0]
    assert list(bars.counts) == [1, 2]


def test_a_faster_sent_value_is_not_counted_as_more_common():
    # One second of 1 sent every 10 ms, then one second of 2 sent once.
    fast = np.arange(0.0, 1.0, 0.01)
    ts = np.concatenate((fast, [1.0, 2.0]))
    vs = np.concatenate((np.ones(fast.size), [2.0, 2.0]))
    series = _series(ts, vs)
    by_time = hist.histogram(series, bins=[0.5, 1.5, 2.5])
    assert by_time.weights == pytest.approx([1.0, 1.0])
    by_samples = hist.histogram(series, bins=[0.5, 1.5, 2.5], mode=hist.SAMPLES)
    assert list(by_samples.weights) == [100.0, 2.0]
    assert by_samples.times == pytest.approx([1.0, 1.0])       # time is still known


def test_a_hold_past_the_gap_limit_is_not_counted():
    # Every 0.1 s for a second, a 99 s silence, then every 0.1 s again.
    before = np.round(np.arange(0.0, 1.01, 0.1), 6)
    after = np.round(100.0 + np.arange(0.0, 1.01, 0.1), 6)
    series = _series(np.concatenate((before, after)),
                     np.concatenate((np.full(before.size, 1.0), np.full(after.size, 2.0))))
    assert hist.gap_limit(series.numpy_timestamps()) == 1.0        # 10 x 0.1 s is under 1 s
    bars = hist.histogram(series, bins=[0.5, 1.5, 2.5])
    assert bars.weights == pytest.approx([1.0, 1.0])           # not 100 s of 1
    assert bars.counted == pytest.approx(2.0) and bars.covered == pytest.approx(101.0)
    # A longer limit counts the silence as the last value seen.
    longer = hist.histogram(series, bins=[0.5, 1.5, 2.5], limit=200.0)
    assert longer.weights == pytest.approx([100.0, 1.0])


def test_the_gap_limit_is_ten_periods_and_at_least_a_second():
    assert hist.gap_limit(np.arange(0.0, 10.0, 0.5)) == 5.0
    assert hist.gap_limit(np.arange(0.0, 1.0, 0.01)) == 1.0
    assert hist.gap_limit(np.array([3.0])) == 1.0


def test_a_time_range_counts_only_its_part_of_each_hold():
    bars = hist.histogram(_STEP, time_range=(1.5, 3.5), bins=10.0)
    assert list(bars.weights) == [1.5, 0.5]
    assert bars.covered == 2.0
    # The value held into the range is no sample taken in it.
    assert list(bars.counts) == [0, 1]
    samples = hist.histogram(_STEP, time_range=(1.5, 3.5), bins=10.0, mode=hist.SAMPLES)
    assert samples.weights.sum() == 1.0


def test_a_range_before_the_first_sample_has_nothing():
    bars = hist.histogram(_series([5.0, 6.0], [1.0, 2.0]), time_range=(0.0, 4.0))
    assert not bars.weights.size and bars.covered == 4.0


def test_values_outside_typed_edges_are_below_or_above_not_dropped():
    series = _series([0, 1, 2, 3, 4, 5], [-5, 10, 30, 50, 70, 70])
    bars = hist.histogram(series, bins=[0.0, 25.0, 60.0])
    assert list(bars.weights) == [1.0, 2.0]
    assert bars.below == 1.0 and bars.above == 1.0
    assert bars.total == bars.counted == 5.0


def test_automatic_bins_are_round_and_cover_every_value():
    series = _series(np.arange(10.0), [18.3, 20, 25, 30, 33, 40, 44, 47, 51.7, 51.7])
    bars = hist.histogram(series)
    width = bars.edges[1] - bars.edges[0]
    assert width == 2.0
    assert bars.edges[0] <= 18.3 and bars.edges[-1] >= 51.7
    assert bars.edges[0] % 2.0 == 0
    assert bars.below == bars.above == 0.0


def test_a_constant_signal_has_one_bar():
    bars = hist.histogram(_series([0, 1, 2], [7, 7, 7]))
    assert bars.weights.size == 1 and bars.weights[0] == 2.0


@pytest.mark.parametrize('low, high, width', [
    (0, 34, 2.0), (0, 100, 5.0), (0, 0.3, 0.01), (0, 7000, 250.0), (5, 5, 1.0)])
def test_nice_widths(low, high, width):
    assert hist.nice_width(low, high) == pytest.approx(width)


@pytest.mark.parametrize('text, parsed', [
    ('', None), ('  ', None), ('Auto', None), ('2', 2.0), ('2.5', 2.5),
    ('0, 25, 45', [0.0, 25.0, 45.0]), ('45;0;25', [0.0, 25.0, 45.0])])
def test_what_the_bins_box_understands(text, parsed):
    assert hist.parse_bins(text) == parsed


@pytest.mark.parametrize('text', ['x', '0', '-2', '5, 5', 'nan', '1, inf'])
def test_what_the_bins_box_refuses(text):
    with pytest.raises(ValueError):
        hist.parse_bins(text)


def test_statistics_are_weighted_by_time():
    stats = hist.histogram(_STEP, bins=10.0).stats
    assert stats['min'] == 10.0 and stats['max'] == 20.0
    assert stats['mean'] == pytest.approx(12.5)
    assert stats['p5'] == 10.0 and stats['p50'] == 10.0 and stats['p95'] == 20.0


def test_a_signal_with_labels_has_a_bar_for_each_state():
    series = _series([0, 1, 3, 6, 7], [0, 2, 1, 2, 2],
                     labels=['Off', 'Drive', 'Ready', 'Drive', 'Drive'])
    bars = hist.histogram(series, bins=10.0)            # bins do not apply
    assert bars.labels == ('Off', 'Ready', 'Drive')
    assert bars.values == (0.0, 1.0, 2.0)
    assert list(bars.weights) == [1.0, 3.0, 3.0]
    assert bars.bar_of(1.0) == 1 and bars.bar_of(5.0) is None


def test_many_distinct_labelled_values_get_numeric_bins():
    values = np.arange(100.0)
    series = _series(np.arange(100.0), values, labels=[str(v) for v in values])
    bars = hist.histogram(series)
    assert not bars.labels


def test_the_bar_a_value_falls_in():
    bars = hist.histogram(_STEP, bins=10.0)
    assert bars.bar_of(10.0) == 0 and bars.bar_of(19.99) == 0
    assert bars.bar_of(20.0) == 1 and bars.bar_of(30.0) == 1   # the last edge closes the last bar
    assert bars.bar_of(9.0) is None and bars.bar_of(31.0) is None


def test_the_stretches_a_bar_covers_are_joined_where_they_meet():
    series = _series([0, 1, 2, 3, 4, 5], [10, 11, 30, 12, 12, 12])
    assert hist.spans(series, 10, 20) == [(0.0, 2.0), (3.0, 5.0)]
    assert hist.spans(series, 20, 30) == []
    assert hist.spans(series, 20, 30, last=True) == [(2.0, 3.0)]
    assert hist.spans(series, 10, 20, time_range=(0.5, 3.5)) == [(0.5, 2.0), (3.0, 3.5)]


def test_stretches_leave_out_holds_past_the_gap_limit():
    series = _series([0.0, 0.1, 0.2, 50.0, 50.1], [1, 1, 1, 1, 1])
    assert hist.spans(series, 0, 2) == [(0.0, pytest.approx(0.2)), (50.0, pytest.approx(50.1))]


def test_the_value_at_a_time_is_the_one_held_then():
    assert hist.value_at(_STEP, 2.9) == 10.0
    assert hist.value_at(_STEP, 3.0) == 20.0
    assert hist.value_at(_STEP, -1.0) is None
