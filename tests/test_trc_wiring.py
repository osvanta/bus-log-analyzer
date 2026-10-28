# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
TRC trace files through the rest of the application: chosen by the reader
factory, pre-scanned for the Database Manager, offered by Open Measurement,
described by Measurement info, converted by Convert files — and, loaded and
decoded, the same as the same frames read from an ASC log.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest

from core.bus_types import BusType
from core.raw_frame_store import FLAG_ERROR, FLAG_REMOTE

COLUMNS = ';$COLUMNS=N,O,T,B,I,d,R,L,D'


def _write(path, lines):
    path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('latin-1'))
    return path


# ── Reader factory and databases ─────────────────────────────────────────

def test_the_factory_reads_a_trc_with_a_database(trc_path, sample_dbc_path):
    from core.readers import reader_factory
    from core.readers.trc_can_reader import TRCCANReader

    reader = reader_factory(str(trc_path), str(sample_dbc_path))
    assert isinstance(reader, TRCCANReader)
    assert reader.source_description.startswith('TRC 2.1 + DBC')


def test_a_trc_needs_a_database_and_never_an_ldf(trc_path):
    from core.readers import (
        database_mandatory_for, dbc_required_for, reader_factory,
    )
    from tests.conftest import FIXTURES_DIR

    assert dbc_required_for(str(trc_path)) and database_mandatory_for(str(trc_path))
    with pytest.raises(ValueError, match='required for TRC'):
        reader_factory(str(trc_path))
    with pytest.raises(ValueError, match='LDF'):
        reader_factory(str(trc_path), str(FIXTURES_DIR / 'sample.ldf'))


def test_the_prescan_finds_channels_and_ids_but_not_error_frames(tmp_path):
    from core.readers import prescan_measurement

    path = _write(tmp_path / 'scan.trc', [';$FILEVERSION=2.1', COLUMNS,
        '       1      1000.000 DT 1      0100 Rx -  1    01',
        '       2      1001.000 ER 1         - Rx -  5    04 00 02 00 00',
        '       3      1002.000 RR 2      0055 Rx -  6',
        '       4      1003.000 DT 2  18FEF100 Tx -  1    02',
    ])
    result = prescan_measurement(str(path))
    assert result.channels == [(BusType.CAN, 1), (BusType.CAN, 2)]
    assert result.ids_per_channel == {(BusType.CAN, 1): {0x100},
                                      (BusType.CAN, 2): {0x55, 0x18FEF100}}


def test_a_trc_of_another_program_is_refused_wherever_it_is_read(tmp_path, sample_dbc_path):
    """Load, Measurement info and Convert files all say why, rather than
    loading nothing. The pre-scan, as for every format, finds nothing and
    leaves the saying to Load."""
    from core.channel_config import ChannelConfig
    from core.converter import ConvertJob, convert
    from core.measurement_info import read_file_info
    from core.readers import prescan_measurement
    from core.readers.trc_can_reader import TRCReadError
    from gui.load_worker import LoadWorker

    path = _write(tmp_path / 'other.trc', ['@ TEXT @ 3 @ 64 @ 0 @ 2 @ 2 @ 00:00:00.002 @',
        '0,001000\t1\t00000004\t123\t2\t01 02\t00000000\t..',
        '0,002000\t1\t00000004\t124\t1\t03\t00000000\t.',
    ])
    refused = "'other.trc' is not a PEAK trace"
    assert prescan_measurement(str(path)).channels == []
    worker = LoadWorker(str(path), ChannelConfig.from_single_dbc(str(sample_dbc_path)))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert len(failures) == 1 and refused in failures[0]
    assert any(refused in note for note in read_file_info(path).notes)
    with pytest.raises(TRCReadError, match=refused):
        convert(ConvertJob([path], tmp_path / 'out.asc'))


# ── Open Measurement ─────────────────────────────────────────────────────

def test_open_measurement_offers_trc_files(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    window = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    asked = Mock(return_value=('', ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', asked)
    try:
        window.choose_blf()
        filters = asked.call_args.args[3].split(';;')
        assert 'PEAK TRC (*.trc)' in filters
        assert '*.trc' in filters[0] and '*.trc' in filters[-1]
    finally:
        window.deleteLater()
        qapp.processEvents()


# ── Measurement info ─────────────────────────────────────────────────────

def test_measurement_info_shows_the_trace_header_and_events(tmp_path):
    import datetime as dt

    from core.measurement_info import read_file_info

    start = dt.datetime(2024, 1, 15, 10, 0, 0)
    days = (start - dt.datetime(1899, 12, 30)).total_seconds() / 86400
    path = _write(tmp_path / 'info.trc', [
        ';$FILEVERSION=2.1', f';$STARTTIME={days:.10f}', COLUMNS,
        ';   Generated by a test tool v1.2.3',
        ';-------------------------------------------------------------------------------',
        ';   Bus  Name          Connection             Protocol',
        ';   1    Powertrain    PT@pcan_usb            CAN',
        ';-------------------------------------------------------------------------------',
        '       1      1000.000 DT 1      0100 Rx -  1    01',
        '       2      1500.000 EV 1  Ignition on',
    ])
    info = read_file_info(path)
    assert info.format == 'TRC (PEAK trace)'
    assert info.start == pytest.approx(start, abs=dt.timedelta(microseconds=10))
    assert ('TRC version', '2.1') in info.header
    assert ('Generated by', 'a test tool v1.2.3') in info.header
    assert ('Bus 1', 'Powertrain, PT@pcan_usb, CAN') in info.header
    assert [(e.time_s, e.name, e.comment) for e in info.events] == [
        (1.5, 'Ignition on', 'Bus 1')]
    assert info.notes == []


# ── Convert files ────────────────────────────────────────────────────────

def _frames(path):
    import can
    reader = {'.blf': can.BLFReader, '.asc': can.ASCReader, '.mf4': can.MF4Reader}[path.suffix](str(path))
    try:
        return [(m.arbitration_id, bytes(m.data), m.is_remote_frame, m.is_error_frame)
                for m in reader]
    finally:
        if hasattr(reader, 'stop'):
            reader.stop()


@pytest.mark.parametrize('target', ['.blf', '.asc', '.mf4'])
def test_convert_files_takes_a_trc(tmp_path, trc_path, target):
    from core.converter import ConvertJob, convert

    out = convert(ConvertJob([trc_path], tmp_path / f'out{target}'))
    frames = _frames(out)
    assert [arb_id for arb_id, *_ in frames] == [0x100, 0x200, 0x300] * 3
    assert frames[0][1] == bytes([0x60, 0x09, 0x64, 0, 0, 0, 0, 0])


def test_convert_files_reads_versions_python_can_cannot(tmp_path):
    """python-can's TRCReader fails on 1.2; Convert files reads it through
    the application's own reader, and keeps the bus."""
    import can

    from core.converter import ConvertJob, convert

    trc = _write(tmp_path / 'v12.trc', [';$FILEVERSION=1.2', ';$STARTTIME=45306.4166666667',
        '     1)      1000.000  2  Rx         0123  2  01 02',
        '     2)      1000.500  2  Tx         0124  1  03',
    ])
    out = convert(ConvertJob([trc], tmp_path / 'v12.asc'))
    with can.ASCReader(str(out)) as reader:
        messages = list(reader)
    assert [(m.arbitration_id, bytes(m.data), m.channel) for m in messages] == [
        (0x123, b'\x01\x02', 1), (0x124, b'\x03', 1)]     # python-can: 0-based


def test_convert_files_cuts_trc_by_time_and_joins_them(tmp_path, trc_path):
    from core.converter import ConvertJob, convert

    cut = convert(ConvertJob([trc_path], tmp_path / 'cut.asc', start=0.005, end=0.015))
    assert [arb_id for arb_id, *_ in _frames(cut)] == [0x100, 0x200, 0x300]
    joined = convert(ConvertJob([trc_path, trc_path], tmp_path / 'joined.blf'))
    assert len(_frames(joined)) == 18


# ── The same frames as TRC and as ASC load the same ──────────────────────

def _load(path, dbc):
    from core.channel_config import ChannelConfig
    from gui.load_worker import LoadWorker

    worker = LoadWorker(str(path), ChannelConfig.from_single_dbc(str(dbc)))
    stores, failures = [], []
    worker.finished.connect(stores.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures
    return stores[0]


def _trace(store):
    rfs = store.raw_frame_store
    records = rfs.get_window(range(len(rfs)))
    return [(round(r.time_s, 6), r.channel, r.arbitration_id, r.dlc, r.is_extended,
             r.is_fd, r.data) for r in records], np.asarray(rfs.flags).tolist()


def test_trc_and_asc_of_the_same_frames_decode_the_same(trc_path, asc_path, sample_dbc_path):
    trc, asc = _load(trc_path, sample_dbc_path), _load(asc_path, sample_dbc_path)
    try:
        assert sorted(trc.all_keys()) == sorted(asc.all_keys())
        assert any(key.endswith('EngineControl::EngSpeed') for key in trc.all_keys())
        for key in asc.all_keys():
            a, t = asc.get_series(key), trc.get_series(key)
            assert np.allclose(t.timestamps, a.timestamps, atol=1e-6), key
            assert list(t.values) == list(a.values), key
        assert _trace(trc) == _trace(asc)
    finally:
        trc.raw_frame_store.close()
        asc.raw_frame_store.close()


def test_trc_and_asc_keep_error_and_remote_frames_alike(tmp_path, sample_dbc_path):
    import can

    asc = tmp_path / 'kinds.asc'
    writer = can.ASCWriter(str(asc))
    writer.on_message_received(can.Message(timestamp=1.000, arbitration_id=0x100, channel=0,
                                           is_extended_id=False, data=bytes.fromhex('1027000000000000')))
    writer.on_message_received(can.Message(timestamp=1.010, arbitration_id=0x55, channel=0,
                                           is_extended_id=False, is_remote_frame=True, dlc=6))
    writer.on_message_received(can.Message(timestamp=1.020, channel=0, is_error_frame=True))
    writer.on_message_received(can.Message(timestamp=1.030, arbitration_id=0x100, channel=0,
                                           is_extended_id=False, data=bytes.fromhex('2027000000000000')))
    writer.stop()
    trc = _write(tmp_path / 'kinds.trc', [';$FILEVERSION=2.1', COLUMNS,
        '       1      1000.000 DT 1      0100 Rx -  8    10 27 00 00 00 00 00 00',
        '       2      1010.000 RR 1      0055 Rx -  6',
        '       3      1020.000 ER 1         - Rx -  5    04 00 02 00 00',
        '       4      1030.000 DT 1      0100 Rx -  8    20 27 00 00 00 00 00 00',
    ])
    from_trc, from_asc = _load(trc, sample_dbc_path), _load(asc, sample_dbc_path)
    try:
        rows_trc, flags_trc = _trace(from_trc)
        rows_asc, flags_asc = _trace(from_asc)
        assert rows_trc == rows_asc
        kinds = FLAG_ERROR | FLAG_REMOTE
        assert [f & kinds for f in flags_trc] == [f & kinds for f in flags_asc] == [
            0, FLAG_REMOTE, FLAG_ERROR, 0]
        speed = [key for key in from_trc.all_keys() if key.endswith('EngineControl::EngSpeed')]
        # 0x2710 and 0x2720 at 0.5 rpm a bit; nothing from the remote or error frame.
        assert list(from_trc.get_series(speed[0]).values) == [5000.0, 5008.0]
    finally:
        from_trc.raw_frame_store.close()
        from_asc.raw_frame_store.close()


def test_what_a_trc_load_skips_is_reported(tmp_path, sample_dbc_path):
    path = _write(tmp_path / 'skips.trc', [';$FILEVERSION=3.0', ';$COLUMNS=N,O,T,B,I,d,R,V,S,A,r,s,L,D',
        '        1      1000.000 DT 1       100 Rx -  -  -  -        - - 8    10 27 00 00 00 00 00 00',
        '        2      1001.000 XL 1       100 Tx -  00 03 00000456 0 0 3    03 11 22 33',
    ])
    store = _load(path, sample_dbc_path)
    try:
        assert store.load_warnings == [
            '1 CAN XL frame(s) are not shown: CAN XL is not supported yet.']
        assert 'CAN XL' in store.diagnostics_text
    finally:
        store.raw_frame_store.close()
