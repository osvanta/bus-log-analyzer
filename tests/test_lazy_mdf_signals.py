# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Pre-decoded MDF signals stay in the file until used.

Decode lists the channels and reads no samples; a signal's group is read the
first time the signal is used, and the store keeps a bounded amount. These
tests hold the lazy store to what the eager one produced, and hold its memory
to the budget rather than to the file size.
"""
from __future__ import annotations

import os
import tracemalloc

import numpy as np
import pytest

asammdf = pytest.importorskip("asammdf")

from core.readers.mdf_reader import MDFReader  # noqa: E402
from core.signal_store import LazySignalSeries, SignalStore  # noqa: E402
from gui.load_worker import LoadWorker  # noqa: E402


def _write_mf4(path, cycles=500, groups=3, duplicate_group=True):
    """Groups of uint8/int16/float32 channels, a text channel, a twin group."""
    from asammdf import MDF, Signal

    mdf = MDF(version="4.10")
    for g in range(groups):
        rng = np.random.default_rng(g)
        t = np.arange(cycles, dtype=np.float64) * 0.01 + 5.0 + g * 1e-3
        signals = [
            Signal(rng.integers(0, 100, cycles).astype(dtype), t, name=f"S{i}", unit="V")
            for i, dtype in enumerate((np.uint8, np.int16, np.float32))
        ]
        signals.append(Signal(
            rng.integers(0, 2, cycles).astype(np.uint8), t, name="State",
            conversion={"val_0": 0, "text_0": b"Off", "val_1": 1, "text_1": b"On"},
        ))
        mdf.append(signals, acq_name=f"Group{g}")
    if duplicate_group:
        # A second group with the first one's name, interleaved in time: its
        # channels join Group0's into one series each, in time order.
        t = np.arange(cycles, dtype=np.float64) * 0.01 + 5.005
        mdf.append(
            [Signal(np.full(cycles, 200, dtype=np.uint8), t, name="S0", unit="V")],
            acq_name="Group0",
        )
    mdf.save(path, overwrite=True)
    mdf.close()
    return path


def _decode(path, monkeypatch=None, eager=False):
    if eager:
        monkeypatch.delattr(MDFReader, "open_sample_source")
    store = SignalStore()
    LoadWorker(str(path))._run_bulk_array(MDFReader(path), store)
    return store


@pytest.fixture()
def mf4(tmp_path):
    return _write_mf4(tmp_path / "lazy.mf4")


def test_decode_lists_every_signal_and_reads_no_samples(mf4):
    store = _decode(mf4)
    try:
        assert store.sample_cache is not None
        assert store.sample_cache.reads == 0
        series = [store.get_series(k) for k in store.all_keys()]
        assert series and all(isinstance(s, LazySignalSeries) for s in series)
        assert not any(s.is_loaded for s in series)
        assert store.total_samples == sum(s.sample_count for s in series)
        payload = store.build_tree_payload()[None]
        assert payload["Group0"] == ["S0", "S1", "S2", "State"]
    finally:
        store.close()


def test_lazy_signals_equal_what_eager_decode_stored(mf4, monkeypatch):
    lazy = _decode(mf4)
    with monkeypatch.context() as m:
        eager = _decode(mf4, m, eager=True)
    try:
        assert eager.sample_cache is None
        assert lazy.all_keys() == eager.all_keys()
        assert lazy.base_ts == eager.base_ts
        assert lazy.total_samples == eager.total_samples
        for key in eager.all_keys():
            want, got = eager.get_series(key), lazy.get_series(key)
            assert np.array_equal(got.numpy_timestamps(), want.numpy_timestamps()), key
            assert np.array_equal(got.numpy_values(), want.numpy_values()), key
            assert got.has_labels == want.has_labels, key
            assert list(got.raw_values) == list(want.raw_values), key
            assert got.unit == want.unit, key
            assert got.sample_count == len(want.timestamps), key
    finally:
        lazy.close()


def test_a_twin_group_merges_in_time_order(mf4):
    store = _decode(mf4)
    try:
        series = store.get_series("CH?::Group0::S0")
        ts = series.numpy_timestamps()
        assert len(ts) == 1000
        assert np.all(np.diff(ts) >= 0)
        assert (series.numpy_values() == 200).sum() == 500
    finally:
        store.close()


def test_channels_of_one_group_share_one_time_array(mf4):
    store = _decode(mf4)
    try:
        s1 = store.get_series("CH?::Group1::S1")
        s2 = store.get_series("CH?::Group1::S2")
        assert s1.timestamps is s2.timestamps
        assert store.sample_cache.reads == 1   # one read filled the group
    finally:
        store.close()


@pytest.fixture()
def six_groups(tmp_path):
    return _write_mf4(tmp_path / "six.mf4", groups=6, duplicate_group=False)


def _group_budget(cache, groups):
    # A group is read whole while it fits in a quarter of the budget, so
    # four groups is the least budget that still reads whole groups.
    return cache.resident_bytes * groups


def test_the_oldest_group_is_forgotten_past_the_budget(six_groups):
    store = _decode(six_groups)
    cache = store.sample_cache
    try:
        first = store.get_series("CH?::Group0::S1")
        values = first.numpy_values().copy()
        group_bytes = cache.resident_bytes
        cache.budget = _group_budget(cache, 4)
        for g in range(1, 5):
            store.get_series(f"CH?::Group{g}::S1").values
        assert cache.resident_bytes <= 4 * group_bytes
        assert not first.is_loaded
        assert store.get_series("CH?::Group1::S2").is_loaded
        # Read again on use, unchanged.
        assert np.array_equal(first.numpy_values(), values)
        assert cache.reads == 6
    finally:
        store.close()


def test_a_used_group_stays_and_the_unused_one_goes(six_groups):
    store = _decode(six_groups)
    cache = store.sample_cache
    try:
        a = store.get_series("CH?::Group0::S1")
        b = store.get_series("CH?::Group1::S1")
        a.values
        cache.budget = _group_budget(cache, 4)
        b.values
        store.get_series("CH?::Group2::S1").values
        store.get_series("CH?::Group3::S1").values
        a.values                    # used last: b is now the oldest
        store.get_series("CH?::Group4::S1").values
        assert a.is_loaded and not b.is_loaded
    finally:
        store.close()


def test_a_group_too_large_for_the_budget_is_read_one_channel_at_a_time(mf4):
    store = _decode(mf4)
    cache = store.sample_cache
    try:
        cache.budget = 1024   # every group is over a quarter of this
        s1 = store.get_series("CH?::Group1::S1")
        assert len(s1.values) == 500
        assert not store.get_series("CH?::Group1::S2").is_loaded
    finally:
        store.close()


def test_time_bounds_and_axes_come_from_the_time_channel_alone(mf4, monkeypatch):
    store = _decode(mf4)
    with monkeypatch.context() as m:
        eager = _decode(mf4, m, eager=True)
    try:
        assert store.time_bounds() == pytest.approx(eager.time_bounds())
        axes = store.time_axes()
        assert len(axes) == 4   # one per group, the twin included
        assert store.sample_cache.reads == 0
        assert not any(store.get_series(k).is_loaded for k in store.all_keys())
    finally:
        store.close()


def test_closing_the_store_releases_the_file(mf4):
    store = _decode(mf4)
    store.get_series("CH?::Group1::S1").values
    store.close()
    os.remove(mf4)   # Windows refuses while asammdf holds the file
    unread = store.get_series("CH?::Group2::S1")
    assert len(unread.values) == 0 and len(unread.timestamps) == 0


def test_assigning_samples_keeps_them_in_memory(mf4):
    store = _decode(mf4)
    cache = store.sample_cache
    try:
        series = store.get_series("CH?::Group1::S1")
        series.values = np.asarray(series.values) * 2
        cache.budget = 0
        store.get_series("CH?::Group2::S1").values
        assert series.is_loaded
        assert float(series.numpy_values().max()) <= 198
    finally:
        store.close()


def test_decode_memory_does_not_follow_the_file_size(tmp_path):
    """Decode's peak allocation is the same for a file ten times the size."""
    peaks = []
    for cycles in (2_000, 200_000):
        path = _write_mf4(tmp_path / f"size{cycles}.mf4", cycles=cycles, groups=20,
                          duplicate_group=False)
        reader = MDFReader(path)
        store = SignalStore()
        tracemalloc.start()
        try:
            LoadWorker(str(path))._run_bulk_array(reader, store)
            peaks.append(tracemalloc.get_traced_memory()[1])
        finally:
            tracemalloc.stop()
            store.close()
    # The larger file decodes to 128 MB. Decode itself holds the channel
    # list and, briefly, one data block for the first time stamp.
    sample_bytes = 20 * 4 * 200_000 * 8
    assert peaks[1] < sample_bytes / 16
    assert peaks[1] < 16 * 1024 * 1024
