# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
An MDF channel can hold several values per sample: a byte array (the payload
column of a bus frame), a member array of a structure. The store keeps one
number per time stamp, so such a channel is read as text: one label per
sample that shows its values, exactly as a structure channel already is.

Each test builds its own small file.
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

asammdf = pytest.importorskip("asammdf")

from core.readers.mdf_reader import MDFReader, _physical_arrays  # noqa: E402

N = 6
T = np.arange(N, dtype=np.float64) * 0.1
BYTES8 = np.arange(N * 8, dtype=np.uint8).reshape(N, 8)


def _lin_frames() -> np.recarray:
    frames = np.zeros(N, dtype=[
        ("LIN_Frame.BusChannel", "u1"), ("LIN_Frame.ID", "u1"),
        ("LIN_Frame.DataLength", "u1"), ("LIN_Frame.DataBytes", "(8,)u1"),
    ])
    frames["LIN_Frame.BusChannel"] = 1
    frames["LIN_Frame.ID"] = 0x20 + np.arange(N) % 2
    frames["LIN_Frame.DataLength"] = 8
    frames["LIN_Frame.DataBytes"] = BYTES8
    return np.rec.array(frames)


def _structure() -> np.recarray:
    # asammdf writes a member array only as bytes.
    rows = np.zeros(N, dtype=[("a", "u1"), ("member_array", "(4,)u1")])
    rows["a"] = np.arange(N)
    rows["member_array"] = np.arange(N * 4).reshape(N, 4) + 0xA0
    return np.rec.array(rows)


def _signals():
    from asammdf import Signal

    return [
        Signal(np.arange(N, dtype=np.float64) * 2.0, T, name="plain", unit="V"),
        Signal(BYTES8, T, name="bytes8"),
        Signal(_structure(), T, name="structure"),
    ]


@pytest.fixture(params=["4.10", "3.30"], ids=["mf4", "mdf3"])
def array_file(request, tmp_path):
    from asammdf import MDF

    version = request.param
    path = tmp_path / ("arrays.mf4" if version.startswith("4") else "arrays.mdf")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mdf = MDF(version=version)
        try:
            mdf.append(_signals(), comment="arrays")
        except Exception as exc:        # a shape this MDF version cannot hold
            pytest.skip(f"asammdf cannot write these channels as MDF {version}: {exc}")
        mdf.save(path, overwrite=True)
        mdf.close()
    MDFReader._content_cache.clear()
    return path


@pytest.fixture()
def lin_file(tmp_path):
    """A bus-logging MF4 of LIN frames, as opened without a database."""
    from asammdf import MDF, Signal

    path = tmp_path / "lin_frames.mf4"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mdf = MDF(version="4.10")
        mdf.append([Signal(_lin_frames(), T, name="LIN_Frame")], comment="LIN")
        mdf.save(path, overwrite=True)
        mdf.close()
    MDFReader._content_cache.clear()
    return path


def _load(path):
    from gui.load_worker import LoadWorker

    worker = LoadWorker(str(path))
    result = {}
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda error: result.setdefault("error", error))
    worker.run()
    assert "error" not in result, result.get("error")
    return result["store"]


def _assert_one_value_per_time(ts, values, labels, name):
    assert np.ndim(values) == 1, f"{name}: values have shape {np.shape(values)}"
    assert len(values) == len(ts), f"{name}: {len(values)} values for {len(ts)} times"
    assert len(labels) in (0, len(ts)), f"{name}: {len(labels)} labels for {len(ts)} times"


def _by_signal(store):
    return {key.rsplit("::", 1)[-1]: store.get_series(key) for key in store.all_keys()}


# ── The store: one value per time stamp ──────────────────────────────────

def test_a_loaded_array_channel_has_one_value_per_time(array_file):
    store = _load(array_file)
    try:
        signals = _by_signal(store)
        assert "bytes8" in signals and "plain" in signals
        for name, series in signals.items():
            _assert_one_value_per_time(
                series.numpy_timestamps(), series.numpy_values(),
                series.raw_values if series.has_labels else [], name)
            assert series.sample_count == N
        assert signals["plain"].numpy_values().tolist() == (np.arange(N) * 2.0).tolist()
    finally:
        store.close()


def test_a_byte_array_reads_as_one_label_per_sample(array_file):
    store = _load(array_file)
    try:
        series = _by_signal(store)["bytes8"]
        assert series.has_labels
        assert series.display_value_at(0) == "00 01 02 03 04 05 06 07"
        assert series.display_value_at(N - 1) == "28 29 2A 2B 2C 2D 2E 2F"
        assert list(series.raw_values[1:3]) == [
            "08 09 0A 0B 0C 0D 0E 0F", "10 11 12 13 14 15 16 17"]
        # Its number is the sample's position, as for any text without one.
        assert series.numpy_values().tolist() == list(range(N))
    finally:
        store.close()


def test_a_member_array_of_a_structure_reads_as_labels(array_file):
    store = _load(array_file)
    try:
        if array_file.suffix == ".mdf":
            pytest.skip("MDF 3 has no structures")
        series = _by_signal(store)["member_array"]
        assert series.has_labels
        assert series.display_value_at(1) == "A4 A5 A6 A7"
        assert series.numpy_values().tolist() == list(range(N))
    finally:
        store.close()


def test_lin_frame_columns_without_a_database(lin_file):
    store = _load(lin_file)
    try:
        signals = _by_signal(store)
        assert set(signals) >= {
            "LIN_Frame.ID", "LIN_Frame.DataLength", "LIN_Frame.DataBytes"}
        for name, series in signals.items():
            _assert_one_value_per_time(
                series.numpy_timestamps(), series.numpy_values(),
                series.raw_values if series.has_labels else [], name)
        payload = signals["LIN_Frame.DataBytes"]
        assert payload.display_value_at(2) == "10 11 12 13 14 15 16 17"
        assert signals["LIN_Frame.ID"].numpy_values().tolist() == [
            32.0, 33.0, 32.0, 33.0, 32.0, 33.0]
    finally:
        store.close()


# ── Every way the reader hands channels out ──────────────────────────────

@pytest.mark.parametrize("batch_all_groups", [False, True])
def test_the_eager_iterator_gives_one_value_per_time(array_file, batch_all_groups):
    reader = MDFReader(array_file)
    names = []
    for (_group, name, _unit), ts, values, labels in reader.iter_channel_arrays(
            batch_all_groups=batch_all_groups):
        _assert_one_value_per_time(ts, values, labels, name)
        names.append(name)
    assert "bytes8" in names


def test_the_per_sample_iterator_gives_scalars(array_file):
    samples = [s for s in MDFReader(array_file) if s.signal_name == "bytes8"]
    assert len(samples) == N
    assert samples[0].value == "00 01 02 03 04 05 06 07"
    assert samples[0].numeric_value == 0.0


def test_a_group_read_from_its_copy_gives_one_value_per_time():
    """A large group's channels are rebuilt from raw samples (_GroupCopy)."""
    values, labels = _physical_arrays(BYTES8, None)
    _assert_one_value_per_time(T, values, labels, "bytes8")
    assert labels[0] == "00 01 02 03 04 05 06 07"


def test_three_dimensions_are_labelled_too():
    cube = np.arange(N * 6, dtype=np.int16).reshape(N, 2, 3)
    values, labels = _physical_arrays(cube, None)
    _assert_one_value_per_time(T, values, labels, "cube")
    assert labels[0] == "[[0, 1, 2], [3, 4, 5]]"
    assert list(labels[4:]) == [
        "[[24, 25, 26], [27, 28, 29]]", "[[30, 31, 32], [33, 34, 35]]"]


def test_a_channel_array_block_keeps_every_element(tmp_path):
    """An MF4 array channel (CA block) with its axis: asammdf lists each
    element beside it, and the array itself is text. One dimension only:
    asammdf writes the elements of a second one at the wrong offsets."""
    from asammdf import MDF, Signal

    curve = np.arange(N * 3, dtype=np.uint16).reshape(N, 3)
    rows = np.zeros(N, dtype=[("curve", "(3,)<u2"), ("curve_x", "(3,)<f8")])
    rows["curve"], rows["curve_x"] = curve, [10.0, 20.0, 30.0]
    path = tmp_path / "channel_array.mf4"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mdf = MDF(version="4.10")
        mdf.append([Signal(np.rec.array(rows), T, name="curve")], comment="curve")
        mdf.save(path, overwrite=True)
        mdf.close()
    MDFReader._content_cache.clear()

    store = _load(path)
    try:
        signals = _by_signal(store)
        for name, series in signals.items():
            _assert_one_value_per_time(
                series.numpy_timestamps(), series.numpy_values(),
                series.raw_values if series.has_labels else [], name)
        assert signals["curve"].has_labels
        for element in range(3):
            assert (signals[f"curve[{element}]"].numpy_values().tolist()
                    == curve[:, element].tolist())
    finally:
        store.close()
