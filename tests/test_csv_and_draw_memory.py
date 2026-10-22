# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""CSV signal files load in bounded memory, each signal whole and on its own;
a long curve's first draw makes no copy of it.

A CSV was read one sample at a time through a call that caches a message's
series by position, so every signal of a message after the first was poured
into the first. It is now read a chunk of rows at a time into arrays per
signal. pyqtgraph worked out on every first draw whether the data was finite,
copying it; the panel now tells it what it checked.
"""
from __future__ import annotations

import tracemalloc

import numpy as np
import pytest

import core.readers.csv_reader as csv_reader
import core.signal_store as signal_store
from core.signal_store import SignalSeries


def _load(path):
    from gui.load_worker import LoadWorker

    worker = LoadWorker(str(path))
    result = {}
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda error: result.setdefault("error", error))
    worker.run()
    assert "error" not in result, result.get("error")
    return result["store"]


def _series(store):
    """Each signal's times, values and labels, NaN as None so they compare."""
    def plain(values):
        return [None if value != value else value for value in values]

    return {
        key: (store.get_series(key).numpy_timestamps().tolist(),
              plain(store.get_series(key).numpy_values().tolist()),
              plain(store.get_series(key).raw_values) if store.get_series(key).has_labels else None)
        for key in store.all_keys()
    }


# ── Each signal on its own ───────────────────────────────────────────────

def test_a_wide_csv_keeps_each_signal_apart(wide_csv_path):
    store = _load(wide_csv_path)
    got = _series(store)
    assert sorted(got) == ["CH?::CSV::EngSpeed", "CH?::CSV::Throttle"]
    assert got["CH?::CSV::EngSpeed"][1] == [600.0, 602.5, 605.0]
    assert got["CH?::CSV::Throttle"][1] == [25.0, 26.0, 27.0]
    assert got["CH?::CSV::Throttle"][0] == pytest.approx([0.0, 0.001, 0.002])
    # One time axis for both: every row has a value for each.
    assert (store.get_series("CH?::CSV::EngSpeed").timestamps
            is store.get_series("CH?::CSV::Throttle").timestamps)


def test_a_narrow_csv_keeps_each_signal_and_its_labels(narrow_csv_path):
    got = _series(_load(narrow_csv_path))
    assert got["CH1::EngineControl::EngSpeed"][1] == [600.0, 602.5, 605.0]
    assert got["CH1::EngineControl::Throttle"][1] == [25.0, 26.0]
    # Text in "value", its number in "raw_value".
    assert got["CH1::GearStatus::Gear"][1:] == ([4.0, 4.0], ["Drive", "Drive"])


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_a_wide_csv_reads_with_its_own_delimiter(tmp_path, delimiter):
    path = tmp_path / "wide.csv"
    rows = [["Time [s]", "A [V]", "B"], ["0.0", "1", "off"], ["0.5", "", "on"], ["1.0", "3", "7"]]
    path.write_text("\n".join(delimiter.join(row) for row in rows) + "\n")
    got = _series(_load(path))
    assert got["CH?::CSV::A"][:2] == ([0.0, 1.0], [1.0, 3.0])
    ts, values, labels = got["CH?::CSV::B"]
    assert ts == [0.0, 0.5, 1.0]
    assert values == [None, None, 7.0]
    assert labels == ["off", "on", 7.0]


# ── In chunks ────────────────────────────────────────────────────────────

def _awkward_wide(path, rows=1000):
    """Gaps, a signal missing for a whole stretch, one that starts late, text,
    and one with every value but those of rows 500-599: it shares the time axis
    until it misses a whole chunk."""
    rng = np.random.default_rng(5)
    lines = ["Timestamps [s],Full [V],Gappy,Late,Text,Pause"]
    for i in range(rows):
        full = f"{rng.normal():.6f}"
        gappy = "" if i % 7 == 0 or 300 <= i < 420 else f"{i}"
        late = f"{i * 2}" if i >= 650 else ""
        text = ("idle" if i % 3 else "run") if i % 5 else f"{i}"
        pause = "" if 500 <= i < 600 else f"{-i}"
        lines.append(f"{i * 0.01:.2f},{full},{gappy},{late},{text},{pause}")
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.mark.parametrize("chunk_rows", [1, 7, 100, 1 << 20])
@pytest.mark.parametrize("spill", [False, True])
def test_reading_in_chunks_equals_reading_whole(tmp_path, chunk_rows, spill, monkeypatch):
    path = _awkward_wide(tmp_path / "awkward.csv")
    want = _series(_load(path))
    monkeypatch.setattr(csv_reader, "_CHUNK_ROWS", chunk_rows)
    monkeypatch.setattr(csv_reader.CSVSignalReader.iter_signal_chunks, "__defaults__", (chunk_rows,))
    if spill:
        monkeypatch.setattr(signal_store, "default_cache_budget", lambda: 0)
    store = _load(path)
    assert _series(store) == want
    if spill:
        assert store.sample_file_bytes > 0
    assert len(want["CH?::CSV::Late"][0]) == 350
    assert len(want["CH?::CSV::Pause"][0]) == 900
    assert len(want["CH?::CSV::Gappy"][0]) == 1000 - 120 - len(
        [i for i in range(1000) if i % 7 == 0 and not 300 <= i < 420])
    store.close()


def test_a_large_csv_loads_in_bounded_memory(tmp_path, monkeypatch):
    """The heap holds a chunk of rows and the store's budget, not the file."""
    path = tmp_path / "large.csv"
    rows = 300_000
    t = np.arange(rows) * 0.001
    data = np.column_stack([t] + [np.sin(t * k) for k in range(1, 9)])
    np.savetxt(path, data, delimiter=",", fmt="%.6f",
               header="Time [s]," + ",".join(f"S{k}" for k in range(1, 9)), comments="")
    monkeypatch.setattr(csv_reader.CSVSignalReader.iter_signal_chunks, "__defaults__", (20_000,))
    monkeypatch.setattr(signal_store, "default_cache_budget", lambda: 1 << 20)
    tracemalloc.start()
    try:
        store = _load(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert store.total_samples == rows * 8
    assert store.get_series("CH?::CSV::S3").numpy_values()[-1] == pytest.approx(
        np.sin(t[-1] * 3), abs=1e-6)
    # 2.4 M samples: 38 MB as two float64 arrays each, far more as Python
    # sample objects. Read in chunks, the heap stays at a chunk's worth.
    assert peak < 32 * 1024 * 1024
    store.close()


# ── A long curve's first draw ────────────────────────────────────────────

@pytest.fixture()
def long_signal():
    n = 4_000_000
    ts = np.arange(n, dtype=np.float64) * 0.001
    return ts, np.sin(ts * 0.01) * 100


def _draw_peak(panel, qapp, ts, vs):
    tracemalloc.start()
    try:
        panel.add_series("Long", SignalSeries(None, "Msg", 1, "Long", "V", ts, vs))
        qapp.processEvents()
        panel.grab()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_the_first_draw_of_finite_data_copies_none_of_it(panel, qapp, long_signal):
    ts, vs = long_signal
    # Before, pyqtgraph took a finite-only copy of x and a mask: 9 B a sample.
    assert _draw_peak(panel, qapp, ts, vs) < 4 * 1024 * 1024


def test_finite_times_are_trusted_even_with_gaps_in_the_values(panel, qapp, long_signal):
    ts, vs = long_signal
    vs = vs.copy()
    vs[10] = np.nan
    # The mask (1 B a sample) and pyqtgraph's finite-only y bounds (9 B) remain;
    # its copy of x for down-sampling (8 B) and our full-length temporaries do not.
    assert _draw_peak(panel, qapp, ts, vs) < 11 * len(ts)


def test_a_record_for_other_data_is_not_trusted(panel, qapp):
    from gui.plot_widget import _note_finite

    panel.add_series("S", SignalSeries(None, "Msg", 1, "S", "V",
                                       np.arange(5.0), np.arange(5.0)))
    qapp.processEvents()
    curve = panel._items["S"].curve
    checked = np.arange(10.0)
    _note_finite(curve, checked, True, True)
    other = np.array([0.0, 1.0, np.inf, 3.0])
    curve.setData(other, np.ones(4))
    dataset = curve._dataset
    assert dataset.xAllFinite is not True       # worked out by pyqtgraph: inf


def test_the_connection_mask_is_the_same_built_in_chunks(monkeypatch):
    import gui.plot_widget as plot_widget
    from gui.plot_widget import PlotPanel

    rng = np.random.default_rng(1)
    ts = np.sort(rng.uniform(0, 10, 50))
    ts[30] = 0.0
    vs = rng.normal(size=50)
    vs[[0, 9, 10, 49]] = [np.nan, np.inf, np.nan, np.nan]
    finite = np.isfinite(ts) & np.isfinite(vs)
    want = np.zeros(50, dtype=bool)
    want[:-1] = finite[:-1] & finite[1:] & (ts[1:] >= ts[:-1])
    for chunk in (1, 3, 7, 1 << 20):
        monkeypatch.setattr(plot_widget, "_RANGE_CHUNK", chunk)
        assert PlotPanel._curve_connect_mask(ts, vs).tolist() == want.tolist()
