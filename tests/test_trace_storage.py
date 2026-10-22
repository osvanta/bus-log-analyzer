# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The CAN Trace keeps 8 bytes a frame on disk and searches without Python per frame.

A classic CAN or LIN frame keeps its 8 bytes; a CAN FD frame's payload goes,
up to its last non-zero word, to an overflow file. Search works per distinct
ID, name or field value, a chunk of frames at a time. These tests hold both to
what the fixed 64-byte records and the per-frame search gave.
"""
from __future__ import annotations

import array
import os
import time
import tracemalloc

import numpy as np
import pytest

import core.raw_frame_store as raw_frame_store
from core.bus_types import BusType
from core.raw_frame_store import FLAG_LIN, FLAG_LONG, RawFrameStore


# ── Payload ──────────────────────────────────────────────────────────────

def _payload_rows(count, seed=0):
    """Classic, CAN FD of every length, stray bytes past a short length, zeros."""
    rng = np.random.default_rng(seed)
    lengths = rng.choice([0, 3, 8, 12, 20, 32, 48, 64], count)
    rows = np.zeros((count, 64), dtype=np.uint8)
    for length in np.unique(lengths):
        chosen = lengths == length
        rows[chosen, :length] = rng.integers(0, 256, (chosen.sum(), length), dtype=np.uint8)
    rows[5, 40] = 0x11              # a stray byte past the length: kept exactly
    rows[6, 63] = 0x22              # the very last byte only
    rows[7, 8] = 0x33               # the first byte past the eighth
    return lengths.astype(np.uint8), rows


def _columns(count, lengths):
    return (
        np.arange(count, dtype=np.float64) * 0.001,
        np.ones(count, dtype=np.uint8),
        np.full(count, 0x123, dtype=np.uint32),
        lengths,
        np.zeros(count, dtype=np.uint8),
        np.zeros(count, dtype=np.uint8),
    )


def _assert_payload(store, rows, lengths):
    store.seal()
    assert np.array_equal(store.rows(slice(0, len(rows))), rows)
    assert np.array_equal(store.rows(np.array([7, 0, 6])), rows[[7, 0, 6]])
    flags = np.asarray(store.flags)
    assert np.array_equal((flags & FLAG_LONG) != 0, rows[:, 8:].any(axis=1))
    for record, row, length in zip(store.get_window(range(len(rows))), rows, lengths):
        assert record.data == row[:length].tobytes()


@pytest.mark.parametrize("path", ["raw_batch", "raw_batch_lists", "numpy_batch",
                                  "numpy_narrow", "append", "append_raw"])
def test_every_append_path_keeps_every_byte(path):
    count = 500
    lengths, rows = _payload_rows(count)
    if path == "numpy_narrow":
        rows[:, 8:] = 0             # an 8-byte-wide DataBytes column
    store = RawFrameStore()
    ts, ch, ids, dl, dirs, fl = _columns(count, lengths)
    if path == "raw_batch":
        store.append_raw_batch(ts, ch, ids, dl, dirs, fl, rows.tobytes())
    elif path == "raw_batch_lists":
        store.append_raw_batch(ts.tolist(), ch.tolist(), ids.tolist(), dl.tolist(),
                               dirs.tolist(), fl.tolist(), bytearray(rows.tobytes()))
    elif path == "numpy_batch":
        store.append_numpy_batch(ts, ch, ids, dl, dirs, fl, rows)
    elif path == "numpy_narrow":
        store.append_numpy_batch(ts, ch, ids, dl, dirs, fl, rows[:, :8])
    elif path == "append":
        for i in range(count):
            store.append(float(ts[i]), 1, 0x123, int(dl[i]), 'Rx', False, False,
                         rows[i].tobytes(), '', False)
    else:
        for i in range(count):
            store.append_raw(float(ts[i]), 1, 0x123, int(dl[i]), 0, False, False,
                             rows[i].tobytes())
    _assert_payload(store, rows, lengths)
    store.close()


def test_classic_frames_take_eight_bytes_and_fd_only_its_words():
    count = 1000
    lengths, rows = _payload_rows(count)
    store = RawFrameStore()
    store.append_raw_batch(*_columns(count, lengths), rows.tobytes())
    store.seal()
    payload = store._payload
    assert os.path.getsize(payload.short_path) == count * 8
    long = rows[:, 8:].any(axis=1)
    words = 8 - np.argmax((rows.view('<u8') != 0)[:, ::-1], axis=1)
    assert payload.long_count == int(long.sum())
    assert payload.long_bytes == int((words[long] * 8).sum())
    store.close()


@pytest.mark.parametrize("merge", [False, True])
def test_sorting_keeps_each_frames_payload(merge, monkeypatch):
    """Two interleaved runs, sorted in memory or merged from the column files."""
    monkeypatch.setattr(raw_frame_store, "_MERGE_BLOCK_ROWS", 7)
    count = 300
    lengths, rows = _payload_rows(count, seed=3)
    _ts, ch, _ids, dl, dirs, fl = _columns(count, lengths)
    ts = np.concatenate([np.arange(count // 2) * 2.0, np.arange(count - count // 2) * 2.0 + 1])
    ids = np.arange(count, dtype=np.uint32)        # each frame's original row
    store = RawFrameStore()
    first = 0
    if not merge:
        # A frame appended on its own first keeps the columns in memory.
        store.append(-1.0, 1, 0x1, 8, 'Rx', False, False, bytes(8), '', False)
        first = 1
    store.append_numpy_batch(ts, ch, ids, dl, dirs, fl, rows)
    assert store.sort_by_time() is True
    store.seal()
    original = np.asarray(store.arb_ids)[first:]
    assert np.array_equal(store.rows(slice(first, first + count)), rows[original])
    assert np.array_equal((np.asarray(store.flags)[first:] & FLAG_LONG) != 0,
                          rows[original, 8:].any(axis=1))
    store.close()


# ── Search ───────────────────────────────────────────────────────────────

def _reference_mask(store, needle, channel_filter, data_search=True):
    """The per-frame search the trace had: the reference the new one must equal."""
    n = len(store)
    if not needle and channel_filter is None:
        return None
    chs = np.asarray(store.channels)
    fls = np.asarray(store.flags)
    aids = np.asarray(store.arb_ids)
    nids = np.asarray(store.name_ids)
    dirs = np.asarray(store.directions)
    dlcs = np.asarray(store.dlcs)
    tss = np.asarray(store.timestamps)
    mask = np.ones(n, dtype=bool)
    if channel_filter is not None:
        bus, number = channel_filter
        mask &= (chs == number) & (((fls & FLAG_LIN) != 0) == (bus is BusType.LIN))
    if not needle:
        return mask
    low = needle.lower()
    id_mask = np.zeros(n, dtype=bool)
    try:
        id_mask = aids == int(low.replace(' ', '').lstrip('0x'), 16)
    except ValueError:
        pass
    if not id_mask.any() and len(low) <= 8 and all(c in '0123456789abcdef' for c in low):
        id_mask = np.array([low in f'{a:X}'.lower() for a in aids.tolist()])
    names = [i for i, nm in enumerate(store.name_table) if low in nm.lower()]
    text = id_mask | np.isin(nids, names)
    if low in 'rx':
        text |= dirs == 0
    if low in 'tx':
        text |= dirs == 1
    try:
        text |= np.abs(tss - float(low)) < 0.001
    except ValueError:
        pass
    hexed = low.replace(' ', '')
    if (data_search and len(low) >= 2 and len(hexed) >= 2 and len(hexed) % 2 == 0
            and all(c in '0123456789abcdef' for c in hexed)):
        rows = store.rows(slice(0, n))
        pattern = np.frombuffer(bytes.fromhex(hexed), np.uint8)
        for offset in range(64 - len(pattern) + 1):
            text |= np.all(rows[:, offset:offset + len(pattern)] == pattern, axis=1)
    if not text.any():
        for i in np.flatnonzero(mask):
            hay = (f"{'can ' + str(chs[i]) if chs[i] != 255 else 'can ?'} {aids[i]:x} "
                   f"{(store.name_table[nids[i]] if nids[i] < len(store.name_table) else '').lower()} "
                   f"{['Rx', 'Tx', 'Unknown'][min(dirs[i], 2)].lower()} {dlcs[i]}")
            text[i] = low in hay
    return mask & text


@pytest.fixture(scope="module")
def searched_store():
    rng = np.random.default_rng(11)
    count = 6000
    lengths, rows = _payload_rows(count, seed=11)
    store = RawFrameStore()
    ids = rng.choice(np.array([0x1A0, 0x7FF, 0x18FEF100, 0x21, 0x1A01], np.uint32), count)
    channels = rng.choice(np.array([1, 2, 255], np.uint8), count)
    flags = (ids > 0x7FF).astype(np.uint8)
    flags[channels == 2] |= FLAG_LIN
    store.append_raw_batch(
        np.cumsum(rng.uniform(0, 0.01, count)), channels, ids, lengths,
        rng.choice(np.array([0, 1, 2], np.uint8), count), flags, rows.tobytes(),
    )
    store.seal()
    store.name_table += ["EngineSpeed", "Gear Box", "Msg1A"]
    np.asarray(store.name_ids)[:] = (ids % 4).astype(np.uint16)
    yield store
    store.close()


NEEDLES = ["1a0", "0x1a0", "7ff", "1a", "1A01", "fef1", "a", "c", "an", "?", "msg",
           "gear box", "gear b", "engine", "rx", "x", "unknown", "can 1", "can ?",
           "1 1a0", "zz9", "64", "12", "0.05", "ff 00", "33", "x 2", " "]
FILTERS = [None, (BusType.CAN, 1), (BusType.LIN, 2)]


@pytest.mark.parametrize("needle", NEEDLES)
def test_search_finds_what_the_per_frame_search_found(searched_store, needle, monkeypatch):
    monkeypatch.setattr(raw_frame_store, "_SEARCH_CHUNK", 1000)   # many chunks
    needle = needle.strip().lower()        # as the window passes it
    for channel_filter in FILTERS:
        want = _reference_mask(searched_store, needle, channel_filter)
        got = searched_store.build_match_mask(needle, channel_filter)
        if want is None:
            assert got is None
        else:
            assert np.array_equal(got, want), (needle, channel_filter)


@pytest.mark.parametrize("needle", ["48", "64", "20", "3"])
def test_lengths_are_found_in_a_store_too_large_for_data_search(
    searched_store, needle, monkeypatch
):
    """Without the data-byte search a frame length is found only in its text."""
    monkeypatch.setattr(raw_frame_store, "_LARGE_STORE_LIMIT", 0)
    for channel_filter in FILTERS:
        want = _reference_mask(searched_store, needle, channel_filter, data_search=False)
        got = searched_store.build_match_mask(needle, channel_filter)
        assert np.array_equal(got, want), (needle, channel_filter)
        if channel_filter is None:
            assert got.any()


def test_an_odd_number_of_hex_digits_no_longer_breaks_a_small_search(searched_store):
    searched_store.matching_indices("1a0", None)   # raised ValueError before


def test_searching_a_long_trace_runs_no_python_per_frame():
    count = 2_000_000
    store = RawFrameStore()
    ids = np.tile(np.arange(0x100, 0x140, dtype=np.uint32), count // 64)
    zeros = np.zeros(count, np.uint8)
    store.append_raw_batch(np.arange(count, dtype=np.float64), zeros + 1, ids,
                           zeros + 8, zeros, zeros, bytes(count * 64))
    store.seal()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        for needle in ("13", "zz9", "1a"):     # ID substring, text fallback, no match
            store.matching_indices(needle, None)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    elapsed = time.perf_counter() - started
    store.close()
    # Per frame, these built strings for every frame: seconds and gigabytes.
    assert peak < 48 * 1024 * 1024
    assert elapsed < 10


# ── The window ───────────────────────────────────────────────────────────

def test_typing_searches_once_after_a_pause(qapp, searched_store, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QElapsedTimer

    from gui.raw_frame_dialog import _SEARCH_PAUSE_MS, RawFrameDialog

    dialog = RawFrameDialog(searched_store)
    calls = []
    real = searched_store.matching_indices
    monkeypatch.setattr(searched_store, "matching_indices",
                        lambda *a: calls.append(a) or real(*a))
    for typed in ("m", "ms", "msg"):
        dialog.search_edit.setText(typed)
    assert calls == []
    clock = QElapsedTimer()
    clock.start()
    while not calls and clock.elapsed() < _SEARCH_PAUSE_MS * 10:
        QCoreApplication.processEvents()
    assert calls == [("msg", None)]
    dialog.search_edit.setText("gear")
    dialog.search_edit.returnPressed.emit()      # Enter searches at once
    assert calls[-1] == ("gear", None)
    assert isinstance(dialog._matching, np.ndarray)
    assert not isinstance(dialog._matching, array.array)
    dialog.close()
