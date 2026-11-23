# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Map files for the GPS track (core.map_tiles): the tile arithmetic, reading a
raster MBTiles file without ever writing it, refusing files that cannot be
shown, and the flat layout the tiles are laid in (core.gps_track.project).

The Web Mercator reference below is written out on its own, in a different
form from the module's, so the two do not agree by construction.
"""
from __future__ import annotations

import math
import os
import sqlite3

import numpy as np
import pytest

from core import map_tiles as mt
from core.gps_track import EARTH_RADIUS, build_track, project, unproject

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20
JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 20


def _reference(lat, lon, z):
    """Web Mercator tile position, the textbook form."""
    size = 1 << z
    s = math.sin(math.radians(lat))
    return (lon + 180.0) / 360.0 * size, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * size


def write_mbtiles(path, tiles, metadata=None, tms=True, table='tiles'):
    """*tiles*: {(z, x, y from the north): bytes}."""
    db = sqlite3.connect(str(path))
    if metadata is not None:
        db.execute('CREATE TABLE metadata (name TEXT, value TEXT)')
        db.executemany('INSERT INTO metadata VALUES (?, ?)', metadata.items())
    db.execute(f'CREATE TABLE {table} (zoom_level INTEGER, tile_column INTEGER, '
               'tile_row INTEGER, tile_data BLOB)')
    for (z, x, y), data in tiles.items():
        row = (1 << z) - 1 - y if tms else y
        db.execute(f'INSERT INTO {table} VALUES (?, ?, ?, ?)', (z, x, row, data))
    db.commit()
    db.close()
    return str(path)


# ── Tile arithmetic ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('lat, lon', [(50.0, 8.0), (-33.9, 151.2), (0.0, 0.0), (64.1, -21.9),
                                      (85.0, 179.9), (-85.0, -179.9)])
@pytest.mark.parametrize('z', [0, 7, 15, 18])
def test_tile_positions_match_web_mercator(lat, lon, z):
    x, y = _reference(lat, lon, z)
    assert mt.tile_x(lon, z) == pytest.approx(x, abs=1e-9 * (1 << z))
    assert mt.tile_y(lat, z) == pytest.approx(y, abs=1e-9 * (1 << z))
    assert mt.tile_lon(mt.tile_x(lon, z), z) == pytest.approx(lon, abs=1e-9)
    assert mt.tile_lat(mt.tile_y(lat, z), z) == pytest.approx(lat, abs=1e-9)


def test_a_known_tile_and_the_poles():
    # 50° N 8° E (the sample drive) lies in tile 534/347 at zoom 10.
    assert (math.floor(mt.tile_x(8.0, 10)), math.floor(mt.tile_y(50.0, 10))) == (534, 347)
    assert mt.tile_y(90.0, 5) == pytest.approx(0.0, abs=1e-9)
    assert mt.tile_y(-90.0, 5) == pytest.approx(32.0, abs=1e-9)
    assert mt.tile_lat(0, 0) == pytest.approx(mt.MAX_LAT)
    west, south, east, north = mt.tile_bounds(10, 534, 347)
    assert west < 8.0 < east and south < 50.0 < north
    assert (east - west) == pytest.approx(360 / 1024)


def test_the_zoom_matches_tile_pixels_to_screen_pixels():
    res15 = mt.ground_resolution(50.0, 15, 256)
    # 2πR·cos 50° / (256·2^15) ≈ 3.07 m.
    assert res15 == pytest.approx(2 * math.pi * EARTH_RADIUS * math.cos(math.radians(50))
                                  / (256 * 2 ** 15))
    assert mt.choose_zoom(res15, 50.0, 256, 0, 20) == 15
    assert mt.choose_zoom(res15 * 1.3, 50.0, 256, 0, 20) == 15
    assert mt.choose_zoom(res15 * 1.6, 50.0, 256, 0, 20) == 14
    assert mt.choose_zoom(res15 / 1.6, 50.0, 256, 0, 20) == 16
    # A 512-pixel tile holds as much as four 256-pixel tiles one zoom nearer.
    assert mt.choose_zoom(res15, 50.0, 512, 0, 20) == 14
    assert mt.choose_zoom(res15, 50.0, 256, 16, 18) == 16          # not further out than the file
    assert mt.choose_zoom(res15, 50.0, 256, 10, 12) == 12          # not nearer than the file
    assert mt.choose_zoom(0.0, 50.0, 256, 10, 12) == 12
    assert mt.choose_zoom(float('nan'), 50.0, 256, 10, 12) == 12


def test_sniff_and_plain_text():
    assert mt.sniff(PNG) == 'png' and mt.sniff(JPG) == 'jpg'
    assert mt.sniff(b'RIFF\x00\x00\x00\x00WEBPVP8 ') == 'webp'
    assert mt.sniff(b'\x1f\x8b\x08\x00') == 'pbf'
    assert mt.sniff(b'\x1a\x02xyz') is None
    assert mt.plain_text('<a href="https://x">&copy; Map   data</a>\n contributors') == \
        '© Map data contributors'


# ── Reading a file ───────────────────────────────────────────────────────────

def test_a_map_file_gives_its_metadata_and_tiles(tmp_path):
    tiles = {(15, 17090, 11120): PNG + b'a', (15, 17091, 11120): PNG + b'b',
             (14, 8545, 5560): PNG + b'c'}
    path = write_mbtiles(tmp_path / 'area.mbtiles', tiles, {
        'name': 'Test <b>area</b>', 'format': 'png', 'bounds': '7.7,49.9,8.1,50.1',
        'attribution': '<a href="https://www.openstreetmap.org/copyright">&copy; '
                       'OpenStreetMap contributors</a>'})
    m = mt.MapFile(path)
    try:
        assert (m.title, m.name, m.format) == ('Test area', 'area.mbtiles', 'png')
        assert m.attribution == '© OpenStreetMap contributors'
        assert m.credit() == '© OpenStreetMap contributors'
        assert (m.minzoom, m.maxzoom) == (14, 15)
        assert m.bounds == (7.7, 49.9, 8.1, 50.1)
        # Rows are kept counted from the south and read counted from the north.
        assert m.tile(15, 17090, 11120) == PNG + b'a'
        assert m.tile(15, 17091, 11120) == PNG + b'b'
        assert m.tile(14, 8545, 5560) == PNG + b'c'
        assert m.tile(15, 17090, (1 << 15) - 1 - 11120) is None
        assert m.tile(16, 0, 0) is None
    finally:
        m.close()
    assert m.tile(15, 17090, 11120) is None        # closed


def test_a_file_counting_rows_from_the_north_is_read_as_it_says(tmp_path):
    path = write_mbtiles(tmp_path / 'xyz.mbtiles', {(3, 4, 1): JPG},
                         {'scheme': 'xyz', 'format': 'jpg'}, tms=False)
    m = mt.MapFile(path)
    assert m.tile(3, 4, 1) == JPG and m.tile(3, 4, 6) is None
    m.close()


def test_without_metadata_the_zooms_bounds_and_format_come_from_the_tiles(tmp_path):
    tiles = {(12, 2136, 1388): JPG, (13, 4272, 2777): JPG, (13, 4274, 2779): JPG}
    m = mt.MapFile(write_mbtiles(tmp_path / 'bare.mbtiles', tiles, metadata=None))
    try:
        assert (m.minzoom, m.maxzoom, m.format) == (12, 13, 'jpg')
        west, _s, _e, north = mt.tile_bounds(13, 4272, 2777)
        _w, south, east, _n = mt.tile_bounds(13, 4274, 2779)
        assert m.bounds == pytest.approx((west, south, east, north))
        assert m.title == 'bare.mbtiles' and m.attribution == ''
        assert m.credit() == 'Map: bare.mbtiles'
    finally:
        m.close()
    # Bounds that make no area are not believed either.
    m = mt.MapFile(write_mbtiles(tmp_path / 'bad.mbtiles', tiles, {'bounds': '9,50,8,49'}))
    assert m.bounds == pytest.approx((west, south, east, north))
    m.close()


@pytest.mark.parametrize('tiles, metadata, reason', [
    ({(10, 1, 1): b'\x1f\x8b\x08\x00vector'}, {}, 'vector tiles'),
    ({(10, 1, 1): b'\x1a\x02raw-vector'}, {'format': 'pbf'}, 'vector tiles'),
    ({(10, 1, 1): b'GIF89a'}, {}, 'not PNG, JPEG or WebP'),
    ({}, {'format': 'png'}, 'no map tiles'),
])
def test_files_that_cannot_be_shown_are_refused_with_the_reason(tmp_path, tiles, metadata,
                                                                 reason):
    path = write_mbtiles(tmp_path / 'm.mbtiles', tiles, metadata)
    with pytest.raises(mt.MapFileError, match=reason):
        mt.MapFile(path)


def test_files_that_are_not_map_files_are_refused(tmp_path):
    with pytest.raises(mt.MapFileError, match='not found'):
        mt.MapFile(tmp_path / 'missing.mbtiles')
    assert not (tmp_path / 'missing.mbtiles').exists()
    text = tmp_path / 'notes.mbtiles'
    text.write_text('not a database at all, just words ' * 20)
    with pytest.raises(mt.MapFileError, match='not an MBTiles'):
        mt.MapFile(text)
    other = write_mbtiles(tmp_path / 'other.db', {(1, 0, 0): PNG}, table='images')
    with pytest.raises(mt.MapFileError, match='not an MBTiles'):
        mt.MapFile(other)


def test_a_map_file_is_never_written(tmp_path):
    path = write_mbtiles(tmp_path / 'ro.mbtiles', {(14, 8545, 5560): PNG}, {'format': 'png'})
    before = (open(path, 'rb').read(), os.path.getmtime(path))
    m = mt.MapFile(path)
    m.tile(14, 8545, 5560)
    with pytest.raises(sqlite3.OperationalError):
        m._db.execute('INSERT INTO metadata VALUES (?, ?)', ('x', 'y'))
    m.close()
    assert (open(path, 'rb').read(), os.path.getmtime(path)) == before
    assert sorted(os.listdir(tmp_path)) == ['ro.mbtiles']


def test_tiles_are_listed_only_over_the_map_and_up_to_a_limit(tmp_path):
    path = write_mbtiles(tmp_path / 'm.mbtiles', {(16, 0, 0): PNG},
                         {'bounds': '8.00,50.00,8.02,50.01'})
    m = mt.MapFile(path)
    try:
        z = 16
        inside = m.tiles_for(8.005, 50.002, 8.006, 50.003, z)
        assert inside == [(math.floor(mt.tile_x(8.005, z)), math.floor(mt.tile_y(50.003, z)))]
        # A wider view lists only the map's own part.
        wide = m.tiles_for(7.9, 49.9, 8.1, 50.1, z)
        xs, ys = {x for x, _ in wide}, {y for _, y in wide}
        assert min(xs) == math.floor(mt.tile_x(8.00, z))
        assert max(xs) == math.floor(mt.tile_x(8.02, z))
        assert min(ys) == math.floor(mt.tile_y(50.01, z))
        assert max(ys) == math.floor(mt.tile_y(50.00, z))
        assert len(wide) == len(xs) * len(ys)
        assert m.tiles_for(7.9, 49.9, 8.1, 50.1, z, limit=len(wide)) == wide
        assert m.tiles_for(7.9, 49.9, 8.1, 50.1, z, limit=len(wide) - 1) is None
        assert m.tiles_for(9.0, 50.0, 9.1, 50.1, z) == []
        assert m.covers(8.01, 50.005, 8.5, 50.5) and m.covers(7.5, 49.5, 8.001, 50.001)
        for away in ((8.03, 50.0, 8.5, 50.5), (7.5, 50.0, 7.99, 50.5),
                     (8.0, 50.02, 8.01, 50.5), (8.0, 49.5, 8.01, 49.99)):
            assert not m.covers(*away)
    finally:
        m.close()


# ── The layout the tiles are laid in ────────────────────────────────────────

def test_start_up_does_not_load_sqlite():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = 'import sys, gui.main_window; print("sqlite3" in sys.modules)'
    done = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True,
                          env=dict(os.environ, QT_QPA_PLATFORM='offscreen',
                                   PYTHONPATH=str(root)), timeout=120)
    assert done.stdout.strip().splitlines()[-1] == 'False', done.stderr[-2000:]


def test_project_and_unproject_are_the_tracks_own_layout():
    from tests.test_gps_view import _drive
    signals = dict(_drive())
    track = build_track(signals['CH1::VP::VP_Latitude'], signals['CH1::VP::VP_Longitude'])
    e, n = project(track.lat, track.lon, track.origin)
    assert np.array_equal(e, track.e) and np.array_equal(n, track.n)
    lat, lon = unproject(track.e, track.n, track.origin)
    assert np.allclose(lat, track.lat, atol=1e-12) and np.allclose(lon, track.lon, atol=1e-12)
    # One degree of latitude is R·π/180 metres north; a scalar stays a scalar.
    lat0, lon0 = track.origin
    e1, n1 = project(lat0 + 1.0, lon0, track.origin)
    assert float(e1) == 0.0 and float(n1) == pytest.approx(EARTH_RADIUS * math.pi / 180)
