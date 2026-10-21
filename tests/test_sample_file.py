# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Decoded samples past the memory budget go to a temporary file.

The store keeps up to ``memory_budget`` bytes of samples in the heap and puts
the rest in a memory-mapped temporary file, which does not count against the
system's commit limit. These tests hold the file-backed store to exactly what
the in-memory one produced, for every bulk-decoded format, and hold the heap
to the budget however much is decoded.
"""
from __future__ import annotations

import array
import gc
import os
import tracemalloc

import numpy as np
import pytest

import core.sample_file as sample_file
import core.signal_store as signal_store
from core.sample_file import SampleFile, SampleFileFull
from core.signal_store import ChoiceLabels, SignalStore


# ── The file ─────────────────────────────────────────────────────────────

def test_stored_arrays_are_file_backed_copies():
    f = SampleFile(segment_bytes=1 << 16)
    a = np.arange(10, dtype=np.float64)
    view = f.store(a[:4], array.array("d", [9.0, 8.0]), a[4:])
    assert view.tolist() == [0, 1, 2, 3, 9, 8, 4, 5, 6, 7, 8, 9]
    assert not np.shares_memory(view, a)
    big = f.store(np.ones(20_000))     # larger than a segment: its own
    assert big.sum() == 20_000 and view[4] == 9.0
    assert f.bytes_stored == (12 + 20_000) * 8
    f.close()


def test_views_outlive_close_and_the_file_goes_with_the_last():
    f = SampleFile()
    view = f.store(np.arange(1000.0))
    path = f.path
    f.close()
    assert view[999] == 999.0
    del view
    gc.collect()
    assert not os.path.exists(path)


def test_a_full_disk_is_refused_before_writing(monkeypatch):
    f = SampleFile()

    class Usage:
        free = 100 * 1024 * 1024

    monkeypatch.setattr(sample_file.shutil, "disk_usage", lambda _p: Usage)
    with pytest.raises(SampleFileFull):
        f.store(np.arange(10.0))
    f.close()


# ── The store ────────────────────────────────────────────────────────────

def _insert(store, name, ts, vs, raw=(), message="Msg"):
    store.add_series_bulk(
        channel=1, message_name=message, message_id=1, signal_name=name,
        unit="V", timestamps=ts, values=vs, raw_values=list(raw) if
        isinstance(raw, tuple) else raw, has_labels=bool(len(raw)),
    )


def test_inserts_past_the_budget_go_to_the_file():
    store = SignalStore()
    store.memory_budget = 2 * 1000 * 8
    ts = np.arange(1000.0)
    _insert(store, "A", ts, ts * 2)            # fills the budget in the heap
    _insert(store, "B", ts, ts * 3)
    _insert(store, "C", ts, ts * 4)
    a, b, c = (store.get_series(f"CH1::Msg::{n}") for n in "ABC")
    assert isinstance(a.values, array.array)
    assert isinstance(b.values, np.ndarray) and isinstance(c.values, np.ndarray)
    # One time axis for all three: A's, which went to the heap first.
    assert a.timestamps is b.timestamps is c.timestamps
    assert np.array_equal(c.numpy_values(), ts * 4)
    assert store.sample_file_bytes == 2 * 1000 * 8   # B's and C's values
    store.close()


def test_a_series_fed_twice_is_merged_and_sorted_in_the_file():
    store = SignalStore()
    store.memory_budget = 0
    lut = np.array(["Off", "On"], dtype=object)
    valid = np.array([True, True])
    late = np.array([1.0, 3.0, 5.0])
    early = np.array([0.0, 2.0, 4.0])
    _insert(store, "S", late, np.array([1.0, 1.0, 1.0]),
            ChoiceLabels(np.array([1.0, 1.0, 1.0]), lut, valid))
    _insert(store, "S", early, np.array([0.0, 0.0, 7.0]),
            ChoiceLabels(np.array([0.0, 0.0, 7.0]), lut, valid))
    assert store.sort_merged_series() == 1
    s = store.get_series("CH1::Msg::S")
    assert isinstance(s.values, np.ndarray)
    assert s.numpy_timestamps().tolist() == [0, 1, 2, 3, 4, 5]
    assert s.numpy_values().tolist() == [0, 1, 0, 1, 7, 1]
    assert list(s.raw_values) == ["Off", "On", "Off", "On", 7.0, "On"]
    store.close()


def test_one_sample_appends_after_a_bulk_insert_in_the_file():
    store = SignalStore()
    store.memory_budget = 0
    _insert(store, "S", np.array([0.0, 1.0]), np.array([5.0, 6.0]))
    from core.models import DecodedSignalSample
    store.add_samples([DecodedSignalSample(
        timestamp=2.0, channel=1, message_id=1, message_name="Msg",
        signal_name="S", value=7.0, unit="V", is_extended_id=False,
        direction="Rx", numeric_value=7.0,
    )])
    s = store.get_series("CH1::Msg::S")
    assert s.numpy_values().tolist() == [5.0, 6.0, 7.0]
    assert s.numpy_timestamps().tolist() == [0.0, 1.0, 2.0]
    store.close()


def test_a_full_disk_keeps_the_rest_in_memory_and_says_so(monkeypatch):
    class Usage:
        free = 0

    monkeypatch.setattr(sample_file.shutil, "disk_usage", lambda _p: Usage)
    store = SignalStore()
    store.memory_budget = 0
    ts = np.arange(100.0)
    _insert(store, "A", ts, ts)
    _insert(store, "B", ts, ts + 1)
    b = store.get_series("CH1::Msg::B")
    assert np.array_equal(b.numpy_values(), ts + 1)
    assert len(store.load_warnings) == 1
    assert "no room" in store.load_warnings[0]
    store.close()


def test_heap_stays_at_the_budget_however_much_is_decoded():
    store = SignalStore()
    store.memory_budget = 1 << 20        # 1 MB
    ts = np.arange(250_000.0)            # 2 MB per array
    tracemalloc.start()
    try:
        for i in range(50):              # 100 MB of values
            _insert(store, f"S{i}", ts, ts + i, message=f"M{i % 5}")
        heap = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert store.sample_file_bytes >= 50 * 250_000 * 8 - (1 << 20)
    assert heap < 16 * 1024 * 1024
    assert store.get_series("CH1::M4::S49").numpy_values()[-1] == 249_999 + 49
    store.close()


# ── Labels ───────────────────────────────────────────────────────────────

def _old_labels(numeric_arr, lut, lut_valid):
    """The per-sample list the decoder built before ChoiceLabels."""
    max_key = len(lut) - 1
    nan_mask = np.isnan(numeric_arr)
    with np.errstate(invalid="ignore"):
        int_arr = numeric_arr.astype(np.int64)
    safe_idx = np.clip(int_arr, 0, max_key)
    in_range = ~nan_mask & (int_arr >= 0) & (int_arr <= max_key)
    has_val = in_range & lut_valid[safe_idx]
    result = np.empty(len(numeric_arr), dtype=object)
    result[nan_mask] = ''
    result[~nan_mask & ~in_range] = numeric_arr[~nan_mask & ~in_range]
    result[in_range & ~lut_valid[safe_idx]] = numeric_arr[in_range & ~lut_valid[safe_idx]]
    if has_val.any():
        result[has_val] = lut[safe_idx[has_val]]
    return result.tolist()


def test_choice_labels_match_the_old_list():
    lut = np.array(["Park", None, "Neutral", "Drive"], dtype=object)
    valid = np.array([True, False, True, True])
    values = np.array([0, 1, 2, 3, 4, -1, np.nan, 2.7, -0.5, 1e6, np.inf, -np.inf])
    labels = ChoiceLabels(values, lut, valid)
    old = _old_labels(values, lut, valid)
    assert list(labels) == old
    assert [labels[i] for i in range(len(values))] == old
    assert labels[-3] == old[-3]
    assert labels[2:5] == old[2:5]
    assert np.array(labels, dtype=object).tolist() == old
    assert len(labels) == len(values) and labels
    with pytest.raises(IndexError):
        labels[len(values)]


# ── Whole loads: the file holds exactly what the heap held ──────────────

def _load(path, dbc=None):
    from gui.load_worker import LoadWorker

    worker = LoadWorker(str(path), str(dbc) if dbc else None)
    result = {}
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda message: result.setdefault("error", message))
    worker.run()
    assert "error" not in result, result.get("error")
    return result["store"]


def _assert_same(heap, disk):
    assert heap.all_keys() == disk.all_keys()
    assert (heap.total_samples, heap.decoded_frames, heap.total_frames) == (
        disk.total_samples, disk.decoded_frames, disk.total_frames)
    for key in heap.all_keys():
        want, got = heap.get_series(key), disk.get_series(key)
        assert np.array_equal(want.numpy_timestamps(), got.numpy_timestamps()), key
        assert np.array_equal(
            want.numpy_values(), got.numpy_values(), equal_nan=True), key
        assert want.has_labels == got.has_labels, key
        assert list(want.raw_values) == list(got.raw_values), key


# Every bulk-decoded format with a database. CSV signal files load sample by
# sample and stay in memory.
@pytest.mark.parametrize("fixture", ["blf_path", "asc_path"])
def test_a_load_kept_in_the_file_equals_one_kept_in_memory(
    fixture, request, sample_dbc_path, monkeypatch
):
    path = request.getfixturevalue(fixture)
    dbc = sample_dbc_path
    heap = _load(path, dbc)
    assert heap.sample_file_bytes == 0
    monkeypatch.setattr(signal_store, "default_cache_budget", lambda: 0)
    disk = _load(path, dbc)
    assert disk.sample_file_bytes > 0
    _assert_same(heap, disk)
    heap.close()
    disk.close()
