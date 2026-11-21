# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The Trace window: two-level time scroll, search down to signals, Filter
and colours (owner-approved sketch, steps 1–3)."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent

from core import trace_query as tq
from core.bus_types import BusType
from tests.test_trace_query import COUNTS, EXPECTED, load, logs  # noqa: F401


@pytest.fixture
def window(qapp, logs, monkeypatch):  # noqa: F811
    from gui import raw_frame_dialog
    from gui.raw_frame_dialog import RawFrameDialog

    monkeypatch.setattr(raw_frame_dialog, "_WINDOW", 100)
    monkeypatch.setattr(raw_frame_dialog, "_STEP", 20)
    dialog = RawFrameDialog(load(logs, 'asc'))
    dialog.resize(900, 500)
    dialog.show()
    dialog.follow_button.setChecked(False)
    yield dialog
    dialog.close()
    dialog.deleteLater()


def _search(dialog, text):
    dialog.search_edit.setText(text)
    dialog._on_filter_changed()
    while dialog._search is not None:
        dialog._search_step()


def _rows(dialog):
    tree = dialog.tree
    return [tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(tree.topLevelItemCount())]


def _ids(dialog):
    from gui.raw_frame_dialog import _ID
    tree = dialog.tree
    return {tree.topLevelItem(i).text(_ID) for i in range(tree.topLevelItemCount())}


# ── Step 2: search finds, down to signals ────────────────────────────────

def test_search_finds_without_hiding_and_steps_through_hits(window):
    _search(window, 'Temp > 50')
    hits = window.hits()
    assert len(hits) == EXPECTED['Temp > 50']
    assert window._matching is None                 # every frame still shown
    # The first hit is current, and F3 / Shift+F3 walk the hits in order.
    assert window._current_frame() == hits[0]
    assert window.hit_label.text() == f'1 of {len(hits)}'
    for expected in hits[1:4]:
        window.find(forward=True)
        assert window._current_frame() == expected
    window.find(forward=False)
    assert window._current_frame() == hits[2]
    assert window.hit_label.text() == f'3 of {len(hits)}'


def test_find_wraps_round(window):
    _search(window, 'Temp > 50')
    hits = window.hits()
    window.find(forward=False)                      # before the first: the last
    assert window._current_frame() == hits[-1]
    window.find(forward=True)
    assert window._current_frame() == hits[0]


def test_a_hit_in_a_signal_opens_its_frame_and_marks_the_signal(window):
    from gui.raw_frame_dialog import _NAME

    _search(window, 'State = Fault')
    item = window.tree.currentItem()
    assert item.isExpanded()
    marked = [item.child(i).text(_NAME) for i in range(item.childCount())
              if item.child(i).background(_NAME).style() != Qt.BrushStyle.NoBrush]
    assert marked == ['State']


def test_a_hit_by_frame_name_does_not_open_the_frame(window):
    _search(window, 'battery')
    assert not window.tree.currentItem().isExpanded()


def test_hit_frames_are_marked_in_the_table(window):
    from gui.raw_frame_dialog import _NAME

    _search(window, 'Temp > 50')
    hits = set(window.hits().tolist())
    for row in range(window.tree.topLevelItemCount()):
        item = window.tree.topLevelItem(row)
        index = item.data(0, Qt.ItemDataRole.UserRole)
        marked = item.background(_NAME).style() != Qt.BrushStyle.NoBrush
        assert marked == (index in hits)


def test_the_funnel_shows_only_the_hits(window):
    _search(window, 'Speed > 30')
    window.only_hits_button.setChecked(True)
    assert np.array_equal(window._matching, window.hits())
    assert _ids(window) == {'200'}
    window.only_hits_button.setChecked(False)
    assert window._matching is None


def test_hits_tick_the_time_bar(window):
    _search(window, 'Rpm >= 1000')
    ticks = window.time_bar._ticks
    assert len(ticks) == 1
    bins = ticks[0][1]
    # Rpm passes 1000 half way through the log: no tick in its first half.
    half = len(bins) // 2
    assert not bins[:half - 2].any() and bins[half + 2:].any()


def test_no_match_says_so(window):
    _search(window, 'Temp > 500')
    assert window.hit_label.text() == 'No match'
    assert not window.find(forward=True)


# ── Step 3: Filter and colours ───────────────────────────────────────────

def test_the_filter_hides_a_channel_and_counts_on_its_button(window):
    window.set_channel_shown((BusType.CAN, 2), False)
    assert '200' not in _ids(window)
    assert window._total_matching() == len(window._raw_store) - COUNTS['drive']
    assert window.filter_button.text() == 'Filter (1) ▾'
    window.set_channel_shown((BusType.CAN, 2), True)
    assert window._matching is None
    assert window.filter_button.text() == 'Filter ▾'


def test_hits_the_filter_hides_are_not_stepped_to(window):
    _search(window, 'Temp > 50 or Speed > 30')
    window.set_channel_shown((BusType.CAN, 1), False)
    assert len(window.hits()) == EXPECTED['Speed > 30']
    assert window.hit_label.text().endswith(f'(+{EXPECTED["Temp > 50"]:,} filtered)')


def test_the_filter_popup_is_one_list_of_checkboxes(window):
    battery = tq.kind_key(0x100, 1, False, False)
    window.colour_id(battery, '#f85149')
    window.hide_id(tq.kind_key(0x200, 2, False, False))
    popup = window.show_filter()
    try:
        names = set(popup.boxes)
        assert {'rx', 'tx', 'data', 'errors', 'remote', 'colour:1', 'colour:0',
                'channel:CAN 1', 'channel:CAN 2'} <= names
        assert any(name.startswith('hide:') for name in names)
        popup.boxes['colour:0'].setChecked(False)         # only the coloured frames
        assert _ids(window) == {'100'}
        assert window._total_matching() == COUNTS['battery'] + 2     # + remote frames
        popup.boxes['errors'].setChecked(False)
        assert window.trace_filter.errors is False
        popup._reset()
        assert window.trace_filter == tq.TraceFilter()
    finally:
        popup.close()


def test_colour_this_id_tints_its_rows(window):
    from gui.raw_frame_dialog import _ID, _TIME

    window.colour_id(tq.kind_key(0x100, 1, False, False), '#3fb950')
    assert window.colour_counts()[1] == COUNTS['battery'] + 2       # + remote frames
    for row in range(window.tree.topLevelItemCount()):
        item = window.tree.topLevelItem(row)
        tinted = item.background(_TIME).style() != Qt.BrushStyle.NoBrush
        assert tinted == (item.text(_ID) == '100')


def test_colour_when_and_removing_a_colour(window):
    window.colour_when('Temp > 50', '#d29922')
    window.colour_id(tq.kind_key(0x200, 2, False, False), '#58a6ff')
    assert window.colour_counts()[1:] == [EXPECTED['Temp > 50'], COUNTS['drive']]
    window.set_colour_shown(2, False)                     # hide the drive colour
    assert '200' not in _ids(window)
    window.remove_colour_rule(1)                          # drive's colour moves up
    assert window.trace_filter.hidden_colours == frozenset({1})
    assert window.colour_counts()[1:] == [COUNTS['drive']]
    assert '200' not in _ids(window)


def test_hide_and_show_only_an_id(window):
    drive = tq.kind_key(0x200, 2, False, False)
    window.show_only_id(drive)
    assert _ids(window) == {'200'}
    window.hide_id(drive)
    assert '200' not in _ids(window) and len(_ids(window)) > 1
    window.forget_id(tq.id_key(drive))
    assert window._matching is None


def test_the_row_menus(window):
    from gui.raw_frame_dialog import _NAME, _SIGNAL_ROLE

    item = window.tree.topLevelItem(0)
    texts = [a.text() for a in window.row_menu(0.0, item).actions()]
    assert {'Set Cursor 1 here', 'Colour this ID', 'Hide this ID',
            'Show only this ID', 'Expand all on screen'} <= set(texts)
    item.setExpanded(True)
    signal = next(item.child(i) for i in range(item.childCount())
                  if item.child(i).data(_NAME, _SIGNAL_ROLE))
    texts = [a.text() for a in window.row_menu(0.0, signal).actions()]
    assert {'Colour when…', 'Find this signal'} <= set(texts)
    assert 'Hide this ID' not in texts


# ── Step 1: two-level time scroll ────────────────────────────────────────

def test_the_time_bar_spans_the_measurement_and_jumps(window):
    store = window._raw_store
    ts = np.frombuffer(store.timestamps, dtype=np.float64)
    bar = window.time_bar
    assert (bar._t0, bar._t1) == (ts[0], ts[len(store) - 1])
    middle = (ts[0] + ts[len(store) - 1]) / 2
    bar.timeRequested.emit(middle)
    window._go_bar_time()
    start, end = bar.window()
    assert start <= middle <= end
    assert window._win_start > 0


def test_clicking_the_time_bar_asks_for_its_time(window, qapp):
    from PySide6.QtTest import QTest

    asked = []
    bar = window.time_bar
    bar.timeRequested.connect(asked.append)
    y = bar.y_of((bar._t0 + bar._t1) * 0.75)
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(10, y))
    assert asked and abs(asked[0] - (bar._t0 + bar._t1) * 0.75) < (bar._t1 - bar._t0) * 0.02


def test_big_steps_move_a_window_of_frames(window):
    from gui import raw_frame_dialog
    window.big_step(+1)
    assert window._win_start == raw_frame_dialog._WINDOW
    window.big_step(-1)
    assert window._win_start == 0


def test_a_small_step_past_the_end_loads_the_next_frames(window, qapp):
    from gui import raw_frame_dialog

    tree = window.tree
    last = tree.topLevelItem(tree.topLevelItemCount() - 1)
    tree.setCurrentItem(last)
    frame = window._current_frame()
    down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    qapp.sendEvent(tree, down)
    assert window._win_start == raw_frame_dialog._STEP
    # The same frame stays current, and Down moved on from it.
    assert window._current_frame() == frame + 1


def test_scrolling_the_table_past_its_end_slides_on(window, qapp):
    from gui import raw_frame_dialog

    bar = window.tree.verticalScrollBar()
    bar.setValue(bar.maximum())
    wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(window.tree.viewport(), wheel)
    assert window._win_start == raw_frame_dialog._STEP
    window.tree.verticalScrollBar().setValue(0)
    up = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(window.tree.viewport(), up)
    assert window._win_start == 0


def test_ctrl_end_and_home_reach_the_whole_measurement(window, qapp):
    tree = window.tree
    tree.setCurrentItem(tree.topLevelItem(0))
    end = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    qapp.sendEvent(tree, end)
    assert window._current_frame() == len(window._raw_store) - 1
    home = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
    qapp.sendEvent(tree, home)
    assert window._current_frame() == 0


def test_cursor_1_shows_on_the_time_bar(window):
    window.follow_time(1.5)
    assert window.time_bar._cursor == 1.5


def test_the_old_paging_controls_are_gone(window):
    for name in ('_nb_start', '_nb_back', '_nb_fwd', '_nb_end', '_scrollbar',
                 'channel_combo', 'expand_btn', 'collapse_btn'):
        assert not hasattr(window, name), name
