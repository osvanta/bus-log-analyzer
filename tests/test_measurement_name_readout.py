# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The cursor line under the plot names the measurement file after the cursor
readout, on the same line and in the same text, so the user sees which file
the plotted signals come from.

Load + Decode runs on a stand-in thread and worker; the decoded store is
handed to the window as the worker would hand it.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest


class _FakeSignal:
    def connect(self, _callback):
        pass


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
        self.progress = _FakeSignal()
        self.finished = _FakeSignal()
        self.failed = _FakeSignal()
        self.tree_update = _FakeSignal()
        self.partial_ready = _FakeSignal()

    def moveToThread(self, _thread):
        pass

    def run(self):
        pass

    def deleteLater(self):
        pass


def _store():
    from core.signal_store import SignalStore

    store = SignalStore()
    store.add_series_bulk(
        channel=1, message_name='Msg', message_id=1, signal_name='Speed',
        unit='km/h',
        timestamps=np.arange(100, dtype=np.float64) * 0.1,
        values=np.arange(100, dtype=np.float64),
        raw_values=[], has_labels=False,
    )
    return store


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr('gui.main_window.dbc_required_for', lambda _path: False)
    monkeypatch.setattr('gui.main_window.QThread', _FakeThread)
    monkeypatch.setattr('gui.main_window.LoadWorker', _FakeWorker)

    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w._temporary_plot_config_path = tmp_path / 'osvanta_temp_plot_config.json'
    w._log = Mock()
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w._cleanup_worker()
    w.close()
    qapp.processEvents()


def _open(window, monkeypatch, path):
    from PySide6.QtWidgets import QFileDialog

    path.write_text('time,signal,value\n0.0,Speed,1.0\n', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    window.choose_blf()


def _load_and_plot(qapp, window):
    window._toolbar_actions['Load + Decode'].trigger()
    store = _store()
    window._on_worker_finished(store)
    window._cleanup_worker()   # as the finished thread would
    window.add_signals_to_plot(store.all_keys())
    qapp.processEvents()


def test_file_name_follows_the_cursor_readout_on_its_line(qapp, window, monkeypatch, tmp_path):
    measurement = tmp_path / 'drive_cycle_03.csv'
    _open(window, monkeypatch, measurement)
    _load_and_plot(qapp, window)
    panel = window.plot_panel
    readout, name = panel.cursor_label, panel.file_name_label

    assert readout.isVisible() and name.isVisible()
    assert name.text() == '   |   drive_cycle_03.csv'
    assert name.toolTip() == str(measurement)
    # Straight after the readout, in a box of the same height and font, so
    # both texts sit on one baseline.
    assert name.x() == readout.geometry().right() + 1
    assert name.y() == readout.y()
    assert name.height() == readout.height()
    assert name.font() == readout.font()

    # Room for the longer readout of both cursors and the whole name.
    window.resize(2400, 800)
    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)
    qapp.processEvents()
    assert 'ΔT=' in readout.text()
    assert name.x() == readout.geometry().right() + 1
    assert name.text().endswith('drive_cycle_03.csv')


def test_no_file_name_without_plotted_signals(qapp, window, monkeypatch, tmp_path):
    _open(window, monkeypatch, tmp_path / 'first.csv')
    window._toolbar_actions['Load + Decode'].trigger()
    window._on_worker_finished(_store())
    window._cleanup_worker()
    qapp.processEvents()
    assert not window.plot_panel.file_name_label.isVisible()

    window.add_signals_to_plot(window.store.all_keys())
    qapp.processEvents()
    assert window.plot_panel.file_name_label.isVisible()

    window.plot_panel.clear_all()
    qapp.processEvents()
    assert not window.plot_panel.file_name_label.isVisible()


def test_another_file_names_itself_once_loaded(qapp, window, monkeypatch, tmp_path):
    _open(window, monkeypatch, tmp_path / 'first.csv')
    _load_and_plot(qapp, window)

    _open(window, monkeypatch, tmp_path / 'second.csv')
    # The first file's data is gone with its name; nothing names the second
    # before it is decoded.
    assert window.plot_panel.file_name_label.name() == ''

    _load_and_plot(qapp, window)
    assert window.plot_panel.file_name_label.text() == '   |   second.csv'


def test_long_file_name_is_shortened_and_does_not_hold_the_plot_wide(qapp, window):
    import array
    from core.signal_store import SignalSeries

    panel = window.plot_panel
    ts = array.array('d', (i * 0.1 for i in range(100)))
    panel.add_series('Speed', SignalSeries(None, 'Msg', 1, 'Speed', 'km/h', ts, ts))
    qapp.processEvents()
    without_name = panel.minimumSizeHint().width()

    long_name = 'measurement_' + 'x' * 300 + '_end.blf'
    panel.set_measurement_file('C:/logs/' + long_name)
    qapp.processEvents()

    label = panel.file_name_label
    assert label.isVisible()
    assert panel.minimumSizeHint().width() == without_name
    assert label.geometry().right() <= panel.width()
    assert label.fontMetrics().horizontalAdvance(label.text()) <= label.width()
    assert '…' in label.text()
    assert label.text().startswith('   |   measurement_')
    assert label.text().endswith('_end.blf')
    assert label.toolTip() == 'C:/logs/' + long_name
