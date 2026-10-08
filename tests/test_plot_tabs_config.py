# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tabs in configuration files, and across measurements.

Save Config writes every tab; Load Config gives the window those tabs, each
with its signals, plot mode and name. A configuration saved before tabs is one
plot, for the tab on screen, and the plot keys of a new one still describe the
first tab for versions without tabs. Opening another measurement gives every
tab its signals back once the new file has decoded.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import numpy as np
import pytest

from PySide6.QtWidgets import QFileDialog, QMessageBox


def _store(*names: str):
    from core.signal_store import SignalStore
    store = SignalStore()
    for name in names:
        store.add_series_bulk(
            channel=1, message_name='Msg', message_id=1, signal_name=name, unit='V',
            timestamps=np.linspace(0.0, 10.0, 101), values=np.linspace(0.0, 1.0, 101),
            raw_values=[], has_labels=False,
        )
    return store


def _key(name: str) -> str:
    return _store(name).all_keys()[0]


@pytest.fixture()
def window(qapp, monkeypatch):
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    # Several tabs are a Pro feature, which these tests are about.
    monkeypatch.setattr('gui.plot_tabs.multiple_tabs_allowed', lambda: True)
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w.store = _store('Speed', 'Torque', 'Current', 'Voltage')
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def _two_tabs(qapp, window):
    """Tab 1: Speed and Torque, multi-axis. "Motor": Current, stacked, on screen."""
    window.add_signals_to_plot([_key('Speed'), _key('Torque')])
    window.btn_multi_axis.setChecked(True)
    window.plot_tabs.add_tab()
    window.plot_tabs.bar.setTabText(1, 'Motor')
    window.add_signals_to_plot([_key('Current')])
    window.plot_panel.set_series_color(_key('Current'), '#00ff00')
    qapp.processEvents()


def _save(window, monkeypatch, tmp_path) -> dict:
    out = tmp_path / 'config.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(out), ''))
    window.save_configuration()
    return json.loads(out.read_text(encoding='utf-8'))


def _load(window, monkeypatch, tmp_path, config: dict, keep_current_data=True) -> None:
    path = tmp_path / 'load.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    answer = QMessageBox.StandardButton.Yes if keep_current_data else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', Mock(return_value=answer))
    window.load_configuration()


def _tabs(window) -> list[tuple[str, list[str]]]:
    return [(name, panel.plotted_keys())
            for name, panel in zip(window.plot_tabs.names(), window.plot_tabs.panels())]


def test_save_writes_every_tab_and_the_first_in_the_old_keys(qapp, window, monkeypatch, tmp_path):
    _two_tabs(qapp, window)
    window.btn_sync_tabs.setChecked(False)

    data = _save(window, monkeypatch, tmp_path)

    tabs = data['tabs']
    assert [tab['name'] for tab in tabs] == ['Tab 1', 'Motor']
    assert [tab['plot_type'] for tab in tabs] == ['multi_axis', 'stacked']
    assert [[s['key'] for s in tab['signals']] for tab in tabs] == [
        [_key('Speed'), _key('Torque')], [_key('Current')]]
    assert tabs[1]['signals'][0]['color'] == '#00ff00'
    assert data['current_tab'] == 1 and data['synchronize_tabs'] is False
    # What a version without tabs opens: the first tab, not the one on screen.
    assert [s['key'] for s in data['signals']] == [_key('Speed'), _key('Torque')]
    assert data['multi_axis'] is True and data['stacked'] is False


def test_loading_a_saved_configuration_gives_back_every_tab(qapp, window, monkeypatch, tmp_path):
    _two_tabs(qapp, window)
    data = _save(window, monkeypatch, tmp_path)
    monkeypatch.setattr(QMessageBox, 'question', Mock(return_value=QMessageBox.StandardButton.Yes))
    window.plot_tabs.close_tab(1)
    window.clear_plot()

    _load(window, monkeypatch, tmp_path, data)
    qapp.processEvents()

    assert _tabs(window) == [('Tab 1', [_key('Speed'), _key('Torque')]),
                             ('Motor', [_key('Current')])]
    first, motor = window.plot_tabs.panels()
    assert first._multi_axis and motor._stacked_mode
    assert motor._items[_key('Current')].color == '#00ff00'
    assert window.plot_panel is motor          # the tab that was on screen
    assert window.btn_stacked.isChecked() and not window.btn_multi_axis.isChecked()


def test_a_configuration_after_a_decode_plots_each_tab(qapp, window, monkeypatch, tmp_path):
    measurement = tmp_path / 'next.csv'
    measurement.write_text('time,signal,value\n0.0,Speed,1.0\n', encoding='utf-8')
    monkeypatch.setattr('gui.main_window.dbc_required_for', lambda _path: False)
    monkeypatch.setattr(window, 'load_data', Mock())
    window.store = None
    window.plot_tabs.add_tab()
    window.plot_tabs.add_tab()                 # three tabs; the configuration has two
    config = {
        'measurement_path': str(measurement),
        'tabs': [
            {'name': 'Speeds', 'plot_type': 'stacked',
             'signals': [{'key': _key('Speed'), 'line_style': 'dash'}]},
            {'name': 'Power', 'plot_type': 'multi_axis', 'cursor1': True,
             'signals': [{'key': _key('Current')}, {'key': _key('Voltage')}]},
        ],
        'current_tab': 0,
    }

    _load(window, monkeypatch, tmp_path, config)

    # At once: the tabs, named and in their plot modes; the signals wait.
    assert window.plot_tabs.names()[:2] == ['Speeds', 'Power']
    assert window.plot_tabs.panels()[1]._multi_axis
    assert window.plot_tabs.panels()[0]._pending_line_styles == {_key('Speed'): 'dash'}
    window.load_data.assert_called_once()

    window._on_worker_finished(_store('Speed', 'Current', 'Voltage'))
    qapp.processEvents()

    assert _tabs(window) == [('Speeds', [_key('Speed')]),
                             ('Power', [_key('Current'), _key('Voltage')])]
    assert window.plot_tabs.panels()[0]._items[_key('Speed')].line_style == 'dash'
    assert window.plot_tabs.panels()[1]._cursor1_enabled
    assert window.plot_panel is window.plot_tabs.panels()[0]


def test_a_configuration_saved_before_tabs_is_the_tab_on_screen(qapp, window, monkeypatch, tmp_path):
    window.add_signals_to_plot([_key('Speed')])
    window.plot_tabs.add_tab()
    window.plot_tabs.bar.setTabText(1, 'Motor')

    _load(window, monkeypatch, tmp_path, {
        'signals': [{'key': _key('Voltage')}],
        'signal_colors': {_key('Voltage'): '#123456'},
        'multi_axis': True,
    })
    qapp.processEvents()

    assert _tabs(window) == [('Tab 1', [_key('Speed')]), ('Motor', [_key('Voltage')])]
    second = window.plot_tabs.panels()[1]
    assert second._multi_axis and second._items[_key('Voltage')].color == '#123456'
    assert window.plot_panel is second


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _FakeThread:
    def __init__(self, *_args, **_kwargs):
        self.started = _FakeSignal()
        self.finished = _FakeSignal()

    def start(self):
        pass

    def quit(self):
        pass

    def deleteLater(self):
        pass


class _FakeWorker:
    def __init__(self, *_args, **_kwargs):
        for name in ('progress', 'finished', 'failed', 'tree_update', 'partial_ready'):
            setattr(self, name, _FakeSignal())

    def moveToThread(self, _thread):
        pass

    def run(self):
        pass

    def deleteLater(self):
        pass


def test_another_measurement_gives_every_tab_its_signals_back(qapp, window, monkeypatch, tmp_path):
    measurement = tmp_path / 'next.csv'
    measurement.write_text('time,signal,value\n0.0,Speed,1.0\n', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(measurement), ''))
    monkeypatch.setattr('gui.main_window.dbc_required_for', lambda _path: False)
    monkeypatch.setattr('gui.main_window.QThread', _FakeThread)
    monkeypatch.setattr('gui.main_window.LoadWorker', _FakeWorker)
    window._temporary_plot_config_path = tmp_path / 'temp.json'
    _two_tabs(qapp, window)
    window.plot_tabs.bar.setCurrentIndex(0)

    window.choose_blf()
    assert all(panel.plotted_keys() == [] for panel in window.plot_tabs.panels())
    window._toolbar_actions['Load + Decode'].trigger()
    window._on_worker_finished(_store('Speed', 'Torque', 'Current'))
    qapp.processEvents()

    assert _tabs(window) == [('Tab 1', [_key('Speed'), _key('Torque')]),
                             ('Motor', [_key('Current')])]
    first, motor = window.plot_tabs.panels()
    assert first._multi_axis and motor._stacked_mode
    assert motor._items[_key('Current')].color == '#00ff00'
    assert window.plot_panel is first            # still the tab on screen
    assert window._temporary_plot_handoff is None
    window._cleanup_worker()


def test_a_generated_signal_is_plotted_in_the_tab_that_asked(qapp, window):
    import time
    from core.calculated_signals import CalculatedSignalDefinition

    definition = CalculatedSignalDefinition('Double', f'`{_key("Speed")}` * 2', 'V')
    window.calculated_signals.commit(definition)
    window._refresh_generated_signal_tree()
    window.plot_tabs.add_tab()
    asking = window.plot_panel

    window.add_signals_to_plot([definition.key])
    window.plot_tabs.bar.setCurrentIndex(0)      # another tab on screen meanwhile
    deadline = time.monotonic() + 5.0
    while window._calc_thread is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()

    assert asking.plotted_keys() == [definition.key]
    assert window.plot_panel.plotted_keys() == []


def test_saved_tabs_read_both_formats():
    from gui.plot_tabs import SavedTabs

    old = SavedTabs.from_config({'signals': ['A', {'key': 'B'}], 'signal_colors': {'B': '#fff'},
                                 'multistack': True, 'cursor2': True})
    assert old.single_plot and len(old.tabs) == 1
    assert old.tabs[0]['plot_type'] == 'multistack' and old.tabs[0]['cursor2'] is True
    assert old.tabs[0]['signals'] == [{'key': 'A'}, {'key': 'B', 'color': '#fff'}]

    new = SavedTabs.from_config({'tabs': [{'name': 'X'}, {'name': 'Y'}], 'current_tab': 7,
                                 'synchronize_tabs': False})
    assert not new.single_plot and new.current == 1 and new.synchronized is False
    assert not old.carried_over and not new.carried_over

    kept = SavedTabs.from_handoff({'plot_type': 'stacked', 'signals': [{'key': 'A'}]})
    assert kept.single_plot and kept.carried_over
    assert kept.tabs == [{'plot_type': 'stacked', 'signals': [{'key': 'A'}]}]
