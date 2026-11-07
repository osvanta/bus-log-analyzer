# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
MultiStack: the signal table split into one section per stack, each level
with its stack and as tall as it (gui.stack_sections).

The panel is laid out as in the main window: its table panel left of it in a
splitter. Requires a Qt platform plugin; skipped (not failed) if none is
available.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest


def _series(name, unit='u', n=50):
    from core.signal_store import SignalSeries
    t = np.linspace(0.0, 1.0, n)
    return SignalSeries(channel=1, message_name='M', message_id=0x100, signal_name=name,
                        unit=unit, timestamps=t, values=np.arange(n, dtype=float),
                        raw_values=[], has_labels=False)


@pytest.fixture()
def host(panel, qapp):
    """The panel beside its table panel, as in the main window."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSplitter
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.addWidget(panel.table_panel)
    splitter.addWidget(panel)
    splitter.setSizes([320, 880])
    splitter.resize(1200, 700)
    splitter.show()
    qapp.processEvents()
    yield splitter
    splitter.hide()
    # Both back out of the splitter, as the bare panel fixture has them, so
    # deleting the splitter deletes neither.
    panel.setParent(None)
    panel.table_panel.setParent(None)
    qapp.processEvents()


def _settle(qapp, rounds=4):
    for _ in range(rounds):
        qapp.processEvents()


@pytest.fixture()
def stacks(panel, host, qapp, monkeypatch):
    """MultiStack with A+B in one stack, then C, then D. The unit warning
    (a modal box) is answered Continue and its questions recorded."""
    for name, unit in (('A', 'x'), ('B', 'x'), ('C', 'y'), ('D', 'z')):
        panel.add_series(name, _series(name, unit))
    panel.set_multistack(True)
    panel.move_signals_to_stack(['B'], panel._items['A'].multistack_id, confirm_units=False)
    panel.unit_questions = []
    monkeypatch.setattr(panel, 'confirm_multistack_units',
                        lambda keys, stack, extra_units=None:
                        panel.unit_questions.append(list(keys)) or True)
    _settle(qapp)
    return panel


def _stack_of(panel, key):
    return panel._items[key].multistack_id


def _section(panel, key):
    return panel.stack_sections().sections()[_stack_of(panel, key)]


def _listed(panel):
    return [section.keys() for section in panel.stack_sections().sections().values()]


def _plot_area(panel, row):
    """Top and height of a stack's plot area in the sections' coordinates."""
    area = panel.stack_sections()
    rect = panel.glw.mapFromScene(panel._stacked_plots[row].vb.sceneBoundingRect()).boundingRect()
    return area.mapFromGlobal(panel.glw.mapToGlobal(rect.topLeft())).y(), rect.height()


def _assert_level(panel):
    """Each section covers its stack's plot area and no other: the first
    from right under the header, each next one from the line above it, each
    to the line below it (the last to its plot area's bottom)."""
    area = panel.stack_sections()
    sections = list(area.sections().values())
    lines = [line.top() for line in area.separators()]
    assert len(sections) == len(panel._stacked_plots)
    for row, section in enumerate(sections):
        top, height = _plot_area(panel, row)
        geometry = section.geometry()
        upper = 0 if row == 0 else lines[row - 1]
        lower = lines[row] if row < len(lines) else top + height
        assert (geometry.top(), geometry.top() + geometry.height()) == (upper, lower), \
            (row, geometry, upper, lower)
        assert geometry.top() <= max(0, top) and geometry.bottom() >= top + height - 1
        if row + 1 < len(sections):
            assert geometry.bottom() < _plot_area(panel, row + 1)[0]


class _Drop:
    """A drop event at *y* in a section's viewport."""

    def __init__(self, mime, y):
        from PySide6.QtCore import QPointF
        self._mime, self._pos, self.accepted = mime, QPointF(5, y), None

    def mimeData(self):
        return self._mime

    def position(self):
        return self._pos

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def _reorder_mime(panel, keys):
    from PySide6.QtCore import QByteArray, QMimeData
    mime = QMimeData()
    mime.setData(panel.table._ROW_REORDER_MIME, QByteArray('\n'.join(keys).encode()))
    return mime


def _row_y(section, row, below=False):
    rect = section.visualRect(section.model().index(row, 1))
    return rect.bottom() - 2 if below else rect.top() + 2


# ── What each section lists ──────────────────────────────────────────────


def test_one_section_per_stack_listing_its_signals(stacks):
    assert _listed(stacks) == [['A', 'B'], ['C'], ['D']]
    assert list(stacks.stack_sections().sections()) == stacks._stacked_stack_ids
    assert stacks.stack_sections().isVisible()
    assert stacks.table.height() < 60                   # the table is its header only


def test_sections_follow_a_new_and_a_removed_stack(stacks, qapp):
    assert stacks.move_signals_to_new_stack(['B'])
    _settle(qapp)
    assert _listed(stacks) == [['A'], ['B'], ['C'], ['D']]
    _assert_level(stacks)
    stacks.remove_series('C')
    _settle(qapp)
    assert _listed(stacks) == [['A'], ['B'], ['D']]
    _assert_level(stacks)


# ── Where each section is ────────────────────────────────────────────────


def test_each_section_is_level_with_its_stack_and_as_tall(stacks):
    _assert_level(stacks)
    heights = [s.height() for s in stacks.stack_sections().sections().values()]
    assert min(heights) > 100


def _assert_lines_in_the_gaps(panel):
    area = panel.stack_sections()
    lines = area.separators()
    assert len(lines) == len(panel._stacked_plots) - 1
    for row, line in enumerate(lines):
        above_top, above_height = _plot_area(panel, row)
        below_top, _height = _plot_area(panel, row + 1)
        assert above_top + above_height - 1 <= line.top() <= below_top, (row, line)
        assert line.left() == 0 and line.width() == area.width()     # across the whole width
        assert line.height() == 1
    return lines


def test_a_thin_line_across_the_table_where_one_stack_meets_the_next(stacks, host, qapp):
    lines = _assert_lines_in_the_gaps(stacks)
    area = stacks.stack_sections()
    image = area.grab().toImage()
    x = image.width() - 3                                 # right of every column
    for line in lines:
        assert image.pixelColor(x, line.top()) == area._line_color
        assert image.pixelColor(x, line.top() + 3) != area._line_color
    host.resize(1200, 1000)
    _settle(qapp, 8)
    assert _assert_lines_in_the_gaps(stacks) != lines


def test_the_rows_start_right_under_each_line(stacks, qapp):
    """No strip between a stack's line and its first row: the column lines
    of the section below begin where the line is."""
    from PySide6.QtGui import QColor
    area = stacks.stack_sections()
    image = area.grab().toImage()
    grid = QColor(stacks._applied_header_colors[2])
    for line, section in zip(area.separators(), list(area.sections().values())[1:]):
        assert section.geometry().top() == line.top()
        assert section.visualRect(section.model().index(0, 1)).top() == 0
        edge = section.columnViewportPosition(1) + section.columnWidth(1) - 1
        colours = [image.pixelColor(x, line.top() + 1) for x in (edge - 1, edge, edge + 1)]
        assert grid in colours, (line, colours)


def test_the_lines_follow_light_and_dark_mode(stacks, qapp):
    from PySide6.QtGui import QColor, QPalette
    area = stacks.stack_sections()
    saved = QPalette(qapp.palette())
    colours = []
    try:
        for window, text in (('#f3f3f3', '#000000'), ('#1e1e1e', '#ffffff')):
            palette = QPalette(saved)
            palette.setColor(QPalette.ColorRole.Window, QColor(window))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(text))
            qapp.setPalette(palette)
            _settle(qapp)
            image = area.grab().toImage()
            line = area.separators()[0]
            assert image.pixelColor(image.width() - 3, line.top()) == area._line_color
            # Between the header's border and its text colour.
            _bg, header_text, border = stacks._header_colors()
            low, high = sorted((QColor(border).lightness(), QColor(header_text).lightness()))
            assert low < area._line_color.lightness() < high
            colours.append(area._line_color.name())
    finally:
        qapp.setPalette(saved)
        _settle(qapp)
    assert colours[0] != colours[1]


# Run in a fresh interpreter at 225 % scaling (the owner's screen): the lines
# where stacks meet at every position mod 4. A line one logical pixel tall
# covers three screen rows at 81 and two at the others, so one looked thicker.
_SCALED_LINES = r'''
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSplitter
app = QApplication([])
from core.signal_store import SignalSeries
from gui.plot_widget import PlotPanel
panel = PlotPanel()
host = QSplitter(Qt.Orientation.Horizontal)
host.addWidget(panel.table_panel)
host.addWidget(panel)
host.resize(1200, 700)
host.show()
t = np.linspace(0.0, 1.0, 20)
panel.add_series('S', SignalSeries(channel=1, message_name='M', message_id=1, signal_name='S',
                                   unit='u', timestamps=t, values=t, raw_values=[],
                                   has_labels=False))
panel.set_multistack(True)
app.processEvents()
area = panel.stack_sections()
area.set_stacks([(0, [], 0, 37), (1, [], 43, 36), (2, [], 84, 36), (3, [], 125, 36),
                 (4, [], 166, 36)], [])
image = area.grab().toImage()
x = image.width() - 4
rows = [y for y in range(image.height()) if image.pixelColor(x, y) == area._line_color]
runs, start = [], None
for i, y in enumerate(rows):
    start = y if start is None else start
    if i + 1 == len(rows) or rows[i + 1] != y + 1:
        runs.append(y - start + 1)
        start = None
import json
print(json.dumps([area._line_ys, image.devicePixelRatio(), runs]))
'''


def test_every_line_is_equally_thick_at_225_percent(qapp):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR='2.25')
    result = subprocess.run([sys.executable, '-c', _SCALED_LINES], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    ys, ratio, runs = json.loads(result.stdout.strip().splitlines()[-1])
    assert ys == [40, 81, 122, 163] and ratio == 2.25
    assert len(runs) == 4 and len(set(runs)) == 1, runs


def test_twelve_stacks_each_get_their_section_and_lines(panel, host, qapp):
    names = [f'S{i}' for i in range(12)]
    for name in names:
        panel.add_series(name, _series(name))
    panel.set_multistack(True)
    _settle(qapp, 6)
    assert _listed(panel) == [[name] for name in names]
    _assert_level(panel)
    _assert_lines_in_the_gaps(panel)


def test_sections_follow_a_resize(stacks, host, qapp):
    before = _section(stacks, 'D').height()
    host.resize(1200, 1000)
    _settle(qapp, 8)
    _assert_level(stacks)
    assert _section(stacks, 'D').height() > before + 50


def test_an_overflowing_section_scrolls_on_its_own(panel, host, qapp):
    names = [f'S{i}' for i in range(30)]
    for name in names:
        panel.add_series(name, _series(name))
    panel.add_series('Lone', _series('Lone'))
    panel.set_multistack(True)
    panel.move_signals_to_stack(names[1:], panel._items['S0'].multistack_id,
                                confirm_units=False)
    _settle(qapp, 6)
    crowded, lone = (panel.stack_sections().sections()[panel._items[k].multistack_id]
                     for k in ('S0', 'Lone'))
    assert crowded.keys() == names
    assert len(names) * crowded.rowHeight(0) > crowded.height()     # more than fit
    assert crowded.verticalScrollBar().maximum() > 0
    assert crowded.verticalScrollBar().isVisible()
    assert not lone.verticalScrollBar().isVisible()
    _assert_level(panel)


# ── What each section shows ──────────────────────────────────────────────


def _text(section, key, column):
    row = section.keys().index(key)
    return section.model().index(row, column).data()


def test_sections_show_the_tables_values(stacks, qapp):
    stacks.set_cursor1_enabled(True)
    stacks.set_cursor2_enabled(True)
    stacks.move_cursor1(0.5)
    stacks.move_cursor2(1.0)
    stacks.set_column_shown('Min', True)
    _settle(qapp)
    section = _section(stacks, 'C')
    assert _text(section, 'C', 2) == stacks.table.item(stacks._row_lookup['C'], 2).text()
    assert _text(section, 'C', 3) == '49.000'
    assert _text(section, 'C', 4) == 'y'
    assert _text(section, 'C', 1).strip() == 'C'
    from gui.plot_widget import _STAT_FIRST
    stacks._update_statistics()
    assert not section.isColumnHidden(_STAT_FIRST)
    assert _text(section, 'C', _STAT_FIRST) == '25.000'          # Min between the cursors


def test_the_hex_column_shows_in_every_section(stacks, qapp):
    from core.signal_properties import RawFormat
    from gui.plot_widget import HEX_COLUMN, _HEX_COL
    stacks.set_raw_format_lookup(lambda key, series: RawFormat(1.0, 0.0, 8, False, False))
    stacks.set_cursor1_enabled(True)
    stacks.move_cursor1(0.5)
    _settle(qapp)
    assert all(section.isColumnHidden(_HEX_COL)                       # off by default
               for section in stacks.stack_sections().sections().values())
    stacks.set_column_shown(HEX_COLUMN, True)
    _settle(qapp)
    header = stacks.table.horizontalHeader()
    for key in ('A', 'B', 'C', 'D'):
        section = _section(stacks, key)
        own = section.horizontalHeader()
        assert not section.isColumnHidden(_HEX_COL)
        assert own.visualIndex(_HEX_COL) == header.visualIndex(_HEX_COL) \
            == header.visualIndex(2) + 1                              # right after Cursor 1
        shown = _text(section, key, _HEX_COL)
        assert shown.startswith('0x') and len(shown) == 4             # 8 bits: two digits
        assert shown == stacks.table.item(stacks._row_lookup[key], _HEX_COL).text()
    before = _text(_section(stacks, 'C'), 'C', _HEX_COL)
    stacks.move_cursor1(0.9)
    _settle(qapp)
    after = _text(_section(stacks, 'C'), 'C', _HEX_COL)
    assert after == stacks.table.item(stacks._row_lookup['C'], _HEX_COL).text() != before


def test_column_choices_widths_and_order_follow_the_header(stacks, qapp):
    stacks.set_column_shown('Unit', False)
    stacks.table.setColumnWidth(2, 133)
    _settle(qapp)
    for section in stacks.stack_sections().sections().values():
        assert section.isColumnHidden(4) and not section.isColumnHidden(2)
        assert section.columnWidth(2) == 133
        header, own = stacks.table.horizontalHeader(), section.horizontalHeader()
        assert [own.logicalIndex(v) for v in range(header.count())] \
            == [header.logicalIndex(v) for v in range(header.count())]


def test_sections_scroll_sideways_with_the_header(stacks, qapp):
    for column in range(2, stacks.table.columnCount()):
        stacks.table.setColumnWidth(column, 300)
    stacks.set_column_shown('Min', True)
    _settle(qapp)
    bar = stacks.stack_sections().scroll_bar
    assert bar.isVisible() and bar.maximum() > 0
    bar.setValue(bar.maximum() // 2)
    _settle(qapp)
    assert stacks.table.horizontalScrollBar().value() == bar.value()
    for section in stacks.stack_sections().sections().values():
        assert section.horizontalScrollBar().value() == bar.value()


# ── Selection ────────────────────────────────────────────────────────────


def _select(section, key, flags=None):
    from PySide6.QtCore import QItemSelectionModel
    flag = QItemSelectionModel.SelectionFlag
    index = section.model().index(section.keys().index(key), 0)
    section.selectionModel().setCurrentIndex(
        index, flags or (flag.ClearAndSelect | flag.Rows))


def _selected_in(section):
    return [section.keys()[i.row()] for i in section.selectionModel().selectedRows()]


def test_a_selection_in_a_section_is_the_tables(stacks, qapp):
    chosen = []
    stacks.selectionChanged.connect(chosen.append)
    _select(_section(stacks, 'C'), 'C')
    _settle(qapp)
    assert stacks.selected_keys() == ['C'] and chosen[-1] == 'C'
    _select(_section(stacks, 'D'), 'D')
    _settle(qapp)
    assert stacks.selected_keys() == ['D']
    assert _selected_in(_section(stacks, 'C')) == []


def test_the_tables_selection_shows_in_the_sections(stacks, qapp):
    stacks._restore_selection(['B', 'D'])
    _settle(qapp)
    assert _selected_in(_section(stacks, 'A')) == ['B']
    assert _selected_in(_section(stacks, 'C')) == []
    assert _selected_in(_section(stacks, 'D')) == ['D']


def test_ctrl_selects_across_sections(stacks, qapp, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel, Qt
    from PySide6.QtWidgets import QApplication
    _select(_section(stacks, 'A'), 'A')
    monkeypatch.setattr(QApplication, 'keyboardModifiers',
                        staticmethod(lambda: Qt.KeyboardModifier.ControlModifier))
    flag = QItemSelectionModel.SelectionFlag
    _select(_section(stacks, 'D'), 'D', flag.Select | flag.Rows)
    _settle(qapp)
    assert sorted(stacks.selected_keys()) == ['A', 'D']


def test_the_right_click_menu_acts_on_the_clicked_signal(stacks, monkeypatch):
    shown = []
    monkeypatch.setattr(stacks, '_show_signal_menu', lambda keys, pos: shown.append(keys))
    section = _section(stacks, 'C')
    section._show_menu(section.visualRect(section.model().index(0, 1)).center())
    assert shown == [['C']]


# ── Boxes ────────────────────────────────────────────────────────────────


def _click_box(section, key, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    index = section.model().index(section.keys().index(key), 0)
    box = section.itemDelegateForColumn(0).box_rect(section.visualRect(index), index)
    QTest.mouseClick(section.viewport(), Qt.MouseButton.LeftButton,
                     pos=box.center().toPoint())
    _settle(qapp)


def test_a_box_in_a_section_hides_its_signal(stacks, qapp):
    _click_box(_section(stacks, 'B'), 'B', qapp)
    assert stacks._items['B'].visible is False
    assert _section(stacks, 'A').keys() == ['A', 'B']         # still listed, greyed
    _click_box(_section(stacks, 'B'), 'B', qapp)
    assert stacks._items['B'].visible is True


def test_a_box_applies_to_every_selected_signal_and_keeps_the_selection(stacks, qapp):
    stacks._restore_selection(['A', 'B', 'D'])
    _settle(qapp)
    _click_box(_section(stacks, 'A'), 'B', qapp)
    assert [stacks._items[k].visible for k in 'ABCD'] == [False, False, True, False]
    assert sorted(stacks.selected_keys()) == ['A', 'B', 'D']


def test_sections_take_the_tables_theme(stacks, qapp):
    from PySide6.QtGui import QColor
    section = _section(stacks, 'D')                # one row: the rest is empty
    area = stacks.stack_sections()
    image = section.grab().toImage()
    behind = area.grab().toImage()
    assert image.pixelColor(image.width() // 2, image.height() - 3) \
        == behind.pixelColor(behind.width() - 2, behind.height() - 2)
    # Its grid lines in the table's grid colour.
    grid = QColor(stacks._applied_header_colors[2])
    edge = section.columnViewportPosition(1) + section.columnWidth(1) - 1
    y = section.visualRect(section.model().index(0, 1)).center().y()
    assert grid in [image.pixelColor(x, y) for x in (edge - 1, edge, edge + 1)]


def test_signals_of_a_hidden_stack_go_under_the_last_stack(stacks, qapp):
    _click_box(_section(stacks, 'C'), 'C', qapp)
    area = stacks.stack_sections()
    assert stacks._items['C'].visible is False
    assert _listed(stacks) == [['A', 'B'], ['D']]
    hidden = area.hidden_section()
    assert hidden is not None and hidden.keys() == ['C'] and hidden.isVisible()
    top, height = _plot_area(stacks, len(stacks._stacked_plots) - 1)
    assert hidden.geometry().top() >= top + height
    under = area.separators()[-1]                        # a line above it too
    assert len(area.separators()) == len(stacks._stacked_plots)
    assert top + height <= under.top() == hidden.geometry().top()
    drop = _Drop(_reorder_mime(stacks, ['A']), _row_y(hidden, 0))
    hidden.dropEvent(drop)                               # not a stack: nothing to join
    assert drop.accepted is False and _stack_of(stacks, 'A') != _stack_of(stacks, 'C')
    _click_box(hidden, 'C', qapp)
    assert stacks._items['C'].visible is True
    assert _listed(stacks) == [['A', 'B'], ['C'], ['D']]
    assert area.hidden_section() is None


# ── Drag and drop ────────────────────────────────────────────────────────


def test_dragging_to_another_section_moves_the_signal_there(stacks, qapp):
    section = _section(stacks, 'D')
    event = _Drop(_reorder_mime(stacks, ['A']), _row_y(section, 0))
    section.dropEvent(event)
    _settle(qapp)
    assert event.accepted
    assert stacks.unit_questions == [['A']]              # x joins z: warned
    assert _stack_of(stacks, 'A') == _stack_of(stacks, 'D')
    assert _listed(stacks) == [['B'], ['C'], ['A', 'D']]
    assert stacks._stacked_row_keys[-1] == ['A', 'D']
    _assert_level(stacks)


def test_a_drop_on_the_lower_half_of_a_row_lands_after_it(stacks, qapp):
    section = _section(stacks, 'A')
    section.dropEvent(_Drop(_reorder_mime(stacks, ['D']), _row_y(section, 0, below=True)))
    _settle(qapp)
    assert _listed(stacks) == [['A', 'D', 'B'], ['C']]


def test_a_drop_under_the_last_row_appends(stacks, qapp):
    section = _section(stacks, 'D')
    section.dropEvent(_Drop(_reorder_mime(stacks, ['A']), section.height() - 3))
    _settle(qapp)
    assert _listed(stacks) == [['B'], ['C'], ['D', 'A']]


def test_cancel_on_the_unit_warning_keeps_the_signal(stacks, qapp, monkeypatch):
    asked = []
    monkeypatch.setattr(stacks, 'confirm_multistack_units',
                        lambda keys, stack, extra_units=None: asked.append(keys) or False)
    section = _section(stacks, 'D')
    section.dropEvent(_Drop(_reorder_mime(stacks, ['A']), _row_y(section, 0)))
    _settle(qapp)
    assert asked == [['A']]
    assert _listed(stacks) == [['A', 'B'], ['C'], ['D']]


def test_reordering_within_a_section(stacks, qapp):
    section = _section(stacks, 'A')
    section.dropEvent(_Drop(_reorder_mime(stacks, ['B']), _row_y(section, 0)))
    _settle(qapp)
    assert _listed(stacks)[0] == ['B', 'A']
    assert stacks._stacked_row_keys[0] == ['B', 'A']
    assert stacks.unit_questions == []                   # same stack: nothing to warn


def test_a_drop_is_undone_in_one_step(stacks, qapp):
    section = _section(stacks, 'D')
    section.dropEvent(_Drop(_reorder_mime(stacks, ['A']), _row_y(section, 0)))
    _settle(qapp)
    stacks.undo()
    _settle(qapp)
    assert _listed(stacks) == [['A', 'B'], ['C'], ['D']]


def test_a_signal_tree_drop_adds_to_that_stack(stacks, qapp):
    from PySide6.QtCore import QByteArray, QMimeData
    from gui.signal_tree import SignalTreeWidget
    received = []
    stacks.signalDroppedToStack.connect(lambda keys, stack: received.append((keys, stack)))
    mime = QMimeData()
    mime.setData(SignalTreeWidget.MIME_TYPE, QByteArray(b'New1\nNew2'))
    section = _section(stacks, 'C')
    event = _Drop(mime, _row_y(section, 0))
    section.dropEvent(event)
    assert event.accepted
    assert received == [(['New1', 'New2'], _stack_of(stacks, 'C'))]


def test_a_drag_carries_the_selected_signals(stacks, qapp, monkeypatch):
    from PySide6.QtGui import QDrag
    carried = []
    monkeypatch.setattr(QDrag, 'exec', lambda self, *a: carried.append(
        bytes(self.mimeData().data(stacks.table._ROW_REORDER_MIME)).decode()) or 0)
    stacks._restore_selection(['D', 'A'])
    _settle(qapp)
    _section(stacks, 'D').startDrag(None)
    assert carried == ['A\nD']


# ── Other layouts ────────────────────────────────────────────────────────


def test_leaving_multistack_shows_the_whole_table(stacks, qapp):
    stacks.set_multistack(False)
    _settle(qapp)
    area = stacks.stack_sections()
    assert not area.isVisible() and area.sections() == {}
    assert stacks.table.height() > 300
    stacks.set_stacked(True)
    _settle(qapp)
    assert not area.isVisible() and area.sections() == {}
    stacks.set_multistack(True)
    _settle(qapp)
    assert area.isVisible() and len(area.sections()) == 3


def test_clearing_the_plot_shows_the_whole_table(stacks, qapp):
    stacks.clear_all()
    _settle(qapp)
    assert not stacks.stack_sections().isVisible()
    assert stacks.table.maximumHeight() > 10000


def test_the_main_window_puts_the_first_section_level_with_the_first_stack(
        qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow
    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    window = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        window.resize(1400, 900)
        window.show()
        panel = window.plot_panel
        for name in ('A', 'B', 'C'):
            panel.add_series(name, _series(name))
        window.btn_multistack.click()
        _settle(qapp, 8)
        assert panel._multistack_mode
        sections = list(panel.stack_sections().sections().values())
        assert [s.keys() for s in sections] == [['A'], ['B'], ['C']]
        # No gap under the header: the sections start where its row ends,
        # and the first section's first row at the sections' top.
        area = panel.stack_sections()
        assert area.geometry().top() == panel.table.geometry().bottom() + 1
        assert sections[0].geometry().top() == 0
        assert sections[0].visualRect(sections[0].model().index(0, 1)).top() == 0
        _assert_level(panel)
    finally:
        window.close()
        qapp.processEvents()
