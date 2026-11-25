# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The map under the GPS track: a map file chosen under the gear is drawn under
the route, lined up with it at every zoom, at the zoom that suits the view,
with its credit in the corner; files that cannot be shown are refused with
the reason; the choice is saved with the tab.

The test maps are drawn here with their own Web Mercator arithmetic, so a
mistake in the app's cannot hide behind the same mistake in the test.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import math
import os
import sqlite3

import numpy as np
import pytest

from tests.test_gps_view import _drive, view, window  # noqa: F401  (fixtures)

LIGHT, DARK_TILE, COARSE = '#eeeeee', '#cfe3f5', '#f5d6a8'
#: The drive lies between these, with room around it.
WEST, SOUTH, EAST, NORTH = 7.990, 49.994, 8.025, 50.006
#: Fixes marked on the most detailed tiles: by time, seconds.
MARKED = (23.0, 112.0, 171.0, 233.0)


def _world_px(lat, lon, z):
    """Web Mercator pixel at zoom *z*, from the north-west corner of the world."""
    size = 256 * (1 << z)
    s = math.sin(math.radians(lat))
    return (lon + 180.0) / 360.0 * size, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * size


def _cell_color(i, j):
    """The colour of cell (*i* east, *j* south) of a coarse tile's 4 x 4 grid."""
    return '#%02x%02x80' % (40 + 50 * i, 40 + 50 * j)


def _png(color, marks=(), size=256, grid=False):
    """A tile of one colour, or of a 4 x 4 grid of cell colours, with a
    black 7-pixel square at each of *marks*, in the tile's pixels."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    painter = QPainter(image)
    if grid:
        cell = size / 4
        for i in range(4):
            for j in range(4):
                painter.fillRect(QRectF(i * cell, j * cell, cell, cell), QColor(_cell_color(i, j)))
    for x, y in marks:
        painter.fillRect(QRectF(x - 3.5, y - 3.5, 7, 7), QColor('#000000'))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(data)


def _fix(time):
    signals = dict(_drive())
    lat, lon = signals['CH1::VP::VP_Latitude'], signals['CH1::VP::VP_Longitude']
    index = int(np.flatnonzero(lat.timestamps == time)[0])
    return float(lat.values[index]), float(lon.values[index])


def _write_map(path, zooms, bounds=(WEST, SOUTH, EAST, NORTH), marks=True, only=None,
               attribution='&copy; <a href="#">Test map makers</a>', stated=None,
               colors=(LIGHT, DARK_TILE), tile_size=256):
    """A raster MBTiles file over *bounds*: checked tiles of *colors* at
    the most detailed of *zooms*, each carrying a black square on each
    MARKED fix, and tiles of a 4 x 4 grid of colours at the others. *only*
    {zoom: [(x, y)]} keeps just those tiles at that zoom; *stated* bounds
    go into the metadata instead of *bounds*."""
    west, south, east, north = bounds
    stated = stated or bounds
    fixes = [_fix(t) for t in MARKED] if marks else []
    db = sqlite3.connect(str(path))
    db.execute('CREATE TABLE metadata (name TEXT, value TEXT)')
    db.executemany('INSERT INTO metadata VALUES (?, ?)', [
        ('name', 'Test map'), ('format', 'png'), ('attribution', attribution),
        ('bounds', ','.join(str(v) for v in stated))])
    db.execute('CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, '
               'tile_row INTEGER, tile_data BLOB)')
    for z in zooms:
        x0, y0 = (int(v // 256) for v in _world_px(north, west, z))
        x1, y1 = (int(v // 256) for v in _world_px(south, east, z))
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if only is not None and z in only and (x, y) not in only[z]:
                    continue
                spots = []
                if z == max(zooms):
                    for lat, lon in fixes:
                        px, py = _world_px(lat, lon, z)
                        if int(px // 256) == x and int(py // 256) == y:
                            spots.append((px - x * 256, py - y * 256))
                if z < max(zooms):
                    data = _png(COARSE, size=tile_size, grid=True)
                else:
                    data = _png(colors[(x + y) % 2], spots, size=tile_size)
                db.execute('INSERT INTO tiles VALUES (?, ?, ?, ?)', (z, x, (1 << z) - 1 - y, data))
    db.commit()
    db.close()
    return str(path)


@pytest.fixture()
def map_path(qapp, tmp_path):
    return _write_map(tmp_path / 'test_area.mbtiles', zooms=(14, 15, 16))


def _settle(qapp, view, e=None, n=None, half=None):
    """Show *half* metres around (e, n), or leave the range, and let the
    tiles follow."""
    if e is not None:
        view.view_box.setRange(xRange=(e - half, e + half), yRange=(n - half, n + half),
                               padding=0)
    qapp.processEvents()
    view.map_layer.update()
    qapp.processEvents()


def _at(view, time):
    index = int(np.flatnonzero(view.track.t == time)[0])
    return float(view.track.e[index]), float(view.track.n[index])


def _pixel(view, e, n):
    """The colour drawn at (*e*, *n*) metres."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor
    image = view.plot.grab().toImage()
    ratio = image.devicePixelRatio()
    at = view.plot.mapFromScene(view.view_box.mapViewToScene(QPointF(e, n)))
    return QColor(image.pixel(int(at.x() * ratio), int(at.y() * ratio)))


def _hide_route(view):
    for item in (view.casing_curve, view.dim_curve, view.line_curve, view.gap_curve,
                 *view.color_curves, view.ends):
        item.setVisible(False)


# ── Lined up with the route ─────────────────────────────────────────────────


def test_the_route_lies_on_the_map_where_the_fixes_were_marked(view, map_path, qapp):
    view.restore({'map': map_path})
    _hide_route(view)
    for time in MARKED:
        e, n = _at(view, time)
        _settle(qapp, view, e, n, 60.0)
        assert view.map_layer.zoom == 16
        assert _pixel(view, e, n).lightness() < 60, f'no mark under the fix at {time} s'
        # 15 m beside it is the plain tile, not the mark.
        for de, dn in ((15, 0), (-15, 0), (0, 15), (0, -15)):
            assert _pixel(view, e + de, n + dn).lightness() > 150


def _check_corners(view):
    """Every tile drawn sits with its corners on their latitude and longitude
    in the track's flat layout, worked out here on its own."""
    from PySide6.QtCore import QPointF
    items = view.map_layer.items
    assert items
    lat0, lon0 = view.track.origin
    metres = 6_371_008.8 * math.pi / 180
    for (z, x, y), item in items.items():
        size = 1 << z
        west, east = x / size * 360 - 180, (x + 1) / size * 360 - 180
        north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / size))))
        south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / size))))
        top_left = item.mapToParent(QPointF(0, 0))
        bottom_right = item.mapToParent(QPointF(item.pixmap().width(), item.pixmap().height()))
        expected = ((west - lon0) * metres * math.cos(math.radians(lat0)), (north - lat0) * metres,
                    (east - lon0) * metres * math.cos(math.radians(lat0)), (south - lat0) * metres)
        got = (top_left.x(), top_left.y(), bottom_right.x(), bottom_right.y())
        assert got == pytest.approx(expected, abs=0.01)
        assert item.zValue() < view.grid.zValue() < view.dim_curve.zValue()


def test_each_tile_sits_on_its_corners_latitude_and_longitude(view, map_path, qapp):
    view.restore({'map': map_path})
    e, n = _at(view, 23.0)
    _settle(qapp, view, e, n, 400.0)
    _check_corners(view)


def test_a_new_drive_moves_the_tiles_with_it(view, map_path, qapp):
    from tests.test_gps_view import _series
    view.restore({'map': map_path})
    _settle(qapp, view)
    before = view.track.origin
    moved = []
    for key, series in _drive():
        if key.endswith('VP_Latitude'):
            values = np.where(series.values <= 90, series.values + 0.002, series.values)
            series = _series(series.signal_name, series.timestamps, values)
        moved.append((key, series))
    view.set_decoded(moved)
    _settle(qapp, view)
    assert view.track.origin != before
    _check_corners(view)


def test_fitting_the_route_ignores_the_map(view, map_path, qapp):
    qapp.processEvents()
    plain = view.view_box.viewRange()
    view.restore({'map': map_path})
    _settle(qapp, view)
    view.fit()
    _settle(qapp, view)
    assert view.map_layer.items
    # The route's wider edge over a map adds a few metres; tiles would add
    # hundreds.
    assert np.allclose(view.view_box.viewRange(), plain, atol=15.0)


# ── The zoom follows the view ───────────────────────────────────────────────


def test_the_zoom_follows_the_view_within_what_the_file_holds(view, map_path, qapp):
    view.restore({'map': map_path})
    e, n = _at(view, 23.0)
    zooms = []
    for half in (2000.0, 700.0, 350.0, 150.0, 20.0):
        _settle(qapp, view, e, n, half)
        zooms.append(view.map_layer.zoom)
        assert all(key[0] == zooms[-1] for key in view.map_layer.items)
    # Further out than zoom 14 the file's farthest tiles are drawn smaller,
    # nearer than 16 its closest ones larger.
    assert zooms == sorted(zooms) and zooms[0] == 14 and zooms[-1] == 16
    assert 15 in zooms


def test_the_scale_bar_and_the_zoom_follow_a_zoom_at_once(view, map_path, qapp):
    """pyqtgraph's own pixel size can still hold the fitted range just
    after the first zoom; the scale bar and the map's zoom do not."""
    from core.map_tiles import choose_zoom
    from gui.gps_view import _nice
    view.restore({'map': map_path})
    # No event loop pass before the first zoom: that is when it lags.
    lagging = []
    view.view_box.sigRangeChanged.connect(
        lambda *_: lagging.append(view.view_box.viewPixelSize()[0]))
    e, n = _at(view, 23.0)
    for half in (2000.0, 700.0, 50.0):
        view.view_box.setRange(xRange=(e - half, e + half), yRange=(n - half, n + half),
                               padding=0)
        (x0, x1), _y = view.view_box.viewRange()
        mpp = (x1 - x0) / view.view_box.width()
        assert view.key.length == _nice(80 * mpp)
        view.map_layer.update()
        assert view.map_layer.zoom == choose_zoom(mpp, view.track.origin[0], 256, 14, 16)


def test_zoomed_out_too_far_the_map_asks_to_zoom_in(view, qapp, tmp_path):
    # A file said to reach far beyond the drive, with tiles at zoom 16 only.
    path = _write_map(tmp_path / 'detail.mbtiles', zooms=(16,), marks=False,
                      stated=(7.0, 49.0, 9.0, 51.0))
    view.restore({'map': path})
    e, n = _at(view, 23.0)
    _settle(qapp, view, e, n, 60_000.0)
    layer = view.map_layer
    assert layer.too_many and layer.items == {}
    assert view.credit.isVisible() and view.credit.text() == 'Zoom in to see the map'
    _settle(qapp, view, e, n, 300.0)
    assert not layer.too_many and layer.items
    assert view.credit.text() == '© Test map makers'


def test_a_missing_tile_is_cut_from_a_coarser_one(view, qapp, tmp_path):
    lat, lon = _fix(23.0)
    px, py = _world_px(lat, lon, 16)
    kept = (int(px // 256), int(py // 256))
    path = _write_map(tmp_path / 'patchy.mbtiles', zooms=(14, 16), marks=False,
                      only={16: [kept]})
    view.restore({'map': path})
    e, n = _at(view, 23.0)
    _settle(qapp, view, e, n, 400.0)
    layer = view.map_layer
    assert layer.zoom == 16 and (16, *kept) in layer.items
    assert layer.items[(16, *kept)].pixmap().width() == 256
    assert any(key[1:] != kept for key in layer.items)
    for (_z, x, y), item in layer.items.items():
        if (x, y) == kept:
            continue
        # The sixteenth of the zoom-14 tile that lies under this one.
        assert item.pixmap().width() == 64
        assert item.pixmap().toImage().pixelColor(32, 32).name() == _cell_color(x % 4, y % 4)
    _check_corners(view)


def test_decoded_tiles_are_kept_only_up_to_a_limit(view, map_path, qapp, monkeypatch):
    from gui import map_background
    monkeypatch.setattr(map_background, 'CACHE_TILES', 5)
    view.restore({'map': map_path})
    for time in (0.0, 50.0, 100.0, 130.0, 200.0, 240.0):
        _settle(qapp, view, *_at(view, time), 150.0)
        assert len(view.map_layer._cache) <= 5
        assert view.map_layer.items


def test_tiles_shown_again_are_not_read_again(view, map_path, qapp, monkeypatch):
    view.restore({'map': map_path})
    _settle(qapp, view, *_at(view, 23.0), 150.0)
    shown = set(view.map_layer.items)
    reads = []
    original = view.map_file().tile
    monkeypatch.setattr(view.map_file(), 'tile', lambda *key: reads.append(key) or original(*key))
    view.map_layer._clear()
    view.map_layer.update()
    assert set(view.map_layer.items) == shown and reads == []


def test_the_tiles_follow_a_pan_by_themselves(view, map_path, qapp):
    from PySide6.QtTest import QTest
    view.restore({'map': map_path})
    _settle(qapp, view, *_at(view, 23.0), 100.0)
    z = view.map_layer.zoom
    px, py = _world_px(*_fix(112.0), z)
    there = (z, int(px // 256), int(py // 256))
    assert there not in view.map_layer.items
    e, n = _at(view, 112.0)
    view.view_box.setRange(xRange=(e - 100, e + 100), yRange=(n - 100, n + 100), padding=0)
    QTest.qWait(250)
    assert view.map_layer.zoom == z and there in view.map_layer.items


def test_larger_tiles_are_drawn_a_zoom_further_out(view, qapp, tmp_path):
    from core.map_tiles import choose_zoom
    path = _write_map(tmp_path / 'large.mbtiles', zooms=(14, 15), marks=False, tile_size=512)
    view.restore({'map': path})
    lat0 = view.track.origin[0]
    differs = 0
    for half in (1000.0, 700.0, 500.0, 350.0, 250.0, 150.0):
        _settle(qapp, view, *_at(view, 23.0), half)
        (x0, x1), _y = view.view_box.viewRange()
        mpp = (x1 - x0) / view.view_box.width()
        assert view.map_layer.zoom == choose_zoom(mpp, lat0, 512, 14, 15)
        if choose_zoom(mpp, lat0, 256, 14, 15) != choose_zoom(mpp, lat0, 512, 14, 15):
            differs += 1
    assert differs
    assert all(item.pixmap().width() == 512 for item in view.map_layer.items.values())
    _check_corners(view)


def test_another_map_file_replaces_the_first(view, qapp, tmp_path):
    first = _write_map(tmp_path / 'first.mbtiles', zooms=(15,), marks=False)
    second = _write_map(tmp_path / 'second.mbtiles', zooms=(15,), marks=False,
                        colors=('#ff0000', '#ff0000'))
    view.restore({'map': first})
    _settle(qapp, view)
    old = view.map_file()
    colors = {item.pixmap().toImage().pixelColor(128, 128).name()
              for item in view.map_layer.items.values()}
    assert colors and colors <= {LIGHT, DARK_TILE}
    view.restore({'map': second})
    _settle(qapp, view)
    colors = {item.pixmap().toImage().pixelColor(128, 128).name()
              for item in view.map_layer.items.values()}
    assert colors == {'#ff0000'}
    assert old._db is None and view.map_file() is not old


# ── Chosen under the gear ───────────────────────────────────────────────────


def test_a_map_file_is_chosen_under_the_gear_and_drawn_on_update(view, map_path, qapp,
                                                                 monkeypatch):
    asked = []
    monkeypatch.setattr(view, '_ask_map_file', lambda start: asked.append(start) or map_path)
    assert view.grid.isVisible() and view.map_file() is None and not view.credit.isVisible()
    assert view.state()['map'] == ''
    view.open_settings()
    assert view.map_label.text() == 'None' and not view.map_none_button.isEnabled()
    view.map_button.click()
    assert view.settings_box.isVisible()
    assert view.map_label.text() == 'test_area.mbtiles' and view.map_none_button.isEnabled()
    assert view.map_file() is None              # not before Update
    view.update_button.click()
    assert view.map_file() is not None and view.map_file().path == map_path
    _settle(qapp, view)
    assert view.map_layer.items
    assert not view.grid.isVisible()
    assert view.credit.isVisible() and view.credit.text() == '© Test map makers'
    assert view.state()['map'] == map_path
    # The route gets its dark edge over a map, under its bright part.
    xs, _ys = view.casing_curve.getData()
    bright, _ = view.line_curve.getData()
    assert len(xs) == len(bright) > 0
    # The next choice starts where this one was made.
    view.open_settings()
    view.map_button.click()
    assert asked[-1] == os.path.dirname(map_path)
    view.cancel_button.click()
    # A new view's first choice starts there too.
    from gui.gps_view import GPSView
    other = GPSView()
    monkeypatch.setattr(other, '_ask_map_file', lambda start: asked.append(start) or '')
    other.choose_map_file()
    assert asked[-1] == os.path.dirname(map_path)


def test_cancel_and_none_leave_or_take_the_map_away(view, map_path, qapp, monkeypatch):
    monkeypatch.setattr(view, '_ask_map_file', lambda start: map_path)
    view.open_settings()
    view.map_button.click()
    view.cancel_button.click()
    assert view.map_file() is None and view.state()['map'] == ''
    view.open_settings()
    assert view.map_label.text() == 'None'          # what the view is drawn with
    view.cancel_button.click()
    view.restore({'map': map_path})
    _settle(qapp, view)
    assert view.map_layer.items
    view.open_settings()
    assert view.map_label.text() == 'test_area.mbtiles'
    view.map_none_button.click()
    assert view.map_label.text() == 'None'
    view.update_button.click()
    _settle(qapp, view)
    assert view.map_file() is None and view.map_layer.items == {}
    assert view.grid.isVisible() and not view.credit.isVisible()
    xs, _ys = view.casing_curve.getData()
    assert xs is None or len(xs) == 0
    assert view.state()['map'] == ''


def test_a_file_that_cannot_be_shown_is_refused_with_the_reason(view, qapp, tmp_path,
                                                                monkeypatch):
    vector = tmp_path / 'streets.mbtiles'
    db = sqlite3.connect(str(vector))
    db.execute('CREATE TABLE tiles (zoom_level, tile_column, tile_row, tile_data)')
    db.execute('INSERT INTO tiles VALUES (14, 1, 1, ?)', (b'\x1f\x8b\x08\x00vector',))
    db.commit()
    db.close()
    monkeypatch.setattr(view, '_ask_map_file', lambda start: str(vector))
    view.open_settings()
    view.map_button.click()
    assert not view.map_note.isHidden()
    assert view.map_note.text().startswith('streets.mbtiles cannot be shown: it holds vector')
    assert view.map_label.text() == 'None'
    view.update_button.click()
    assert view.map_file() is None and view.state()['map'] == ''


# ── What the summary says ───────────────────────────────────────────────────


def test_a_map_file_gone_or_elsewhere_is_said_in_the_summary(view, qapp, tmp_path):
    gone = tmp_path / 'gone.mbtiles'
    view.restore({'map': str(gone)})
    assert 'map file gone.mbtiles: the file was not found' in view.summary.text()
    assert view.map_file() is None and view.grid.isVisible()
    # Once the file is back, the same setting opens it again.
    _write_map(gone, zooms=(15,), marks=False)
    view.restore({'map': str(gone)})
    assert view.map_file() is not None and 'gone.mbtiles' not in view.summary.text()
    # And so does Update under the gear.
    later = tmp_path / 'later.mbtiles'
    view.restore({'map': str(later)})
    assert view.map_file() is None and 'later.mbtiles' in view.summary.text()
    _write_map(later, zooms=(15,), marks=False)
    view.open_settings()
    view.update_button.click()
    assert view.map_file() is not None and 'later.mbtiles' not in view.summary.text()
    # A map of somewhere else is opened, and said not to cover the drive.
    away = _write_map(tmp_path / 'away.mbtiles', zooms=(15,), marks=False,
                      bounds=(9.10, 51.00, 9.12, 51.01))
    view.restore({'map': away})
    assert view.map_file() is not None
    assert view.summary.text().endswith('the map file does not cover this drive')


def test_without_a_track_no_map_is_drawn(qapp, map_path):
    from gui.gps_view import GPSView
    other = GPSView()
    other.resize(400, 300)
    other.restore({'map': map_path})
    other.show()
    _settle(qapp, other)
    assert other.map_file() is not None
    assert not other.map_layer.shown() and other.map_layer.items == {}
    assert other.map_layer.map_file is None
    # Nor when the layer is given a map without a track's layout.
    other.map_layer.set_map(other.map_file(), None)
    assert not other.map_layer.shown() and other.map_layer.items == {}
    other.map_layer.set_map(None, None)
    assert not other.credit.isVisible()
    other.set_decoded(_drive())
    _settle(qapp, other)
    assert other.map_layer.shown() and other.map_layer.items
    other.close()


def test_the_credit_sits_beside_north_and_the_scale_or_over_them(view, map_path, qapp):
    view.restore({'map': map_path})
    view.resize(1200, 500)
    _settle(qapp, view)
    credit, key = view.credit.geometry(), view.key.geometry()
    assert view.credit.isVisible() and credit.bottom() == key.bottom()
    assert credit.left() >= key.left() + view.key.needed_width()
    view.resize(260, 500)
    _settle(qapp, view)
    credit, key = view.credit.geometry(), view.key.geometry()
    assert credit.bottom() < key.top()


def test_a_map_is_faded_on_a_dark_background(view, map_path, qapp):
    view.restore({'map': map_path})
    _settle(qapp, view)
    items = list(view.map_layer.items.values())
    assert items and all(item.opacity() == 1.0 for item in items)
    view.set_colors('#202020', '#ffffff')
    assert all(item.opacity() == pytest.approx(0.6) for item in view.map_layer.items.values())
    # Tiles drawn after the change, at another zoom, are faded too.
    zoom = view.map_layer.zoom
    _settle(qapp, view, *_at(view, 100.0), 2000.0)
    assert view.map_layer.zoom != zoom and view.map_layer.items
    assert all(item.opacity() == pytest.approx(0.6) for item in view.map_layer.items.values())
    view.set_colors('#ffffff', '#000000')
    assert all(item.opacity() == 1.0 for item in view.map_layer.items.values())


# ── Saved with the tab ──────────────────────────────────────────────────────


def test_a_tab_keeps_its_map_file(window, map_path, qapp):  # noqa: F811
    from gui.gps_view import GPSView
    panel = window.plot_panel
    key = panel.add_xy_view('track')
    panel.xy_views()[key].restore({'map': map_path})
    saved = panel.xy_views_state()
    assert saved[0]['map'] == map_path
    panel.delete_xy_view(key)
    panel.set_xy_views_state(saved)
    again = panel.xy_views()['gps']
    assert isinstance(again, GPSView) and again.map_file() is not None
    assert again.map_file().path == map_path
    # A tab saved before maps existed opens without one.
    panel.delete_xy_view('gps')
    panel.set_xy_views_state([{k: v for k, v in saved[0].items() if k != 'map'}])
    assert panel.xy_views()['gps'].map_file() is None
