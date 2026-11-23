# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
GPS track (core.gps_track): finding the latitude and longitude among the
decoded signals, cleaning the fixes, gaps, distance, and what the view asks
of a track: where a cursor is, the passes near a point, the times in a box.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from core import gps_track as gps
from core.signal_store import SignalSeries

#: Metres per degree of latitude, and of longitude at the equator.
_M = gps.EARTH_RADIUS * math.pi / 180


def _series(name, times, values, unit='deg', message='VP', channel=1):
    return SignalSeries(channel=channel, message_name=message, message_id=1, signal_name=name,
                        unit=unit, timestamps=np.asarray(times, dtype=float),
                        values=np.asarray(values, dtype=float))


def _route(points_m, times, lat0=50.0, lon0=8.0):
    """Latitude and longitude series through (east, north) metres. Not from
    0°/0°: that is taken for a receiver without a fix."""
    east = np.asarray([p[0] for p in points_m], dtype=float)
    north = np.asarray([p[1] for p in points_m], dtype=float)
    lat = lat0 + north / _M
    lon = lon0 + east / (_M * math.cos(math.radians(lat0)))
    return _series('VP_Latitude', times, lat), _series('VP_Longitude', times, lon)


def _straight_east(metres=1000.0, seconds=100.0, rate=10):
    times = np.linspace(0, seconds, int(seconds * rate) + 1)
    east = np.linspace(0, metres, times.size)
    return _route(list(zip(east, np.zeros_like(east))), times)


# ── Finding the position signals ────────────────────────────────────────


@pytest.mark.parametrize('name, words', [
    ('VP_Latitude', ['vp', 'latitude']),
    ('GPSLat', ['gps', 'lat']),
    ('lateralAccel', ['lateral', 'accel']),
    ('PosLon2', ['pos', 'lon', '2']),
    ('Long_Accel', ['long', 'accel']),
])
def test_a_names_words_split_at_case_and_marks(name, words):
    assert gps.name_words(name) == words


@pytest.mark.parametrize('name, unit, role', [
    ('VP_Latitude', 'deg', gps.LAT),
    ('GPS_Lat', '°', gps.LAT),
    ('Longitude', '', gps.LON),
    ('PosLng', 'degrees', gps.LON),
    ('IMU_LateralAccel', 'm/s2', None),     # "lat" only starts "lateral"
    ('LongAccel', 'm/s2', None),            # the word, but not degrees
    ('LatAcc', 'g', None),
    ('GNSS_Heading', 'deg', None),          # degrees, but not a position
    ('LatLonValid', '', None),              # both: neither
])
def test_a_signal_is_a_position_by_its_name_and_unit(name, unit, role):
    assert gps.position_role(_series(name, [0], [1], unit=unit)) == role


def test_the_latitude_and_longitude_are_found_among_decoys():
    times = [0, 1, 2]
    signals = [
        ('a', _series('IMU_LateralAccel', times, [0.1, 0.2, 0.3], 'm/s2', 'IMU')),
        ('b', _series('LongAccel', times, [1, 2, 3], '', 'IMU')),
        ('c', _series('VP_Latitude', times, [50.0, 50.1, 50.2])),
        ('d', _series('VP_Longitude', times, [8.0, 8.1, 8.2])),
        ('e', _series('GNSS_Heading', times, [10, 20, 30])),
    ]
    assert gps.find_position_signals(signals) == ('c', 'd')


def test_a_pair_from_one_message_wins():
    times = [0, 1, 2]
    signals = [
        ('lat_a', _series('Lat', times, [50, 50, 50], message='A')),
        ('lon_b', _series('Lon', times, [8, 8, 8], message='B')),
        ('lon_a', _series('Lon', times, [8, 8, 8], message='A')),
    ]
    assert gps.find_position_signals(signals) == ('lat_a', 'lon_a')


def test_signals_without_a_plausible_value_are_passed_over():
    times = [0, 1, 2]
    signals = [
        # Every value 0 or out of range ("not available"): never a position.
        ('zero', _series('Latitude', times, [0, 0, 0], message='A')),
        ('lon_a', _series('Longitude', times, [211.1, 211.1, 211.1], message='A')),
        ('lat_b', _series('GPS_Lat', times, [48.1, 48.2, 48.3], message='B')),
        ('lon_b', _series('GPS_Lon', times, [11.5, 11.6, 11.7], message='B')),
    ]
    assert gps.find_position_signals(signals) == ('lat_b', 'lon_b')
    assert gps.find_position_signals(signals[:2]) is None
    assert gps.find_position_signals([]) is None
    # A latitude that never leaves 0 (a receiver without a fix) is passed
    # over even beside a longitude that looks right in its own message.
    no_fix = [('lat_c', _series('Latitude', times, [0, 0, 0], message='C')),
              ('lon_c', _series('Longitude', times, [8.0, 8.1, 8.2], message='C'))]
    assert gps.find_position_signals(no_fix + signals[2:]) == ('lat_b', 'lon_b')


# ── Cleaning ────────────────────────────────────────────────────────────


def test_bad_fixes_are_dropped():
    times = [0, 1, 2, 3, 4, 5, 6]
    lat = _series('Lat', times, [50.0, np.nan, 211.1, 0.0, 50.0, 95.0, 50.0])
    lon = _series('Lon', times, [8.0, 8.0, 211.1, 0.0, 8.0, 8.0, 190.0])
    track = gps.build_track(lat, lon)
    # Kept: t=0 and t=4. Missing, "not available", 0/0, out of range: dropped.
    assert track.t.tolist() == [0.0, 4.0]
    assert track.ok.tolist() == [True, False, False, False, True, False, False]


def test_zero_latitude_alone_is_a_real_place():
    lat = _series('Lat', [0, 1], [0.0, 0.0])
    lon = _series('Lon', [0, 1], [8.0, 8.0001])
    assert gps.build_track(lat, lon).size == 2


def test_latitude_and_longitude_from_different_times_are_paired_by_holding():
    lat = _series('Lat', [0.0, 2.0], [50.0, 50.001])
    lon = _series('Lon', [1.0, 3.0], [8.0, 8.001])
    track = gps.build_track(lat, lon)
    # t=0 has no longitude yet; then each time of either, both held.
    assert track.t.tolist() == [1.0, 2.0, 3.0]
    assert track.lat.tolist() == [50.0, 50.001, 50.001]
    assert track.lon.tolist() == [8.0, 8.0, 8.001]


def test_the_track_is_laid_flat_around_its_middle_north_up():
    lat, lon = _route([(0, 0), (1000, 0), (1000, 500)], [0, 1, 2], lat0=50.0, lon0=8.0)
    track = gps.build_track(lat, lon)
    assert track.e.tolist() == pytest.approx([-500, 500, 500], abs=0.5)
    assert track.n.tolist() == pytest.approx([-250, -250, 250], abs=0.5)


# ── Gaps and distance ───────────────────────────────────────────────────


def test_a_kilometre_east_measures_a_kilometre():
    track = gps.build_track(*_straight_east(1000.0))
    assert track.distance == pytest.approx(1000.0, rel=0.005)
    assert track.duration == pytest.approx(100.0)
    assert track.gaps() == [] and track.gap_distance == 0.0


def test_no_fix_longer_than_the_gap_limit_breaks_the_line():
    times = np.arange(0, 30.01, 0.5)
    east = times * 10.0                                # 10 m/s east
    lat, lon = _route(list(zip(east, np.zeros_like(east))), times)
    values = lat.numpy_values().copy()
    tunnel = (times > 10) & (times < 15)               # no fix for 5 s
    values[tunnel] = np.nan
    lat = _series('VP_Latitude', times, values)
    track = gps.build_track(lat, lon)
    gaps = track.gaps()
    assert len(gaps) == 1
    before, after = gaps[0]
    assert (track.t[before], track.t[after]) == (10.0, 15.0)
    assert track.no_fix_seconds() == pytest.approx(5.0)
    # The gap counts as the straight line across it, and is told apart.
    assert track.gap_distance == pytest.approx(50.0, rel=0.01)
    assert track.distance == pytest.approx(300.0, rel=0.01)
    # A longer limit joins it again.
    assert gps.build_track(lat, lon, gap_seconds=6.0).gaps() == []


def test_a_jump_faster_than_300_km_h_breaks_the_line():
    lat, lon = _route([(0, 0), (10, 0), (5000, 0), (5010, 0)], [0, 1, 2, 3])
    track = gps.build_track(lat, lon)
    assert track.gaps() == [(1, 2)]


def test_wander_while_standing_adds_no_distance():
    rng = np.random.default_rng(3)
    times = np.arange(0, 600, 0.1)                     # ten minutes parked
    wander = rng.normal(0, 0.7, (times.size, 2))
    lat, lon = _route([tuple(p) for p in wander], times, lat0=50.0)
    track = gps.build_track(lat, lon)
    assert track.distance < 20.0
    # Each step summed would have made kilometres of it.
    steps = np.hypot(*np.diff(wander, axis=0).T).sum()
    assert steps > 3000


# ── What the view asks ──────────────────────────────────────────────────


def test_a_cursor_shows_the_fix_at_its_time_or_the_last_one_known():
    times = np.arange(0, 30.01, 0.5)
    east = times * 10.0
    lat, lon = _route(list(zip(east, np.zeros_like(east))), times)
    values = lat.numpy_values().copy()
    values[(times > 10) & (times < 15)] = np.nan
    track = gps.build_track(_series('VP_Latitude', times, values), lon)
    assert track.fix_at(-1.0) == (None, False)
    index, has = track.fix_at(5.2)
    assert track.t[index] == 5.0 and has
    index, has = track.fix_at(12.0)                    # in the tunnel
    assert track.t[index] == 10.0 and not has
    assert track.distance_at(5.0) == pytest.approx(50.0, rel=0.02)


def _out_and_back():
    """East 1 km on y=0, north 300 m, west 1 km on y=300, then back south
    across the first street at x=400: that spot is passed twice."""
    legs = [((0, 0), (1000, 0)), ((1000, 0), (1000, 300)), ((1000, 300), (400, 300)),
            ((400, 300), (400, -300))]
    points = []
    for (x0, y0), (x1, y1) in legs:
        steps = int(math.hypot(x1 - x0, y1 - y0) / 10)
        points += [(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps) for k in range(steps)]
    times = np.arange(len(points)) * 1.0               # 10 m/s
    return gps.build_track(*_route(points, times, lat0=50.0))


def test_a_place_passed_twice_has_two_passes_in_time_order():
    track = _out_and_back()
    # The crossing, in the flat frame (its origin is the drive's middle).
    e = 400 - (0 + 1000) / 2
    n = 0 - (-300 + 300) / 2
    passes = track.passes_near(e, n, radius=15.0)
    assert len(passes) == 2
    first, second = passes
    assert track.t[first] < track.t[second]
    assert track.t[first] == pytest.approx(40.0, abs=1.5)
    assert track.t[second] > 200
    # Only the time shown counts.
    assert track.passes_near(e, n, 15.0, time_range=(0.0, 100.0)) == [first]
    assert track.passes_near(e + 2000, n, 15.0) == []


def test_the_times_in_a_box_are_each_pass_through_it():
    track = _out_and_back()
    e, n = 400 - 500, 0.0
    spans = track.spans_in_box(e - 30, e + 30, n - 30, n + 30)
    assert len(spans) == 2
    (a0, a1), (b0, b1) = spans
    assert a0 < a1 < b0 < b1
    # Held until the next fix after the box: 60 m at 10 m/s is about 6 s.
    assert a1 - a0 == pytest.approx(7.0, abs=1.5)
    assert track.spans_in_box(e - 30, e + 30, n - 30, n + 30, time_range=(0, 100)) == [spans[0]]
    assert track.spans_in_box(3000, 3100, 3000, 3100) == []


def test_without_fixes_the_track_is_empty():
    lat = _series('Lat', [0, 1], [np.nan, 211.1])
    lon = _series('Lon', [0, 1], [8.0, 211.1])
    track = gps.build_track(lat, lon)
    assert track.size == 0 and track.distance == 0.0 and track.gaps() == []
    assert track.fix_at(0.5) == (None, False)
    assert track.passes_near(0, 0, 100) == [] and track.spans_in_box(-1, 1, -1, 1) == []
