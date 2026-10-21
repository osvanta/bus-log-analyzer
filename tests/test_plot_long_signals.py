# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Fitting and drawing a signal of millions of samples stays cheap.

Fit to window, fit vertical, centring and live autoscale used to build a
Python list of every plotted sample: seconds and gigabytes for a long signal.
They now reduce the arrays in C, a chunk at a time, and a curve with no break
draws without a per-sample connection mask. These tests hold the new code to
the old answers and the panel's heap to a bound.
"""
from __future__ import annotations

import tracemalloc

import numpy as np
import pytest

import gui.plot_widget as plot_widget
from core.signal_store import SignalSeries
from gui.plot_widget import (
    PlotPanel,
    _curve_connection,
    _union_range,
    _value_range,
    _window_range,
)


def _old_value_range(values):
    vals = [v for v in values if v == v]
    return (min(vals), max(vals)) if vals else None


def _old_window_range(ts, vs, x_min, x_max):
    vals = [float(v) for t, v in zip(ts, vs) if x_min <= float(t) <= x_max and v == v]
    return (min(vals), max(vals)) if vals else None


@pytest.fixture(params=[3, 1 << 20])
def chunk(request, monkeypatch):
    monkeypatch.setattr(plot_widget, "_RANGE_CHUNK", request.param)
    return request.param


def _awkward(n=200, seed=0):
    rng = np.random.default_rng(seed)
    ts = np.sort(rng.uniform(0, 100, n))
    ts[150:] -= 60                     # a time reset: not in order
    vs = rng.normal(0, 10, n)
    vs[::7] = np.nan
    vs[11] = np.inf
    vs[12] = -np.inf
    return ts, vs


def test_value_range_matches_the_list_it_replaced(chunk):
    ts, vs = _awkward()
    assert _value_range(vs) == _old_value_range(vs)
    assert _value_range(ts) == _old_value_range(ts)
    assert _value_range(np.full(5, np.nan)) is None
    assert _value_range(np.empty(0)) is None


@pytest.mark.parametrize("window", [(0, 100), (20, 30), (35, 36), (-5, 0.1), (200, 300)])
def test_window_range_matches_the_list_it_replaced(chunk, window):
    ts, vs = _awkward()
    assert _window_range(ts, vs, *window) == _old_window_range(ts, vs, *window)


def test_union_range_skips_missing_spans():
    assert _union_range([(-1.0, 2.5), None, (2.0, 3.0)]) == (-1.0, 3.0)
    assert _union_range([(2.0, 3.0), None, (-1.0, 2.5)]) == (-1.0, 3.0)
    assert _union_range([None, None]) is None


def test_an_unbroken_curve_needs_no_mask(chunk):
    ts = np.arange(10.0)
    assert _curve_connection(ts, ts * 2, PlotPanel._curve_connect_mask) == ('all', True)


@pytest.mark.parametrize("broken_at", [0, 2, 3, 4, 9])
def test_a_broken_curve_keeps_its_exact_mask(chunk, broken_at):
    # A break on each side of a three-sample chunk edge, and at both ends.
    for kind in ("nan", "reset"):
        ts = np.arange(10.0)
        vs = ts * 2
        if kind == "nan":
            vs[broken_at] = np.nan
        elif broken_at:
            ts[broken_at] = 0.0
        else:
            continue
        connect, finite = _curve_connection(ts, vs, PlotPanel._curve_connect_mask)
        assert isinstance(connect, np.ndarray), (kind, broken_at)
        assert connect.tolist() == PlotPanel._curve_connect_mask(ts, vs).tolist()
        assert finite is False


def test_fitting_a_long_signal_takes_no_list_of_its_samples(panel, qapp):
    n = 2_000_000
    ts = np.arange(n, dtype=np.float64) * 0.001
    vs = np.sin(ts) * 50.0
    vs[n // 2] = 400.0
    panel.add_series("Long", SignalSeries(None, "Msg", 1, "Long", "V", ts, vs))
    qapp.processEvents()
    tracemalloc.start()
    try:
        panel.fit_to_window()
        panel.fit_vertical()
        panel.center_on_time(500.0, 10.0)
        panel.fit_vertical()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    # The lists were 2 M Python floats each, over 60 MB; chunked reductions
    # hold a few MB.
    assert peak < 12 * 1024 * 1024
    y_min, y_max = panel.plot.plotItem.vb.viewRange()[1]
    assert y_min < -45 and y_max > 45


def test_fit_to_window_still_finds_the_spike(panel, qapp):
    ts = np.arange(1000.0)
    vs = np.zeros(1000)
    vs[500] = 1000.0
    vs[10] = np.nan
    panel.add_series("Spike", SignalSeries(None, "Msg", 1, "Spike", "V", ts, vs))
    qapp.processEvents()
    panel.fit_to_window()
    x_range, y_range = panel.plot.plotItem.vb.viewRange()
    assert x_range[0] <= 0 and x_range[1] >= 999
    assert y_range[0] < 0 and y_range[1] > 1000
