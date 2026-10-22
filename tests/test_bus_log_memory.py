# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A bus-logging MF4 decodes through a DBC in memory that does not grow with it.

asammdf still decodes, but its decoded file is left on disk and read a group
at a time on first use; the CAN Trace is read in record chunks into mapped
column files and merged into time order a block at a time; and no file is
opened with asammdf's bus-logging map, which reads every frame. These tests
hold each of those to what the in-memory path produced.
"""
from __future__ import annotations

import os
import re
import tracemalloc

import numpy as np
import pytest

import core.raw_frame_store as raw_frame_store
import core.readers.mdf_can_reader as mdf_can_reader
from core.bus_types import BusType
from core.channel_config import ChannelConfig
from core.raw_frame_store import RawFrameStore
from core.readers.mdf_can_reader import MDFCANReader, _bus_logging_keys
from core.signal_store import LazySignalSeries

asammdf = pytest.importorskip("asammdf")
pytest.importorskip("can")

from tests.test_mdf_trace_order import (  # noqa: E402
    LIN_ID, START, _bus_group, _can_rows, _write_mf4,
)

CAN1, CAN2 = (BusType.CAN, 1), (BusType.CAN, 2)


# ── Trace: merging time-ordered runs ─────────────────────────────────────

def _runs_store(runs):
    """A trace of the given time-ordered runs; payload byte 0..3 = row number."""
    store = RawFrameStore()
    row = 0
    for times in runs:
        count = len(times)
        data = np.zeros((count, 64), dtype=np.uint8)
        data[:, :4] = (np.arange(row, row + count, dtype=np.uint32)
                       .view(np.uint8).reshape(count, 4))
        store.append_numpy_batch(
            np.asarray(times, dtype=np.float64), np.full(count, 1, np.uint8),
            np.arange(row, row + count, dtype=np.uint32), np.full(count, 8, np.uint8),
            np.zeros(count, np.uint8), np.zeros(count, np.uint8), data,
        )
        row += count
    return store


@pytest.mark.parametrize("block, max_runs", [(5, 4096), (1 << 20, 4096), (5, 2)])
def test_merging_runs_equals_a_stable_sort(block, max_runs, monkeypatch):
    monkeypatch.setattr(raw_frame_store, "_MERGE_BLOCK_ROWS", block)
    monkeypatch.setattr(raw_frame_store, "_MAX_MERGE_RUNS", max_runs)
    rng = np.random.default_rng(block + max_runs)
    # Whole-number times: plenty of ties within and across runs.
    runs = [np.sort(rng.integers(0, 40, size)).astype(float) for size in (37, 1, 52, 23)]
    store = _runs_store(runs)
    joined = np.concatenate(runs)
    order = np.argsort(joined, kind="stable")

    assert store.sort_by_time() is True
    store.seal()
    assert np.asarray(store.timestamps).tolist() == joined[order].tolist()
    assert np.asarray(store.arb_ids).tolist() == order.tolist()
    rows = [int.from_bytes(record.data[:4], "little")
            for record in store.get_window(range(len(store)))]
    assert rows == order.tolist()
    store.close()


def test_a_trace_already_in_time_order_is_left_alone():
    store = _runs_store([np.arange(10.0), np.arange(10.0, 20.0)])
    path = store._data_path
    assert store.sort_by_time() is False
    assert store._data_path == path
    store.close()


# ── Trace: reading records in chunks ─────────────────────────────────────

def _emitted(path, monkeypatch, chunk):
    monkeypatch.setattr(mdf_can_reader, "_TRACE_CHUNK_RECORDS", chunk)
    can_batches, lin_batches = [], []
    source = asammdf.MDF(str(path), use_display_names=False, process_bus_logging=False)
    try:
        MDFCANReader._emit_raw_frame_arrays(source, lambda *cols: can_batches.append(cols))
        MDFCANReader._emit_raw_lin_frame_arrays(source, lambda *cols: lin_batches.append(cols))
    finally:
        source.close()
    # CAN payloads are 64 bytes wide, LIN 8: each bus is joined on its own.
    return [
        np.concatenate([np.asarray(batch[i]).reshape(len(batch[0]), -1) for batch in batches])
        for batches in (can_batches, lin_batches)
        for i in range(7)
    ], len(can_batches) + len(lin_batches)


def test_trace_records_read_in_chunks_equal_one_read(tmp_path, monkeypatch):
    path = _write_mf4(tmp_path / "bus.mf4", _can_rows(50),
                      [(START + i * 0.013, 1, bytes([i] * 8)) for i in range(30)])
    whole, whole_batches = _emitted(path, monkeypatch, 1 << 20)
    chunked, chunked_batches = _emitted(path, monkeypatch, 7)
    assert whole_batches == 3 and chunked_batches == 8 + 8 + 5
    for a, b in zip(whole, chunked):
        assert np.array_equal(a, b)


def test_reading_the_trace_in_chunks_bounds_its_memory(tmp_path, monkeypatch):
    # Far more than one of asammdf's data blocks, the floor of any read.
    path = _write_mf4(tmp_path / "long.mf4", _can_rows(200_000, channels=(1,)))

    def peak(chunk):
        monkeypatch.setattr(mdf_can_reader, "_TRACE_CHUNK_RECORDS", chunk)
        source = asammdf.MDF(str(path), use_display_names=False, process_bus_logging=False)
        tracemalloc.start()
        try:
            MDFCANReader._emit_raw_frame_arrays(source, lambda *cols: None)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
            source.close()

    assert peak(1000) < peak(1 << 20) / 4


# ── Bus channels without asammdf's map ───────────────────────────────────

def test_bus_logging_keys_match_asammdfs_map(tmp_path):
    path = _write_mf4(tmp_path / "bus.mf4", _can_rows(20, channels=(1, 3)),
                      [(START + i * 0.02, 1, bytes(8)) for i in range(5)])
    full = asammdf.MDF(str(path), use_display_names=False)
    want = {bus: sorted(int(key) for key in full._mdf.bus_logging_map[bus])
            for bus in ("CAN", "LIN")}
    full.close()
    lean = asammdf.MDF(str(path), use_display_names=False, process_bus_logging=False)
    try:
        assert _bus_logging_keys(lean) == want == {"CAN": [1, 3], "LIN": [LIN_ID]}
    finally:
        lean.close()


# ── Whole loads: left in the decoded file equals read into memory ───────

def _write_mixed(path):
    """The two-channel bus log plus a group the recorder already decoded."""
    _write_mf4(path, _can_rows(300))
    mdf = asammdf.MDF(str(path))
    t = np.arange(200) * 0.05
    mdf.append([asammdf.Signal(np.sin(t), t, name="Speed", unit="km/h"),
                asammdf.Signal((t * 10).astype(np.uint8), t, name="Gear")],
               acq_name="Recorder")
    mdf.save(str(path), overwrite=True)
    mdf.close()
    return path


def _load(path, sample_dbc_path):
    from gui.load_worker import LoadWorker

    config = ChannelConfig(channels={CAN1: str(sample_dbc_path), CAN2: str(sample_dbc_path)})
    worker = LoadWorker(str(path), config)
    result = {}
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda error: result.setdefault("error", error))
    worker.run()
    assert "error" not in result, result.get("error")
    return result["store"]


def _assert_same(want, got):
    assert want.all_keys() == got.all_keys()
    assert (want.total_samples, want.decoded_frames, want.total_frames,
            want.unmatched_frames, want.base_ts) == (
        got.total_samples, got.decoded_frames, got.total_frames,
        got.unmatched_frames, got.base_ts)
    for key in want.all_keys():
        a, b = want.get_series(key), got.get_series(key)
        assert np.array_equal(a.numpy_timestamps(), b.numpy_timestamps()), key
        assert np.array_equal(a.numpy_values(), b.numpy_values(), equal_nan=True), key
        assert (a.has_labels, a.unit) == (b.has_labels, b.unit), key
        assert list(a.raw_values) == list(b.raw_values), key
    for name in ("timestamps", "channels", "arb_ids", "flags", "name_ids"):
        assert np.array_equal(np.asarray(getattr(want.raw_frame_store, name)),
                              np.asarray(getattr(got.raw_frame_store, name))), name
    timeless = lambda text: re.sub(r"[0-9.]+ s\b", "", text)  # noqa: E731
    assert timeless(want.diagnostics_text) == timeless(got.diagnostics_text)


@pytest.mark.parametrize("mixed", [False, True])
def test_a_bus_log_left_in_the_decoded_file_equals_one_read_in(
    mixed, tmp_path, sample_dbc_path, monkeypatch
):
    import gui.load_worker as load_worker

    path = tmp_path / "bus.mf4"
    _write_mixed(path) if mixed else _write_mf4(path, _can_rows(300))
    with monkeypatch.context() as m:
        # The trace's shift and labels go a chunk at a time: many chunks here.
        m.setattr(load_worker, "_GROUP_CHUNK_ROWS", 7)
        got = _load(path, sample_dbc_path)
    series = [got.get_series(key) for key in got.all_keys()]
    assert series and all(isinstance(s, LazySignalSeries) for s in series)
    assert got.sample_cache.reads == 0
    if mixed:
        assert got.get_series("CH?::Recorder::Speed") is not None
    with monkeypatch.context() as m:
        m.delattr(MDFCANReader, "open_decoded_sources")
        want = _load(path, sample_dbc_path)
    _assert_same(want, got)
    for store in (want, got):
        store.close()
        store.raw_frame_store.close()


def test_closing_the_store_releases_the_bus_log(tmp_path, sample_dbc_path):
    # A plain bus log. asammdf keeps the mixed file above open after close()
    # until the cyclic collector runs, even for a bare open and close; the
    # suite turns the collector off once a Qt test has run.
    path = _write_mf4(tmp_path / "bus.mf4", _can_rows(300))
    store = _load(path, sample_dbc_path)
    next(iter(store._series_by_key.values())).values
    joined = store.sample_cache._source
    store.close()
    store.raw_frame_store.close()
    os.remove(path)          # Windows refuses while any handle is open
    # asammdf's decoded file (its own temporary) is closed too.
    assert joined._dbc._mdf is None


def test_no_open_builds_the_bus_logging_map(tmp_path, sample_dbc_path, monkeypatch):
    """asammdf's map reads every frame on open; nothing on the load path asks for it."""
    path = _write_mixed(tmp_path / "bus.mf4")
    opens = []
    real = asammdf.MDF

    def recording(*args, **kwargs):
        if args:
            opens.append(kwargs.get("process_bus_logging", True))
        return real(*args, **kwargs)

    monkeypatch.setattr(asammdf, "MDF", recording)
    store = _load(path, sample_dbc_path)
    store.close()
    store.raw_frame_store.close()
    assert opens and not any(opens), opens
