# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
How long two signals spent together in each cell of a grid
(core.residency): time is cut at every sample of either signal, each piece
counts for its length, pieces past a gap limit count for nothing.
"""
from __future__ import annotations

import numpy as np
import pytest

from core import residency as res
from core.histogram import SAMPLES
from core.signal_store import SignalSeries


def _series(ts, vs):
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name='Sig', unit='',
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float))


# X every second, Y every two: 10 with 1 for two seconds, then 30 with 5.
_X = _series([0, 1, 2, 3, 4], [10, 10, 30, 30, 30])
_Y = _series([0, 2, 4], [1, 5, 5])


def _cells(grid):
    return {(i, j): float(grid.weights[i, j])
            for i, j in zip(*np.nonzero(grid.weights))}


def test_time_is_cut_at_every_sample_of_either_signal():
    parts = res.pieces(_X, _Y)
    assert list(parts.starts) == [0, 1, 2, 3]
    assert list(parts.durations) == [1, 1, 1, 1]
    assert list(parts.x) == [10, 10, 30, 30] and list(parts.y) == [1, 1, 5, 5]
    assert parts.counted == 4.0 and parts.covered == 4.0


def test_each_cell_has_the_time_both_signals_were_in_it():
    grid = res.residency(_X, _Y, x_bins=20.0, y_bins=4.0)
    assert list(grid.x_edges) == [0, 20, 40] and list(grid.y_edges) == [0, 4, 8]
    assert _cells(grid) == {(0, 0): 2.0, (1, 1): 2.0}
    assert grid.total == grid.counted == 4.0 and grid.outside == 0.0
    assert grid.cell_of(15, 3) == (0, 0) and grid.cell_of(40, 8) == (1, 1)
    assert grid.cell_of(41, 3) is None


def test_a_signal_sent_faster_does_not_count_for_more():
    # X every 10 ms; Y twice. One second at each Y value whatever X does.
    fast = np.round(np.arange(0.0, 2.0, 0.01), 6)
    x = _series(fast, np.full(fast.size, 5.0))
    y = _series([0.0, 1.0, 2.0], [1.0, 3.0, 3.0])
    grid = res.residency(x, y, x_bins=[0, 10], y_bins=[0, 2, 4])
    assert grid.weights[0] == pytest.approx([1.0, 1.0])
    by_samples = res.residency(x, y, x_bins=[0, 10], y_bins=[0, 2, 4], mode=SAMPLES)
    assert list(by_samples.weights[0]) == [100.0, 100.0]


def test_nothing_counts_before_both_signals_have_a_value():
    late_y = _series([1, 2, 4], [1, 5, 5])
    parts = res.pieces(_X, late_y)
    assert list(parts.starts) == [1, 2, 3]
    assert parts.covered == 4.0 and parts.counted == 3.0


def test_a_silence_of_either_signal_past_its_gap_limit_is_not_counted():
    # Y every 0.1 s, then silent from 1 s to 50 s; X every 0.1 s throughout.
    times = np.round(np.arange(0.0, 51.0, 0.1), 6)
    x = _series(times, np.full(times.size, 2.0))
    y_times = np.concatenate((np.round(np.arange(0.0, 1.01, 0.1), 6), [50.0, 50.5, 51.0]))
    y = _series(y_times, np.full(y_times.size, 7.0))
    grid = res.residency(x, y, x_bins=[0, 4], y_bins=[0, 10])
    assert grid.counted == pytest.approx(2.0)
    assert grid.weights[0, 0] == pytest.approx(2.0)
    longer = res.residency(x, y, x_bins=[0, 4], y_bins=[0, 10], y_limit=100.0)
    assert longer.weights[0, 0] == pytest.approx(51.0)


def test_a_time_range_counts_only_its_part():
    grid = res.residency(_X, _Y, time_range=(0.5, 2.5), x_bins=20.0, y_bins=4.0)
    assert _cells(grid) == {(0, 0): 1.5, (1, 1): 0.5}
    assert grid.covered == 2.0
    # The values held into the range are no samples taken in it.
    samples = res.residency(_X, _Y, time_range=(0.5, 2.5), x_bins=20.0, y_bins=4.0,
                            mode=SAMPLES)
    assert _cells(samples) == {(0, 0): 1.0, (1, 1): 1.0}


def test_time_outside_typed_edges_is_outside_not_dropped():
    grid = res.residency(_X, _Y, x_bins=[0, 20], y_bins=[0, 8])
    assert _cells(grid) == {(0, 0): 2.0}
    assert grid.outside == 2.0 and grid.total == 4.0


def test_automatic_cells_are_round_and_about_twenty_along_each_axis():
    x = _series(np.arange(100.0), np.linspace(800, 6200, 100))
    y = _series(np.arange(100.0), np.linspace(-40, 180, 100))
    grid = res.residency(x, y)
    # About 5,400 rpm over 20 cells is 270 each, rounded up to 500; about
    # 220 Nm, 11 each, to 20.
    assert grid.x_edges[1] - grid.x_edges[0] == 500.0
    assert grid.y_edges[1] - grid.y_edges[0] == 20.0
    assert grid.x_edges[0] <= 800 and grid.x_edges[-1] >= 6145
    assert grid.outside == 0.0


def test_each_cell_can_keep_a_third_signals_mean():
    z = _series([0, 1, 2, 3], [100, 200, 300, 400])
    grid = res.residency(_X, _Y, x_bins=20.0, y_bins=4.0, z=z)
    assert grid.mean[0, 0] == pytest.approx(150.0)
    assert grid.mean[1, 1] == pytest.approx(350.0)
    assert np.isnan(grid.mean[0, 1]) and np.isnan(grid.mean[1, 0])
    # Before the third signal has a value, the time has no mean to give.
    late = _series([2, 3], [300, 400])
    assert np.isnan(res.residency(_X, _Y, x_bins=20.0, y_bins=4.0, z=late).mean[0, 0])


def test_the_mean_is_weighted_by_time_and_skips_unknown_values():
    # One cell; the third signal is 10 for 3 s, 50 for 1 s, then unknown.
    x = _series([0, 5], [1, 1])
    y = _series([0, 5], [1, 1])
    z = _series([0, 3, 4], [10, 50, np.nan])
    grid = res.residency(x, y, x_bins=[0, 2], y_bins=[0, 2], z=z)
    assert grid.mean[0, 0] == pytest.approx((10 * 3 + 50 * 1) / 4)


def test_without_samples_there_is_no_grid():
    grid = res.residency(_series([], []), _Y)
    assert grid.weights.size == 0 and grid.counted == 0.0


def test_the_stretches_a_cell_covers():
    x = _series([0, 1, 2, 3, 4, 5], [10, 10, 30, 10, 10, 10])
    y = _series([0, 5], [1, 1])
    assert res.cell_spans(x, y, (0, 20), (0, 4)) == [(0.0, 2.0), (3.0, 5.0)]
    assert res.cell_spans(x, y, (20, 30), (0, 4)) == []
    assert res.cell_spans(x, y, (20, 30), (0, 4), x_last=True) == [(2.0, 3.0)]
    assert res.cell_spans(x, y, (0, 20), (0, 1)) == []
    assert res.cell_spans(x, y, (0, 20), (0, 1), y_last=True) == [(0.0, 2.0), (3.0, 5.0)]
    assert res.cell_spans(x, y, (0, 20), (0, 4), time_range=(0.5, 3.5)) == [
        (0.5, 2.0), (3.0, 3.5)]


def test_stretches_leave_out_pieces_past_a_gap_limit():
    x = _series([0.0, 0.1, 0.2, 50.0, 50.1], [1, 1, 1, 1, 1])
    y = _series([0.0, 0.1, 0.2, 50.0, 50.1], [1, 1, 1, 1, 1])
    assert res.cell_spans(x, y, (0, 2), (0, 2)) == [
        (0.0, pytest.approx(0.2)), (50.0, pytest.approx(50.1))]


def test_the_table_has_a_row_per_y_cell_highest_first():
    grid = res.residency(_X, _Y, x_bins=20.0, y_bins=4.0)
    text = res.table_text(grid, grid.weights, 'Speed', 'Load')
    assert text.splitlines() == [
        'Load \\ Speed,0 to 20,20 to 40',
        '4 to 8,,2',
        '0 to 4,2,',
    ]
    tabbed = res.table_text(grid, grid.weights, 'A, B', 'C', separator=',')
    assert tabbed.splitlines()[0].startswith('"C \\ A, B"')
