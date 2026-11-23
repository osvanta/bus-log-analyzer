# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Map files for the GPS track: streets under the route, from a raster
MBTiles file kept on the PC. An MBTiles file is one SQLite file of map
tiles; QGIS and other tile tools write it from whatever map data the user
may use. Nothing is fetched over the network.

The tiles are the usual web map tiles: Web Mercator, where zoom z splits the
world into 2**z by 2**z square tiles, x counted east from 180° W and y south
from 85.05° N. MBTiles counts the row from the south instead (TMS);
MapFile.tile takes y from the north and turns it round.

This module finds and reads tiles; the view decodes and draws them.
"""
from __future__ import annotations

import html
import math
import os
import re
from pathlib import Path

from core.gps_track import EARTH_RADIUS

#: Web Mercator ends here, north and south: the latitude that makes the
#: world square.
MAX_LAT = 85.0511287798066
#: Tile images the view can draw.
RASTER_FORMATS = frozenset({'png', 'jpg', 'webp'})
_FORMAT_NAMES = {'png': 'png', 'jpg': 'jpg', 'jpeg': 'jpg', 'webp': 'webp',
                 'pbf': 'pbf', 'mvt': 'pbf'}
_TAGS = re.compile(r'<[^>]*>')
_SPACES = re.compile(r'\s+')


class MapFileError(Exception):
    """A map file that cannot be used; the message says why, in words."""


# ── Tile arithmetic ──────────────────────────────────────────────────────────

def tile_x(lon: float, z: int) -> float:
    """Longitude as a tile column at zoom *z*, with the fraction."""
    return (lon + 180.0) / 360.0 * (1 << z)


def tile_y(lat: float, z: int) -> float:
    """Latitude as a tile row at zoom *z*, counted from the north, with the
    fraction. Beyond ±MAX_LAT the first or last row."""
    phi = math.radians(max(-MAX_LAT, min(MAX_LAT, lat)))
    return (1.0 - math.asinh(math.tan(phi)) / math.pi) / 2.0 * (1 << z)


def tile_lon(x: float, z: int) -> float:
    """The longitude of tile column edge *x* at zoom *z*."""
    return x / (1 << z) * 360.0 - 180.0


def tile_lat(y: float, z: int) -> float:
    """The latitude of tile row edge *y* (from the north) at zoom *z*."""
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / (1 << z)))))


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a tile, in degrees."""
    return tile_lon(x, z), tile_lat(y + 1, z), tile_lon(x + 1, z), tile_lat(y, z)


def ground_resolution(lat: float, z: int, tile_pixels: int) -> float:
    """Metres on the ground per tile pixel at latitude *lat* and zoom *z*."""
    return (2.0 * math.pi * EARTH_RADIUS * math.cos(math.radians(lat))
            / (tile_pixels * (1 << z)))


def choose_zoom(metres_per_pixel: float, lat: float, tile_pixels: int,
                low: int, high: int) -> int:
    """The zoom whose tile pixels come nearest the screen's pixels, kept
    within *low* to *high*: nearer than the file goes, its closest tiles
    are drawn larger; further out, its farthest tiles are drawn smaller."""
    if not metres_per_pixel > 0 or not math.isfinite(metres_per_pixel):
        return high
    z = round(math.log2(ground_resolution(lat, 0, tile_pixels) / metres_per_pixel))
    return max(low, min(high, z))


def sniff(data: bytes) -> str | None:
    """What a tile holds, by its first bytes: 'png', 'jpg', 'webp', 'pbf'
    (a vector tile, gzip-packed as MBTiles keeps them) or None."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    if data.startswith(b'\x1f\x8b'):
        return 'pbf'
    return None


def plain_text(text: str) -> str:
    """A metadata value as plain words: tags dropped, entities read,
    spaces closed up. Attributions are often a line of HTML."""
    return _SPACES.sub(' ', html.unescape(_TAGS.sub('', text or ''))).strip()


def _number_list(text: str, count: int) -> list[float] | None:
    try:
        values = [float(part) for part in str(text).split(',')]
    except ValueError:
        return None
    if len(values) != count or not all(math.isfinite(v) for v in values):
        return None
    return values


def _connect(path: str):
    """The file opened for reading only: never created, never written."""
    import sqlite3
    try:
        return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1',
                               uri=True)
    except (sqlite3.Error, ValueError):
        # A path a file: address cannot carry (a network share); the file
        # exists, so a plain open does not create one.
        connection = sqlite3.connect(path)
        connection.execute('PRAGMA query_only = 1')
        return connection


# ── A map file ───────────────────────────────────────────────────────────────

class MapFile:
    """A raster MBTiles file, open for reading only. Raises MapFileError
    when the file cannot be used."""

    def __init__(self, path: str | os.PathLike) -> None:
        # Imported here, not at start-up: only a chosen map file needs it.
        import sqlite3
        self.path = str(path)
        self.name = Path(self.path).name
        if not os.path.isfile(self.path):
            raise MapFileError('the file was not found')
        try:
            self._db = _connect(self.path)
        except sqlite3.Error as error:
            raise MapFileError('it could not be opened') from error
        try:
            self._read_header()
        except sqlite3.DatabaseError as error:
            self.close()
            raise MapFileError('it is not an MBTiles map file') from error
        except MapFileError:
            self.close()
            raise

    def _read_header(self) -> None:
        import sqlite3
        try:
            rows = self._db.execute('SELECT name, value FROM metadata').fetchall()
        except sqlite3.OperationalError:
            rows = []         # metadata is optional; the tiles are not
        meta = {str(name).strip().lower(): str(value) for name, value in rows
                if name is not None and value is not None}
        self.metadata = meta
        try:
            sample = self._db.execute(
                'SELECT tile_data FROM tiles WHERE tile_data IS NOT NULL LIMIT 1').fetchone()
        except sqlite3.OperationalError as error:
            raise MapFileError('it is not an MBTiles map file') from error
        if sample is None:
            raise MapFileError('it holds no map tiles')
        self.sample = bytes(sample[0])
        kind = sniff(self.sample) or _FORMAT_NAMES.get(meta.get('format', '').strip().lower())
        if kind == 'pbf':
            raise MapFileError('it holds vector tiles; only map files of images '
                               '(PNG, JPEG or WebP tiles) can be shown')
        if kind not in RASTER_FORMATS:
            raise MapFileError('its tiles are not PNG, JPEG or WebP images')
        self.format = kind
        # Rows counted from the south, as MBTiles has them, unless the file
        # says otherwise.
        self._tms = meta.get('scheme', 'tms').strip().lower() != 'xyz'
        low, high = self._db.execute(
            'SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles').fetchone()
        self.minzoom, self.maxzoom = int(low), int(high)
        self.bounds = self._bounds(meta.get('bounds', ''))
        self.title = plain_text(meta.get('name', '')) or self.name
        self.attribution = plain_text(meta.get('attribution', ''))

    def _bounds(self, text: str) -> tuple[float, float, float, float]:
        """(west, south, east, north): as the file states them, else where
        its most detailed tiles lie."""
        stated = _number_list(text, 4)
        if stated is not None:
            west, south, east, north = stated
            west, east = max(west, -180.0), min(east, 180.0)
            south, north = max(south, -MAX_LAT), min(north, MAX_LAT)
            if west < east and south < north:
                return west, south, east, north
        z = self.maxzoom
        x0, x1, r0, r1 = self._db.execute(
            'SELECT MIN(tile_column), MAX(tile_column), MIN(tile_row), MAX(tile_row) '
            'FROM tiles WHERE zoom_level = ?', (z,)).fetchone()
        last = (1 << z) - 1
        y0, y1 = (last - r1, last - r0) if self._tms else (r0, r1)
        return tile_lon(x0, z), tile_lat(y1 + 1, z), tile_lon(x1 + 1, z), tile_lat(y0, z)

    def credit(self) -> str:
        """What the map's corner says: its attribution, else its name."""
        return self.attribution or f'Map: {self.title}'

    def covers(self, west: float, south: float, east: float, north: float) -> bool:
        """Whether any of the area lies inside the map."""
        w, s, e, n = self.bounds
        return west <= e and east >= w and south <= n and north >= s

    def tiles_for(self, west: float, south: float, east: float, north: float, z: int,
                  limit: int | None = None) -> list[tuple[int, int]] | None:
        """The tiles at zoom *z*, (x, y) from the north, over the part of the
        area inside the map; None when more than *limit* would be needed."""
        w, s, e, n = self.bounds
        west, east = max(west, w), min(east, e)
        south, north = max(south, s), min(north, n)
        if west >= east or south >= north:
            return []
        last = (1 << z) - 1
        x0 = max(0, min(last, math.floor(tile_x(west, z))))
        x1 = max(0, min(last, math.floor(tile_x(east, z))))
        y0 = max(0, min(last, math.floor(tile_y(north, z))))
        y1 = max(0, min(last, math.floor(tile_y(south, z))))
        if limit is not None and (x1 - x0 + 1) * (y1 - y0 + 1) > limit:
            return None
        return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        """The image data of tile (*z*, *x*, *y*), y counted from the north;
        None when the file has no such tile."""
        if self._db is None:
            return None
        import sqlite3
        row = (1 << z) - 1 - y if self._tms else y
        try:
            found = self._db.execute(
                'SELECT tile_data FROM tiles WHERE zoom_level = ? AND tile_column = ? '
                'AND tile_row = ?', (z, x, row)).fetchone()
        except sqlite3.Error:
            return None
        return bytes(found[0]) if found and found[0] else None

    def close(self) -> None:
        db, self._db = getattr(self, '_db', None), None
        if db is not None:
            db.close()
