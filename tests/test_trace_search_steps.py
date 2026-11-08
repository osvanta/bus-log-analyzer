# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The CAN Trace searches a large measurement a chunk of frames per step.

TODO issue 22: on a 3 GB log (64 M frames) a search with a space ('ff 00')
froze the window for 7.6 s, numbering every frame's text on the GUI thread
before the first result, and other searches froze it for up to 2 s.
"""

from __future__ import annotations

import numpy as np
import pytest

import core.raw_frame_store as raw_frame_store
from tests.test_trace_storage import _reference_mask, searched_store  # noqa: F401

SPACED = ["gear box", "1 1a0", "ff 00", "x 2", "can ?"]


def _indices(store, needle, channel_filter=None):
    mask = _reference_mask(store, needle, channel_filter)
    return None if mask is None else np.flatnonzero(mask)


def test_a_search_runs_a_chunk_of_frames_per_step(searched_store, monkeypatch):
    monkeypatch.setattr(raw_frame_store, "_SEARCH_CHUNK", 1000)
    monkeypatch.setattr(searched_store, "_distinct_id_cache", None, raising=False)
    search = searched_store.search("gear box", None)
    progress = []
    while not search.step():
        progress.append(search.progress)
    # 6,000 frames: 6 steps for the distinct IDs, 6 for the frames.
    assert len(progress) >= 11
    assert progress == sorted(progress) and 0 < progress[0] <= progress[-1] <= 1
    assert search.progress == 1
    assert np.array_equal(search.result, _indices(searched_store, "gear box"))


@pytest.mark.parametrize("needle", SPACED)
def test_a_search_left_half_way_does_not_spoil_the_next(searched_store, needle, monkeypatch):
    monkeypatch.setattr(raw_frame_store, "_SEARCH_CHUNK", 1000)
    monkeypatch.setattr(searched_store, "_combination_index", None, raising=False)
    monkeypatch.setattr(searched_store, "_distinct_id_cache", None, raising=False)
    first = searched_store.search("1 1a0", None)      # found in the text only
    for _ in range(9):          # the distinct IDs, then half the frames
        first.step()
    del first
    for _ in range(2):          # the index finished, then read back
        got = searched_store.matching_indices(needle, None)
        assert np.array_equal(got, _indices(searched_store, needle)), needle


def test_the_frames_are_numbered_once(searched_store, monkeypatch):
    monkeypatch.setattr(raw_frame_store, "_SEARCH_CHUNK", 1000)
    monkeypatch.setattr(searched_store, "_combination_index", None, raising=False)
    searched_store.matching_indices("gear box", None)
    calls = []
    real = searched_store._combination_keys
    monkeypatch.setattr(searched_store, "_combination_keys",
                        lambda *a: calls.append(a) or real(*a))
    for needle in SPACED:
        searched_store.matching_indices(needle, None)
    assert calls == []


def test_a_search_of_a_closed_measurement_stops():
    rng = np.random.default_rng(3)
    count = 5000
    store = raw_frame_store.RawFrameStore()
    zeros = np.zeros(count, np.uint8)
    store.append_raw_batch(np.arange(count, dtype=np.float64), zeros + 1,
                           rng.integers(0x100, 0x110, count).astype(np.uint32),
                           zeros + 8, zeros, zeros, bytes(count * 64))
    store.seal()
    search = store.search("can 1", None)
    search.step()
    store.close()
    with pytest.raises(RuntimeError):
        search.run()


# ── The window ───────────────────────────────────────────────────────────

@pytest.fixture
def large_window(qapp, searched_store, monkeypatch):
    """The Trace window on a store it counts as large: searched in steps."""
    from gui import raw_frame_dialog
    from gui.raw_frame_dialog import RawFrameDialog

    monkeypatch.setattr(raw_frame_store, "_SEARCH_CHUNK", 1000)
    monkeypatch.setattr(raw_frame_dialog, "_small_file", lambda: 1000)
    dialog = RawFrameDialog(searched_store)
    while dialog._search is not None or not dialog._previous.ready:
        dialog._search_step() if dialog._search is not None else dialog._index_step()
    yield dialog
    dialog.close()
    dialog.deleteLater()


def test_the_window_shows_a_large_search_when_done(large_window, searched_store):
    dialog = large_window
    assert dialog._matching is None
    dialog.search_edit.setText("gear box")
    dialog._on_filter_changed()
    # Searching: the frames shown so far stay, and the status says so.
    assert dialog._search is not None
    assert dialog._matching is None
    assert "Searching" in dialog.status_label.text()
    steps = 0
    while dialog._search is not None:
        dialog._search_step()
        steps += 1
    assert steps >= 5
    assert np.array_equal(dialog._matching, _indices(searched_store, "gear box"))
    assert "Searching" not in dialog.status_label.text()


def test_a_new_search_replaces_the_one_under_way(large_window, searched_store):
    dialog = large_window
    dialog.search_edit.setText("gear box")
    dialog._on_filter_changed()
    dialog._search_step()
    dialog.search_edit.setText("msg")
    dialog._on_filter_changed()
    while dialog._search is not None:
        dialog._search_step()
    assert np.array_equal(dialog._matching, _indices(searched_store, "msg"))


def test_clearing_the_search_shows_every_frame_at_once(large_window):
    dialog = large_window
    dialog.search_edit.setText("msg")
    dialog._on_filter_changed()
    while dialog._search is not None:
        dialog._search_step()
    dialog.search_edit.setText("")
    dialog._on_filter_changed()
    assert dialog._search is None and dialog._matching is None


def test_the_delta_t_index_waits_for_a_search(qapp, searched_store, monkeypatch):
    from core import trace_differences
    from gui import raw_frame_dialog
    from gui.raw_frame_dialog import RawFrameDialog

    monkeypatch.setattr(raw_frame_store, "_SEARCH_CHUNK", 1000)
    monkeypatch.setattr(raw_frame_dialog, "_small_file", lambda: 1000)
    monkeypatch.setattr(trace_differences, "CHUNK", 500)
    monkeypatch.setattr(trace_differences, "_INDEXES", {})   # a new index, in steps
    dialog = RawFrameDialog(searched_store)
    try:
        assert not dialog._previous.ready and dialog._index_timer.isActive()
        dialog.search_edit.setText("gear box")
        dialog._on_filter_changed()
        assert dialog._search is not None
        assert not dialog._index_timer.isActive()
        while dialog._search is not None:
            dialog._search_step()
        assert dialog._index_timer.isActive()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_a_finished_search_redraws_the_window_once(large_window, monkeypatch):
    from gui import raw_frame_dialog

    dialog = large_window
    monkeypatch.setattr(raw_frame_dialog, "_WINDOW", 100)
    dialog._on_filter_changed()          # every frame: 60 windows
    while dialog._search is not None:
        dialog._search_step()
    # At the end, past where the next search's matches end: the scrollbar
    # moved to their end, then to the start, each redrawing the window.
    dialog._go_end()
    calls = []
    real = dialog._render_window
    monkeypatch.setattr(dialog, "_render_window", lambda: calls.append(1) or real())
    dialog.search_edit.setText("msg")
    dialog._on_filter_changed()
    while dialog._search is not None:
        dialog._search_step()
    assert len(calls) == 1
