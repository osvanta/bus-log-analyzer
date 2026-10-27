# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Named markers on the plot: added at the time under a right-click or at
Cursor 1 (M), dragged, renamed, deleted and gone to; drawn on every row of
the stacked layout; kept through Clear Plot and in the configuration,
dropped when another measurement opens; and written to MF4 as marker events.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from PySide6.QtWidgets import QMenu


def _series(name):
    from core.signal_store import SignalSeries
    ts = np.arange(0.0, 100.0, 0.5)
    return SignalSeries(1, 'Msg', 0x100, name, 'u', ts, np.sin(ts))


@pytest.fixture()
def panel(qapp):
    from gui.plot_widget import PlotPanel
    p = PlotPanel()
    p.resize(900, 600)
    p.add_series('A', _series('A'))
    p.add_series('B', _series('B'))
    p.show()
    qapp.processEvents()
    yield p
    p.close()


def _lines(panel):
    return [line for _plot, line in panel._marker_lines]


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


class _RecordingMenu(QMenu):
    """Records its actions instead of opening, as exec() and popup() would."""
    shown: list = []

    def exec(self, *_args):
        _RecordingMenu.shown.append(self)

    def popup(self, *_args):
        _RecordingMenu.shown.append(self)


@pytest.fixture()
def recorded(panel, monkeypatch):
    _RecordingMenu.shown = []
    monkeypatch.setattr(panel, '_make_menu', lambda parent=None: _RecordingMenu())
    return _RecordingMenu.shown


# ── Adding, naming, removing ─────────────────────────────────────────────


def test_markers_are_named_in_turn_and_kept_in_time_order(panel):
    panel.add_marker(30.0)
    panel.add_marker(10.0)
    named = panel.add_marker(20.0, 'Brake test')
    assert [(m.time, m.name) for m in panel.markers()] == [
        (10.0, 'M2'), (20.0, 'Brake test'), (30.0, 'M1')]
    panel.remove_marker(panel.markers()[0])
    assert panel.add_marker(5.0).name == 'M2'         # the first name free
    assert named in panel.markers()


def test_a_marker_at_cursor_1_needs_cursor_1(panel):
    panel.set_cursor1_enabled(False)
    assert panel.add_marker_at_cursor1() is None
    panel.set_cursor1_enabled(True)
    panel.move_cursor1(42.0)
    assert panel.add_marker_at_cursor1().time == pytest.approx(42.0)


def test_rename_ignores_a_blank_name(panel):
    marker = panel.add_marker(1.0)
    panel.rename_marker(marker, '  ')
    assert marker.name == 'M1'
    panel.rename_marker(marker, ' Launch ')
    assert marker.name == 'Launch'
    assert _lines(panel)[0].label.toPlainText() == 'Launch'


# ── Drawing ──────────────────────────────────────────────────────────────


def test_one_line_per_marker_on_each_row_named_once(panel):
    from gui.plot_widget import _MarkerLine
    panel.set_stacked(True)
    panel.add_marker(25.0)
    rows = len(panel._stacked_plots)
    assert rows == 2
    lines = _lines(panel)
    assert len(lines) == rows and all(isinstance(line, _MarkerLine) for line in lines)
    assert [getattr(line, 'label', None) is not None for line in lines] == [True, False]
    assert {line.value() for line in lines} == {25.0}
    panel.set_stacked(False)
    assert len(_lines(panel)) == 1                    # redrawn on the one plot


def test_dragging_one_row_moves_the_marker_everywhere(panel):
    panel.set_stacked(True)
    marker = panel.add_marker(25.0)
    other = panel.add_marker(30.0)
    first, second = [line for line in _lines(panel) if line.marker is marker]
    first.setPos(40.0)
    first.sigDragged.emit(first)
    assert second.value() == pytest.approx(40.0)
    first.sigPositionChangeFinished.emit(first)
    assert marker.time == pytest.approx(40.0)
    assert panel.markers() == [other, marker]          # still in time order


def test_a_rebuild_keeps_the_markers(panel):
    panel.add_marker(25.0)
    panel.remove_series('B')
    assert len(_lines(panel)) == 1


# ── Menus ────────────────────────────────────────────────────────────────


def test_the_plot_menu_offers_the_marker_commands(panel):
    panel.set_cursor1_enabled(False)
    menu = QMenu()
    panel._add_marker_actions(menu, 12.5)
    texts = _texts(menu)
    assert texts[:3] == ['Add marker here', 'Add marker at Cursor 1', 'Go to marker']
    assert 'Show measurement events' in texts
    actions = {a.text(): a for a in menu.actions()}
    assert not actions['Add marker at Cursor 1'].isEnabled()      # Cursor 1 off
    assert actions['Add marker at Cursor 1'].shortcut().toString() == 'M'
    assert not actions['Go to marker'].isEnabled()                 # none yet
    actions['Add marker here'].trigger()
    assert [m.time for m in panel.markers()] == [12.5]


def test_go_to_marker_lists_them_and_centres_keeping_the_zoom(panel):
    panel.add_marker(70.0, 'Late')
    panel.plot.plotItem.vb.setXRange(10.0, 20.0, padding=0)
    menu = QMenu()
    panel._add_marker_actions(menu)
    assert 'Add marker here' not in _texts(menu)      # no time without a click
    go_to = next(a for a in menu.actions() if a.text() == 'Go to marker').menu()
    assert _texts(go_to) == ['Late  ·  70.000 s']
    go_to.actions()[0].trigger()
    low, high = panel._visible_x_range()
    assert (low + high) / 2 == pytest.approx(70.0, abs=0.01)
    assert high - low == pytest.approx(10.0, rel=0.01)


def test_right_click_on_the_plot_puts_the_commands_on_top(panel, recorded):
    shown = recorded
    vb = panel.plot.plotItem.vb
    scene_pos = vb.mapViewToScene(vb.mapToView(vb.boundingRect().center()))
    event = SimpleNamespace(scenePos=lambda: scene_pos,
                            screenPos=lambda: SimpleNamespace(toPoint=lambda: None),
                            accept=lambda: None, acceptedItem=vb)
    vb.raiseContextMenu(event)
    texts = _texts(shown[0])
    assert texts[0] == 'Add marker here'
    assert 'View All' in texts                       # pyqtgraph's own still there
    shown[0].actions()[0].trigger()
    assert panel.markers()[0].time == pytest.approx(vb.mapSceneToView(scene_pos).x())


def test_the_signal_menu_offers_a_marker_at_the_clicked_time(panel, recorded):
    panel._show_signal_menu(['A'], None, time_s=3.0)
    panel._show_signal_menu(['A'], None)               # from the table: no time
    shown = [_texts(menu) for menu in recorded]
    assert 'Add marker here' in shown[0] and 'Add marker here' not in shown[1]
    assert 'Add marker at Cursor 1' in shown[1]


def test_a_markers_own_menu(panel, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    marker = panel.add_marker(8.0)
    clicked = []
    panel.plotAreaClicked.connect(clicked.append)
    actions = {a.text(): a for a in panel.marker_menu(marker).actions()}
    assert set(actions) == {'Rename…', 'Move Cursor 1 here', 'Delete marker'}
    actions['Move Cursor 1 here'].trigger()
    assert clicked == [8.0]
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Gear shift', True))
    actions['Rename…'].trigger()
    assert marker.name == 'Gear shift'
    actions['Delete marker'].trigger()
    assert panel.markers() == []


def test_double_click_renames_and_right_click_opens_the_menu(panel, monkeypatch, recorded):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QInputDialog
    marker = panel.add_marker(8.0)
    line = _lines(panel)[0]
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Stop', True))

    def click(button, double=False):
        return SimpleNamespace(button=lambda: button, double=lambda: double,
                               accept=lambda: None)

    line.mouseClickEvent(click(Qt.MouseButton.LeftButton, double=True))
    assert marker.name == 'Stop'
    line = _lines(panel)[0]
    line.mouseClickEvent(click(Qt.MouseButton.RightButton))
    assert [_texts(menu) for menu in recorded] == [
        ['Rename…', 'Move Cursor 1 here', 'Delete marker']]


# ── Clearing, undo, configuration ────────────────────────────────────────


def test_clearing_the_plot_keeps_them(panel):
    # Clear Plot, and the clear before every Decode, keep the markers:
    # a configuration sets them before its Decode starts.
    panel.add_marker(5.0, 'Keep')
    panel.clear_all()
    assert [(m.time, m.name) for m in panel.markers()] == [(5.0, 'Keep')]
    panel.add_series('A', _series('A'))
    assert len(_lines(panel)) == 1


def test_markers_from_a_configuration(panel):
    panel.set_markers([{'time': 9, 'name': 'Late'}, {'time': 'x'}, (1.5, ''),
                       {'time': float('nan')}, 'junk', {'time': 3.0, 'name': None}])
    assert [(m.time, m.name) for m in panel.markers()] == [
        (1.5, 'M1'), (3.0, 'M2'), (9.0, 'Late')]
    assert panel.markers_config() == [
        {'time': 1.5, 'name': 'M1'}, {'time': 3.0, 'name': 'M2'},
        {'time': 9.0, 'name': 'Late'}]


# ── MF4 ──────────────────────────────────────────────────────────────────


def test_markers_are_written_as_mf4_events(tmp_path):
    from core.bus_types import BusType
    from core.measurement_info import read_file_info
    from core.mf4_writer import write_mf4
    from core.signal_store import SignalSeries
    series = SignalSeries((BusType.CAN, 1), 'Msg', 0x100, 'Speed', 'km/h',
                          np.arange(0.0, 10.0, 0.5), np.arange(20.0))
    path = tmp_path / 'with_markers.mf4'
    write_mf4([series], path, events=[(2.5, 'Brake test'), (7.0, 'M2')])
    events = read_file_info(str(path)).events
    assert [(e.time_s, e.name) for e in events] == [(2.5, 'Brake test'), (7.0, 'M2')]


# ── The main window ──────────────────────────────────────────────────────


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from gui import app_data
    from gui.main_window import MainWindow
    monkeypatch.setenv(app_data.DATA_DIR_ENV_VAR, str(tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    yield w
    w.close()
    w.deleteLater()


def test_m_adds_a_marker_at_cursor_1(window):
    from PySide6.QtGui import QKeySequence
    from PySide6.QtGui import QShortcut
    shortcut = next(s for s in window.findChildren(QShortcut)
                    if s.key() == QKeySequence('M'))
    shortcut.activated.emit()
    assert window.plot_panel.markers() == []           # Cursor 1 is off
    window.btn_cursor1.setChecked(True)
    window.plot_panel.move_cursor1(4.0)
    shortcut.activated.emit()
    assert [m.time for m in window.plot_panel.markers()] == [pytest.approx(4.0)]
    assert 'M' in dict(window._SHORTCUTS)


def test_the_configuration_keeps_the_markers(window, monkeypatch, tmp_path):
    import json
    from PySide6.QtWidgets import QFileDialog
    window.plot_panel.add_marker(3.25, 'Start')
    target = tmp_path / 'config.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        lambda *a, **k: (str(target), ''))
    window.save_configuration()
    saved = json.loads(target.read_text(encoding='utf-8'))
    assert saved['markers'] == [{'time': 3.25, 'name': 'Start'}]


def test_loading_a_configuration_restores_the_markers_through_decode(window, monkeypatch,
                                                                    tmp_path):
    import json
    from unittest.mock import Mock
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    measurement = tmp_path / 'm.mf4'
    measurement.write_bytes(b'')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'measurement_path': str(measurement), 'signals': [],
                                  'markers': [{'time': 2.0, 'name': 'Start'}]}))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(config), ''))
    decoded = []
    # Decode starts by clearing the plot, as the real one does.
    monkeypatch.setattr(window, 'load_data', lambda **_k: decoded.append(
        window.plot_panel.clear_all()))
    window.load_configuration()
    assert decoded
    assert [(m.time, m.name) for m in window.plot_panel.markers()] == [(2.0, 'Start')]


def test_a_new_measurement_drops_the_markers(window):
    window.plot_panel.add_marker(2.0)
    window._reset_for_new_measurement()
    assert window.plot_panel.markers() == []


def test_exported_mf4_carries_the_markers(window, monkeypatch, tmp_path):
    from core import mf4_writer
    window.plot_panel.add_marker(1.0, 'One')
    seen = {}
    monkeypatch.setattr(mf4_writer, 'write_mf4',
                        lambda items, path, **kw: seen.update(kw) or 0)
    window._write_mf4([], tmp_path / 'x.mf4', 'Exporting to MF4')
    assert seen['events'] == [(1.0, 'One')]
