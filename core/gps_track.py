# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""GPS track: a drive seen from above, from a latitude and a longitude signal.

Which decoded signals are the position is found from their names, units and
values. The fixes are cleaned (no value, out of range, the "not available"
codes, 0/0), cut where the receiver lost its fix or jumped, laid flat in
metres around the drive's middle, and measured. What the view asks of a
track (where a cursor is, which passes go near a point, when the vehicle was
inside a box) is answered here, apart from any drawing.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from core.signal_store import SignalSeries

#: Mean radius of the Earth, in metres.
EARTH_RADIUS = 6_371_008.8
#: No fix for longer than this, in seconds, breaks the line.
GAP_SECONDS = 2.0
#: A step faster than this, in m/s (300 km/h), is a jump, not a drive.
MAX_SPEED = 300.0 / 3.6
#: Distance counts a step only once the position has moved this far, in
#: metres, from the last counted point: a parked receiver wanders a metre
#: or two each second, and 3 m still let ten minutes parked add 360 m.
MIN_STEP = 5.0
LAT, LON = 'lat', 'lon'
#: Words naming a latitude or a longitude in a signal's name.
_LAT_WORDS = frozenset({'lat', 'latitude'})
_LON_WORDS = frozenset({'lon', 'lng', 'long', 'longitude'})
_FULL_WORDS = frozenset({'latitude', 'longitude'})
#: Units a position may carry; none at all is allowed too.
_DEGREE_UNITS = frozenset({'deg', 'deg.', 'degree', 'degrees', '°', 'grad', 'dd'})
_WORDS = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+')
#: Values read to judge whether a signal holds positions.
_SAMPLE = 65536


def name_words(name: str) -> list[str]:
    """The words of a signal name, lower case: VP_Latitude -> vp, latitude;
    GPSLat -> gps, lat; lateralAccel -> lateral, accel."""
    return [word.lower() for word in _WORDS.findall(name or '')]


def position_role(series: SignalSeries) -> str | None:
    """LAT or LON when the name and unit fit a position, else None. The
    values are not read; plausible() does that."""
    unit = (series.unit or '').strip().lower()
    if unit and unit not in _DEGREE_UNITS:
        return None
    words = set(name_words(series.signal_name))
    lat, lon = bool(words & _LAT_WORDS), bool(words & _LON_WORDS)
    if lat == lon:
        return None
    return LAT if lat else LON


def _limit(role: str) -> float:
    return 90.0 if role == LAT else 180.0


def plausible(series: SignalSeries, role: str) -> bool:
    """Whether the first values of *series* hold a position: at least one
    that is in range and not 0."""
    values = np.asarray(series.numpy_values(), dtype=np.float64)[:_SAMPLE]
    values = values[np.isfinite(values)]
    return bool(np.any((np.abs(values) <= _limit(role)) & (values != 0.0)))


def _score(series: SignalSeries) -> int:
    score = 0
    if set(name_words(series.signal_name)) & _FULL_WORDS:
        score += 2
    if (series.unit or '').strip():
        score += 2
    return score


def find_position_signals(signals: Iterable[tuple[str, SignalSeries]]) -> tuple[str, str] | None:
    """The keys of the latitude and longitude among *signals*, (key,
    series) each, or None. A pair from one message wins, then full words
    (Latitude over Lat) and a degree unit."""
    lats, lons = [], []
    for key, series in signals:
        role = position_role(series)
        if role == LAT:
            lats.append((key, series))
        elif role == LON:
            lons.append((key, series))
    pairs = []
    for lat_key, lat in lats:
        for lon_key, lon in lons:
            same = (lat.channel, lat.message_name) == (lon.channel, lon.message_name)
            pairs.append((4 * same + _score(lat) + _score(lon), lat_key, lat, lon_key, lon))
    pairs.sort(key=lambda pair: -pair[0])
    judged: dict[str, bool] = {}
    for _score_, lat_key, lat, lon_key, lon in pairs:
        if lat_key not in judged:
            judged[lat_key] = plausible(lat, LAT)
        if lon_key not in judged:
            judged[lon_key] = plausible(lon, LON)
        if judged[lat_key] and judged[lon_key]:
            return lat_key, lon_key
    return None


def held_at(series: SignalSeries, times: np.ndarray) -> np.ndarray:
    """*series*' value at each of *times*: the last sample at or before it,
    NaN before its first."""
    stamps = np.asarray(series.numpy_timestamps(), dtype=np.float64)
    values = np.asarray(series.numpy_values(), dtype=np.float64)
    size = min(stamps.size, values.size)
    stamps, values = stamps[:size], values[:size]
    index = np.searchsorted(stamps, times, side='right') - 1
    out = np.full(times.shape, np.nan)
    has = index >= 0
    out[has] = values[index[has]]
    return out


def project(lat, lon, origin: tuple[float, float]):
    """Latitude and longitude, in degrees, as metres east and north of
    *origin* (lat0, lon0): the flat layout a track is drawn in. Arrays or
    single values."""
    lat0, lon0 = origin
    e = EARTH_RADIUS * math.cos(math.radians(lat0)) * np.radians(np.subtract(lon, lon0))
    n = EARTH_RADIUS * np.radians(np.subtract(lat, lat0))
    return e, n


def unproject(e, n, origin: tuple[float, float]):
    """Metres east and north of *origin* back to latitude and longitude."""
    lat0, lon0 = origin
    lat = lat0 + np.degrees(np.divide(n, EARTH_RADIUS))
    lon = lon0 + np.degrees(np.divide(e, EARTH_RADIUS * math.cos(math.radians(lat0))))
    return lat, lon


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres; works on arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


@dataclass(frozen=True)
class Track:
    """A cleaned track. *times* and *ok* are every paired sample, whether it
    had a fix. The fixes (the samples with one) are t, lat, lon, laid flat
    as e and n (metres east and north of *origin*); joined[i] says whether
    the line runs to fix i from the one before; dist[i] is the distance
    driven up to fix i."""

    times: np.ndarray
    ok: np.ndarray
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    e: np.ndarray
    n: np.ndarray
    joined: np.ndarray
    dist: np.ndarray
    origin: tuple[float, float]
    gap_distance: float
    gap_seconds: float

    @property
    def size(self) -> int:
        """The number of fixes."""
        return int(self.t.size)

    @property
    def distance(self) -> float:
        return float(self.dist[-1]) if self.size else 0.0

    @property
    def duration(self) -> float:
        return float(self.t[-1] - self.t[0]) if self.size else 0.0

    def gaps(self) -> list[tuple[int, int]]:
        """Each break in the line: (the fix before it, the fix after it)."""
        after = np.flatnonzero(~self.joined[1:]) + 1
        return [(int(i) - 1, int(i)) for i in after]

    def no_fix_seconds(self) -> float:
        """The time the receiver had no fix between the first and last."""
        return float(sum(self.t[b] - self.t[a] for a, b in self.gaps()))

    def fix_at(self, time: float) -> tuple[int | None, bool]:
        """The fix shown for *time*: the last at or before it (None before
        the first), and whether there was a fix at *time* itself."""
        index = int(np.searchsorted(self.t, time, side='right')) - 1
        if index < 0:
            return None, False
        sample = int(np.searchsorted(self.times, time, side='right')) - 1
        has = (sample >= 0 and bool(self.ok[sample])
               and float(time - self.t[index]) <= self.gap_seconds)
        return index, bool(has)

    def distance_at(self, time: float) -> float:
        index, _has = self.fix_at(time)
        return 0.0 if index is None else float(self.dist[index])

    def in_range(self, time_range: tuple[float, float] | None) -> np.ndarray:
        """Which fixes lie in *time_range* (all for None)."""
        if time_range is None:
            return np.ones(self.size, dtype=bool)
        low, high = time_range
        return (self.t >= low) & (self.t <= high)

    def passes_near(self, e: float, n: float, radius: float,
                    time_range: tuple[float, float] | None = None) -> list[int]:
        """The fixes nearest (*e*, *n*) within *radius* metres, one for each
        pass the vehicle made by there, in time order."""
        near = (np.hypot(self.e - e, self.n - n) <= radius) & self.in_range(time_range)
        index = np.flatnonzero(near)
        if not index.size:
            return []
        distance = np.hypot(self.e[index] - e, self.n[index] - n)
        # A pass is a run of neighbouring fixes, unbroken by a gap.
        new = np.ones(index.size, dtype=bool)
        new[1:] = (np.diff(index) != 1) | ~self.joined[index[1:]]
        starts = np.flatnonzero(new)
        ends = np.append(starts[1:], index.size)
        return [int(index[a + int(np.argmin(distance[a:b]))]) for a, b in zip(starts, ends)]

    def spans_in_box(self, e0: float, e1: float, n0: float, n1: float,
                     time_range: tuple[float, float] | None = None) -> list[tuple[float, float]]:
        """When the vehicle was inside the box: (start, end) each, a stretch
        held until the next fix after it when the line runs on to it."""
        e0, e1 = sorted((e0, e1))
        n0, n1 = sorted((n0, n1))
        inside = ((self.e >= e0) & (self.e <= e1) & (self.n >= n0) & (self.n <= n1)
                  & self.in_range(time_range))
        index = np.flatnonzero(inside)
        if not index.size:
            return []
        new = np.ones(index.size, dtype=bool)
        new[1:] = (np.diff(index) != 1) | ~self.joined[index[1:]]
        starts = np.flatnonzero(new)
        ends = np.append(starts[1:], index.size) - 1
        spans = []
        for a, b in zip(index[starts].tolist(), index[ends].tolist()):
            end = b + 1 if b + 1 < self.size and self.joined[b + 1] else b
            spans.append((float(self.t[a]), float(self.t[end])))
        return spans


def _no_fixes(times: np.ndarray, ok: np.ndarray, gap_seconds: float) -> Track:
    none = np.empty(0)
    return Track(times, ok, none, none, none, none, none, np.empty(0, dtype=bool), none,
                 (0.0, 0.0), 0.0, gap_seconds)


EMPTY = _no_fixes(np.empty(0), np.empty(0, dtype=bool), GAP_SECONDS)


def _pair(lat: SignalSeries, lon: SignalSeries) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Latitude and longitude at each sample time of either, each held."""
    lat_t = np.asarray(lat.numpy_timestamps(), dtype=np.float64)
    lon_t = np.asarray(lon.numpy_timestamps(), dtype=np.float64)
    lat_v = np.asarray(lat.numpy_values(), dtype=np.float64)
    lon_v = np.asarray(lon.numpy_values(), dtype=np.float64)
    if lat_t.size == lon_t.size == lat_v.size == lon_v.size and np.array_equal(lat_t, lon_t):
        return lat_t, lat_v, lon_v
    times = np.union1d(lat_t, lon_t)
    return times, held_at(lat, times), held_at(lon, times)


def build_track(lat: SignalSeries, lon: SignalSeries,
                gap_seconds: float = GAP_SECONDS) -> Track:
    """The track of *lat* and *lon*. A fix is dropped when either value is
    missing or out of range (the "not available" codes, such as J1939's
    211.1°, fall there) or both are exactly 0. The line breaks where no
    fix came for longer than *gap_seconds* or the position jumped faster
    than MAX_SPEED."""
    times, lat_v, lon_v = _pair(lat, lon)
    with np.errstate(invalid='ignore'):
        ok = (np.isfinite(lat_v) & np.isfinite(lon_v) & (np.abs(lat_v) <= 90.0)
              & (np.abs(lon_v) <= 180.0) & ~((lat_v == 0.0) & (lon_v == 0.0)))
    t, la, lo = times[ok], lat_v[ok], lon_v[ok]
    if not t.size:
        return _no_fixes(times, ok, gap_seconds)
    lat0 = (float(la.min()) + float(la.max())) / 2
    lon0 = (float(lo.min()) + float(lo.max())) / 2
    e, n = project(la, lo, (lat0, lon0))
    step = haversine(la[:-1], lo[:-1], la[1:], lo[1:])
    dt = np.diff(t)
    with np.errstate(divide='ignore', invalid='ignore'):
        jump = np.where(dt > 0, step / np.where(dt > 0, dt, 1.0) > MAX_SPEED, step > 100.0)
    joined = np.concatenate(([False], (dt <= gap_seconds) & ~jump))
    dist, gap_distance = _distance(la, lo, e, n, joined)
    return Track(times, ok, t, la, lo, e, n, joined, dist, (lat0, lon0), gap_distance,
                 gap_seconds)


def _distance(lat: np.ndarray, lon: np.ndarray, e: np.ndarray, n: np.ndarray,
              joined: np.ndarray) -> tuple[np.ndarray, float]:
    """Distance driven up to each fix, and the part of it across breaks. A
    step counts once the fix is MIN_STEP from the last counted one (judged
    flat, measured on the sphere). At a break, the way to the fix before it
    counts, then the straight line across; so does the way to the last fix."""
    size = lat.size
    counted_from, counted_to = [], []
    ref = 0
    es, ns, joins = e.tolist(), n.tolist(), joined.tolist()

    def count(a: int, b: int) -> None:
        counted_from.append(a)
        counted_to.append(b)

    for i in range(1, size):
        if not joins[i]:
            if ref != i - 1:
                count(ref, i - 1)
            count(i - 1, i)
            ref = i
        elif math.hypot(es[i] - es[ref], ns[i] - ns[ref]) >= MIN_STEP:
            count(ref, i)
            ref = i
    if ref != size - 1 and size > 1:
        count(ref, size - 1)
    step = np.zeros(size)
    gap_distance = 0.0
    if counted_to:
        a, b = np.asarray(counted_from), np.asarray(counted_to)
        lengths = haversine(lat[a], lon[a], lat[b], lon[b])
        step[b] = lengths
        gap_distance = float(lengths[~joined[b]].sum())
    return np.cumsum(step), gap_distance
