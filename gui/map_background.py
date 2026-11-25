# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The map under the GPS track: tiles of a map file (core.map_tiles) laid in
the track's flat layout, only those the view shows, at the zoom that suits
it. Each tile is placed by its corners' latitude and longitude, so the
streets lie under the route at any zoom; within one tile the map's own
projection and the track's differ by far less than a pixel.

A tile the file lacks is cut from the nearest coarser tile it has, so a
file detailed only around the test area still shows the roads to it.
Decoded tiles are kept for reuse, up to a fixed number.
"""
from __future__ import annotations

from collections import OrderedDict

import pyqtgraph as pg
from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem

from core import map_tiles
from core.gps_track import project, unproject

#: Decoded tiles kept for reuse: 96 tiles of 256 pixels are 25 MB.
CACHE_TILES = 96
#: More tiles than this at the zoom the view needs, and none are drawn: the
#: view is zoomed out far beyond what the file was made for.
MAX_TILES = 100
#: A missing tile is cut from a coarser one up to this many zooms out.
FALLBACK_ZOOMS = 6
#: Tiles follow a pan or zoom at most this often, in milliseconds.
_UPDATE_MS = 40
_Z_VALUE = -30


class MapBackground(QObject):
    """The tiles of one map file in one view box. set_map() gives the file
    and the track's origin; the tiles then follow the view by themselves."""

    #: The tiles were drawn again: the zoom, or too_many, may have changed.
    drawn = Signal()

    def __init__(self, view_box: pg.ViewBox) -> None:
        super().__init__()
        self.view_box = view_box
        self.map_file: map_tiles.MapFile | None = None
        self.origin: tuple[float, float] | None = None
        #: The zoom drawn, and whether the view needed too many tiles.
        self.zoom: int | None = None
        self.too_many = False
        self.items: dict[tuple[int, int, int], QGraphicsPixmapItem] = {}
        self._cache: OrderedDict[tuple[int, int, int], QPixmap | None] = OrderedDict()
        self._tile_pixels = 256
        self._opacity = 1.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_UPDATE_MS)
        self._timer.timeout.connect(self.update)
        view_box.sigRangeChanged.connect(self._soon)
        view_box.sigResized.connect(self._soon)

    def set_map(self, map_file: map_tiles.MapFile | None,
                origin: tuple[float, float] | None) -> None:
        """Draw *map_file* under a track laid out around *origin*; None for
        either draws no map."""
        if map_file is not self.map_file:
            self._clear()
            self._cache.clear()
            self.map_file = map_file
            if map_file is not None:
                sample = QPixmap()
                sample.loadFromData(map_file.sample)
                self._tile_pixels = sample.width() if sample.width() > 0 else 256
        if origin != self.origin:
            self._clear()           # every tile moves
            self.origin = origin
        self.update()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = opacity
        for item in self.items.values():
            item.setOpacity(opacity)

    def shown(self) -> bool:
        """Whether a map is drawn under the track."""
        return self.map_file is not None and self.origin is not None

    def _soon(self, *_args) -> None:
        # Not restarted while a pan goes on: the tiles keep up with the drag.
        if self.shown() and not self._timer.isActive():
            self._timer.start()

    def update(self) -> None:
        """Draw the tiles the view shows now and drop the others."""
        self._timer.stop()
        wanted = self._wanted()
        for key in [key for key in self.items if key not in wanted]:
            self.view_box.removeItem(self.items.pop(key))
        for key in wanted:
            if key not in self.items:
                pixmap = self._pixmap(*key)
                if pixmap is not None:
                    self.items[key] = self._place(key, pixmap)
        self.drawn.emit()

    def _wanted(self) -> list[tuple[int, int, int]]:
        self.zoom, self.too_many = None, False
        map_file, origin = self.map_file, self.origin
        if map_file is None or origin is None:
            return []
        (e0, e1), (n0, n1) = self.view_box.viewRange()
        south, west = unproject(e0, n0, origin)
        north, east = unproject(e1, n1, origin)
        # Metres per pixel from the range itself: pyqtgraph's viewPixelSize()
        # can still hold the range before a setRange().
        width = self.view_box.width()
        metres_per_pixel = (e1 - e0) / width if width > 0 else 0.0
        z = map_tiles.choose_zoom(metres_per_pixel, origin[0], self._tile_pixels,
                                  map_file.minzoom, map_file.maxzoom)
        tiles = map_file.tiles_for(float(west), float(south), float(east), float(north), z,
                                   limit=MAX_TILES)
        if tiles is None:
            self.too_many = True
            return []
        self.zoom = z
        return [(z, x, y) for x, y in tiles]

    def _decoded(self, z: int, x: int, y: int) -> QPixmap | None:
        """The file's own tile, decoded; None when it has none."""
        key = (z, x, y)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        data = self.map_file.tile(z, x, y)
        pixmap = None
        if data:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data) or pixmap.isNull():
                pixmap = None
        self._cache[key] = pixmap
        while len(self._cache) > CACHE_TILES:
            self._cache.popitem(last=False)
        return pixmap

    def _pixmap(self, z: int, x: int, y: int) -> QPixmap | None:
        """The image for tile (*z*, *x*, *y*): the file's own, else the part
        of the nearest coarser tile that covers it."""
        for k in range(FALLBACK_ZOOMS + 1):
            if z - k < self.map_file.minzoom:
                break
            source = self._decoded(z - k, x >> k, y >> k)
            if source is None:
                continue
            if k == 0:
                return source
            part = source.width() / (1 << k)
            left = round((x - ((x >> k) << k)) * part)
            top = round((y - ((y >> k) << k)) * part)
            size = max(1, round(part))
            return source.copy(QRect(left, top, size, size))
        return None

    def _place(self, key: tuple[int, int, int], pixmap: QPixmap) -> QGraphicsPixmapItem:
        """The tile as an item, its corners on their latitude and longitude."""
        west, south, east, north = map_tiles.tile_bounds(*key)
        left, top = project(north, west, self.origin)
        right, bottom = project(south, east, self.origin)
        item = QGraphicsPixmapItem(pixmap)
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setPos(float(left), float(top))
        # Image rows run south; the view's metres run north.
        item.setTransform(QTransform.fromScale(float(right - left) / pixmap.width(),
                                               -float(top - bottom) / pixmap.height()))
        item.setZValue(_Z_VALUE)
        item.setOpacity(self._opacity)
        self.view_box.addItem(item, ignoreBounds=True)
        return item

    def _clear(self) -> None:
        for item in self.items.values():
            self.view_box.removeItem(item)
        self.items.clear()
