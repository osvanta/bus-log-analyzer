# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The Trace's Δt column, its changed-byte highlight and its following of plot
Cursor 1. Δt and the changed bytes compare a frame with the previous frame of
the same kind: the same ID on the same bus and channel, standard and extended
apart, error and remote frames apart; over the whole file, whatever the
Trace's filter shows.

Requires a Qt platform plugin for the dialog tests; skipped (not failed) if
none is available.
"""
from __future__ import annotations

import numpy as np
import pytest

from core import trace_differences
from core.raw_frame_store import (FLAG_ERROR, FLAG_EXTENDED, FLAG_LIN,
                                  FLAG_REMOTE, RawFrameStore)
from core.trace_differences import (PreviousFrames, previous_frames,
                                    window_differences)

KIND = FLAG_EXTENDED | FLAG_LIN | FLAG_ERROR | FLAG_REMOTE


def _store(times, channels, ids, flags=None, rows=None, lengths=None):
    count = len(times)
    store = RawFrameStore()
    store.append_numpy_batch(
        np.asarray(times, dtype=np.float64),
        np.asarray(channels, dtype=np.uint8),
        np.asarray(ids, dtype=np.uint32),
        np.asarray(lengths if lengths is not None else [8] * count, dtype=np.uint8),
        np.zeros(count, dtype=np.uint8),
        np.asarray(flags if flags is not None else [0] * count, dtype=np.uint8),
        np.asarray(rows if rows is not None else np.zeros((count, 8)), dtype=np.uint8))
    store.seal()
    return store


def _brute_force(channels, ids, flags):
    last, previous = {}, []
    for index, key in enumerate(zip(channels, ids, (f & KIND for f in flags))):
        previous.append(last.get(key, -1))
        last[key] = index
    return previous


@pytest.fixture()
def random_store():
    rng = np.random.default_rng(7)
    count = 3_000
    channels = rng.integers(1, 3, count)
    ids = rng.integers(0x100, 0x110, count)
    flags = rng.choice([0, FLAG_EXTENDED, FLAG_LIN, FLAG_ERROR, FLAG_REMOTE, 2], count)
    store = _store(np.arange(count) * 0.001, channels, ids, flags)
    yield store, _brute_force(channels.tolist(), ids.tolist(), flags.tolist())
    store.close()


# ── The index ────────────────────────────────────────────────────────────


def test_previous_frame_of_the_same_kind(random_store):
    store, expected = random_store
    assert PreviousFrames(store).build().tolist() == expected


def test_chunks_carry_the_last_frame_over(random_store, monkeypatch):
    store, expected = random_store
    monkeypatch.setattr(trace_differences, 'CHUNK', 97)
    index = PreviousFrames(store)
    steps = 1
    while not index.step():
        steps += 1
        assert 0 < index.progress < 1
    assert steps == -(-len(store) // 97)
    assert index.previous.tolist() == expected


def test_kinds_are_kept_apart():
    # 0x100 standard, extended, on channel 2, on LIN, as an error frame and
    # as a remote frame: six kinds, then each once more.
    flags = [0, FLAG_EXTENDED, 0, FLAG_LIN, FLAG_ERROR, FLAG_REMOTE] * 2
    channels = [1, 1, 2, 1, 1, 1] * 2
    store = _store(np.arange(12.0), channels, [0x100] * 12, flags)
    try:
        assert PreviousFrames(store).build().tolist() == [-1] * 6 + list(range(6))
    finally:
        store.close()


def test_one_index_per_store_until_it_changes(random_store):
    store, _expected = random_store
    assert previous_frames(store) is previous_frames(store)
    other = _store([0.0], [1], [1])
    try:
        assert previous_frames(other) is not previous_frames(store)
    finally:
        other.close()


def test_a_closed_store_stops_the_work(random_store, monkeypatch):
    store, _expected = random_store
    monkeypatch.setattr(trace_differences, 'CHUNK', 100)
    index = PreviousFrames(store)
    index.step()
    store.close()
    with pytest.raises(RuntimeError):
        index.step()


# ── Δt and changed bytes ─────────────────────────────────────────────────


def test_delta_time_and_changed_bytes():
    rows = np.array([[1, 2, 3, 4, 5, 6, 7, 8],
                     [9, 9, 9, 9, 9, 9, 9, 9],       # another ID in between
                     [1, 2, 0, 4, 5, 6, 7, 0]], dtype=np.uint8)
    store = _store([1.0, 1.004, 1.010], [1, 1, 1], [0x100, 0x200, 0x100], rows=rows)
    try:
        previous = PreviousFrames(store).build()
        delta, changed = window_differences(store, previous, [0, 1, 2])
        assert np.isnan(delta[0]) and np.isnan(delta[1])
        assert delta[2] == pytest.approx(0.010)
        assert not changed[:2].any()
        assert np.flatnonzero(changed[2]).tolist() == [2, 7]
    finally:
        store.close()


def test_a_longer_frame_counts_its_new_bytes_as_changed():
    rows = np.zeros((2, 12), dtype=np.uint8)
    store = _store([0.0, 0.1], [1, 1], [0x100, 0x100], rows=rows, lengths=[4, 12])
    try:
        _delta, changed = window_differences(store, PreviousFrames(store).build(), [1])
        assert np.flatnonzero(changed[0]).tolist() == list(range(4, 12))
    finally:
        store.close()


def test_a_remote_frame_has_no_changed_bytes():
    rows = np.array([[0] * 8, [5] * 8], dtype=np.uint8)
    store = _store([0.0, 0.1], [1, 1], [0x100, 0x100], [FLAG_REMOTE] * 2, rows=rows)
    try:
        delta, changed = window_differences(store, PreviousFrames(store).build(), [1])
        assert delta[0] == pytest.approx(0.1) and not changed.any()
    finally:
        store.close()


# ── The Trace window ─────────────────────────────────────────────────────


@pytest.fixture()
def trace_store():
    """0x100 every 10 ms on channel 1, its byte 0 counting up; 0x200 every
    25 ms on channel 2, unchanged."""
    times, channels, ids, rows = [], [], [], []
    for n in range(40):
        times.append(n * 0.010)
        channels.append(1)
        ids.append(0x100)
        rows.append([n, 0xAA, 0, 0, 0, 0, 0, 0])
    for n in range(16):
        times.append(n * 0.025 + 0.001)
        channels.append(2)
        ids.append(0x200)
        rows.append([7] * 8)
    order = np.argsort(times, kind='stable')
    store = _store(np.asarray(times)[order], np.asarray(channels)[order],
                   np.asarray(ids)[order], rows=np.asarray(rows)[order])
    yield store
    store.close()


@pytest.fixture()
def dialog(qapp, trace_store, tmp_path):
    from gui.raw_frame_dialog import RawFrameDialog
    d = RawFrameDialog(trace_store, settings_path=tmp_path / 'settings.json')
    d.show()
    qapp.processEvents()
    yield d
    d.close()
    d.deleteLater()


def _rows(dialog):
    from gui.raw_frame_dialog import _CHN, _DATA, _DELTA, _ID
    tree = dialog.tree
    return [(tree.topLevelItem(i).text(_ID), tree.topLevelItem(i).text(_CHN),
             tree.topLevelItem(i).text(_DELTA), tree.topLevelItem(i).text(_DATA))
            for i in range(tree.topLevelItemCount())]


def test_the_trace_shows_delta_time_per_id(dialog):
    from gui.raw_frame_dialog import _DELTA, _HEADERS
    assert dialog.tree.headerItem().text(_DELTA) == 'Δt (ms)' == _HEADERS[1]
    rows = _rows(dialog)
    by_id = {}
    for frame_id, _chn, delta, _data in rows:
        by_id.setdefault(frame_id, []).append(delta)
    assert by_id['100'][0] == '' and by_id['200'][0] == ''
    assert set(by_id['100'][1:]) == {'10.000'}
    assert set(by_id['200'][1:]) == {'25.000'}


def test_changed_bytes_are_marked(dialog):
    from gui.raw_frame_dialog import _CHANGED_ROLE, _DATA, _ID
    tree = dialog.tree
    marks = {}
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        marks.setdefault(item.text(_ID), []).append(item.data(_DATA, _CHANGED_ROLE))
    assert marks['100'][0] is None                     # the first of its ID
    assert set(marks['100'][1:]) == {'10000000'}       # byte 0 counts up
    assert set(marks['200']) == {None}                 # never changes


def test_a_filter_does_not_change_delta_time(dialog, qapp):
    # Showing only every frame of 0x100 whose data has "AA": still 10 ms.
    dialog.search_edit.setText('100')
    dialog._on_filter_changed()
    rows = _rows(dialog)
    assert {r[0] for r in rows} == {'100'}
    assert {r[2] for r in rows[1:]} == {'10.000'}


def test_highlighted_bytes_are_drawn_bold_and_coloured(dialog, qapp):
    from PySide6.QtGui import QFont, QPainter
    from gui.raw_frame_dialog import _DATA, _DataDelegate
    delegate = dialog.tree.itemDelegateForColumn(_DATA)
    assert isinstance(delegate, _DataDelegate)
    drawn = []
    real_draw = QPainter.drawText

    def record(painter, *args):
        drawn.append((args[-1], painter.font().bold(), painter.pen().color().name()))
        return real_draw(painter, *args)

    QPainter.drawText = record
    try:
        dialog.tree.viewport().grab()
    finally:
        QPainter.drawText = real_draw
    accent = _DataDelegate.colour(dialog.tree.palette()).name()
    bold = {(text, colour) for text, is_bold, colour in drawn if is_bold}
    assert bold and {colour for _text, colour in bold} == {accent}
    assert ('AA', False) in {(text, is_bold) for text, is_bold, _c in drawn}
    assert QFont().bold() is False


def test_header_menu_hides_delta_and_highlight_and_remembers(dialog, tmp_path, qapp,
                                                             trace_store):
    from gui.raw_frame_dialog import RawFrameDialog, _DATA
    actions = {a.text(): a for a in dialog.header_menu().actions()}
    assert set(actions) == {'Δt column', 'Highlight changed bytes'}
    assert all(a.isChecked() for a in actions.values())
    actions['Δt column'].trigger()
    assert not dialog.delta_column_shown()
    actions['Highlight changed bytes'].trigger()
    assert not dialog.highlight_changes
    delegate = dialog.tree.itemDelegateForColumn(_DATA)
    item = dialog.tree.topLevelItem(5)
    assert delegate._changes(dialog.tree.indexFromItem(item, _DATA)) == ''
    again = RawFrameDialog(trace_store, settings_path=tmp_path / 'settings.json')
    try:
        assert not again.delta_column_shown() and not again.highlight_changes
        assert again.follow_button.isChecked()
    finally:
        again.deleteLater()


def test_a_large_file_is_indexed_in_steps(qapp, trace_store, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QElapsedTimer
    from gui.raw_frame_dialog import RawFrameDialog
    monkeypatch.setattr(trace_differences, 'CHUNK', 10)
    trace_differences._INDEXES.clear()
    d = RawFrameDialog(trace_store)
    try:
        assert not d._previous.ready
        assert 'Δt: working out' in d.status_label.text()
        assert {r[2] for r in _rows(d)} == {''}
        clock = QElapsedTimer()
        clock.start()
        while not d._previous.ready and clock.elapsed() < 5000:
            QCoreApplication.processEvents()
        QCoreApplication.processEvents()
        assert 'working out' not in d.status_label.text()
        assert '10.000' in {r[2] for r in _rows(d)}
    finally:
        d.deleteLater()
        trace_differences._INDEXES.clear()


# ── Following Cursor 1 ───────────────────────────────────────────────────


def _time_of_current(dialog):
    from gui.raw_frame_dialog import _TIME
    return float(dialog.tree.currentItem().text(_TIME))


def test_picking_a_frame_asks_for_cursor_1(dialog):
    from gui.raw_frame_dialog import _TIME
    asked = []
    dialog.timeClicked.connect(asked.append)
    item = dialog.tree.topLevelItem(7)
    dialog.tree.setCurrentItem(item)
    assert asked == [pytest.approx(float(item.text(_TIME)))]
    dialog.follow_button.setChecked(False)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(9))
    assert len(asked) == 1


def test_cursor_1_brings_its_frame_into_view(dialog, qapp):
    asked = []
    dialog.timeClicked.connect(asked.append)
    dialog.follow_time(0.2555)
    dialog._follow_now()
    # The last frame at or before 0.2555 s: 0x200 at 0.251 s.
    assert _time_of_current(dialog) == pytest.approx(0.251)
    assert dialog.cursor_label.text() == 'Cursor 1: 0.255500 s'
    assert asked == []                         # following does not ask back


def test_following_waits_for_the_cursor_to_rest(dialog, qapp):
    from PySide6.QtCore import QCoreApplication, QElapsedTimer
    for t in (0.05, 0.10, 0.15):
        dialog.follow_time(t)
    assert dialog.tree.currentItem() is None
    clock = QElapsedTimer()
    clock.start()
    while dialog.tree.currentItem() is None and clock.elapsed() < 2000:
        QCoreApplication.processEvents()
    assert _time_of_current(dialog) == pytest.approx(0.15)


def test_a_frame_off_screen_is_scrolled_to(qapp, monkeypatch, tmp_path):
    from gui import raw_frame_dialog
    monkeypatch.setattr(raw_frame_dialog, '_WINDOW', 10)
    count = 100
    store = _store(np.arange(count) * 0.1, [1] * count, [0x100] * count)
    d = raw_frame_dialog.RawFrameDialog(store)
    try:
        d.show()
        d.follow_time(7.25)
        d._follow_now()
        assert d._win_start <= 72 < d._win_start + 10
        assert _time_of_current(d) == pytest.approx(7.2)
    finally:
        d.close()
        d.deleteLater()
        store.close()


def test_the_same_frame_stays_picked_when_its_cursor_comes_back(qapp):
    # Two frames share a time: picking the first moves Cursor 1 there, and
    # Cursor 1 coming back must not move the pick to the other one.
    from gui.raw_frame_dialog import RawFrameDialog
    store = _store([0.0, 0.5, 0.5, 0.9], [1, 1, 2, 1], [0x100, 0x100, 0x200, 0x100])
    d = RawFrameDialog(store)
    try:
        d.show()
        d.timeClicked.connect(d.follow_time)
        picked = d.tree.topLevelItem(1)
        d.tree.setCurrentItem(picked)
        d._follow_now()
        assert d.tree.currentItem() is picked
    finally:
        d.close()
        d.deleteLater()
        store.close()


def test_the_row_menu_sets_either_cursor(dialog):
    one, two = [], []
    dialog.timeClicked.connect(one.append)
    dialog.cursor2Requested.connect(two.append)
    actions = {a.text(): a for a in dialog.row_menu(1.5).actions()}
    actions['Set Cursor 1 here'].trigger()
    actions['Set Cursor 2 here'].trigger()
    assert (one, two) == ([1.5], [1.5])


def test_the_main_window_wires_the_trace_to_the_plot(qapp, trace_store, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from gui import app_data
    from gui.main_window import MainWindow
    monkeypatch.setenv(app_data.DATA_DIR_ENV_VAR, str(tmp_path))
    window = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        window.store = SimpleNamespace(raw_frame_store=trace_store)
        window.btn_cursor1.setChecked(True)
        window.plot_panel.move_cursor1(0.3)
        window.show_raw_frames()
        trace = window._raw_frame_dialog
        assert trace.cursor_label.text() == 'Cursor 1: 0.300000 s'
        trace._follow_now()
        assert _time_of_current(trace) == pytest.approx(0.3)
        window.plot_panel.move_cursor1(0.1)
        assert trace._follow_time == pytest.approx(0.1)
        trace.tree.setCurrentItem(trace.tree.topLevelItem(2))
        assert window.plot_panel.v_line.value() == pytest.approx(_time_of_current(trace))
        trace.cursor2Requested.emit(0.35)
        assert window.btn_cursor2.isChecked()
        assert window.plot_panel.v_line2.value() == pytest.approx(0.35)
        trace.close()
    finally:
        window.store = None
        window.close()
        window.deleteLater()
