# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The Trace's rows are written when first shown, and its columns fitted
without measuring every cell again: a step of the Trace (Cursor 1 moved to
another part of the file) took 0.35 s for 5,000 rows of which forty are seen.

Nothing here times anything. Each test counts work (rows written, cells
measured, tables rebuilt), so it holds on a slow PC as on a fast one.
"""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import Qt

from core.bus_types import channel_label
from core.trace_differences import window_differences
from tests.test_trace_differences import _store
from tests.test_trace_query import FORMATS, load, logs  # noqa: F401

COUNT = 12_000


@pytest.fixture()
def big(qapp, tmp_path):
    """12,000 frames of seven IDs, their data all different, in a Trace
    shown 900 x 500."""
    from gui.raw_frame_dialog import RawFrameDialog

    rng = np.random.default_rng(11)
    store = _store(np.arange(COUNT) * 0.001, [1 + n % 2 for n in range(COUNT)],
                   [0x100 + n % 7 for n in range(COUNT)],
                   rows=rng.integers(0, 256, (COUNT, 8)),
                   lengths=[1 + n % 8 for n in range(COUNT)])
    dialog = RawFrameDialog(store, settings_path=tmp_path / 'settings.json')
    dialog.resize(900, 500)
    dialog.show()
    qapp.processEvents()
    yield dialog
    dialog.close()
    dialog.deleteLater()
    store.close()


def _written(dialog) -> int:
    """Rows of the table whose cells have been worked out."""
    tree = dialog.tree
    return sum(tree.topLevelItem(row)._rows is None
               for row in range(tree.topLevelItemCount()))


def _expected_rows(store, indices, previous):
    """What each row shows, worked out here the plain way, cell by cell."""
    delta, changed = window_differences(store, previous, indices)
    rows = []
    for position, rec in enumerate(store.get_window(indices)):
        data = rec.data[:rec.dlc]
        marks = ''.join('1' if c else '0' for c in changed[position, :len(data)])
        rows.append((
            [f'{rec.time_s:.6f}',
             '' if np.isnan(delta[position]) else f'{delta[position] * 1000:.3f}',
             channel_label(rec.channel_key),
             f'{rec.arbitration_id:X}',
             rec.frame_name or ('(decoded)' if rec.decoded else '(unmatched)'),
             rec.direction,
             str(rec.dlc),
             ' '.join(f'{b:02X}' for b in data)],
            marks if '1' in marks else None,
            rec.decoded))
    return rows


# ── Rows are written when first shown, and read as they always did ───────

def test_a_step_writes_only_the_rows_it_needs(big, qapp):
    from gui.raw_frame_dialog import _WINDOW

    big._set_window(6_000)
    qapp.processEvents()
    tree = big.tree
    assert tree.topLevelItemCount() == _WINDOW
    # Written: the rows on screen and the few measured for the column widths.
    assert _written(big) < 200
    # A row far down the table is not written until asked for, and then
    # reads as its frame.
    far = tree.topLevelItem(4_000)
    assert far._rows is not None
    # Its frame is known without writing it (the Trace finds rows by it).
    assert far.data(0, Qt.ItemDataRole.UserRole) == 6_000 + 4_000
    assert far._rows is not None
    assert far.text(0) == f'{(6_000 + 4_000) * 0.001:.6f}'
    assert far._rows is None
    assert far.data(0, Qt.ItemDataRole.UserRole) == 6_000 + 4_000


@pytest.mark.parametrize('fmt', FORMATS)
def test_every_row_reads_as_before_in_every_format(qapp, logs, fmt):  # noqa: F811
    from gui.raw_frame_dialog import (_CHANGED_ROLE, _COL, _DELTA, _OPENS,
                                      RawFrameDialog)

    store = load(logs, fmt)
    dialog = RawFrameDialog(store)
    try:
        dialog.show()
        qapp.processEvents()
        tree = dialog.tree
        count = tree.topLevelItemCount()
        assert count == min(len(store), 5_000)
        expected = _expected_rows(store, list(range(count)), dialog._previous.previous)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        opened = 0
        for row, (texts, marks, decoded) in enumerate(expected):
            item = tree.topLevelItem(row)
            assert [item.text(col) for col in range(_COL)] == texts, row
            assert item.data(7, _CHANGED_ROLE) == marks, row
            assert item.data(0, Qt.ItemDataRole.UserRole) == row
            assert item.textAlignment(_DELTA) == right
            # A decoded frame has its arrow and no row under it until opened.
            assert (item.childIndicatorPolicy() == _OPENS) == decoded, row
            assert item.childCount() == 0
            opened += decoded
        assert opened, 'the log decodes some frames'
    finally:
        dialog.close()
        dialog.deleteLater()


def test_an_opened_frame_shows_its_signals_once(qapp, logs):  # noqa: F811
    from gui.raw_frame_dialog import _NAME, _OPENS, RawFrameDialog

    dialog = RawFrameDialog(load(logs, 'asc'))
    try:
        dialog.show()
        qapp.processEvents()
        tree = dialog.tree
        item = next(tree.topLevelItem(row) for row in range(tree.topLevelItemCount())
                    if tree.topLevelItem(row).childIndicatorPolicy() == _OPENS)
        item.setExpanded(True)
        names = [item.child(n).text(_NAME) for n in range(item.childCount())]
        assert names and '…' not in names
        assert item.childIndicatorPolicy() != _OPENS
        item.setExpanded(False)
        item.setExpanded(True)
        assert [item.child(n).text(_NAME) for n in range(item.childCount())] == names
    finally:
        dialog.close()
        dialog.deleteLater()


def test_every_row_is_one_height(qapp, logs):  # noqa: F811
    # The table is told its rows are equally high, so Qt places a row by its
    # number instead of asking every row above it. That must be true of what
    # it shows: frames, and the signals under an opened frame.
    from gui.raw_frame_dialog import _OPENS, RawFrameDialog

    dialog = RawFrameDialog(load(logs, 'asc'))
    try:
        dialog.resize(900, 500)
        dialog.show()
        tree = dialog.tree
        assert tree.uniformRowHeights()
        tree.setUniformRowHeights(False)        # each row now answers for itself
        item = next(tree.topLevelItem(row) for row in range(tree.topLevelItemCount())
                    if tree.topLevelItem(row).childIndicatorPolicy() == _OPENS)
        item.setExpanded(True)
        qapp.processEvents()
        rows = [tree.topLevelItem(row) for row in range(tree.indexOfTopLevelItem(item) + 1)]
        rows += [item.child(n) for n in range(item.childCount())]
        heights = {tree.visualItemRect(row).height() for row in rows}
        assert item.childCount() and len(heights) == 1 and min(heights) > 0, heights
    finally:
        dialog.close()
        dialog.deleteLater()


def test_rows_not_yet_written_stay_quiet_once_the_measurement_is_closed(big, qapp):
    big._set_window(0)
    qapp.processEvents()
    item = big.tree.topLevelItem(4_500)
    assert item._rows is not None
    big._raw_store.close()
    assert item.text(0) == ''           # nothing to read, and no error


def test_rows_coloured_and_found_are_tinted_when_written(big, qapp):
    from gui.raw_frame_dialog import _NAME

    big.colour_id(0x103, '#f85149')
    big.search_edit.setText('105')
    big._on_filter_changed()
    while big._search is not None:
        big._search_step()
    tree = big.tree
    start = big._win_start
    for row in (3_000, 3_001, 3_002, 3_003, 3_004, 3_005, 3_006):
        item = tree.topLevelItem(row)
        arb_id = 0x100 + (start + row) % 7
        tinted = item.background(0).style() != Qt.BrushStyle.NoBrush
        assert tinted == (arb_id == 0x103), hex(arb_id)
        found = item.background(_NAME).color().name() == '#e3b341'
        assert found == (arb_id == 0x105), hex(arb_id)


# ── Column widths: as Qt fits them, without measuring every cell ─────────

def _qt_widths(dialog) -> list[int]:
    """The width Qt fits each column to, and the width it then has. (The
    last column fills the room left, so only the first shows its fit.)"""
    from gui.raw_frame_dialog import _COL
    tree, header = dialog.tree, dialog.tree.header()
    fitted = [max(tree.sizeHintForColumn(col), header.sectionSizeHint(col)) for col in range(_COL)]
    for col in range(_COL):
        tree.resizeColumnToContents(col)
    return fitted + [header.sectionSize(col) for col in range(_COL)]


def _widths(dialog) -> list[int]:
    from gui.raw_frame_dialog import _COL
    return list(dialog._fitted) + [dialog.tree.header().sectionSize(col) for col in range(_COL)]


@pytest.mark.parametrize('scroll_to', [None, 2_500, 4_999])
def test_columns_are_as_wide_as_qt_fits_them(big, qapp, scroll_to):
    tree = big.tree
    for start in (0, 3_000, COUNT - 5_000):
        big._set_window(start)
        if scroll_to is not None:
            tree.scrollToItem(tree.topLevelItem(scroll_to))
            big._render_window()
        assert _widths(big) == _qt_widths(big), (start, scroll_to)


def test_the_rows_measured_are_the_rows_qt_measures(big, qapp, monkeypatch):
    from gui import raw_frame_dialog
    from gui.raw_frame_dialog import _DATA, _DataDelegate

    asked: list[int] = []

    class Recording(_DataDelegate):
        def sizeHint(self, option, index):
            asked.append(index.row())
            return super().sizeHint(option, index)

    tree = big.tree
    usual = tree.itemDelegateForColumn(_DATA)
    recording = Recording(big)
    for window, scroll_to in ((5_000, None), (5_000, 30), (5_000, 2_500), (5_000, 4_999),
                              (5_000, 4_970), (1_000, None), (1_000, 999), (999, 500),
                              (1_200, 1_199), (40, None), (3, None)):
        monkeypatch.setattr(raw_frame_dialog, '_WINDOW', window)
        big._set_window(0)
        if scroll_to is not None:
            tree.scrollToItem(tree.topLevelItem(scroll_to))
        qapp.processEvents()
        mine = big._measured_rows(tree.topLevelItemCount())
        tree.setItemDelegateForColumn(_DATA, recording)
        tree.executeDelayedItemsLayout()    # a new delegate lays the rows out again
        asked.clear()
        tree.resizeColumnToContents(_DATA)
        tree.setItemDelegateForColumn(_DATA, usual)
        assert mine == asked, (window, scroll_to)       # the same rows, in Qt's order


def test_a_cell_drawn_bold_is_given_its_bold_width(big, qapp):
    # Changed bytes are bold, and a bold text is wider: the column must be
    # fitted to it. Without the highlight it is fitted to the plain text.
    big._set_window(2_000)
    assert _widths(big) == _qt_widths(big)
    big.set_option('highlight_changes', False)
    big._set_window(4_000)
    assert _widths(big) == _qt_widths(big)


def test_the_widest_data_is_found_plain_and_bold(qapp, tmp_path):
    # The widest plain text and the widest bold one are different cells here:
    # 0x100 never changes; 0x200 changes every frame, so it is drawn bold.
    from gui.raw_frame_dialog import _DATA, RawFrameDialog

    count = 2_400
    ids = [0x100 if n % 3 else 0x200 for n in range(count)]
    rows = [[0xDD] * 7 + [0x11] if n % 3 else [0xDD] * 7 + [0x10 + n % 2] for n in range(count)]
    # Far down, past the rows measured, one wider than any: it must not count.
    rows[2_000] = [0xDD] * 8
    ids[2_000] = 0x300
    store = _store(np.arange(count) * 0.001, [1] * count, ids, rows=rows)
    dialog = RawFrameDialog(store, settings_path=tmp_path / 'settings.json')
    try:
        dialog.resize(900, 500)
        dialog.show()
        qapp.processEvents()
        for highlight in (True, False):
            dialog.set_option('highlight_changes', highlight)
            dialog._set_window(0)
            ours = dialog._fitted[_DATA]
            assert ours == max(dialog.tree.sizeHintForColumn(_DATA),
                               dialog.tree.header().sectionSizeHint(_DATA)), highlight
    finally:
        dialog.close()
        dialog.deleteLater()
        store.close()


def test_a_step_asks_qt_for_no_width_it_already_has(big, qapp):
    tree = big.tree
    big._set_window(3_000)
    asked = []
    size_hint = tree.sizeHintForIndex
    tree.sizeHintForIndex = lambda index: (asked.append(index.row()), size_hint(index))[1]
    big._set_window(3_000)              # the same frames again: all known
    # Of the Data cells, all different, the widest plain and the widest bold.
    assert len(asked) <= 2, len(asked)
    widths = _widths(big)
    del tree.sizeHintForIndex
    assert widths == _qt_widths(big)


def test_a_new_font_measures_again(big, qapp):
    tree = big.tree
    big._set_window(3_000)
    font = tree.font()
    font.setPointSizeF(font.pointSizeF() + 5)
    tree.setFont(font)
    big._set_window(3_000)
    assert _widths(big) == _qt_widths(big)


# ── Following Cursor 1 ───────────────────────────────────────────────────

def _time_of_current(dialog):
    index = dialog._current_frame()
    return None if index is None else dialog._frame_time(index)


def test_following_inside_the_frames_loaded_rebuilds_nothing(big, qapp):
    big.follow_button.setChecked(True)
    big._set_window(2_000)
    qapp.processEvents()
    rebuilt = []
    render = big._render_window
    big._render_window = lambda: (rebuilt.append(1), render())[1]
    before = _written(big)
    for frame in (2_100, 4_000, 6_900, 2_001, 5_555):       # all loaded
        big.follow_time(frame * 0.001)
        big._follow_now()
        qapp.processEvents()
        assert _time_of_current(big) == pytest.approx(frame * 0.001)
    assert not rebuilt
    # Each move wrote the rows it brought on screen, no more.
    assert _written(big) - before < 5 * 60


def test_following_to_another_part_rebuilds_once(big, qapp):
    big.follow_button.setChecked(True)
    big._set_window(0)
    qapp.processEvents()
    rebuilt = []
    render = big._render_window
    big._render_window = lambda: (rebuilt.append(1), render())[1]
    big.follow_time(9.5)
    big._follow_now()
    qapp.processEvents()
    assert len(rebuilt) == 1
    assert _time_of_current(big) == pytest.approx(9.5)
    assert _written(big) < 200
