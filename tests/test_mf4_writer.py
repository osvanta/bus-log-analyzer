# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Decoded signals saved as MF4, and the file opened again.

The saved file must reopen with the same signal keys ("CH1::Message::Signal"),
values, units and labels, so a plot configuration saved on the original
measurement works on the MF4. Only MF4s this application wrote get their bus
channel back: any other MDF keeps the "CH?" keys it always had.
"""
from __future__ import annotations

import numpy as np
import pytest


def _load(path, dbc=None):
    from core.bus_types import BusType, all_channels_key
    from core.channel_config import ChannelConfig
    from gui.load_worker import LoadWorker

    bus = BusType.LIN if dbc and str(dbc).lower().endswith('.ldf') else BusType.CAN
    config = ChannelConfig(channels={all_channels_key(bus): str(dbc)}) if dbc else ChannelConfig()
    worker = LoadWorker(str(path), config)
    stores, failures = [], []
    worker.finished.connect(stores.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures
    return stores[0]


def _close(store):
    if getattr(store, 'raw_frame_store', None) is not None:
        store.raw_frame_store.close()
    cache = getattr(store, 'sample_cache', None)
    if cache is not None and hasattr(cache, 'close'):
        cache.close()


@pytest.fixture()
def decoded(asc_path, sample_dbc_path):
    store = _load(asc_path, sample_dbc_path)
    yield store
    _close(store)


def _series(store):
    return [store.get_series(key) for key in store.all_keys()]


def test_the_saved_file_reopens_with_the_same_keys_values_units_and_labels(decoded, tmp_path):
    from core.mf4_writer import write_mf4

    path = tmp_path / 'decoded.mf4'
    written = write_mf4(_series(decoded), path, base_ts=decoded.base_ts)
    reopened = _load(path)
    try:
        assert written == len(decoded.all_keys())
        assert reopened.all_keys() == decoded.all_keys()
        assert not any(key.startswith('CH?::') for key in reopened.all_keys())
        for key in decoded.all_keys():
            before, after = decoded.get_series(key), reopened.get_series(key)
            assert after.unit == before.unit
            assert np.allclose(after.numpy_values(), before.numpy_values(), equal_nan=True)
            assert np.allclose(after.numpy_timestamps() - after.numpy_timestamps()[0],
                               before.numpy_timestamps() - before.numpy_timestamps()[0])
        gear_key = next(k for k in decoded.all_keys() if k.endswith('::Gear'))
        assert list(reopened.get_series(gear_key).raw_values) == \
            list(decoded.get_series(gear_key).raw_values)
    finally:
        _close(reopened)


def test_one_group_per_message_when_timestamps_are_shared(decoded, tmp_path):
    from asammdf import MDF
    from core.mf4_writer import SOURCE_NAME, write_mf4

    path = tmp_path / 'decoded.mf4'
    write_mf4(_series(decoded), path)
    messages = {key.split('::')[1] for key in decoded.all_keys()}
    with MDF(str(path)) as mdf:
        names = [g.channel_group.acq_name for g in mdf.groups]
        sources = {(g.channel_group.acq_source.name, g.channel_group.acq_source.path)
                   for g in mdf.groups}
    assert sorted(names) == sorted(messages)
    channels = {'CAN' + key.split('::')[0][2:] for key in decoded.all_keys()}
    assert sources == {(SOURCE_NAME, channel) for channel in channels}


def test_signals_of_one_message_with_own_timestamps_get_groups_of_their_own(tmp_path):
    from asammdf import MDF
    from core.mf4_writer import write_mf4
    from core.signal_store import SignalSeries

    a = SignalSeries((None), 'Msg', 1, 'A', '', np.array([0.0, 1.0]), np.array([1.0, 2.0]))
    b = SignalSeries((None), 'Msg', 1, 'B', '', np.array([0.5, 1.5]), np.array([3.0, 4.0]))
    path = tmp_path / 'two.mf4'
    write_mf4([a, b], path)
    with MDF(str(path)) as mdf:
        assert len(mdf.groups) == 2


def test_another_tools_mf4_keeps_its_ch_question_keys(tmp_path):
    """A group whose source is not this application loads as before."""
    from asammdf import MDF, Signal
    from asammdf.blocks import v4_constants as v4c
    from asammdf.blocks.source_utils import Source

    path = tmp_path / 'other.mf4'
    mdf = MDF(version='4.10')
    mdf.append([Signal(np.array([1.0, 2.0]), np.array([0.0, 1.0]), name='Speed', unit='km/h')],
               acq_name='VehicleSpeed',
               acq_source=Source('asammdf', 'CAN1', '', v4c.SOURCE_BUS, v4c.BUS_TYPE_CAN))
    mdf.save(str(path), overwrite=True)
    mdf.close()
    store = _load(path)
    try:
        assert store.all_keys() == ['CH?::VehicleSpeed::Speed']
    finally:
        _close(store)


def test_a_cancelled_save_writes_nothing(decoded, tmp_path):
    from core.mf4_writer import Cancelled, write_mf4

    def cancel(done, total):
        raise Cancelled

    path = tmp_path / 'never.mf4'
    with pytest.raises(Cancelled):
        write_mf4(_series(decoded), path, progress=cancel)
    assert not path.exists()


def test_the_recording_start_goes_into_the_header(decoded, tmp_path):
    from asammdf import MDF
    from core.mf4_writer import write_mf4

    path = tmp_path / 'decoded.mf4'
    write_mf4(_series(decoded), path, base_ts=1_700_000_000.0)
    with MDF(str(path)) as mdf:
        assert abs(mdf.header.start_time.timestamp() - 1_700_000_000.0) < 1


def test_bus_paths():
    from core.bus_types import BusType
    from core.mf4_writer import bus_path
    assert bus_path((BusType.CAN, 2)) == 'CAN2'
    assert bus_path((BusType.LIN, 1)) == 'LIN1'
    assert bus_path(None) == ''


@pytest.fixture()
def can_mf4_path(tmp_path):
    """A CAN bus-logging MF4, written by python-can: EngineControl frames."""
    import can
    path = tmp_path / 'bus.mf4'
    writer = can.MF4Writer(str(path))
    for i in range(20):
        writer.on_message_received(can.Message(
            timestamp=1.0 + i * 0.1, arbitration_id=0x100, is_extended_id=False,
            data=bytes([0x10, 0x27, 0x40, 0, 0, 0, 0, 0]), channel=0))
    writer.stop()
    return path


@pytest.mark.parametrize('fixture, database', [
    ('blf_path', 'sample_dbc_path'),
    ('raw_can_csv_path', 'sample_dbc_path'),
    ('can_mf4_path', 'sample_dbc_path'),
], ids=['blf', 'csv', 'mf4-bus-logging'])
def test_every_format_round_trips(request, tmp_path, fixture, database):
    """Rule 08: the same round trip from each measurement format."""
    from core.mf4_writer import write_mf4

    store = _load(request.getfixturevalue(fixture), request.getfixturevalue(database))
    path = tmp_path / 'decoded.mf4'
    try:
        assert store.all_keys()
        write_mf4(_series(store), path, base_ts=store.base_ts)
        reopened = _load(path)
        try:
            assert reopened.all_keys() == store.all_keys()
            for key in store.all_keys():
                assert np.allclose(reopened.get_series(key).numpy_values(),
                                   store.get_series(key).numpy_values(), equal_nan=True)
        finally:
            _close(reopened)
    finally:
        _close(store)


# ── In the window ────────────────────────────────────────────────────────


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    yield w
    w.close()
    qapp.processEvents()


def _save_to(monkeypatch, path):
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(path), ''))


def test_save_decoded_writes_every_decoded_signal(window, decoded, asc_path, tmp_path, monkeypatch):
    from asammdf import MDF

    window.store, window.measurement_path = decoded, str(asc_path)
    target = tmp_path / 'saved'
    _save_to(monkeypatch, target)
    window.save_decoded_as_mf4()
    with MDF(str(target) + '.mf4') as mdf:
        names = sorted(c.name for g in mdf.groups for c in g.channels if c.name != 'time')
    assert names == sorted(key.split('::')[2] for key in decoded.all_keys())


def test_save_decoded_refuses_the_open_measurement(window, decoded, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    source = tmp_path / 'open.mf4'
    source.write_bytes(b'original')
    window.store, window.measurement_path = decoded, str(source)
    _save_to(monkeypatch, source)
    window.save_decoded_as_mf4()
    assert source.read_bytes() == b'original'
    assert QMessageBox.warning.call_args[0][1] == 'Not saved'


def test_export_offers_mf4(window, decoded, tmp_path):
    from asammdf import MDF

    window.store = decoded
    labels = [label for label, *_ in window._export_formats()]
    assert labels[-1].startswith('MF4')
    *_, handler = window._export_formats()[-1]
    series = [decoded.get_series(k) for k in decoded.all_keys()][:2]
    assert handler(series, str(tmp_path / 'two.mf4'))
    with MDF(str(tmp_path / 'two.mf4')) as mdf:
        assert sum(len(g.channels) - 1 for g in mdf.groups) == 2
