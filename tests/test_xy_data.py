# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The points of an XY plot: on the X signal's sample times, with the value Y
and the colour signal last had then, cut to a time range and thinned evenly.
"""
from __future__ import annotations

import numpy as np

from core.signal_store import SignalSeries
from core.xy_data import color_steps, nearest_point, xy_at, xy_points


def _series(name, ts, vs):
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit='u',
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float),
    )


def test_points_sit_on_the_x_signals_sample_times():
    speed = _series('Speed', [0.0, 1.0, 2.0, 3.0], [10, 20, 30, 40])
    torque = _series('Torque', [0.0, 1.0, 2.0, 3.0], [1, 2, 3, 4])
    points = xy_points(speed, torque)
    assert points.t.tolist() == [0.0, 1.0, 2.0, 3.0]
    assert points.x.tolist() == [10, 20, 30, 40]
    assert points.y.tolist() == [1, 2, 3, 4]
    assert points.color is None and points.stride == 1 and points.total == 4


def test_y_is_the_value_it_last_had_never_one_between_samples():
    speed = _series('Speed', [0.0, 1.0, 2.0, 3.0], [10, 20, 30, 40])
    torque = _series('Torque', [0.5, 2.5], [100, 200])   # another frame, slower
    points = xy_points(speed, torque)
    # No Torque yet at t=0: no point. At 1 and 2 it is still 100, not 140 or 180.
    assert points.t.tolist() == [1.0, 2.0, 3.0]
    assert points.y.tolist() == [100, 100, 200]
    assert points.x.tolist() == [20, 30, 40]


def test_a_sample_at_the_same_time_counts():
    points = xy_points(_series('X', [1.0], [5]), _series('Y', [1.0], [7]))
    assert points.y.tolist() == [7]


def test_only_the_time_range_is_taken_ends_included():
    speed = _series('Speed', [0.0, 1.0, 2.0, 3.0, 4.0], [10, 20, 30, 40, 50])
    torque = _series('Torque', [0.0, 1.0, 2.0, 3.0, 4.0], [1, 2, 3, 4, 5])
    points = xy_points(speed, torque, time_range=(1.0, 3.0))
    assert points.t.tolist() == [1.0, 2.0, 3.0]
    assert points.total == 3
    assert xy_points(speed, torque, time_range=(10.0, 20.0)).count == 0


def test_the_colour_signal_is_held_too_and_needs_a_value():
    speed = _series('Speed', [0.0, 1.0, 2.0], [10, 20, 30])
    torque = _series('Torque', [0.0, 1.0, 2.0], [1, 2, 3])
    temp = _series('Temp', [0.9, 1.9], [60, 70])
    points = xy_points(speed, torque, temp)
    assert points.t.tolist() == [1.0, 2.0]
    assert points.color.tolist() == [60, 70]
    assert points.x.tolist() == [20, 30] and points.y.tolist() == [2, 3]


def test_values_that_are_not_numbers_give_no_point():
    speed = _series('Speed', [0.0, 1.0, 2.0], [10, np.nan, 30])
    torque = _series('Torque', [0.0, 1.0, 2.0], [1, 2, np.inf])
    assert xy_points(speed, torque).t.tolist() == [0.0]


def test_many_samples_are_thinned_evenly_and_say_so():
    n = 1000
    times = np.arange(n, dtype=float)
    points = xy_points(_series('X', times, times), _series('Y', times, times * 2),
                       max_points=100)
    assert points.stride == 10 and points.total == n
    assert points.count == 100
    assert points.t[:3].tolist() == [0.0, 10.0, 20.0]
    assert points.y[:3].tolist() == [0.0, 20.0, 40.0]
    # One past the cap already takes every second sample.
    assert xy_points(_series('X', times[:101], times[:101]),
                     _series('Y', times[:101], times[:101]), max_points=100).stride == 2


def test_an_empty_signal_gives_no_points():
    empty = _series('E', [], [])
    full = _series('F', [0.0, 1.0], [1, 2])
    assert xy_points(empty, full).count == 0
    assert xy_points(full, empty).count == 0
    assert xy_points(full, full, empty).count == 0


def test_where_the_signals_are_at_a_time_is_exact_whatever_was_thinned():
    times = np.arange(100, dtype=float)
    x, y = _series('X', times, times), _series('Y', times + 0.5, times * 2)
    assert xy_points(x, y, max_points=10).stride == 10
    assert xy_at(x, y, 37.6) == (37.0, 74.0)      # sample 37, not a thinned 30
    assert xy_at(x, y, 37.4) == (37.0, 72.0)      # Y still holds sample 36
    assert xy_at(x, y, 0.2) is None               # no Y yet
    assert xy_at(x, _series('E', [], []), 5.0) is None


def test_the_nearest_point_is_measured_in_the_units_given():
    points = xy_points(_series('X', [0, 1, 2], [0.0, 100.0, 200.0]),
                       _series('Y', [0, 1, 2], [0.0, 1.0, 0.0]))
    # In plain units (120, 0.9) is 20 from the second point...
    index, distance = nearest_point(points, 120.0, 0.9)
    assert index == 1 and round(distance, 1) == 20.0
    # ...but where a step of X is 100 wide and one of Y 0.1, it is 1.02 away.
    index, distance = nearest_point(points, 120.0, 0.9, x_unit=100.0, y_unit=0.1)
    assert index == 1 and round(distance, 2) == 1.02
    # With Y that fine, the first and third points are nearer a click at Y = 0.
    assert nearest_point(points, 120.0, 0.0, x_unit=100.0, y_unit=0.1)[0] == 2
    assert nearest_point(xy_points(_series('E', [], []), _series('E', [], [])), 0, 0) is None


def test_colour_steps_span_the_signals_range():
    steps, low, high = color_steps(np.array([10.0, 20.0, 15.0, 12.5]), levels=4)
    assert (low, high) == (10.0, 20.0)
    assert steps.tolist() == [0, 3, 2, 1]


def test_a_constant_colour_signal_is_one_step():
    steps, low, high = color_steps(np.array([5.0, 5.0]))
    assert steps.tolist() == [0, 0] and low == 5.0 and high > low
    assert color_steps(np.empty(0))[0].size == 0
