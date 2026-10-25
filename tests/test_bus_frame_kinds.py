# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Frames that are not messages, and what a logger records about the bus.

Error frames, remote (RTR) frames and CAN FD's bit rate switch are kept as
flags on the frame (FLAG_ERROR, FLAG_REMOTE, FLAG_BRS). Error and remote
frames were stored as ordinary frames before, so a remote frame decoded as a
message of all zeros; they are never decoded now. Driver statistics, controller
state, error counters and overload frames are kept as BusStatus records.

No measurement on hand holds any of these, so each test writes its own file
from the documented layouts: BLF through python-can's writer, ASC as text,
MF4 through asammdf.
"""
from __future__ import annotations

import struct

import numpy as np
import pytest

from core.raw_frame_store import FLAG_BRS, FLAG_ERROR, FLAG_FD, FLAG_REMOTE

ENGINE = 0x100     # EngineControl in sample.dbc: 8 bytes, EngSpeed in bytes 0-1


# ── Reading a file into store columns ────────────────────────────────────


def _blf_rows(path, per_object=False):
    """Every frame of a BLF, both ways the app can read it, as column lists,
    and the reader's bus status records."""
    from core.blf_reader import BLFReaderService

    service = BLFReaderService(path)
    batches = (service.iter_raw_batches_per_object() if per_object
               else service.iter_raw_batches())
    columns = {'ts': [], 'ch': [], 'id': [], 'dlc': [], 'flags': []}
    for base, ts, ch, ids, dlcs, _dirs, flags, _data in batches:
        columns['ts'] += [base + float(t) for t in ts]
        columns['ch'] += list(np.asarray(ch).tolist())
        columns['id'] += list(np.asarray(ids).tolist())
        columns['dlc'] += list(np.asarray(dlcs).tolist())
        columns['flags'] += list(np.asarray(flags).tolist())
    return columns, service.bus_status


def _message(**kwargs):
    import can
    kwargs.setdefault('is_extended_id', False)
    kwargs.setdefault('channel', 0)
    return can.Message(**kwargs)


# ── BLF ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def blf_kinds(tmp_path):
    """A data frame, a remote frame, an error frame, CAN FD with and without
    BRS, and every object python-can does not write for itself."""
    import can
    from can.io import blf

    path = tmp_path / 'kinds.blf'
    writer = can.BLFWriter(str(path))
    t0 = 1_700_000_000.0
    writer.on_message_received(_message(timestamp=t0, arbitration_id=ENGINE,
                                        data=bytes([0x10, 0x27, 0, 0, 0, 0, 0, 0])))
    writer.on_message_received(_message(timestamp=t0 + 0.1, arbitration_id=ENGINE,
                                        is_remote_frame=True, dlc=8))
    writer.on_message_received(_message(timestamp=t0 + 0.2, arbitration_id=ENGINE,
                                        is_error_frame=True, data=bytes(8)))
    writer.on_message_received(_message(timestamp=t0 + 0.3, arbitration_id=0x300,
                                        is_fd=True, bitrate_switch=True, data=bytes(12)))
    writer.on_message_received(_message(timestamp=t0 + 0.4, arbitration_id=0x301,
                                        is_fd=True, bitrate_switch=False, data=bytes(12)))
    # CAN_FD_MESSAGE_64 with BRS (0x2000) on channel 2.
    fd64 = blf.CAN_FD_MSG_64_STRUCT.pack(2, 9, 12, 0, 0x302, 0, 0x1000 | 0x2000,
                                         0, 0, 0, 0, 0, 0, 0, 0)
    writer._add_object(blf.CAN_FD_MESSAGE_64, fd64 + bytes(12), t0 + 0.5)
    # CAN_ERROR (2) and CAN_FD_ERROR_64 (104).
    writer._add_object(2, struct.pack('<HHL', 1, 0, 0), t0 + 0.6)
    writer._add_object(104, struct.pack('<BBBBHHHBBL', 2, 0, 0, 0, 0, 0, 0, 0, 0, 0x55)
                       + bytes(60), t0 + 0.7)
    # Bus status: overload (3), driver error (31), driver statistic (72),
    # driver error ext (74).
    writer._add_object(3, struct.pack('<HHL', 1, 0, 0), t0 + 0.8)
    writer._add_object(31, struct.pack('<HBBL', 1, 130, 5, 7), t0 + 0.9)
    writer._add_object(72, struct.pack('<HHLLLLLLL', 1, 4250, 100, 20, 3, 1, 2, 0, 0), t0 + 1.0)
    writer._add_object(74, struct.pack('<HBBLLBBH16x', 2, 0, 99, 0, 0, 6, 0, 0), t0 + 1.1)
    writer.stop()
    return path


@pytest.mark.parametrize('per_object', [False, True], ids=['bulk', 'per-object'])
def test_blf_frame_kinds_are_flagged(blf_kinds, per_object):
    rows, _status = _blf_rows(blf_kinds, per_object)
    flags = rows['flags']

    assert len(flags) == 8
    data, remote, error, brs, plain_fd, fd64, error2, fd_error = flags
    assert not data & (FLAG_REMOTE | FLAG_ERROR)
    assert remote & FLAG_REMOTE and rows['dlc'][1] == 8
    assert error & FLAG_ERROR
    assert brs & FLAG_BRS and brs & FLAG_FD
    assert plain_fd & FLAG_FD and not plain_fd & FLAG_BRS
    assert fd64 & FLAG_BRS and fd64 & FLAG_FD and rows['ch'][5] == 2
    assert error2 & FLAG_ERROR and rows['ch'][6] == 1
    assert fd_error & FLAG_ERROR and rows['id'][7] == 0x55 and rows['ch'][7] == 2


@pytest.mark.parametrize('per_object', [False, True], ids=['bulk', 'per-object'])
def test_blf_bus_status_records(blf_kinds, per_object):
    _rows, status = _blf_rows(blf_kinds, per_object)

    kinds = [(record.kind, record.channel) for record in status]
    assert kinds == [('overload', 1), ('controller', 1), ('statistics', 1), ('controller', 2)]
    controller, statistics = status[1], status[2]
    assert controller.values['tec'] == 130 and controller.values['rec'] == 5
    assert controller.values['state'] == 'error passive'
    assert statistics.values['bus_load'] == pytest.approx(42.5)
    assert (statistics.values['std_data'], statistics.values['ext_data'],
            statistics.values['std_remote'], statistics.values['error_frames']) == (100, 20, 3, 2)
    assert status[3].values['state'] == 'warning' and status[3].values['state_code'] == 6
    assert status[0].time_s == pytest.approx(1_700_000_000.8, abs=1e-3)


def test_both_blf_readers_agree(blf_kinds):
    bulk, bulk_status = _blf_rows(blf_kinds)
    per_object, per_object_status = _blf_rows(blf_kinds, per_object=True)
    assert bulk == per_object
    assert bulk_status == per_object_status


def test_a_truncated_status_object_is_skipped(tmp_path):
    import can

    path = tmp_path / 'short.blf'
    writer = can.BLFWriter(str(path))
    writer.on_message_received(_message(timestamp=1.0, arbitration_id=ENGINE, data=bytes(8)))
    writer._add_object(72, struct.pack('<HH', 1, 100), 1.1)   # 4 of 32 bytes
    writer.stop()
    for per_object in (False, True):
        rows, status = _blf_rows(path, per_object)
        assert len(rows['flags']) == 1 and status == []


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


def test_blf_error_and_remote_frames_are_not_decoded(blf_kinds, sample_dbc_path):
    store = _load(blf_kinds, sample_dbc_path)
    try:
        speed = [store.get_series(key) for key in store.all_keys()
                 if key.endswith('EngineControl::EngSpeed')]
        assert len(speed) == 1
        # Only the data frame: 0x2710 * 0.5 = 5000 rpm, not a zero from the
        # remote frame or the error frame.
        assert list(speed[0].values) == [5000.0]
        rfs = store.raw_frame_store
        assert len(rfs) == 8
        assert [record.kind for record in rfs.bus_status] == [
            'overload', 'controller', 'statistics', 'controller']
        # On the frames' time base: the first frame is at 0.
        assert rfs.bus_status[0].time_s == pytest.approx(0.8, abs=1e-3)
    finally:
        store.raw_frame_store.close()


# ── ASC ──────────────────────────────────────────────────────────────────

# CANalyzer's own layout: an 11-character time, fixed CAN FD columns.
_ASC = '''date Sat Oct 25 10:00:00.000 am 2026
base hex  timestamps absolute
internal events logged
Begin Triggerblock Sat Oct 25 10:00:00.000 am 2026
   0.000000 Start of measurement
   0.010000 1  100             Rx   d 8 10 27 00 00 00 00 00 00
   0.020000 1  100             Rx   r 8
   0.030000 1  ErrorFrame
   0.040000 CANFD   1 Rx        300                                   1 0 9 12 00 00 00 00 00 00 00 00 00 00 00 00        0    0     3000        0        0        0        0        0
   0.050000 CANFD   2 Rx        301                                   0 0 9 12 00 00 00 00 00 00 00 00 00 00 00 00        0    0     1000        0        0        0        0        0
   0.060000 1  Statistic: D 81 R 1 XD 4 XR 0 E 2 O 0 B 9.53%
   0.070000 CAN 1 Status:chip status error passive - TxErr: 131 RxErr: 4
   0.080000 CANFD   2 Rx ErrorFrame
End TriggerBlock
'''


@pytest.fixture()
def asc_kinds(tmp_path):
    path = tmp_path / 'kinds.asc'
    path.write_text(_ASC.replace('\n', '\r\n'), encoding='ascii')
    return path


def _asc_rows(path):
    from core.dbc_decoder import DBCDecoder
    from core.readers.asc_can_reader import ASCCANReader
    from pathlib import Path

    dbc = Path(__file__).parent / 'fixtures' / 'sample.dbc'
    reader = ASCCANReader(path, DBCDecoder(str(dbc)))
    columns = {'ch': [], 'id': [], 'dlc': [], 'flags': []}
    for _base, _ts, ch, ids, dlcs, _dirs, flags, _data in reader.iter_raw_batches():
        columns['ch'] += list(ch)
        columns['id'] += list(ids)
        columns['dlc'] += list(dlcs)
        columns['flags'] += list(flags)
    return columns, reader.bus_status


def test_asc_frame_kinds_are_flagged(asc_kinds):
    rows, _status = _asc_rows(asc_kinds)
    data, remote, error, brs, plain_fd, fd_error = rows['flags']

    assert not data & (FLAG_REMOTE | FLAG_ERROR)
    assert remote & FLAG_REMOTE and rows['dlc'][1] == 8
    assert error & FLAG_ERROR and rows['ch'][2] == 1
    assert brs & FLAG_BRS and brs & FLAG_FD
    assert plain_fd & FLAG_FD and not plain_fd & FLAG_BRS
    assert fd_error & FLAG_ERROR and fd_error & FLAG_FD and rows['ch'][5] == 2


def test_asc_statistic_and_status_lines(asc_kinds):
    _rows, status = _asc_rows(asc_kinds)

    statistics, controller = status
    assert (statistics.kind, statistics.channel) == ('statistics', 1)
    # Counts are decimal even in a 'base hex' file.
    assert statistics.values == {'std_data': 81, 'std_remote': 1, 'ext_data': 4,
                                 'ext_remote': 0, 'error_frames': 2, 'overload_frames': 0,
                                 'bus_load': pytest.approx(9.53)}
    assert (controller.kind, controller.channel) == ('controller', 1)
    assert controller.values == {'state': 'error passive', 'tec': 131, 'rec': 4}


def test_asc_written_by_python_can(tmp_path):
    """python-can's own ASC layout: a shorter time column, so the general parser."""
    import can

    path = tmp_path / 'pycan.asc'
    writer = can.ASCWriter(str(path))
    writer.on_message_received(_message(timestamp=1.0, arbitration_id=0x300, is_fd=True,
                                        bitrate_switch=True, data=bytes(12)))
    writer.on_message_received(_message(timestamp=1.1, arbitration_id=0x55,
                                        is_remote_frame=True, dlc=6))
    writer.on_message_received(_message(timestamp=1.2, is_error_frame=True))
    writer.stop()

    rows, _status = _asc_rows(path)
    brs, remote, error = rows['flags']
    assert brs & FLAG_BRS
    assert remote & FLAG_REMOTE and rows['dlc'][1] == 6
    assert error & FLAG_ERROR


def test_asc_error_and_remote_frames_are_not_decoded(asc_kinds, sample_dbc_path):
    store = _load(asc_kinds, sample_dbc_path)
    try:
        speed = [store.get_series(key) for key in store.all_keys()
                 if key.endswith('EngineControl::EngSpeed')]
        assert len(speed) == 1 and list(speed[0].values) == [5000.0]
        rfs = store.raw_frame_store
        assert [record.kind for record in rfs.bus_status] == ['statistics', 'controller']
        # On the frames' time base: the first frame (0.010 s) is at 0.
        assert rfs.bus_status[0].time_s == pytest.approx(0.050)
    finally:
        store.raw_frame_store.close()


# ── MF4 (ASAM MDF bus logging) ───────────────────────────────────────────


def _mdf_source():
    """Data frames (one with BRS), an error frame group and a remote frame
    group, as the ASAM bus-logging layout names them."""
    from types import SimpleNamespace

    groups = {
        'CAN_DataFrame': np.array(
            [(1, 0x100, 0, 8, [0x10, 0x27, 0, 0, 0, 0, 0, 0], 0, 0, 0),
             (2, 0x300, 0, 12, [0] * 8, 0, 1, 1)],
            dtype=[('CAN_DataFrame.BusChannel', 'u1'), ('CAN_DataFrame.ID', '<u4'),
                   ('CAN_DataFrame.IDE', 'u1'), ('CAN_DataFrame.DataLength', 'u1'),
                   ('CAN_DataFrame.DataBytes', 'u1', (8,)), ('CAN_DataFrame.Dir', 'u1'),
                   ('CAN_DataFrame.EDL', 'u1'), ('CAN_DataFrame.BRS', 'u1')]),
        'CAN_ErrorFrame': np.array(
            [(1, 3)],
            dtype=[('CAN_ErrorFrame.BusChannel', 'u1'), ('CAN_ErrorFrame.ErrorType', 'u1')]),
        'CAN_RemoteFrame': np.array(
            [(1, 0x100, 0, 8, 0)],
            dtype=[('CAN_RemoteFrame.BusChannel', 'u1'), ('CAN_RemoteFrame.ID', '<u4'),
                   ('CAN_RemoteFrame.IDE', 'u1'), ('CAN_RemoteFrame.DLC', 'u1'),
                   ('CAN_RemoteFrame.Dir', 'u1')]),
    }
    names = list(groups)
    source_groups = [
        SimpleNamespace(channels=[SimpleNamespace(name='Timestamp'), SimpleNamespace(name=n)])
        for n in names
    ]

    def get(group, **_kwargs):
        samples = groups[names[group]]
        return SimpleNamespace(samples=samples,
                               timestamps=np.arange(len(samples), dtype=float) + group)

    return SimpleNamespace(groups=source_groups, get=get)


def test_mf4_frame_kinds_are_flagged():
    from core.readers.mdf_can_reader import MDFCANReader

    batches = []
    count = MDFCANReader._emit_raw_frame_arrays(_mdf_source(),
                                                lambda *columns: batches.append(columns))
    assert count == 4
    flags = [int(f) for batch in batches for f in batch[5]]
    ids = [int(i) for batch in batches for i in batch[2]]
    dlcs = [int(d) for batch in batches for d in batch[3]]

    data, brs, error, remote = flags
    assert not data & (FLAG_REMOTE | FLAG_ERROR | FLAG_BRS)
    assert brs & FLAG_BRS and brs & FLAG_FD
    assert error & FLAG_ERROR and ids[2] == 0
    assert remote & FLAG_REMOTE and ids[3] == 0x100 and dlcs[3] == 8


def test_mf4_frame_groups_without_brs_or_error_groups_read_as_before():
    """A file with only CAN_DataFrame and no BRS column: unchanged."""
    from types import SimpleNamespace
    from core.readers.mdf_can_reader import MDFCANReader

    samples = np.array(
        [(1, 0x100, 0, 8, [0] * 8)],
        dtype=[('CAN_DataFrame.BusChannel', 'u1'), ('CAN_DataFrame.ID', '<u4'),
               ('CAN_DataFrame.IDE', 'u1'), ('CAN_DataFrame.DataLength', 'u1'),
               ('CAN_DataFrame.DataBytes', 'u1', (8,))])
    source = SimpleNamespace(
        groups=[SimpleNamespace(channels=[SimpleNamespace(name='CAN_DataFrame')])],
        get=lambda **_k: SimpleNamespace(samples=samples, timestamps=np.array([0.0])))
    batches = []
    MDFCANReader._emit_raw_frame_arrays(source, lambda *columns: batches.append(columns))
    assert batches[0][5].tolist() == [0]


# ── CSV (ASAM data-frame columns) ────────────────────────────────────────


def test_csv_brs_and_rtr_columns_are_flagged(tmp_path):
    from core.dbc_decoder import DBCDecoder
    from core.readers.csv_reader import CSVRawCANReader
    from pathlib import Path

    path = tmp_path / 'kinds.csv'
    path.write_text(
        'TimestampEpoch;BusChannel;ID;IDE;DLC;DataLength;Dir;EDL;BRS;ESI;RTR;DataBytes\n'
        '1.0;1;100;0;8;8;0;0;0;0;0;1027000000000000\n'
        '1.1;1;100;0;8;8;0;0;0;0;1;\n'
        '1.2;2;300;0;9;12;0;1;1;0;0;000000000000000000000000\n',
        encoding='utf-8')
    dbc = Path(__file__).parent / 'fixtures' / 'sample.dbc'
    reader = CSVRawCANReader(path, DBCDecoder(str(dbc)))
    flags = [f for batch in reader.iter_raw_batches() for f in batch[6]]

    data, remote, brs = flags
    assert not data & (FLAG_REMOTE | FLAG_BRS)
    assert remote & FLAG_REMOTE
    assert brs & FLAG_BRS and brs & FLAG_FD
