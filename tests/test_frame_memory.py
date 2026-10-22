# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A raw-frame log decodes in memory that does not grow with its length.

The CAN Trace's frame index goes to mapped column files instead of the heap,
a long log is grouped by CAN ID a chunk at a time, and a CAN ID with many
frames is decoded a slice at a time. These tests hold the chunked and sliced
paths to exactly what the one-pass decode produced, and hold the decode's
heap to a bound however many frames the log has.
"""
from __future__ import annotations

import array
import re
import tracemalloc

import numpy as np
import pytest

import core.sample_file as sample_file
import core.signal_store as signal_store
import gui.load_worker as load_worker
from core.raw_frame_store import FLAG_LIN, RawFrameStore
from core.signal_store import SignalStore
from gui.load_worker import (
    LoadWorker,
    _frame_keys,
    _group_frames,
    _group_frames_chunked,
)


# ── Grouping ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("chunk_rows", [1, 7, 1000, 1 << 20])
def test_chunked_grouping_equals_the_one_pass_grouping(chunk_rows, monkeypatch):
    # File-backed sort order, as for a long log.
    real = sample_file.scratch_array
    monkeypatch.setattr(sample_file, "scratch_array",
                        lambda n, dtype: real(n, dtype, heap_limit=0))
    rng = np.random.default_rng(chunk_rows)
    n = 20_000
    channels = rng.choice(np.array([1, 2, 255], dtype=np.uint8), n)
    arb_ids = rng.choice(np.array([0x10, 0x7FF, 0x18FEF100, 0x10], dtype=np.uint32), n)
    flags = rng.choice(np.array([0, 1, FLAG_LIN], dtype=np.uint8), n)
    flags[-3:] = FLAG_LIN            # a key that only appears at the end
    arb_ids[-3:] = 0x3C

    want_order, want_bounds = _group_frames(_frame_keys(flags, channels, arb_ids))
    order, bounds = _group_frames_chunked(flags, channels, arb_ids, chunk_rows)
    assert np.array_equal(bounds, want_bounds)
    assert np.array_equal(np.asarray(order), want_order)


# ── The frame index ──────────────────────────────────────────────────────

def _batch(start, count):
    ts = np.arange(start, start + count, dtype=np.float64) * 0.001
    ch = np.full(count, 1, dtype=np.uint8)
    ids = np.where(np.arange(count) % 2, 256, 512).astype(np.uint32)
    dlcs = np.full(count, 8, dtype=np.uint8)
    dirs = np.zeros(count, dtype=np.uint8)
    flags = np.zeros(count, dtype=np.uint8)
    data = np.zeros((count, 64), dtype=np.uint8)
    data[:, 0] = np.arange(start, start + count) % 251
    data[:, 1] = 3
    return ts, ch, ids, dlcs, dirs, flags, data


def test_the_frame_index_is_mapped_from_files_after_seal():
    store = RawFrameStore()
    for start in range(0, 30_000, 10_000):
        store.append_raw_batch(*_batch(start, 10_000))
    assert len(store) == 30_000
    store.seal()
    for name in ("timestamps", "channels", "arb_ids", "dlcs", "flags", "name_ids"):
        column = getattr(store, name)
        assert isinstance(column, np.ndarray) and len(column) == 30_000, name
    assert not isinstance(store.timestamps.base, np.ndarray)   # a map, not a heap copy
    record = store.get_window([29_999])[0]
    assert record.time_s == pytest.approx(29.999)
    assert type(record.arbitration_id) is int and record.arbitration_id == 256
    assert record.data[0] == 29_999 % 251
    store.close()
    assert isinstance(store.timestamps, array.array) and not len(store.timestamps)


def test_frames_built_in_memory_are_moved_to_files_on_seal():
    store = RawFrameStore()
    store.append(0.5, 1, 0x123, 8, 'Rx', False, False, b'\x01' * 8, '', False)
    store.append(0.6, None, 0x124, 2, 'Tx', True, False, b'\x02\x03', '', False)
    store.seal()
    assert isinstance(store.arb_ids, np.ndarray)
    first, second = store.get_window([0, 1])
    assert (first.arbitration_id, first.direction, first.data) == (0x123, 'Rx', b'\x01' * 8)
    assert (second.channel, second.is_extended, second.dlc) == (None, True, 2)
    store.close()


# ── Whole loads: chunked and sliced equal the one-pass decode ────────────

def _load(path, dbc):
    worker = LoadWorker(str(path), str(dbc))
    result = {}
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda message: result.setdefault("error", message))
    worker.run()
    assert "error" not in result, result.get("error")
    return result["store"]


def _assert_same(want, got):
    assert want.all_keys() == got.all_keys()
    assert (want.total_samples, want.decoded_frames, want.total_frames,
            want.unmatched_frames) == (got.total_samples, got.decoded_frames,
                                       got.total_frames, got.unmatched_frames)
    for key in want.all_keys():
        a, b = want.get_series(key), got.get_series(key)
        assert np.array_equal(a.numpy_timestamps(), b.numpy_timestamps()), key
        assert np.array_equal(a.numpy_values(), b.numpy_values(), equal_nan=True), key
        assert a.has_labels == b.has_labels, key
        assert list(a.raw_values) == list(b.raw_values), key
    rw, rg = want.raw_frame_store, got.raw_frame_store
    for name in ("timestamps", "channels", "arb_ids", "flags", "name_ids"):
        assert np.array_equal(np.asarray(getattr(rw, name)), np.asarray(getattr(rg, name))), name
    assert rw.name_table == rg.name_table
    # Equal but for how long each load took.
    timeless = lambda text: re.sub(r"time: [0-9.]+ s", "", text)  # noqa: E731
    assert timeless(want.diagnostics_text) == timeless(got.diagnostics_text)


@pytest.mark.parametrize("fixture", ["blf_path", "asc_path", "raw_can_csv_path"])
@pytest.mark.parametrize("spill", [False, True])
def test_chunked_and_sliced_decode_equals_one_pass(
    fixture, spill, request, sample_dbc_path, monkeypatch
):
    path = request.getfixturevalue(fixture)
    want = _load(path, sample_dbc_path)
    monkeypatch.setattr(load_worker, "_GROUP_CHUNK_ROWS", 7)
    # The fixtures hold three frames per CAN ID: two-row slices split each.
    monkeypatch.setattr(load_worker, "_DECODE_SLICE_ROWS", 2)
    if spill:
        monkeypatch.setattr(signal_store, "default_cache_budget", lambda: 0)
    got = _load(path, sample_dbc_path)
    if spill:
        assert got.sample_file_bytes > 0
    _assert_same(want, got)
    want.close()
    got.close()


_MUX_DBC = """VERSION ""
NS_ :
BS_:
BU_: Vector__XXX
BO_ 300 MuxMsg: 3 Vector__XXX
 SG_ Mux M : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ BranchA m1 : 8|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ BranchB m2 : 16|8@1+ (1,0) [0|255] "" Vector__XXX
VAL_ 300 BranchB 0 "Zero" 1 "One" ;
"""


class _FrameReader:
    """Raw frames on CAN 1, one ID, the given payload bytes, in one batch."""

    has_raw_frames = True
    source_description = "synthetic"
    load_messages: list = []

    def __init__(self, arb_id, payload):
        self.arb_id, self.payload = arb_id, payload

    def iter_raw_batches(self, batch_size):
        count = len(self.payload)
        data = np.zeros((count, 64), dtype=np.uint8)
        data[:, :self.payload.shape[1]] = self.payload
        yield (0.0, np.arange(count, dtype=np.float64) * 0.01,
               np.ones(count, np.uint8), np.full(count, self.arb_id, np.uint32),
               np.full(count, 3, np.uint8), np.zeros(count, np.uint8),
               np.zeros(count, np.uint8), data)


def test_sliced_multiplexed_signals_equal_one_pass(tmp_path, monkeypatch):
    """A branch valid in every row of some slices and in some rows of others."""
    dbc = tmp_path / "mux.dbc"
    dbc.write_text(_MUX_DBC)
    mux = np.array([1] * 6 + [1, 2, 2, 1, 2, 2, 2, 1, 1, 2, 1], dtype=np.uint8)
    payload = np.stack([mux, np.arange(len(mux)), np.arange(len(mux)) % 3], axis=1)

    def decode():
        store = SignalStore()
        LoadWorker("synthetic.blf", str(dbc))._run_can_raw_vectorized(
            _FrameReader(300, payload.astype(np.uint8)), store)
        return store

    want = decode()
    monkeypatch.setattr(load_worker, "_GROUP_CHUNK_ROWS", 3)
    monkeypatch.setattr(load_worker, "_DECODE_SLICE_ROWS", 2)
    got = decode()
    assert len(want.get_series("CH1::MuxMsg::BranchA").timestamps) == 11
    assert want.get_series("CH1::MuxMsg::BranchB").has_labels
    _assert_same(want, got)


# ── Memory ───────────────────────────────────────────────────────────────

class _SyntheticReader:
    """A raw-frame reader of *count* frames, alternating two DBC messages."""

    has_raw_frames = True
    source_description = "synthetic"
    load_messages: list = []

    def __init__(self, count):
        self.count = count

    def iter_raw_batches(self, batch_size):
        for start in range(0, self.count, batch_size):
            ts, ch, ids, dlcs, dirs, flags, data = _batch(
                start, min(batch_size, self.count - start))
            yield 0.0, ts, ch, ids, dlcs, dirs, flags, data


def _decode_peak(count, dbc):
    worker = LoadWorker("synthetic.blf", str(dbc))
    store = SignalStore()
    store.memory_budget = 1 << 20
    tracemalloc.start()
    try:
        worker._run_can_raw_vectorized(_SyntheticReader(count), store)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert store.total_frames == count
    assert len(store.get_series("CH1::GearStatus::Gear").timestamps) == count // 2
    store.close()
    store.raw_frame_store.close()
    return peak


def test_decode_heap_does_not_follow_the_frame_count(sample_dbc_path, monkeypatch):
    monkeypatch.setattr(load_worker, "_GROUP_CHUNK_ROWS", 1 << 18)
    monkeypatch.setattr(load_worker, "_DECODE_SLICE_ROWS", 1 << 17)
    small = _decode_peak(500_000, sample_dbc_path)
    large = _decode_peak(2_000_000, sample_dbc_path)
    # Four times the frames: the one-pass decode held about 60 bytes of
    # heap per frame (120 MB here); chunked, the peak stays put.
    assert large < small * 1.5
    assert large < 48 * 1024 * 1024
