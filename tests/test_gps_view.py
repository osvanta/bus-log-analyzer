# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The GPS track view: the drive seen from above, a panel beside the time plot.
It finds its latitude and longitude among every decoded signal, draws the
route (dimmed outside the time shown, dashed where there was no fix), marks
the cursors, sends Cursor 1 where the route is clicked and shades the times
the vehicle was inside a Shift+dragged box.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import math
from unittest.mock import Mock

import numpy as np
import pytest

_M = 6_371_008.8 * math.pi / 180
_LAT0, _LON0 = 50.0, 8.0


def _series(name, times, values, unit='deg', message='VP'):
    from core.signal_store import SignalSeries
    return SignalSeries(channel=1, message_name=message, message_id=0x500, signal_name=name,
                        unit=unit, timestamps=np.asarray(times, dtype=float),
                        values=np.asarray(values, dtype=float))


def _drive():
    """East 1 km, north 300 m, west 600 m, then south 600 m across the first
    street at 400 m east: passed twice there. 10 m/s, a fix each second; no
    fix (raw "not available") for 5 s on the way west."""
    legs = [((0, 0), (1000, 0)), ((1000, 0), (1000, 300)), ((1000, 300), (400, 300)),
            ((400, 300), (400, -300))]
    points = []
    for (x0, y0), (x1, y1) in legs:
        steps = int(math.hypot(x1 - x0, y1 - y0) / 10)
        points += [(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps)
                   for k in range(steps)]
    times = np.arange(len(points)) * 1.0
    east = np.array([p[0] for p in points])
    north = np.array([p[1] for p in points])
    lat = _LAT0 + north / _M
    lon = _LON0 + east / (_M * math.cos(math.radians(_LAT0)))
    tunnel = (times > 150) & (times < 156)
    lat[tunnel] = lon[tunnel] = 211.1
    speed = np.where(times < 100, 36.0, 72.0)
    return [
        ('CH1::VP::VP_Latitude', _series('VP_Latitude', times, lat)),
        ('CH1::VP::VP_Longitude', _series('VP_Longitude', times, lon)),
        ('CH1::VCU::VehicleSpeed', _series('VehicleSpeed', times, speed, 'km/h', 'VCU')),
        ('CH1::IMU::IMU_LateralAccel',
         _series('IMU_LateralAccel', times, np.zeros_like(times), 'm/s2', 'IMU')),
    ]


def _crossing(view):
    """Where the route crosses itself, in the track's flat frame: the fix
    at t=40 s, 400 m east on the way east."""
    index = int(np.flatnonzero(view.track.t == 40.0)[0])
    return float(view.track.e[index]), float(view.track.n[index])


def _box(view, half=30.0):
    e, n = _crossing(view)
    return e - half, n - half, e + half, n + half


@pytest.fixture()
def view(qapp):
    from gui.gps_view import GPSView
    v = GPSView()
    v.resize(600, 500)
    v.set_decoded(_drive())
    v.show()
    qapp.processEvents()
    yield v
    v.close()


def _points(curve):
    xs, _ys = curve.getData()
    return 0 if xs is None else len(xs)


# ── The view on its own ──────────────────────────────────────────────────


def test_it_finds_the_latitude_and_longitude_on_its_own(view):
    assert view.used() == ('CH1::VP::VP_Latitude', 'CH1::VP::VP_Longitude')
    assert view.chosen('lat') is None and view.chosen('lon') is None   # automatic
    track = view.track
    assert track.size == 250 - 5 and len(track.gaps()) == 1
    text = view.summary.text()
    assert 'km' in text and '245 fixes' in text and '1 gap' in text


def test_it_says_what_is_missing(qapp):
    from gui.gps_view import GPSView
    v = GPSView()
    assert 'Load a measurement' in v.summary.text()
    v.set_decoded(_drive()[2:])                       # no position among them
    assert v.used() == (None, None)
    assert 'No latitude and longitude' in v.summary.text()
    assert _points(v.line_curve) == 0


def test_the_route_is_drawn_and_its_gap_dashed(view):
    from PySide6.QtCore import Qt
    track = view.track
    joined = int(track.joined.sum())
    assert _points(view.line_curve) == 2 * joined      # a piece per joined fix
    assert _points(view.dim_curve) == 0
    xs, ys = view.gap_curve.getData()
    before, after = track.gaps()[0]
    assert list(xs) == [track.e[before], track.e[after]]
    assert view.gap_curve.opts['pen'].style() == Qt.PenStyle.DashLine
    # Start and end are marked.
    assert view.ends.data['x'].tolist() == [track.e[0], track.e[-1]]


def test_outside_the_time_shown_the_route_is_dimmed_unless_whole_is_asked(view):
    view.set_time_range((0.0, 100.0))
    view._redraw()
    shown, dimmed = _points(view.line_curve) // 2, _points(view.dim_curve) // 2
    assert shown == 100 and dimmed > 0
    view.whole_box.setChecked(True)
    view.apply_settings()
    assert _points(view.dim_curve) == 0


def test_a_colour_signal_colours_the_route_low_blue_and_high_red(view):
    view.choose('color', 'CH1::VCU::VehicleSpeed')
    counts = [_points(curve) // 2 for curve in view.color_curves]
    # 36 km/h before t=100, 72 after: the two ends of the scale.
    assert counts[0] > 0 and counts[-1] > 0 and sum(counts[1:-1]) == 0
    assert _points(view.line_curve) == 0
    assert not view.scale.isHidden()
    assert 'VCU.VehicleSpeed [km/h]' in view.scale.text()


def test_each_cursor_is_marked_where_the_vehicle_was_and_dotted_without_a_fix(view):
    from PySide6.QtCore import Qt
    track = view.track
    view.set_cursors(20.0, 153.0)                     # Cursor 2 in the tunnel
    index = int(np.flatnonzero(track.t == 20.0)[0])
    assert view.mark_position(1) == (track.e[index], track.n[index])
    last = int(np.flatnonzero(track.t == 150.0)[0])
    assert view.mark_position(2) == (track.e[last], track.n[last])
    assert view.mark_has_fix(1) and not view.mark_has_fix(2)
    assert view.mark2.opts['pen'].style() == Qt.PenStyle.DotLine
    assert 'C1 to C2' in view.summary.text()
    view.set_cursors(None, None)
    assert view.mark_position(1) is None and view.mark_position(2) is None


def test_a_click_on_the_route_asks_for_cursor_1_and_again_for_the_next_pass(view):
    asked = []
    view.timeClicked.connect(asked.append)
    cross = _crossing(view)
    assert view.pick(*cross)
    first = asked[-1]
    assert first == pytest.approx(40.0, abs=2)        # on the way east
    view.set_cursors(first, None)                     # as the time plot answers
    assert view.pick(*cross)
    assert asked[-1] > 200                            # on the way south
    view.set_cursors(asked[-1], None)
    assert view.pick(*cross)
    assert asked[-1] == first                         # and round again
    assert not view.pick(5000.0, 5000.0)
    assert len(asked) == 3


def test_a_click_a_few_pixels_beside_the_route_picks_it_and_far_off_does_not(view):
    asked = []
    view.timeClicked.connect(asked.append)
    pixel = float(view.view_box.viewPixelSize()[0])
    index = int(np.flatnonzero(view.track.t == 20.0)[0])
    e, n = float(view.track.e[index]), float(view.track.n[index])
    # The street runs east: north of it is beside it.
    assert view.pick(e, n + 6 * pixel)
    assert asked[-1] == pytest.approx(20.0, abs=1)
    assert not view.pick(e, n + 30 * pixel)
    assert len(asked) == 1


def test_a_click_away_from_the_route_lets_a_box_go(view):
    chosen = []
    view.spansChosen.connect(chosen.append)
    view.choose_box(*_box(view))
    assert view.view_box.box_item.isVisible()
    view.pick(5000.0, 5000.0)
    assert chosen[-1] == [] and not view.view_box.box_item.isVisible()


def test_a_box_shades_each_pass_through_it_and_sends_cursor_1_to_the_first(view):
    chosen, asked = [], []
    view.spansChosen.connect(chosen.append)
    view.timeClicked.connect(asked.append)
    spans = view.choose_box(*_box(view))
    assert len(spans) == 2 and chosen[-1] == spans
    assert asked == [spans[0][0]]
    assert view.picked_spans() == spans
    view.pick_bar(None)                                # another view shaded
    assert chosen[-1] == [] and view.picked_spans() == []


class _Drag:
    """A mouse drag as pyqtgraph's ViewBox receives it."""

    def __init__(self, start, end, shift, finish=True):
        from PySide6.QtCore import QPointF, Qt
        self._start, self._end = QPointF(*start), QPointF(*end)
        self._shift, self._finish = shift, finish
        self.accepted = False
        self._button = Qt.MouseButton.LeftButton

    def button(self):
        return self._button

    def modifiers(self):
        from PySide6.QtCore import Qt
        return (Qt.KeyboardModifier.ShiftModifier if self._shift
                else Qt.KeyboardModifier.NoModifier)

    def buttonDownPos(self, *_):
        return self._start

    def pos(self):
        return self._end

    def lastPos(self):
        return self._start

    def isStart(self):
        return False

    def isFinish(self):
        return self._finish

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def test_shift_drag_draws_a_box_and_a_plain_drag_pans(view, monkeypatch):
    box = view.view_box
    drawn = []
    monkeypatch.setattr(view, 'choose_box', lambda *corners: drawn.append(corners))
    box.on_box = view._box_drawn
    start = box.mapFromView(box.mapToView(box.rect().center()))
    a, b = (start.x() - 20, start.y() - 20), (start.x() + 20, start.y() + 20)
    box.mouseDragEvent(_Drag(a, b, shift=True, finish=False))
    assert box.box_item.isVisible() and drawn == []
    box.mouseDragEvent(_Drag(a, b, shift=True))
    assert len(drawn) == 1
    box.box_item.hide()
    panned = []
    monkeypatch.setattr(type(box).__mro__[1], 'mouseDragEvent',
                        lambda self, event, axis=None: panned.append(event))
    box.mouseDragEvent(_Drag(a, b, shift=False))
    assert len(panned) == 1 and len(drawn) == 1 and not box.box_item.isVisible()


def test_the_settings_offer_positions_first_and_apply_on_update(view):
    view.open_settings()
    assert view.lat_box.itemText(0) == 'Automatic (VP.VP_Latitude [deg])'
    assert view.lat_box.itemData(1) == 'CH1::VP::VP_Latitude'      # positions first
    assert view.lon_box.itemData(1) == 'CH1::VP::VP_Longitude'
    assert view.color_box.itemText(0) == 'None'
    assert view.lat_box.count() == 1 + 4
    # Cancel leaves it as it was.
    view.lon_box.setCurrentIndex(view.lon_box.findData('CH1::IMU::IMU_LateralAccel'))
    view.cancel_button.click()
    assert view.chosen('lon') is None
    # Update takes it, whatever the signal: the user's choice.
    view.open_settings()
    view.lon_box.setCurrentIndex(view.lon_box.findData('CH1::IMU::IMU_LateralAccel'))
    view.gap_edit.setText('10')
    view.update_button.click()
    assert view.chosen('lon') == 'CH1::IMU::IMU_LateralAccel'
    assert view.used()[1] == 'CH1::IMU::IMU_LateralAccel'
    assert view.option('gap') == '10'
    # Automatic again.
    view.open_settings()
    view.lon_box.setCurrentIndex(0)
    view.update_button.click()
    assert view.chosen('lon') is None and view.used()[1] == 'CH1::VP::VP_Longitude'
    assert not view.settings_box.isVisible()


def test_a_longer_gap_limit_joins_the_tunnel(view):
    view.restore({'gap': '10'})
    assert view.track.gaps() == []


def test_its_choices_are_kept_and_taken_back(view, qapp):
    from gui.gps_view import GPSView
    view.choose('color', 'CH1::VCU::VehicleSpeed')
    view.restore({**view.state(), 'whole_measurement': True})
    state = view.state()
    assert state == {'lat': None, 'lon': None, 'color': 'CH1::VCU::VehicleSpeed',
                     'whole_measurement': True, 'gap': '', 'map': '', 'view': 'track'}
    other = GPSView()
    other.restore(state)
    other.set_decoded(_drive())
    assert other.state() == state and other.used() == view.used()
    other.rename_signal('CH1::VCU::VehicleSpeed', 'CH2::VCU::VehicleSpeed')
    assert other.chosen('color') == 'CH2::VCU::VehicleSpeed'


def test_a_released_measurement_is_let_go(view):
    view.set_decoded([])
    assert view.track.size == 0 and view.used() == (None, None)
    assert 'Load a measurement' in view.summary.text()


def test_the_scale_bar_and_grid_are_round_numbers(view, qapp):
    from gui.gps_view import _nice, distance_text
    assert [_nice(x) for x in (0.7, 7.3, 260, 1999)] == [0.5, 5.0, 200.0, 1000.0]
    assert [distance_text(x) for x in (850, 4321, 128_400)] == ['850 m', '4.32 km', '128 km']
    view.show()
    view.fit()
    qapp.processEvents()
    view._place_overlays()
    assert view.key.length in {1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000}
    assert view.key.text() == distance_text(view.key.length)


# ── In the window ────────────────────────────────────────────────────────


class _Store:
    """Enough of a SignalStore for the panel."""

    def __init__(self, signals):
        self._signals = dict(signals)

    def all_keys(self):
        return list(self._signals)

    def get_series(self, key):
        return self._signals.get(key)


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    signals = _drive()
    w.store = _Store(signals)
    panel = w.plot_panel
    panel.add_series(signals[2][0], signals[2][1])
    panel._rebuild_curves(preserve_selection=True)
    yield w
    w.store = None
    w.close()
    qapp.processEvents()


def _views_menu(window):
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    window._fill_views_menu(menu)
    window._kept_menu = menu
    return {action.text().split('\t')[0]: action
            for action in menu.actions() if not action.isSeparator()}


def test_views_adds_gps_tracks_as_panels_of_their_own(window, qapp):
    from gui.gps_view import GPSView
    panel = window.plot_panel
    _views_menu(window)['Add GPS track'].trigger()
    _views_menu(window)['Add GPS track'].trigger()
    views = panel.xy_views()
    assert list(views) == ['gps', 'gps-2']
    assert [panel.panels.title(key) for key in views] == ['GPS track', 'GPS track 2']
    assert all(isinstance(view, GPSView) and panel.panels.is_open(key)
               for key, view in views.items())
    # Every decoded signal reached them, not only the plotted speed.
    assert views['gps'].used() == ('CH1::VP::VP_Latitude', 'CH1::VP::VP_Longitude')
    assert 'GPS track' in _views_menu(window)


def test_a_click_on_the_route_moves_cursor_1(window, qapp):
    panel = window.plot_panel
    key = panel.add_xy_view('track')
    view = panel.xy_views()[key]
    window.resize(1400, 800)
    window.show()
    qapp.processEvents()
    asked = []
    panel.plotAreaClicked.connect(asked.append)
    assert view.pick(*_crossing(view))
    assert asked and asked[-1] == pytest.approx(40.0, abs=2)


def test_a_box_shades_the_time_plot_and_another_views_shading_lets_it_go(window, qapp):
    panel = window.plot_panel
    key = panel.add_xy_view('track')
    view = panel.xy_views()[key]
    spans = view.choose_box(*_box(view))
    assert panel.time_spans() == spans and len(spans) == 2
    key = panel.add_xy_view('histogram')
    histogram = panel.xy_views()[key]
    histogram.pick_bar(0)
    assert view.picked_spans() == [] and not view.view_box.box_item.isVisible()
    assert panel.time_spans() == histogram.picked_spans()


def test_the_cursors_reach_its_marks(window, qapp):
    panel = window.plot_panel
    key = panel.add_xy_view('track')
    view = panel.xy_views()[key]
    panel.plotAreaClicked.emit(20.0)               # a click places Cursor 1
    qapp.processEvents()
    panel._show_xy_cursors()
    index = int(np.flatnonzero(view.track.t == 20.0)[0])
    assert panel._cursor1_enabled
    assert view.mark_position(1) == pytest.approx(
        (view.track.e[index], view.track.n[index]), abs=15)


def test_a_new_measurement_reaches_it_and_a_released_one_is_let_go(window, qapp):
    panel = window.plot_panel
    key = panel.add_xy_view('track')
    view = panel.xy_views()[key]
    window.store = _Store(_drive()[2:])               # no position in this one
    panel.refresh_decoded()
    assert view.used() == (None, None)
    window.store = _Store(_drive())
    panel.refresh_decoded()
    assert view.used()[0] == 'CH1::VP::VP_Latitude'
    panel.panels.close_panel('gps')
    window._release_store()                           # closed views let go too
    assert view.track.size == 0 and view.used() == (None, None)


def test_a_tab_keeps_its_gps_track_and_sets_it_up_again(window, qapp):
    from gui.gps_view import GPSView
    panel = window.plot_panel
    key = panel.add_xy_view('track')
    view = panel.xy_views()[key]
    view.choose('color', 'CH1::VCU::VehicleSpeed')
    panel.panels.set_title(key, 'Test drive')
    saved = panel.xy_views_state()
    assert saved == [{'key': 'gps', 'name': 'Test drive', 'lat': None, 'lon': None,
                      'color': 'CH1::VCU::VehicleSpeed', 'whole_measurement': False,
                      'gap': '', 'map': '', 'view': 'track'}]
    panel.delete_xy_view(key)
    assert panel.xy_views() == {}
    panel.set_xy_views_state(saved)
    again = panel.xy_views()['gps']
    assert isinstance(again, GPSView) and panel.panels.title('gps') == 'Test drive'
    assert again.state() == {k: v for k, v in saved[0].items() if k not in ('key', 'name')}


def test_a_saved_tabs_renamed_signal_follows_into_its_gps_track():
    from gui.plot_tabs import SavedTabs
    tabs = SavedTabs([{'xy_views': [{'key': 'gps', 'view': 'track', 'lat': 'A', 'lon': 'B',
                                     'color': 'C'}]}], 0)
    tabs.rename_signal('A', 'A2')
    tabs.rename_signal('B', 'B2')
    assert tabs.tabs[0]['xy_views'][0] == {'key': 'gps', 'view': 'track', 'lat': 'A2',
                                           'lon': 'B2', 'color': 'C'}


def test_a_decode_finishing_gives_an_open_gps_track_its_position(window, qapp):
    from core.signal_store import SignalStore
    panel = window.plot_panel
    window.store = None
    key = panel.add_xy_view('track')
    view = panel.xy_views()[key]
    assert view.used() == (None, None)
    store = SignalStore()
    for _key, series in _drive():
        store.add_series_bulk(
            channel=1, message_name=series.message_name, message_id=1,
            signal_name=series.signal_name, unit=series.unit,
            timestamps=np.asarray(series.numpy_timestamps()),
            values=np.asarray(series.numpy_values()), raw_values=[], has_labels=False)
    window._on_worker_finished(store)
    qapp.processEvents()
    lat, lon = view.used()
    assert lat.endswith('VP::VP_Latitude') and lon.endswith('VP::VP_Longitude')
    assert view.track.size == 245
