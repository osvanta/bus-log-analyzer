# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A large MF4 group is read once, not once a channel.

A group too large to keep in memory is read one channel at a time, and
asammdf reads the whole group for each: seconds a signal for a logger's
gigabyte group, every signal plotted. After the first such read, the group is
copied channel by channel into a temporary file in the background; its other
channels then come from the copy, with exactly the values a direct read gives.
"""
from __future__ import annotations

import os
import time
from collections import namedtuple

import numpy as np
import pytest

asammdf = pytest.importorskip("asammdf")

import core.readers.mdf_reader as mdf_reader  # noqa: E402
import core.sample_file as sample_file  # noqa: E402
from core.readers.mdf_reader import MDFReader  # noqa: E402
from core.sample_file import PieceFile, SampleFileFull  # noqa: E402
from tests.test_lazy_mdf_signals import _decode  # noqa: E402

CYCLES = 1000


def _write(path):
    """One group of every kind of channel the copy meets, and a small one."""
    from asammdf import MDF, Signal

    mdf = MDF(version="4.10")
    rng = np.random.default_rng(3)
    t = np.arange(CYCLES, dtype=np.float64) * 0.01 + 2.0
    signals = [
        Signal(rng.integers(-500, 500, CYCLES).astype(np.int16), t, name="Scaled",
               unit="km/h", conversion={"a": 0.25, "b": -3.0}),
        Signal(rng.integers(0, 2, CYCLES).astype(np.uint8), t, name="State",
               conversion={"val_0": 0, "text_0": b"Off", "val_1": 1, "text_1": b"On"}),
        Signal(rng.normal(size=CYCLES), t, name="Float", unit="V"),
        Signal(rng.integers(-1 << 30, 1 << 30, CYCLES).astype(np.int32), t, name="Count"),
        Signal(rng.normal(size=CYCLES).astype(np.float32), t, name="Single"),
        # Three values a record: not a plain column, so the copy leaves it out.
        Signal(np.arange(3 * CYCLES, dtype=np.uint8).reshape(CYCLES, 3), t, name="Vector"),
        # Two channels of one name in one group: both belong to the series.
        Signal(np.full(CYCLES, 7, dtype=np.uint8), t, name="Dup"),
        Signal(np.full(CYCLES, 9, dtype=np.uint8), t, name="Dup"),
    ]
    mdf.append(signals, acq_name="Big")
    mdf.append([Signal(np.arange(10, dtype=np.uint8), np.arange(10.0), name="Small")],
               acq_name="Little")
    mdf.save(path, overwrite=True)
    mdf.close()
    return path


@pytest.fixture()
def mf4(tmp_path):
    return _write(tmp_path / "groups.mf4")


@pytest.fixture()
def quick(monkeypatch):
    """Copies start at once, rest not, and take a few records a chunk."""
    monkeypatch.setattr(mdf_reader, "_COPY_DELAY_S", 0)
    monkeypatch.setattr(mdf_reader, "_COPY_REST_S", 0)
    monkeypatch.setattr(mdf_reader, "_COPY_CHUNK_BYTES", 300)


def _source(path):
    source = MDFReader(path).open_sample_source()
    group = next(g for g in source.groups if g[1] == "Big")
    return source, group[0], sorted({name for _idx, name, _unit in group[2]})


def _finish(source):
    copier = source._copier
    if copier is not None:
        copier.join(30)
    assert source._copier is None


Read = namedtuple("Read", "timestamps values display unit")


def _parts(results, group, name):
    return [Read(np.asarray(ts), np.asarray(vs), list(ds), unit)
            for ts, vs, ds, unit in results[(group, name)]]


def _same(got, want):
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert np.array_equal(g.timestamps, w.timestamps)
        assert g.values.dtype == w.values.dtype
        assert np.array_equal(g.values, w.values)
        assert g.display == w.display
        assert g.unit == w.unit


def _read_direct(source, group, name):
    """A read that cannot use a copy: the path every read took before."""
    copies, source._copies = source._copies, {group: None}
    try:
        return _parts(source.read_groups([group], {name}), group, name)
    finally:
        source._copies = copies


# ── The piece file ───────────────────────────────────────────────────────

def test_pieces_of_any_type_are_joined_back():
    f = PieceFile()
    a = f.append(np.array([1, 2, 3], dtype=np.int16))
    b = f.append(np.array([0.5, 1.5]))
    c = f.append(np.array([4, 5], dtype=np.int16))
    f.seal()
    assert f.read([a, c]).tolist() == [1, 2, 3, 4, 5]
    assert f.read([a, c]).dtype == np.int16
    assert f.read([b]).tolist() == [0.5, 1.5]
    path = f.path
    f.close()
    assert not os.path.exists(path)


def test_no_room_on_the_disk_is_refused(monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(sample_file.shutil, "disk_usage", lambda _p: usage(1, 1, 10 << 20))
    with pytest.raises(SampleFileFull):
        PieceFile(1 << 20)


# ── The copy ─────────────────────────────────────────────────────────────

def test_every_channel_from_the_copy_equals_the_direct_read(mf4, quick):
    source, group, names = _source(mf4)
    try:
        source.read_groups([group], {names[0]})
        _finish(source)
        assert source._copies[group] is not None
        for name in names:
            got = _parts(source.read_groups([group], {name}), group, name)
            _same(got, _read_direct(source, group, name))
        # The duplicate name: both channels, in file order.
        assert [p.values[0] for p in _parts(source.read_groups([group], {"Dup"}), group, "Dup")] \
            == [7.0, 9.0]
        # Text through a conversion: the raw number, and the text to show.
        state = _parts(source.read_groups([group], {"State"}), group, "State")[0]
        assert set(state.values) <= {0.0, 1.0} and set(state.display) <= {"Off", "On"}
    finally:
        source.close()


def test_later_reads_of_the_group_do_not_touch_the_file(mf4, quick, monkeypatch):
    source, group, names = _source(mf4)
    try:
        assert source._copier is None
        source.read_groups([group], {"Scaled"})
        _finish(source)
        direct = []
        real = MDFReader._iter_arrays_all_groups
        monkeypatch.setattr(MDFReader, "_iter_arrays_all_groups",
                            staticmethod(lambda *a, **k: direct.append(a) or real(*a, **k)))
        for name in ("Float", "Count", "Single", "Scaled"):
            source.read_groups([group], {name})
        assert direct == []
    finally:
        source.close()


def test_whole_group_reads_start_no_copy(mf4, quick):
    source, group, _names = _source(mf4)
    try:
        source.read_groups([group])
        assert source._copier is None and source._copies == {}
    finally:
        source.close()


def test_a_channel_the_copy_cannot_take_is_read_directly(mf4, quick):
    source, group, _names = _source(mf4)
    try:
        source.read_groups([group], {"Scaled"})
        _finish(source)
        assert source._copies[group] is not None
        got = _parts(source.read_groups([group], {"Vector"}), group, "Vector")
        _same(got, _read_direct(source, group, "Vector"))
        assert got[0].values.shape == (CYCLES, 3)
    finally:
        source.close()


def test_no_room_for_a_copy_leaves_the_group_read_directly(mf4, quick, monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(sample_file.shutil, "disk_usage", lambda _p: usage(1, 1, 0))
    source, group, _names = _source(mf4)
    try:
        want = _read_direct(source, group, "Float")
        _same(_parts(source.read_groups([group], {"Float"}), group, "Float"), want)
        _finish(source)
        assert source._copies == {group: None}
        _same(_parts(source.read_groups([group], {"Float"}), group, "Float"), want)
        assert source._copier is None          # not tried again
    finally:
        source.close()


def test_closing_stops_a_copy_and_its_file_goes(mf4, quick, monkeypatch):
    monkeypatch.setattr(mdf_reader, "_COPY_REST_S", 0.05)   # a slow copy
    made = []
    real = PieceFile.__init__

    def record(self, *a, **k):
        real(self, *a, **k)
        made.append(self.path)

    monkeypatch.setattr(PieceFile, "__init__", record)
    source, group, _names = _source(mf4)
    source.read_groups([group], {"Float"})
    until = time.monotonic() + 10
    while not made and time.monotonic() < until:
        time.sleep(0.01)
    copier = source._copier
    start = time.monotonic()
    source.close()
    # Stopped between two chunks, not left to finish (a hundred chunks).
    assert time.monotonic() - start < 1.0
    assert not copier.is_alive()
    assert source._copies == {}
    assert not os.path.exists(made[0])


def test_a_copy_starts_once_the_read_signal_is_drawn(mf4, quick, monkeypatch):
    monkeypatch.setattr(mdf_reader, "_COPY_DELAY_S", 0.3)
    started = []
    real = PieceFile.__init__

    def record(self, *a, **k):
        started.append(time.monotonic())
        real(self, *a, **k)

    monkeypatch.setattr(PieceFile, "__init__", record)
    source, group, _names = _source(mf4)
    try:
        read = time.monotonic()
        source.read_groups([group], {"Float"})
        _finish(source)
        # Less a clock tick: Windows' timers step by about 15 ms.
        assert started and started[0] - read >= 0.25
    finally:
        source.close()


# ── Through the store ────────────────────────────────────────────────────

def test_a_large_group_through_the_store_equals_the_eager_decode(mf4, quick, monkeypatch):
    store = _decode(mf4)
    with monkeypatch.context() as m:
        eager = _decode(mf4, m, eager=True)
    cache = store.sample_cache
    try:
        cache.budget = 1024   # every group is over a quarter of this
        keys = [k for k in eager.all_keys() if "::Big::" in k]
        store.get_series(keys[0]).values
        _finish(cache._source)
        assert cache._source._copies
        for key in keys:
            want, got = eager.get_series(key), store.get_series(key)
            assert np.array_equal(got.numpy_timestamps(), want.numpy_timestamps()), key
            assert np.array_equal(got.numpy_values(), want.numpy_values()), key
            assert list(got.raw_values) == list(want.raw_values), key
            assert got.unit == want.unit, key
    finally:
        store.close()
