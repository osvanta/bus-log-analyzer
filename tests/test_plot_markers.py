# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Named markers on the plot: added at the time under a right-click or at
Cursor 1 (M), dragged, renamed, deleted and gone to; drawn on every row of
the stacked layout; kept through Clear Plot and in the configuration,
dropped when another measurement opens; and written to MF4 as marker events.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from PySide6.QtWidgets import QMenu


def _series(name):
    from core.signal_store import SignalSeries
    ts = np.arange(0.0, 100.0, 0.5)
    return SignalSeries(1, 'Msg', 0x100, name, 'u', ts, np.sin(ts))


@pytest.fixture()
def panel(qapp):
    from gui.plot_widget import PlotPanel
    p = PlotPanel()
    p.resize(900, 600)
    p.add_series('A', _series('A'))
    p.add_series('B', _series('B'))
    p.show()
    qapp.processEvents()
    yield p
    p.close()


def _lines(panel):
    return [line for _plot, line in panel._marker_lines]


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


class _RecordingMenu(QMenu):
    """Records its actions instead of opening, as exec() and popup() would."""
    shown: list = []

    def exec(self, *_args):
        _RecordingMenu.shown.append(self)

    def popup(self, *_args):
        _RecordingMenu.shown.append(self)


@pytest.fixture()
def recorded(panel, monkeypatch):
    _RecordingMenu.shown = []
    monkeypatch.setattr(panel, '_make_menu', lambda parent=None: _RecordingMenu())
    return _RecordingMenu.shown


# ── Adding, naming, removing ─────────────────────────────────────────────


def test_markers_are_named_in_turn_and_kept_in_time_order(panel):
    panel.add_marker(30.0)
    panel.add_marker(10.0)
    named = panel.add_marker(20.0, 'Brake test')
    assert [(m.time, m.name) for m in panel.markers()] == [
        (10.0, 'M2'), (20.0, 'Brake test'), (30.0, 'M1')]
    panel.remove_marker(panel.markers()[0])
    assert panel.add_marker(5.0).name == 'M2'         # the first name free
    assert named in panel.markers()


def test_a_marker_at_cursor_1_needs_cursor_1(panel):
    panel.set_cursor1_enabled(False)
    assert panel.add_marker_at_cursor1() is None
    panel.set_cursor1_enabled(True)
    panel.move_cursor1(42.0)
    assert panel.add_marker_at_cursor1().time == pytest.approx(42.0)


def test_rename_ignores_a_blank_name(panel):
    marker = panel.add_marker(1.0)
    panel.rename_marker(marker, '  ')
    assert marker.name == 'M1'
    panel.rename_marker(marker, ' Launch ')
    assert marker.name == 'Launch'
    assert _lines(panel)[0].label.toPlainText() == 'Launch'


# ── Drawing ──────────────────────────────────────────────────────────────


def test_one_line_per_marker_on_each_row_named_once(panel):
    from gui.plot_widget import _MarkerLine
    panel.set_stacked(True)
    panel.add_marker(25.0)
    rows = len(panel._stacked_plots)
    assert rows == 2
    lines = _lines(panel)
    assert len(lines) == rows and all(isinstance(line, _MarkerLine) for line in lines)
    assert [getattr(line, 'label', None) is not None for line in lines] == [True, False]
    assert {line.value() for line in lines} == {25.0}
    panel.set_stacked(False)
    assert len(_lines(panel)) == 1                    # redrawn on the one plot


def test_dragging_one_row_moves_the_marker_everywhere(panel):
    panel.set_stacked(True)
    marker = panel.add_marker(25.0)
    other = panel.add_marker(30.0)
    first, second = [line for line in _lines(panel) if line.marker is marker]
    first.setPos(40.0)
    first.sigDragged.emit(first)
    assert second.value() == pytest.approx(40.0)
    first.sigPositionChangeFinished.emit(first)
    assert marker.time == pytest.approx(40.0)
    assert panel.markers() == [other, marker]          # still in time order


def test_a_rebuild_keeps_the_markers(panel):
    panel.add_marker(25.0)
    panel.remove_series('B')
    assert len(_lines(panel)) == 1


# ── Names that would write over each other ───────────────────────────────


def test_names_apart_share_the_top_row():
    from gui.plot_widget import marker_name_rows
    assert marker_name_rows([(0, 100), (200, 300), (400, 500)], rows=5) == [0, 0, 0]
    assert marker_name_rows([], rows=5) == []


def test_names_on_top_of_each_other_get_a_row_each_the_left_one_on_top():
    from gui.plot_widget import marker_name_rows
    assert marker_name_rows([(0, 100), (50, 150)], rows=5) == [0, 1]
    assert marker_name_rows([(0, 100), (20, 120), (40, 140)], rows=5) == [0, 1, 2]
    # The answer is per name as given, whatever the order they come in.
    assert marker_name_rows([(40, 140), (0, 100), (20, 120)], rows=5) == [2, 0, 1]


def test_a_row_is_used_again_once_it_is_free():
    from gui.plot_widget import marker_name_rows
    # The third name is clear of the first, which the second is not.
    assert marker_name_rows([(0, 100), (50, 150), (120, 220)], rows=5) == [0, 1, 0]


def test_two_names_on_one_row_keep_a_gap():
    from gui.plot_widget import marker_name_rows
    assert marker_name_rows([(0, 100), (110, 200)], rows=5, gap=10) == [0, 0]
    assert marker_name_rows([(0, 100), (109, 200)], rows=5, gap=10) == [0, 1]


def test_with_every_row_taken_a_name_goes_where_the_least_is_covered():
    from gui.plot_widget import marker_name_rows
    # Two rows only: the third name covers the end of the first (20 pixels),
    # not the middle of the second (60 pixels).
    assert marker_name_rows([(0, 100), (10, 140), (80, 200)], rows=2) == [0, 1, 0]
    assert marker_name_rows([(0, 140), (10, 100), (80, 200)], rows=2) == [0, 1, 1]
    assert max(marker_name_rows([(i, 100 + i) for i in range(9)], rows=3)) == 2
    assert marker_name_rows([(0, 100), (50, 150)], rows=0) == [0, 0]      # a plot too low for two


def _names(panel):
    """(name, scene rectangle, position along the line) of every marker name."""
    return [(line.marker.name, line.label.sceneBoundingRect(), line.label.orthoPos)
            for line in _lines(panel) if getattr(line, 'label', None) is not None]


def _letters(panel):
    """(name, scene rectangle of its letters): a name's rectangle without the
    empty margin around the letters, which may lie over another name's or
    over the plot's edge."""
    edge = _lines(panel)[0].label.textItem.document().documentMargin()
    return [(name, rect.adjusted(edge, edge, -edge, -edge)) for name, rect, _pos in _names(panel)]


def _overlapping(panel):
    """The pairs of names whose letters share pixels."""
    names = _letters(panel)
    return [(a[0], b[0]) for i, a in enumerate(names) for b in names[i + 1:]
            if a[1].intersects(b[1])]


def _top_view(panel):
    return _lines(panel)[0].getViewBox()


def _inside(panel):
    """Every name is inside the plot it is written in, top to bottom."""
    frame = _top_view(panel).sceneBoundingRect()
    return all(frame.top() <= rect.top() and rect.bottom() <= frame.bottom()
               for _name, rect in _letters(panel))


def test_names_of_close_markers_do_not_write_over_each_other(panel, qapp):
    from gui.plot_widget import _MARKER_NAME_POSITION
    for i, name in enumerate(('1. Fan request at 100 percent', '2. Coolant above 105 degC',
                              '3. Derate active')):
        panel.add_marker(20.0 + i, name)
    qapp.processEvents()
    assert _overlapping(panel) == []
    assert _inside(panel)
    positions = [pos for _name, _rect, pos in _names(panel)]
    assert positions[0] == pytest.approx(_MARKER_NAME_POSITION)      # the first where it always was
    assert positions[0] > positions[1] > positions[2]                # the others under it
    (_a, first, _p), (_b, second, _q), (_c, third, _r) = _names(panel)
    # A line of text apart, not a whole rectangle with its margins.
    assert 0 < second.top() - first.top() < first.height()
    assert third.top() - second.top() == pytest.approx(second.top() - first.top())


def test_names_of_markers_far_apart_stay_on_the_top_row(panel, qapp):
    from gui.plot_widget import _MARKER_NAME_POSITION
    for t, name in ((5.0, 'A1'), (50.0, 'A2'), (95.0, 'A3')):
        panel.add_marker(t, name)
    qapp.processEvents()
    assert [pos for _name, _rect, pos in _names(panel)] == [pytest.approx(_MARKER_NAME_POSITION)] * 3


def test_names_that_face_each_other_across_the_middle_are_kept_apart(panel, qapp):
    """Left of the middle a name stands right of its line, right of the
    middle left of it: these two reach for the same place."""
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    panel.add_marker(40.0, 'A name as long as a name of an event can be')
    panel.add_marker(60.0, 'Another name as long as the first one is')
    qapp.processEvents()
    (_a, first, _p), (_b, second, _q) = _names(panel)
    assert first.left() < second.right() and second.left() < first.right()   # they share the width
    assert _overlapping(panel) == []


def test_names_one_and_a_half_names_apart_across_the_middle(panel, qapp):
    """Each name is clear of the other marker's line, and still they meet in
    the middle: only where each name really stands tells."""
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    probe = panel.add_marker(10.0, 'First event')
    qapp.processEvents()
    name_s = 100.0 * _names(panel)[0][1].width() / _top_view(panel).width()
    panel.remove_marker(probe)
    panel.add_marker(50.0 - 0.75 * name_s, 'First event')
    panel.add_marker(50.0 + 0.75 * name_s, 'Other event')
    qapp.processEvents()
    (_a, first, _p), (_b, second, _q) = _names(panel)
    assert first.left() < second.right() and second.left() < first.right()
    assert _overlapping(panel) == []


def test_the_rows_follow_the_zoom(panel, qapp):
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    panel.add_marker(20.0, 'First event')
    panel.add_marker(21.0, 'Second event')
    qapp.processEvents()
    assert len({pos for _name, _rect, pos in _names(panel)}) == 2
    panel.zoom_to_time(19.9, 23.9, margin=0.0)          # in: room for both on one row
    qapp.processEvents()
    assert len({pos for _name, _rect, pos in _names(panel)}) == 1
    assert _overlapping(panel) == []
    panel.zoom_to_time(0.0, 100.0, margin=0.0)          # and out again
    qapp.processEvents()
    assert len({pos for _name, _rect, pos in _names(panel)}) == 2
    assert _overlapping(panel) == []


def test_a_narrower_plot_takes_another_row(panel, qapp):
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    panel.add_marker(10.0, 'First event')
    qapp.processEvents()
    # The second one and a half names further on, whatever the font.
    name_s = 100.0 * _names(panel)[0][1].width() / _top_view(panel).width()
    panel.add_marker(10.0 + 1.5 * name_s, 'Second event')
    qapp.processEvents()
    assert len({pos for _name, _rect, pos in _names(panel)}) == 1
    panel.resize(500, 600)                               # the plot about half as wide
    qapp.processEvents()
    assert _overlapping(panel) == []
    assert len({pos for _name, _rect, pos in _names(panel)}) == 2


def test_a_marker_dragged_onto_another_makes_room(panel, qapp):
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    panel.add_marker(10.0, 'First event')
    moved = panel.add_marker(45.0, 'Second event')
    qapp.processEvents()
    line = next(line for line in _lines(panel) if line.marker is moved)
    line.setPos(11.0)
    line.sigDragged.emit(line)
    qapp.processEvents()
    assert _overlapping(panel) == []
    assert len({pos for _name, _rect, pos in _names(panel)}) == 2
    line.setPos(45.0)
    line.sigPositionChangeFinished.emit(line)
    qapp.processEvents()
    assert len({pos for _name, _rect, pos in _names(panel)}) == 1


def test_a_removed_marker_gives_its_row_back(panel, qapp):
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    first = panel.add_marker(20.0, 'First event')
    panel.add_marker(21.0, 'Second event')
    panel.remove_marker(first)
    qapp.processEvents()
    from gui.plot_widget import _MARKER_NAME_POSITION
    assert [pos for _name, _rect, pos in _names(panel)] == [pytest.approx(_MARKER_NAME_POSITION)]


def test_in_a_low_plot_the_names_stay_inside_it(panel, qapp):
    """A stack of many signals leaves the top plot a few names high: a name
    is not cut off at the plot's upper edge, and two still stand apart."""
    for name in 'CDEFGH':
        panel.add_series(name, _series(name))
    panel.set_stacked(True)
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    panel.add_marker(20.0, 'First event')
    panel.add_marker(21.0, 'Second event')
    qapp.processEvents()
    assert _top_view(panel).height() < 120                       # low indeed
    assert _inside(panel)
    assert _overlapping(panel) == []
    for i in range(8):                                           # more names than rows fit
        panel.add_marker(22.0 + i, f'Event {i}')
    qapp.processEvents()
    assert _inside(panel)


def test_the_rows_follow_the_zoom_after_the_plots_were_rebuilt(panel, qapp):
    panel.add_marker(20.0, 'First event')
    panel.add_marker(21.0, 'Second event')
    panel.set_stacked(True)                                      # other plots, another view
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    qapp.processEvents()
    assert len({pos for _name, _rect, pos in _names(panel)}) == 2
    panel.zoom_to_time(19.9, 23.9, margin=0.0)
    qapp.processEvents()
    assert len({pos for _name, _rect, pos in _names(panel)}) == 1
    panel.set_stacked(False)
    panel.zoom_to_time(0.0, 100.0, margin=0.0)
    qapp.processEvents()
    assert _overlapping(panel) == [] and len({pos for _n, _r, pos in _names(panel)}) == 2


# ── Menus ────────────────────────────────────────────────────────────────


def test_the_plot_menu_offers_the_marker_commands(panel):
    panel.set_cursor1_enabled(False)
    menu = QMenu()
    panel._add_marker_actions(menu, 12.5)
    texts = _texts(menu)
    assert texts[:3] == ['Add marker here', 'Add marker at Cursor 1', 'Go to marker']
    assert 'Show measurement events' in texts
    actions = {a.text(): a for a in menu.actions()}
    assert not actions['Add marker at Cursor 1'].isEnabled()      # Cursor 1 off
    assert actions['Add marker at Cursor 1'].shortcut().toString() == 'M'
    assert not actions['Go to marker'].isEnabled()                 # none yet
    actions['Add marker here'].trigger()
    assert [m.time for m in panel.markers()] == [12.5]


def test_go_to_marker_lists_them_and_centres_keeping_the_zoom(panel):
    panel.add_marker(70.0, 'Late')
    panel.plot.plotItem.vb.setXRange(10.0, 20.0, padding=0)
    menu = QMenu()
    panel._add_marker_actions(menu)
    assert 'Add marker here' not in _texts(menu)      # no time without a click
    go_to = next(a for a in menu.actions() if a.text() == 'Go to marker').menu()
    assert _texts(go_to) == ['Late  ·  70.000 s']
    go_to.actions()[0].trigger()
    low, high = panel._visible_x_range()
    assert (low + high) / 2 == pytest.approx(70.0, abs=0.01)
    assert high - low == pytest.approx(10.0, rel=0.01)


def _open_go_to(panel):
    """The Go to marker list of a plot menu, open; the menu is returned too,
    as the list is its child and goes with it."""
    from PySide6.QtCore import QPoint
    menu = panel._make_menu()                          # styled, as the plot's menus are
    panel._add_marker_actions(menu)
    go_to = next(a for a in menu.actions() if a.text() == 'Go to marker').menu()
    go_to.popup(QPoint(0, 0))
    return menu, go_to


def _send_mouse(menu, kind, pos):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    held = (Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress
            else Qt.MouseButton.NoButton)
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    QApplication.sendEvent(menu, QMouseEvent(kind, QPointF(pos), QPointF(menu.mapToGlobal(pos)),
                                             button, held, Qt.KeyboardModifier.NoModifier))


def _click(menu, pos):
    from PySide6.QtCore import QEvent
    _send_mouse(menu, QEvent.Type.MouseButtonPress, pos)
    _send_mouse(menu, QEvent.Type.MouseButtonRelease, pos)


def _name_of(menu, row):
    from PySide6.QtCore import QPoint
    return menu.actionGeometry(row).topLeft() + QPoint(20, 5)


def test_each_go_to_row_has_a_bin_that_deletes_just_its_marker(panel):
    panel.add_marker(10.0, 'spike 1')
    target = panel.add_marker(20.0, 'spike 2')        # as the assistant names them
    panel.add_marker(30.0)
    menu, go_to = _open_go_to(panel)
    _click(go_to, go_to.bin_area(go_to.actions()[1]).center())
    assert target not in panel.markers()
    assert [m.name for m in panel.markers()] == ['spike 1', 'M1']
    assert len(_lines(panel)) == 2
    # The list stays open on the rest, so the next goes with another click.
    assert go_to.isVisible()
    assert _texts(go_to) == ['spike 1  ·  10.000 s', 'M1  ·  30.000 s']
    _click(go_to, go_to.bin_area(go_to.actions()[0]).center())
    assert [m.name for m in panel.markers()] == ['M1']
    go_to.hide()


def test_a_click_on_a_rows_name_goes_to_its_marker_and_keeps_it(panel):
    panel.add_marker(70.0, 'Early')
    panel.add_marker(90.0, 'Late')
    panel.plot.plotItem.vb.setXRange(10.0, 20.0, padding=0)
    menu, go_to = _open_go_to(panel)
    _click(go_to, _name_of(go_to, go_to.actions()[0]))
    assert [m.name for m in panel.markers()] == ['Early', 'Late']
    low, high = panel._visible_x_range()
    assert (low + high) / 2 == pytest.approx(70.0, abs=0.01)


def test_the_last_bin_closes_the_list(panel):
    panel.add_marker(5.0)
    menu, go_to = _open_go_to(panel)
    _click(go_to, go_to.bin_area(go_to.actions()[0]).center())
    assert panel.markers() == []
    assert not go_to.isVisible() and not go_to.isEnabled()


def test_delete_deletes_the_highlighted_marker(panel):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    keep = panel.add_marker(5.0)
    panel.add_marker(6.0)
    menu, go_to = _open_go_to(panel)
    go_to.setActiveAction(go_to.actions()[1])
    QApplication.sendEvent(go_to, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                                            Qt.KeyboardModifier.NoModifier))
    assert panel.markers() == [keep]
    assert go_to.isVisible()
    go_to.hide()


def test_the_bins_are_drawn_where_they_are_clicked_and_light_up_under_the_pointer(panel):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QColor
    panel.add_marker(5.0)
    panel.add_marker(6.0)
    menu, go_to = _open_go_to(panel)
    first, second = go_to.actions()

    def colors(area):
        image = go_to.grab().toImage()
        return [QColor(image.pixel(x, y)) for x in range(area.left(), area.right())
                for y in range(area.top(), area.bottom())]

    def red(color):
        return color.red() > 150 and color.green() < 80 and color.blue() < 80

    _send_mouse(go_to, QEvent.Type.MouseMove, go_to.bin_area(first).center())
    assert go_to._hot is first
    hot, idle = colors(go_to.bin_area(first)), colors(go_to.bin_area(second))
    assert sum(map(red, hot)) > 40                    # its red square
    assert not any(map(red, idle))
    assert sum(c.lightness() > 150 for c in idle) > 10   # the bin, in the text colour
    _send_mouse(go_to, QEvent.Type.MouseMove, _name_of(go_to, first))
    assert go_to._hot is None
    go_to.hide()


def test_the_bins_sit_in_one_column_clear_of_the_longest_text(panel):
    # One column toward the right, not each bin right after its own name
    # (feedback item 12).
    from gui.bin_menu import BinMenu
    panel.add_marker(1.0)                                     # short: M1
    panel.add_marker(123456.789, 'A rather long marker name')
    menu, go_to = _open_go_to(panel)
    image = go_to.grab().toImage()

    def lit(x, top, bottom):                              # text and bin are light on dark
        from PySide6.QtGui import QColor
        return any(QColor(image.pixel(x, y)).lightness() > 128 for y in range(top, bottom))

    for row in go_to.actions():
        geometry, area = go_to.actionGeometry(row), go_to.bin_area(row)
        top, bottom = geometry.top() + 1, geometry.bottom()
        text_end = max(x for x in range(geometry.left(), area.left()) if lit(x, top, bottom))
        assert area.left() - text_end >= BinMenu.BIN_GAP // 2   # well clear of the text ...
        assert not any(lit(x, top, bottom) for x in range(area.left(), area.left() + 4))
        assert any(lit(x, top, bottom) for x in range(area.left(), area.right()))   # ... drawn
        assert area.right() == geometry.right() - BinMenu.BIN_RIGHT  # at the row's right
    short, long_ = go_to.actions()
    assert go_to.bin_area(short).left() == go_to.bin_area(long_).left()   # one column
    go_to.hide()


def test_right_click_on_the_plot_puts_the_commands_on_top(panel, recorded):
    shown = recorded
    vb = panel.plot.plotItem.vb
    scene_pos = vb.mapViewToScene(vb.mapToView(vb.boundingRect().center()))
    event = SimpleNamespace(scenePos=lambda: scene_pos,
                            screenPos=lambda: SimpleNamespace(toPoint=lambda: None),
                            accept=lambda: None, acceptedItem=vb)
    vb.raiseContextMenu(event)
    texts = _texts(shown[0])
    assert texts[0] == 'Add marker here'
    assert 'View All' in texts                       # pyqtgraph's own still there
    shown[0].actions()[0].trigger()
    assert panel.markers()[0].time == pytest.approx(vb.mapSceneToView(scene_pos).x())


def test_the_signal_menu_offers_a_marker_at_the_clicked_time(panel, recorded):
    panel._show_signal_menu(['A'], None, time_s=3.0)
    panel._show_signal_menu(['A'], None)               # from the table: no time
    shown = [_texts(menu) for menu in recorded]
    assert 'Add marker here' in shown[0] and 'Add marker here' not in shown[1]
    assert 'Add marker at Cursor 1' in shown[1]


def test_a_markers_own_menu(panel, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    marker = panel.add_marker(8.0)
    clicked = []
    panel.plotAreaClicked.connect(clicked.append)
    actions = {a.text(): a for a in panel.marker_menu(marker).actions()}
    assert set(actions) == {'Rename…', 'Move Cursor 1 here', 'Delete marker'}
    actions['Move Cursor 1 here'].trigger()
    assert clicked == [8.0]
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Gear shift', True))
    actions['Rename…'].trigger()
    assert marker.name == 'Gear shift'
    actions['Delete marker'].trigger()
    assert panel.markers() == []


def test_double_click_renames_and_right_click_opens_the_menu(panel, monkeypatch, recorded):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QInputDialog
    marker = panel.add_marker(8.0)
    line = _lines(panel)[0]
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Stop', True))

    def click(button, double=False):
        return SimpleNamespace(button=lambda: button, double=lambda: double,
                               accept=lambda: None)

    line.mouseClickEvent(click(Qt.MouseButton.LeftButton, double=True))
    assert marker.name == 'Stop'
    line = _lines(panel)[0]
    line.mouseClickEvent(click(Qt.MouseButton.RightButton))
    assert [_texts(menu) for menu in recorded] == [
        ['Rename…', 'Move Cursor 1 here', 'Delete marker']]


# ── Deleting many ────────────────────────────────────────────────────────


def test_the_delete_markers_menu_deletes_one_or_all(panel):
    panel.add_marker(10.0)
    middle = panel.add_marker(20.0, 'Brake')
    panel.add_marker(30.0)
    menu = panel.delete_markers_menu()
    assert _texts(menu) == ['Delete all markers (3)', 'M1  ·  10.000 s',
                            'Brake  ·  20.000 s', 'M2  ·  30.000 s']
    entries = [a for a in menu.actions() if not a.isSeparator()]
    entries[2].trigger()                               # Brake only
    assert middle not in panel.markers()
    assert [m.name for m in panel.markers()] == ['M1', 'M2']
    assert len(_lines(panel)) == 2
    menu = panel.delete_markers_menu()
    assert _texts(menu)[0] == 'Delete all markers (2)'
    menu.actions()[0].trigger()
    assert panel.markers() == [] and _lines(panel) == []
    assert panel.markers_config() == []


def test_deleting_all_markers_takes_them_off_every_row(panel):
    panel.set_stacked(True)
    panel.add_marker(10.0)
    panel.add_marker(20.0)
    assert len(_lines(panel)) == 2 * len(panel._stacked_plots)
    panel.remove_all_markers()
    assert _lines(panel) == []
    for plot_item in panel._stacked_plots:
        assert not [item for item in plot_item.items
                    if type(item).__name__ == '_MarkerLine']


def test_markers_changed_follows_every_add_and_delete(panel):
    changes = []
    panel.markersChanged.connect(lambda: changes.append(len(panel.markers())))
    marker = panel.add_marker(1.0)
    panel.add_marker(2.0)
    panel.remove_marker(marker)
    panel.remove_marker(marker)                        # already gone: no change
    panel.set_markers([(3.0, 'A'), (4.0, 'B')])
    panel.remove_all_markers()
    assert changes == [1, 2, 1, 2, 0]


# ── Clearing, undo, configuration ────────────────────────────────────────


def test_clearing_the_plot_keeps_them(panel):
    # Clear Plot, and the clear before every Decode, keep the markers:
    # a configuration sets them before its Decode starts.
    panel.add_marker(5.0, 'Keep')
    panel.clear_all()
    assert [(m.time, m.name) for m in panel.markers()] == [(5.0, 'Keep')]
    panel.add_series('A', _series('A'))
    assert len(_lines(panel)) == 1


def test_markers_from_a_configuration(panel):
    panel.set_markers([{'time': 9, 'name': 'Late'}, {'time': 'x'}, (1.5, ''),
                       {'time': float('nan')}, 'junk', {'time': 3.0, 'name': None}])
    assert [(m.time, m.name) for m in panel.markers()] == [
        (1.5, 'M1'), (3.0, 'M2'), (9.0, 'Late')]
    assert panel.markers_config() == [
        {'time': 1.5, 'name': 'M1'}, {'time': 3.0, 'name': 'M2'},
        {'time': 9.0, 'name': 'Late'}]


# ── MF4 ──────────────────────────────────────────────────────────────────


def test_markers_are_written_as_mf4_events(tmp_path):
    from core.bus_types import BusType
    from core.measurement_info import read_file_info
    from core.mf4_writer import write_mf4
    from core.signal_store import SignalSeries
    series = SignalSeries((BusType.CAN, 1), 'Msg', 0x100, 'Speed', 'km/h',
                          np.arange(0.0, 10.0, 0.5), np.arange(20.0))
    path = tmp_path / 'with_markers.mf4'
    write_mf4([series], path, events=[(2.5, 'Brake test'), (7.0, 'M2')])
    events = read_file_info(str(path)).events
    assert [(e.time_s, e.name) for e in events] == [(2.5, 'Brake test'), (7.0, 'M2')]


# ── The main window ──────────────────────────────────────────────────────


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from gui import app_data
    from gui.main_window import MainWindow
    monkeypatch.setenv(app_data.DATA_DIR_ENV_VAR, str(tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    yield w
    w.close()
    w.deleteLater()


def test_m_adds_a_marker_at_cursor_1(window):
    from PySide6.QtGui import QKeySequence
    from PySide6.QtGui import QShortcut
    shortcut = next(s for s in window.findChildren(QShortcut)
                    if s.key() == QKeySequence('M'))
    shortcut.activated.emit()
    assert window.plot_panel.markers() == []           # Cursor 1 is off
    window.btn_cursor1.setChecked(True)
    window.plot_panel.move_cursor1(4.0)
    shortcut.activated.emit()
    assert [m.time for m in window.plot_panel.markers()] == [pytest.approx(4.0)]
    assert 'M' in dict(window._SHORTCUTS)


def test_the_configuration_keeps_the_markers(window, monkeypatch, tmp_path):
    import json
    from PySide6.QtWidgets import QFileDialog
    window.plot_panel.add_marker(3.25, 'Start')
    target = tmp_path / 'config.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        lambda *a, **k: (str(target), ''))
    window.save_configuration()
    saved = json.loads(target.read_text(encoding='utf-8'))
    assert saved['markers'] == [{'time': 3.25, 'name': 'Start'}]


def test_loading_a_configuration_restores_the_markers_through_decode(window, monkeypatch,
                                                                    tmp_path):
    import json
    from unittest.mock import Mock
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    measurement = tmp_path / 'm.mf4'
    measurement.write_bytes(b'')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'measurement_path': str(measurement), 'signals': [],
                                  'markers': [{'time': 2.0, 'name': 'Start'}]}))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(config), ''))
    decoded = []
    # Decode starts by clearing the plot, as the real one does.
    monkeypatch.setattr(window, 'load_data', lambda **_k: decoded.append(
        window.plot_panel.clear_all()))
    window.load_configuration()
    assert decoded
    assert [(m.time, m.name) for m in window.plot_panel.markers()] == [(2.0, 'Start')]


def test_a_new_measurement_drops_the_markers(window):
    window.plot_panel.add_marker(2.0)
    window._reset_for_new_measurement()
    assert window.plot_panel.markers() == []


def test_delete_markers_is_enabled_only_while_there_are_markers(window):
    button = window.btn_delete_markers
    panel = window.plot_panel
    assert not button.isEnabled()                      # none at start
    marker = panel.add_marker(2.0)
    assert button.isEnabled()
    panel.remove_marker(marker)
    assert not button.isEnabled()
    panel.set_markers([{'time': 1.0, 'name': 'From config'}])
    assert button.isEnabled()
    window._reset_for_new_measurement()
    assert not button.isEnabled()


def test_delete_markers_opens_its_menu_and_deletes_them_all(window, monkeypatch):
    panel = window.plot_panel
    shown = []
    monkeypatch.setattr(panel, '_make_menu', lambda parent=None: _RecordingMenu())
    _RecordingMenu.shown = shown
    panel.add_marker(2.0)
    panel.add_marker(4.0, 'Stop')
    window.btn_delete_markers.click()
    assert [_texts(menu)[0] for menu in shown] == ['Delete all markers (2)']
    shown[0].actions()[0].trigger()
    assert panel.markers() == []
    assert not window.btn_delete_markers.isEnabled()
    window.btn_delete_markers.click()                  # disabled: no menu
    assert len(shown) == 1


def test_delete_markers_works_from_the_overflow_menu(window, qapp, monkeypatch):
    # Squeezed out of the row, the button is reached through "»", whose
    # entry clicks it; the menu then opens at the pointer.
    from PySide6.QtGui import QCursor
    panel = window.plot_panel
    button = window.btn_markers
    fill_menu = button.menu
    shown = []

    class _Shown:
        # The Markers menu as it would open, recorded where a real one
        # would wait for a click.
        def __init__(self):
            self.menu = fill_menu()

        def exec(self, at):
            shown.append((self.menu, at))

        def deleteLater(self):
            pass

    monkeypatch.setattr(button, 'menu', _Shown)
    panel.add_marker(2.0)
    window.show()
    window.resize(300, 600)
    qapp.processEvents()
    row = window.plot_button_row
    assert button in row.overflowed_buttons()
    row._fill_menu()
    entry = next(a for a in row.more_menu.actions() if a.text() == 'Markers')
    assert entry.isEnabled()
    entry.trigger()
    menu, at = shown[0]
    assert at == QCursor.pos()
    next(a for a in menu.actions() if a.text() == 'Delete all markers (1)').trigger()
    assert panel.markers() == []


def test_exported_mf4_carries_the_markers(window, monkeypatch, tmp_path):
    from core import mf4_writer
    window.plot_panel.add_marker(1.0, 'One')
    seen = {}
    monkeypatch.setattr(mf4_writer, 'write_mf4',
                        lambda items, path, **kw: seen.update(kw) or 0)
    window._write_mf4([], tmp_path / 'x.mf4', 'Exporting to MF4')
    assert seen['events'] == [(1.0, 'One')]
