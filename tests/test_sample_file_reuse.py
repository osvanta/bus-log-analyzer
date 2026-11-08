# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The sample file reuses the space of samples nothing holds any more.

TODO issue 24: on a 5 GB log the decode wrote every sample twice into the
append-only sample file, once as the part a slice kept and once joined into
its series, so the file grew to 2.3 times the samples it held.
"""

from __future__ import annotations

import threading

import numpy as np

from core.sample_file import SampleFile
from core.signal_store import SignalStore

MB = 1 << 20


def test_a_region_is_used_again_once_no_array_holds_it():
    f = SampleFile(segment_bytes=MB)
    try:
        for i in range(100):
            a = f.store(np.full(100_000, float(i)))
            assert a[0] == i and a[-1] == i
            del a
        # 100 arrays of 0.8 MB, one at a time: one segment, not 80 MB.
        assert f.file_bytes == MB
        assert f.bytes_stored == 0
    finally:
        f.close()


def test_a_slice_or_buffer_of_an_array_keeps_its_samples():
    f = SampleFile(segment_bytes=MB)
    try:
        a = f.store(np.arange(100_000.0))
        tail = a[50_000:]
        b = f.store(np.arange(20_000.0))
        buffer = memoryview(b)
        del a, b
        f.store(np.full(100_000, -1.0))
        f.store(np.full(20_000, -1.0))
        assert np.array_equal(tail, np.arange(50_000.0, 100_000.0))
        assert np.array_equal(np.frombuffer(buffer), np.arange(20_000.0))
    finally:
        f.close()


def test_neighbours_let_go_together_take_a_larger_array():
    f = SampleFile(segment_bytes=MB)          # 131,072 samples
    try:
        a = f.store(np.zeros(40_000))
        b = f.store(np.zeros(40_000))
        last = f.store(np.zeros(10))
        del a, b
        # 80,000 samples fit only where a and b were, joined.
        c = f.store(np.ones(80_000))
        assert f.file_bytes == MB
        assert c.sum() == 80_000 and last.sum() == 0
    finally:
        f.close()


def test_an_array_let_go_on_another_thread_frees_its_region():
    f = SampleFile(segment_bytes=MB)
    try:
        held = [f.store(np.zeros(100_000))]
        thread = threading.Thread(target=held.clear)
        thread.start()
        thread.join()
        f.store(np.zeros(100_000))
        assert f.file_bytes == MB
    finally:
        f.close()


def test_parts_joined_into_their_series_leave_no_second_copy_on_disk():
    """As the BLF decode does it: a slice at a time, then one series."""
    store = SignalStore()
    store.memory_budget = 0                    # every sample to the file
    store._sample_file = SampleFile(segment_bytes=MB)
    rows, slices, signals, messages = 5_000, 4, 5, 20
    try:
        for message in range(messages):
            ts_parts = [store.keep(np.arange(i * rows, (i + 1) * rows, dtype=float))
                        for i in range(slices)]
            for signal in range(signals):
                values = [store.keep(np.full(rows, float(signal))) for _ in range(slices)]
                store.add_series_bulk(1, f"M{message}", message, f"S{signal}", "",
                                      ts_parts, values, [], has_labels=False)
            del ts_parts, values
        store.sort_merged_series()             # lets go of the last time parts
        series = rows * slices * 8
        live = messages * (series + signals * series)   # shared time + values
        assert store.sample_file_bytes == live
        # The parts' space went to later parts: before, the file held 2x.
        assert store._sample_file.file_bytes < 1.25 * live
        got = store.get_series(store._make_key(1, "M7", "S3"))
        assert np.array_equal(got.numpy_values(), np.full(rows * slices, 3.0))
        assert np.array_equal(got.numpy_timestamps(), np.arange(rows * slices, dtype=float))
    finally:
        store.close()


def test_parts_count_once_against_the_memory_budget():
    """Parts joined into a series and dropped no longer count as samples."""
    store = SignalStore()
    rows = 4_000
    # While joining, the parts and the series are both held: 3 x 32 KB.
    store.memory_budget = 3 * rows * 8 + 4096
    ts = [store.keep(np.arange(i * 1000, (i + 1) * 1000, dtype=float)) for i in range(4)]
    values = [store.keep(np.zeros(1000)) for _ in range(4)]
    store.add_series_bulk(1, "M", 1, "S", "", ts, values, [], has_labels=False)
    del ts, values
    store.sort_merged_series()
    # The next signal still fits the budget: the parts' bytes were given back.
    store.add_series_bulk(1, "M", 1, "T", "", np.arange(10.0), np.zeros(10), [],
                          has_labels=False)
    assert store._sample_file is None
    store.close()


def test_the_budget_for_samples_in_memory_follows_free_memory(monkeypatch):
    """TODO issue 25: an eighth of the memory whatever was free, on a PC with
    4 GB of 16 free, took system commit to 95 % while a 5 GB log decoded."""
    from core import signal_store

    gb = 1 << 30
    cases = [
        ((16 * gb, 4 * gb), gb // 2),       # an eighth of what is free
        ((32 * gb, 24 * gb), 2 * gb),       # at most 2 GB
        ((16 * gb, 16 * gb), 2 * gb),       # an eighth of the memory
        ((4 * gb, 4 * gb), gb // 2),        # an eighth of a small PC
        ((16 * gb, gb), 256 * MB),          # never below 256 MB
        ((0, 0), 256 * MB),                 # unknown
    ]
    for memory, want in cases:
        monkeypatch.setattr(signal_store, "physical_memory", lambda m=memory: m)
        assert signal_store.default_cache_budget() == want, memory
