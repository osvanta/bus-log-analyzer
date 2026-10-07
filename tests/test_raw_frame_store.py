# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for core/raw_frame_store.py — append, seal, get_window, match_mask."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from core.raw_frame_store import RawFrameStore


def _populated_store(n: int = 3, sealed: bool = False) -> RawFrameStore:
    store = RawFrameStore()
    for i in range(n):
        store.append(
            timestamp=float(i) * 0.001,
            channel=1,
            arb_id=0x100 + i,
            dlc=8,
            direction="Rx",
            is_extended=False,
            is_fd=False,
            data=bytes([i] * 8),
            frame_name=f"Msg{i}",
            decoded=(i == 0),
        )
    if sealed:
        store.seal()
    return store


# ── append / __len__ ─────────────────────────────────────────────────────

def test_append_increments_len():
    store = _populated_store(3)
    assert len(store) == 3


def test_append_stores_timestamp():
    store = _populated_store(1)
    assert store.timestamps[0] == pytest.approx(0.0)


def test_append_stores_arb_id():
    store = _populated_store(1)
    assert store.arb_ids[0] == 0x100


def test_append_stores_channel():
    store = _populated_store(1)
    assert store.channels[0] == 1


def test_append_none_channel_stored_as_255():
    store = RawFrameStore()
    store.append(
        timestamp=0.0, channel=None, arb_id=0x100, dlc=8,
        direction="Rx", is_extended=False, is_fd=False,
        data=bytes(8), frame_name="", decoded=False,
    )
    assert store.channels[0] == 255


# ── seal / get_window ─────────────────────────────────────────────────────

def test_get_window_returns_records():
    store = _populated_store(3, sealed=True)
    records = store.get_window([0, 1, 2])
    assert len(records) == 3


def test_get_window_record_timestamp():
    store = _populated_store(3, sealed=True)
    rec = store.get_window([0])[0]
    assert rec.time_s == pytest.approx(0.0)


def test_get_window_record_arb_id():
    store = _populated_store(3, sealed=True)
    rec = store.get_window([0])[0]
    assert rec.arbitration_id == 0x100


def test_get_window_record_channel():
    store = _populated_store(3, sealed=True)
    rec = store.get_window([0])[0]
    assert rec.channel == 1


def test_get_window_decoded_flag():
    store = _populated_store(3, sealed=True)
    # Frame 0 was appended with decoded=True
    rec0 = store.get_window([0])[0]
    rec1 = store.get_window([1])[0]
    assert rec0.decoded is True
    assert rec1.decoded is False


def test_get_window_out_of_range_skipped():
    store = _populated_store(3, sealed=True)
    records = store.get_window([99])
    assert records == []


def test_get_window_data_bytes():
    store = _populated_store(1, sealed=True)
    rec = store.get_window([0])[0]
    # Frame 0 data = bytes([0]*8); get up to dlc=8 bytes
    assert rec.data[:1] == bytes([0])


# ── append_raw ────────────────────────────────────────────────────────────

def test_append_raw_increments_len():
    store = RawFrameStore()
    store.append_raw(0.0, 1, 0x100, 8, 0, False, False, bytes(8))
    assert len(store) == 1


def test_append_raw_name_always_empty():
    store = RawFrameStore()
    store.append_raw(0.0, 1, 0x100, 8, 0, False, False, bytes(8))
    store.seal()
    rec = store.get_window([0])[0]
    assert rec.frame_name == ""


def test_append_raw_batch_preserves_metadata_and_payload():
    store = RawFrameStore()
    data = bytearray(2 * 64)
    data[0:3] = b"\x01\x02\x03"
    data[64:66] = b"\xAA\xBB"
    store.append_raw_batch(
        timestamps=[0.0, 0.1],
        channels=[1, 2],
        arb_ids=[0x123, 0x18FEF100],
        dlcs=[3, 2],
        directions=[0, 1],
        flags=[0, 3],
        data_block=data,
    )
    store.seal()

    first, second = store.get_window([0, 1])
    assert first.data == b"\x01\x02\x03"
    assert first.direction == "Rx"
    assert second.data == b"\xAA\xBB"
    assert second.channel == 2
    assert second.direction == "Tx"
    assert second.is_extended is True
    assert second.is_fd is True


def test_append_numpy_batch_preserves_columns_and_pads_payload():
    store = RawFrameStore()
    try:
        store.append_numpy_batch(
            timestamps=np.array([2.0, 2.1], dtype=np.float64),
            channels=np.array([1, 2], dtype=np.uint8),
            arb_ids=np.array([0x123, 0x18FEF100], dtype=np.uint32),
            dlcs=np.array([3, 2], dtype=np.uint8),
            directions=np.array([0, 1], dtype=np.uint8),
            flags=np.array([0, 3], dtype=np.uint8),
            data_rows=np.array([
                [1, 2, 3, 0, 0, 0, 0, 0],
                [0xAA, 0xBB, 0, 0, 0, 0, 0, 0],
            ], dtype=np.uint8),
        )
        store.seal()

        first, second = store.get_window([0, 1])
        assert first.time_s == pytest.approx(2.0)
        assert first.data == b"\x01\x02\x03"
        assert second.channel == 2
        assert second.data == b"\xAA\xBB"
        assert second.direction == "Tx"
        assert second.is_extended is True
        assert second.is_fd is True
    finally:
        store.close()


# ── build_match_mask ──────────────────────────────────────────────────────

def test_match_mask_no_filter_returns_none():
    store = _populated_store(3, sealed=True)
    mask = store.build_match_mask("", None)
    assert mask is None


def test_match_mask_channel_filter():
    store = RawFrameStore()
    store.append(0.0, 1, 0x100, 8, "Rx", False, False, bytes(8), "", False)
    store.append(0.001, 2, 0x200, 8, "Rx", False, False, bytes(8), "", False)
    store.seal()
    mask = store.build_match_mask("", channel_filter=1)
    assert mask is not None
    assert mask[0] is np.bool_(True)
    assert mask[1] is np.bool_(False)


def test_match_mask_empty_store():
    store = RawFrameStore()
    store.seal()
    mask = store.build_match_mask("rx", None)
    assert len(mask) == 0


# ── close / cleanup ───────────────────────────────────────────────────────

def _disappears(path, seconds: float = 5.0) -> bool:
    """Whether *path* is gone within *seconds*.

    Windows deletes a delete-on-close file once its last handle closes, and
    an antivirus scan may hold one a moment after the store's.
    """
    import os
    import time

    deadline = time.monotonic() + seconds
    while os.path.exists(path):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def test_close_removes_temp_file():
    store = _populated_store(1, sealed=True)
    path = store._data_path
    store.close()
    assert _disappears(path)


def test_close_idempotent():
    store = _populated_store(1, sealed=True)
    store.close()
    store.close()   # second close must not raise


_UNCLOSED_STORE_SCRIPT = r"""
import sys, time
sys.path.insert(0, %r)
from core.raw_frame_store import RawFrameStore
store = RawFrameStore()
for number in range(20_000):    # past the 1 MB write buffer, so on disk
    store.append(number * 0.001, 1, 0x100, 8, 'Rx', False, False, bytes(8), 'Msg', True)
store.seal()
import os
print(os.getpid(), store._data_path, flush=True)
time.sleep(60)
"""


def test_the_temp_file_goes_when_the_process_ends_without_close():
    # The window still held the last measurement's store at exit, nothing
    # called close(), and every session left its CAN Trace in the temp
    # folder. Killed here: the hardest way a process can end, as Task Manager
    # does, and a crash.
    import os
    import signal
    import subprocess

    repo_root = str(Path(__file__).resolve().parents[1])
    process = subprocess.Popen(
        [sys.executable, '-c', _UNCLOSED_STORE_SCRIPT % repo_root],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        pid, path = process.stdout.readline().split(' ', 1)
        path = path.strip()
        assert os.path.getsize(path) == 20_000 * 64
        # By its own pid: a virtual environment's python.exe is a launcher
        # that runs the interpreter as a child, which outlives the launcher.
        os.kill(int(pid), signal.SIGTERM)    # TerminateProcess on Windows
    finally:
        process.kill()
        process.wait(30)
        process.stdout.close()

    assert _disappears(path), "the killed process left its trace file behind"


def test_sorting_and_closing_leave_no_temp_file(tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    store = _numbered_store([0.3, 0.1, 0.2])
    unsorted = store._data_path

    assert store.sort_by_time() is True
    assert _disappears(unsorted), "the unsorted copy was left behind"
    sorted_copy = store._data_path
    store.seal()
    store.close()
    assert _disappears(sorted_copy)
    assert list(tmp_path.iterdir()) == []


def _numbered_rows(store: RawFrameStore) -> list[int]:
    return [record.data[0] for record in store.get_window(range(len(store)))]


def test_leftover_files_are_removed_and_a_running_copy_keeps_its_own(tmp_path, monkeypatch):
    import tempfile

    from core.raw_frame_store import remove_leftover_files

    (tmp_path / 'osvanta_a1b2c3d4.rawdata').write_bytes(bytes(64 * 100))
    (tmp_path / 'osvanta_e5f6g7h8.rawdata').write_bytes(b'')
    kept = [tmp_path / 'osvanta_crash.log', tmp_path / 'other.rawdata',
            tmp_path / 'osvanta_recovered_x.mf4']
    for path in kept:
        path.write_bytes(b'keep')
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    # Another copy of the application, still loading: its rows are in the
    # write buffer, its file still empty, and it is sealed only afterwards.
    running = _numbered_store([0.0, 0.1, 0.2])

    removed, freed = remove_leftover_files(tmp_path)

    assert (removed, freed) == (3, 64 * 100)
    assert sorted(tmp_path.iterdir()) == sorted(kept)
    running.append(0.3, 1, 0x103, 8, 'Rx', False, False, bytes([3] * 8), 'Msg3', True)
    running.seal()
    assert _numbered_rows(running) == [0, 1, 2, 3]
    running.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows refuses to delete an open file')
def test_a_file_an_earlier_version_still_has_open_is_skipped(tmp_path):
    import os

    from core.raw_frame_store import remove_leftover_files

    # How earlier versions opened it: without delete-on-close.
    fd = os.open(tmp_path / 'osvanta_old.rawdata', os.O_RDWR | os.O_CREAT | os.O_BINARY)
    with os.fdopen(fd, 'w+b') as handle:
        handle.write(bytes(64))
        handle.flush()
        assert remove_leftover_files(tmp_path) == (0, 0)
    assert remove_leftover_files(tmp_path) == (1, 64)


# ── LIN frames in the shared trace ─────────────────────────────────────────

def test_lin_frames_round_trip_through_the_shared_store():
    """LIN rides in flag bit 3 — no record-layout change, no new column.

    A 6-bit LIN ID fits the uint32 arbitration-ID column and a LIN payload is
    at most 8 of the 64 available bytes, so the on-disk record is untouched.
    """
    from core.bus_types import BusType

    store = RawFrameStore()
    store.append(
        timestamp=0.1, channel=1, arb_id=0x100, dlc=8, direction='Rx',
        is_extended=False, is_fd=False, data=b'\x01\x02',
        frame_name='EngineControl', decoded=True,
    )
    store.append(
        timestamp=0.2, channel=1, arb_id=0x20, dlc=2, direction='Rx',
        is_extended=False, is_fd=False, data=b'\x0a\x01',
        frame_name='DoorCmd', decoded=True, bus=BusType.LIN,
    )
    store.seal()

    can_record, lin_record = store.get_window([0, 1])

    assert can_record.bus is BusType.CAN
    assert can_record.channel_key == (BusType.CAN, 1)
    assert lin_record.bus is BusType.LIN
    assert lin_record.channel_key == (BusType.LIN, 1)
    assert lin_record.arbitration_id == 0x20
    assert lin_record.data[:2] == b'\x0a\x01'
    store.close()


def test_channel_one_on_each_bus_lists_and_filters_separately():
    """The collision the bus bit exists to prevent.

    Both frames sit on channel number 1. Filtering on the number alone would
    return both; only the bus bit tells them apart.
    """
    from core.bus_types import BusType

    store = RawFrameStore()
    store.append(
        timestamp=0.1, channel=1, arb_id=0x100, dlc=2, direction='Rx',
        is_extended=False, is_fd=False, data=b'\x01', frame_name='Can',
        decoded=True,
    )
    store.append(
        timestamp=0.2, channel=1, arb_id=0x20, dlc=2, direction='Rx',
        is_extended=False, is_fd=False, data=b'\x02', frame_name='Lin',
        decoded=True, bus=BusType.LIN,
    )
    store.seal()

    assert store.channel_keys() == [(BusType.CAN, 1), (BusType.LIN, 1)]

    can_mask = store.build_match_mask("", (BusType.CAN, 1))
    lin_mask = store.build_match_mask("", (BusType.LIN, 1))
    assert list(can_mask) == [True, False]
    assert list(lin_mask) == [False, True]
    store.close()


def test_integer_channel_filter_still_means_can():
    """Backward compatibility for callers that pass a bare channel number."""
    from core.bus_types import BusType

    store = RawFrameStore()
    store.append(
        timestamp=0.1, channel=2, arb_id=0x100, dlc=2, direction='Rx',
        is_extended=False, is_fd=False, data=b'\x01', frame_name='Can',
        decoded=True,
    )
    store.append(
        timestamp=0.2, channel=2, arb_id=0x20, dlc=2, direction='Rx',
        is_extended=False, is_fd=False, data=b'\x02', frame_name='Lin',
        decoded=True, bus=BusType.LIN,
    )
    store.seal()

    assert list(store.build_match_mask("", 2)) == [True, False]
    store.close()


# ── sort_by_time ─────────────────────────────────────────────────────────

def _channel_by_half(number: int) -> int:
    return 1 if number < 50 else 2


def _numbered_store(timestamps, channel_of=lambda number: 1) -> RawFrameStore:
    """
    One row per timestamp. Every column, and both ends of the 64-byte payload
    record, is derived from the row's own number, so a row that lost its
    payload or any single column in a reorder no longer agrees with itself.
    """
    from core.bus_types import BusType

    store = RawFrameStore()
    for number, timestamp in enumerate(timestamps):
        payload = bytearray(64)
        payload[0] = payload[63] = number
        store.append(
            timestamp=timestamp, channel=channel_of(number),
            arb_id=0x100 + number, dlc=64 + number % 3,
            direction=('Rx', 'Tx', 'Unknown')[number % 3],
            is_extended=number % 2 == 1, is_fd=True, data=bytes(payload),
            frame_name=f'Msg{number}', decoded=number % 4 == 0,
            bus=BusType.LIN if number % 5 == 0 else BusType.CAN,
        )
    return store


def _assert_rows_intact(store: RawFrameStore, channel_of=lambda number: 1) -> None:
    from core.bus_types import BusType

    for record in store.get_window(range(len(store))):
        number = record.arbitration_id - 0x100
        assert record.data[0] == record.data[63] == number
        assert record.dlc == 64 + number % 3
        assert record.direction == ('Rx', 'Tx', 'Unknown')[number % 3]
        assert record.is_extended == (number % 2 == 1)
        assert record.decoded == (number % 4 == 0)
        assert record.bus is (BusType.LIN if number % 5 == 0 else BusType.CAN)
        assert record.frame_name == f'Msg{number}'
        assert record.channel == channel_of(number)


def test_sort_by_time_interleaves_channel_groups_and_keeps_each_row_whole():
    # Appended one channel group after the other, as the MF4 bus-logging
    # path does: CAN 1 on even milliseconds, then CAN 2 on odd ones.
    timestamps = ([i * 0.002 for i in range(50)]
                  + [i * 0.002 + 0.001 for i in range(50)])
    store = _numbered_store(timestamps, _channel_by_half)

    assert store.sort_by_time() is True
    store.seal()

    times = np.frombuffer(store.timestamps, dtype=np.float64)
    assert np.all(np.diff(times) > 0)
    assert np.frombuffer(store.channels, dtype=np.uint8)[:4].tolist() == [1, 2, 1, 2]
    _assert_rows_intact(store, _channel_by_half)
    store.close()


def test_sort_by_time_is_stable_for_equal_timestamps():
    count = 3_000
    numbers = np.arange(count)
    timestamps = ((-numbers) % 3).astype(np.float64)       # 0, 2, 1, 0, 2, 1 ...
    store = RawFrameStore()
    store.append_numpy_batch(
        timestamps=timestamps,
        channels=np.ones(count, dtype=np.uint8),
        arb_ids=numbers.astype(np.uint32),
        dlcs=np.full(count, 4, dtype=np.uint8),
        directions=np.zeros(count, dtype=np.uint8),
        flags=np.zeros(count, dtype=np.uint8),
        data_rows=numbers.astype('<u4').view(np.uint8).reshape(count, 4),
    )

    store.sort_by_time()
    store.seal()

    arb_ids = np.frombuffer(store.arb_ids, dtype=np.uint32)
    assert arb_ids.tolist() == numbers[np.argsort(timestamps, kind='stable')].tolist()
    payload_numbers = [int.from_bytes(record.data, 'little')
                       for record in store.get_window(range(count))]
    assert payload_numbers == arb_ids.tolist()
    store.close()


def test_sort_by_time_leaves_an_ordered_store_untouched():
    store = _numbered_store([0.0, 0.1, 0.1, 0.2])    # equal neighbours are in order
    data_path = store._data_path

    assert store.sort_by_time() is False
    assert store._data_path == data_path, "an ordered store was rewritten"
    store.seal()
    assert np.frombuffer(store.arb_ids, dtype=np.uint32).tolist() == [
        0x100, 0x101, 0x102, 0x103]
    _assert_rows_intact(store)
    store.close()


def test_sort_by_time_refuses_a_sealed_store():
    store = _numbered_store([0.2, 0.1])
    store.seal()

    with pytest.raises(RuntimeError, match="before seal"):
        store.sort_by_time()
    store.close()


def test_a_failed_sort_leaves_the_store_as_it_was(monkeypatch):
    import os
    import tempfile

    store = _numbered_store([0.3, 0.1, 0.2])
    data_path = store._data_path
    created = []
    real_mkstemp, real_fdopen = tempfile.mkstemp, os.fdopen

    def mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    class FullDisk:
        def __init__(self, handle):
            self._handle = handle

        def write(self, _data):
            raise OSError("No space left on device")

        def close(self):
            self._handle.close()

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(os, "fdopen",
                        lambda fd, *a, **k: FullDisk(real_fdopen(fd, *a, **k)))
    with pytest.raises(OSError, match="No space"):
        store.sort_by_time()
    monkeypatch.undo()

    assert len(created) == 1 and _disappears(created[0]), \
        "the half-written copy was left behind"
    assert store._data_path == data_path
    assert np.frombuffer(store.timestamps, dtype=np.float64).tolist() == [0.3, 0.1, 0.2]
    store.seal()
    _assert_rows_intact(store)
    store.close()
