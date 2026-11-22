# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The panels of a plot tab (gui.panel_area): tiled side by side, tabbed
together, maximised and restored, closed and opened again where they were,
moved into windows of their own and docked back, and described for a
configuration and restored from it.

A panel's title shows as a row only where panels share a place, or on
request; a panel alone in its place shows it while the pointer rests on its
top edge; a panel alone in the tab shows none.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QLabel, QWidget

from gui.panel_area import CARD_INSET


@pytest.fixture()
def area(qapp):
    from gui.panel_area import PanelArea

    host = QWidget()
    host.resize(1000, 600)
    area = PanelArea(host)
    area.resize(1000, 600)
    host.show()
    qapp.processEvents()
    yield area
    area.dock_all()
    host.close()
    host.deleteLater()
    qapp.processEvents()


def _panel(area, key, **kwargs):
    widget = QLabel(key)
    widget.setMinimumSize(40, 30)
    area.add(key, widget, key.title(), **kwargs)
    return widget


def _place(area, key):
    return area._group_of(key)


def _left_top(area, widget):
    point = widget.mapTo(area, QPoint(0, 0))
    return point.x(), point.y()


def _settle(qapp):
    for _ in range(3):
        qapp.processEvents()


# ── Opening, and where panels go ─────────────────────────────────────────


def test_the_first_panel_opens_alone_and_shows_no_title(area, qapp):
    from gui.panel_area import NONE

    plot = _panel(area, 'plot', closable=False, floatable=False)
    _settle(qapp)
    assert area.is_open('plot') and plot.isVisible()
    assert _place(area, 'plot').mode() == NONE
    assert not _place(area, 'plot').strip.isVisible()
    assert not _place(area, 'plot').grip.isVisible()


def test_later_panels_wait_closed_until_opened(area, qapp):
    _panel(area, 'plot', closable=False)
    xy = _panel(area, 'xy')
    _settle(qapp)
    assert not area.is_open('xy') and not xy.isVisible()


def test_a_panel_opens_beside_another_with_its_share(area, qapp):
    plot = _panel(area, 'plot', closable=False)
    xy = _panel(area, 'xy', home={'beside': 'plot', 'side': 'right', 'share': 0.4})
    table = _panel(area, 'table', home={'beside': 'plot', 'side': 'bottom', 'share': 0.3})
    area.open_panel('xy')
    area.open_panel('table')
    _settle(qapp)
    (plot_x, plot_y), (xy_x, _y), (table_x, table_y) = (
        _left_top(area, widget) for widget in (plot, xy, table))
    assert plot_x < xy_x                       # right of the plot
    assert table_y > plot_y and table_x == plot_x     # below it, in its column
    assert abs(_place(area, 'xy').width() / area.width() - 0.4) < 0.03


def test_a_panel_opens_at_an_edge_of_the_whole_tab(area, qapp):
    plot = _panel(area, 'plot', closable=False)
    xy = _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    trace = _panel(area, 'trace', home={'edge': 'bottom', 'share': 0.25})
    area.open_panel('xy')
    area.open_panel('trace')
    _settle(qapp)
    assert _left_top(area, trace)[1] > max(_left_top(area, plot)[1], _left_top(area, xy)[1])
    assert abs(_place(area, 'trace').width() - area.width()) <= 2       # the whole width
    assert abs(_place(area, 'trace').height() / area.height() - 0.25) < 0.03


def test_a_panel_opens_as_a_tab_beside_another(area, qapp):
    from gui.panel_area import ROW

    _panel(area, 'plot', closable=False)
    _panel(area, 'table', home={'beside': 'plot', 'side': 'right'})
    _panel(area, 'trace', home={'with': 'table'})
    area.open_panel('table')
    area.open_panel('trace')
    _settle(qapp)
    place = _place(area, 'trace')
    assert place is _place(area, 'table')
    assert place.keys() == ['table', 'trace'] and place.current() == 'trace'
    # Their tabs are how to switch between them: always in view.
    assert place.mode() == ROW and place.strip.isVisible()
    assert [place.bar.tabText(i) for i in range(place.bar.count())] == ['Table', 'Trace']


# ── Titles ───────────────────────────────────────────────────────────────


def test_a_panel_alone_in_its_place_folds_its_title_away(area, qapp):
    from gui.panel_area import OVERLAY

    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    _settle(qapp)
    for key in ('plot', 'xy'):
        place = _place(area, key)
        assert place.mode() == OVERLAY
        assert not place.strip.isVisible()          # no room taken
        assert place.grip.isVisible()               # but a mark on the top edge
        assert place.stack.geometry().top() == CARD_INSET     # only its card's edge above


def test_show_titles_keeps_every_title_in_view_but_a_lone_panels(area, qapp):
    from gui.panel_area import NONE, ROW

    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.set_titles_shown(True)
    _settle(qapp)
    assert _place(area, 'plot').mode() == NONE          # a tab with its plot alone
    area.open_panel('xy')
    _settle(qapp)
    for key in ('plot', 'xy'):
        assert _place(area, key).mode() == ROW
        assert _place(area, key).strip.isVisible()
        assert _place(area, key).stack.geometry().top() > 0


def _edge_of(area, key, below=1):
    return _place(area, key).mapToGlobal(QPoint(20, below))


def test_resting_on_the_top_edge_shows_the_title(area, qapp):
    from gui.panel_area import FOLD_DELAY, PEEK_DELAY

    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    _settle(qapp)
    area._timer.stop()                          # the tests move the pointer
    place, edge = _place(area, 'xy'), _edge_of(area, 'xy')
    area.follow_pointer(edge, 10.0)
    assert not place.strip.isVisible()          # not yet
    area.follow_pointer(edge, 10.0 + PEEK_DELAY / 2)
    assert not place.strip.isVisible()          # still resting, not long enough
    area.follow_pointer(edge, 10.0 + PEEK_DELAY)
    assert place.strip.isVisible() and area.peeked() is place
    assert place.strip.geometry().top() == CARD_INSET   # over the panel, not beside it
    assert not place.grip.isVisible()
    # On the title itself it stays.
    on_title = place.mapToGlobal(QPoint(30, 20))
    area.follow_pointer(on_title, 11.0)
    area.follow_pointer(on_title, 12.0)
    assert place.strip.isVisible()
    # It folds a while after the pointer leaves.
    away = place.mapToGlobal(QPoint(30, 200))
    area.follow_pointer(away, 13.0)
    assert place.strip.isVisible()
    area.follow_pointer(away, 13.0 + FOLD_DELAY)
    assert not place.strip.isVisible() and area.peeked() is None


def test_passing_over_the_edge_does_not_show_the_title(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    _settle(qapp)
    area._timer.stop()
    place = _place(area, 'xy')
    area.follow_pointer(_edge_of(area, 'xy'), 5.0)
    area.follow_pointer(place.mapToGlobal(QPoint(30, 100)), 5.05)   # on, into the panel
    area.follow_pointer(place.mapToGlobal(QPoint(30, 100)), 6.0)
    assert not place.strip.isVisible()


def test_the_rows_below_the_edge_do_not_show_the_title(area, qapp):
    # A panel's own top row (its name, its buttons) is just under the edge:
    # 12 px down is inside it.
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    _settle(qapp)
    area._timer.stop()
    inside = _edge_of(area, 'xy', below=12)
    area.follow_pointer(inside, 1.0)
    area.follow_pointer(inside, 3.0)
    assert not _place(area, 'xy').strip.isVisible()


def test_a_click_on_the_mark_shows_the_title_for_a_while(area, qapp):
    from gui.panel_area import HOLD_DELAY

    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    _settle(qapp)
    area._timer.stop()
    place = _place(area, 'xy')
    place.grip.clicked.emit()
    assert place.strip.isVisible()
    away = place.mapToGlobal(QPoint(30, 200))
    import time
    now = time.monotonic()
    area.follow_pointer(away, now)
    area.follow_pointer(away, now + 1.0)
    assert place.strip.isVisible()               # held, though the pointer is away
    area.follow_pointer(away, now + HOLD_DELAY + 1.0)
    area.follow_pointer(away, now + HOLD_DELAY + 2.0)
    assert not place.strip.isVisible()


def test_a_name_label_shows_while_the_title_is_folded(area, qapp):
    _panel(area, 'plot', closable=False)
    name = QLabel()
    widget = QLabel('xy')
    area.add('xy', widget, 'XY plot', home={'beside': 'plot', 'side': 'right'}, name_label=name)
    widget.setParent(None)
    assert name.text() == 'XY plot'
    area.open_panel('xy')
    assert not name.isHidden()
    area.set_titles_shown(True)
    assert name.isHidden()                       # the title row names it
    area.set_title('xy', 'Load map')
    assert name.text() == 'Load map'
    assert _place(area, 'xy').bar.tabText(0) == 'Load map'
    area.set_titles_shown(False)
    area.float_panel('xy')
    assert name.isHidden()                       # its window's title row names it
    assert area.panel('xy').window.group.bar.tabText(0) == 'Load map'
    area.dock('xy')
    assert not name.isHidden()


# ── Close, reopen ────────────────────────────────────────────────────────


def test_a_closed_panel_opens_again_where_it_was_and_as_large(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'table', home={'beside': 'plot', 'side': 'right'})
    _panel(area, 'xy', home={'edge': 'right', 'share': 0.3})
    area.open_panel('table')
    area.open_panel('xy')
    _settle(qapp)
    before = _place(area, 'xy').geometry()
    area.close_panel('xy')
    _settle(qapp)
    assert not area.is_open('xy') and area.closed_keys() == ['xy']
    assert area.reopen_last() == 'xy'
    _settle(qapp)
    after = _place(area, 'xy').geometry()
    assert abs(after.x() - before.x()) <= 2 and abs(after.width() - before.width()) <= 2


def test_a_panel_that_shared_a_place_comes_back_into_it(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'table', home={'beside': 'plot', 'side': 'right'})
    _panel(area, 'trace', home={'with': 'table'})
    area.open_panel('table')
    area.open_panel('trace')
    area.close_panel('trace')
    assert _place(area, 'table').keys() == ['table']
    area.open_panel('trace')
    assert _place(area, 'trace') is _place(area, 'table')


def test_a_place_left_empty_goes_and_its_neighbours_take_the_room(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    _panel(area, 'table', home={'beside': 'xy', 'side': 'bottom'})
    area.open_panel('xy')
    area.open_panel('table')
    _settle(qapp)
    area.close_panel('xy')
    area.close_panel('table')
    _settle(qapp)
    assert len(area._groups()) == 1
    assert area._splitters() == [area._root]
    assert abs(_place(area, 'plot').width() - area.width()) <= 2


def test_only_a_users_close_is_announced(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    closed = []
    area.panelClosed.connect(closed.append)
    area.open_panel('xy')
    area.close_panel('xy')                     # by the program, as Views ▾ does
    area.open_panel('xy')
    _place(area, 'xy').close_button.click()      # by its ✕
    assert closed == ['xy']


def test_the_time_plot_does_not_close(area, qapp):
    _panel(area, 'plot', closable=False, floatable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    area.close_panel('plot', user=True)
    assert area.is_open('plot')
    assert not _place(area, 'plot').close_button.isVisibleTo(_place(area, 'plot'))
    assert not _place(area, 'plot').window_button.isVisibleTo(_place(area, 'plot'))


def test_remove_forgets_a_panel_and_gives_its_widget_back(area, qapp):
    _panel(area, 'plot', closable=False)
    trace = _panel(area, 'trace', home={'edge': 'bottom'})
    area.open_panel('trace')
    assert area.remove('trace') is trace
    assert area.panel('trace') is None and trace.parent() is None
    assert len(area._groups()) == 1


# ── Maximise ─────────────────────────────────────────────────────────────


def test_maximise_fills_the_tab_and_restore_gives_the_room_back(area, qapp):
    from gui.panel_area import ROW

    plot = _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    _panel(area, 'table', home={'beside': 'xy', 'side': 'bottom'})
    area.open_panel('xy')
    area.open_panel('table')
    _settle(qapp)
    sizes = [splitter.sizes() for splitter in area._splitters()]
    area.maximise('table')
    _settle(qapp)
    place = _place(area, 'table')
    assert area.maximised() == 'table'
    assert not plot.isVisible() and not _place(area, 'xy').isVisible()
    assert abs(place.width() - area.width()) <= 2 and abs(place.height() - area.height()) <= 2
    # Its title is in view, with the button that gives the room back.
    assert place.mode() == ROW and place.max_button.kind == 'restore'
    area.maximise('table')                         # again: restore
    _settle(qapp)
    assert area.maximised() is None and plot.isVisible()
    assert [splitter.sizes() for splitter in area._splitters()] == sizes


def test_opening_a_panel_restores_first(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    _panel(area, 'table', home={'beside': 'plot', 'side': 'bottom'})
    area.open_panel('xy')
    area.maximise('plot')
    area.open_panel('table')
    _settle(qapp)
    assert area.maximised() is None
    assert all(place.isVisible() for place in area._groups())


# ── A window of its own ──────────────────────────────────────────────────


def test_a_panel_moves_into_its_own_window_and_docks_back(area, qapp):
    _panel(area, 'plot', closable=False, floatable=False)
    xy = _panel(area, 'xy', home={'beside': 'plot', 'side': 'right', 'share': 0.35})
    area.window_title = lambda title: f'Tab 1 · {title}'
    area.open_panel('xy')
    _settle(qapp)
    before = _place(area, 'xy').width()
    area.float_panel('xy')
    _settle(qapp)
    window = area.panel('xy').window
    assert area.is_floating('xy') and area.has_windows()
    assert window.isWindow() and window.isVisible() and window.windowTitle() == 'Tab 1 · Xy'
    assert xy.window() is window
    assert len(area._groups()) == 1                 # its place went with it
    window.close()                                  # its window's ✕ docks it back
    _settle(qapp)
    assert not area.is_floating('xy') and area.is_open('xy')
    assert xy.window() is area.window()
    assert abs(_place(area, 'xy').width() - before) <= 3


def test_in_the_tab_its_button_undocks_and_in_its_window_docks_back(area, qapp):
    from gui.panel_area import ROW

    _panel(area, 'plot', closable=False, floatable=False)
    xy = _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    area.set_titles_shown(True)
    _settle(qapp)
    place = _place(area, 'xy')
    assert place.window_button.kind == 'undock' and place.window_button.isVisible()
    assert place.window_button.toolTip().startswith('Undock')
    place.window_button.click()
    _settle(qapp)
    window = area.panel('xy').window
    assert area.is_floating('xy')
    # Its own window shows its title row, with Dock back and a separate ✕.
    group = window.group
    assert group.mode() == ROW and group.strip.isVisible()
    assert group.bar.tabText(0) == 'Xy'
    assert group.window_button.kind == 'dock' and group.window_button.isVisible()
    assert group.window_button.toolTip().startswith('Dock back')
    assert group.close_button.isVisible() and not group.max_button.isVisible()
    group.window_button.click()
    _settle(qapp)
    assert not area.is_floating('xy') and area.is_open('xy')
    assert xy.window() is area.window() and not window.isVisible()


def test_the_cross_in_its_own_window_closes_the_panel(area, qapp):
    _panel(area, 'plot', closable=False, floatable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    closed = []
    area.panelClosed.connect(closed.append)
    area.open_panel('xy')
    area.float_panel('xy')
    _settle(qapp)
    window = area.panel('xy').window
    window.group.close_button.click()
    _settle(qapp)
    assert closed == ['xy']
    assert not area.is_open('xy') and not area.is_floating('xy') and not window.isVisible()
    assert area.closed_keys() == ['xy']
    area.reopen_last()                       # back where it was in the tab
    assert area.is_open('xy') and not area.is_floating('xy')


def test_the_panel_menu_says_undock_dock_back_and_rename(area, qapp):
    _panel(area, 'plot', closable=False, floatable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'},
           rename=lambda: area.edit_title('xy'))
    _panel(area, 'table', home={'beside': 'plot', 'side': 'bottom'})
    area.open_panel('xy')
    area.open_panel('table')
    texts = [action.text() for action in area.panel_menu('xy').actions() if action.text()]
    assert texts == ['Maximise', 'Undock', 'Rename…', 'Close']
    assert 'Rename…' not in [action.text() for action in area.panel_menu('table').actions()]
    area.float_panel('xy')
    texts = [action.text() for action in area.panel_menu('xy').actions() if action.text()]
    assert texts == ['Dock back', 'Rename…', 'Close']


def _editor(parent):
    from gui.tab_drawing import _NameEditor
    editors = [child for child in parent.findChildren(_NameEditor) if child.isVisible()]
    assert len(editors) == 1
    return editors[0]


def _type_and(qapp, editor, text, key):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    editor.setText(text)
    QTest.keyClick(editor, key)
    _settle(qapp)


def test_a_panel_is_renamed_in_place_on_its_name(area, qapp):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    _panel(area, 'plot', closable=False, floatable=False)
    name = QLabel()
    widget = QWidget()
    from PySide6.QtWidgets import QVBoxLayout
    QVBoxLayout(widget).addWidget(name)
    area.add('xy', widget, 'XY plot', home={'beside': 'plot', 'side': 'right'},
             name_label=name, rename=lambda: area.edit_title('xy', 20))
    renamed = []
    area.panelRenamed.connect(lambda key, title: renamed.append((key, title)))
    area.open_panel('xy')
    _settle(qapp)
    assert name.isVisible() and name.toolTip() == 'Double-click to rename'
    # A double-click on its name, folded title and all.
    press = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(4, 4), QPointF(4, 4),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(name, press)
    _settle(qapp)
    editor = _editor(name)
    assert editor.text() == 'XY plot' and editor.maxLength() == 20
    _type_and(qapp, editor, '  Load   map ', Qt.Key.Key_Return)
    assert area.title('xy') == 'Load map' and name.text() == 'Load map'
    assert renamed == [('xy', 'Load map')]
    # Escape leaves it as it was.
    area.rename('xy')
    _type_and(qapp, _editor(name), 'Other', Qt.Key.Key_Escape)
    assert area.title('xy') == 'Load map' and renamed == [('xy', 'Load map')]


def test_with_titles_shown_a_panel_is_renamed_on_its_tab(area, qapp):
    from PySide6.QtCore import Qt

    _panel(area, 'plot', closable=False, floatable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'},
           rename=lambda: area.edit_title('xy'))
    area.open_panel('xy')
    area.set_titles_shown(True)
    _settle(qapp)
    place = _place(area, 'xy')
    place.bar.tabBarDoubleClicked.emit(0)
    _settle(qapp)
    editor = _editor(place.bar)
    _type_and(qapp, editor, 'Gear map', Qt.Key.Key_Return)
    assert area.title('xy') == 'Gear map' and place.bar.tabText(0) == 'Gear map'
    # A panel that cannot be renamed is not.
    place_of_plot = _place(area, 'plot')
    place_of_plot.bar.tabBarDoubleClicked.emit(0)
    _settle(qapp)
    assert not place_of_plot.bar.findChildren(type(editor))


def test_a_panels_gear_is_left_of_its_titles_other_buttons(area, qapp):
    from gui.panel_area import TITLE_BUTTONS_ROOM

    _panel(area, 'plot', closable=False, floatable=False)
    host = QWidget()
    gear = QLabel('gear', host)
    opened = []
    area.add('xy', host, 'XY plot', home={'beside': 'plot', 'side': 'right'},
             settings=opened.append, settings_button=gear)
    _panel(area, 'table', home={'beside': 'plot', 'side': 'bottom'})
    area.open_panel('xy')
    area.open_panel('table')
    area.set_titles_shown(True)
    _settle(qapp)
    place = _place(area, 'xy')
    strip_gear = place.settings_button
    assert strip_gear.isVisible() and strip_gear.toolTip() == 'Settings'
    # Just before maximise, undock and close, which take TITLE_BUTTONS_ROOM.
    assert strip_gear.geometry().right() < place.max_button.x()
    assert place.strip.width() - strip_gear.geometry().right() - 1 == TITLE_BUTTONS_ROOM
    assert not _place(area, 'table').settings_button.isVisible()
    strip_gear.click()
    assert opened == [strip_gear]
    menu = area.panel_menu('xy')
    texts = [action.text() for action in menu.actions() if action.text()]
    assert texts == ['Maximise', 'Undock', 'Settings…', 'Close']
    [action for action in menu.actions() if action.text() == 'Settings…'][0].trigger()
    assert opened == [strip_gear, None]
    # The panel's own gear shows while its title does not.
    assert gear.isHidden()
    area.set_titles_shown(False)
    _settle(qapp)
    assert not gear.isHidden()
    area.float_panel('xy')
    _settle(qapp)
    assert gear.isHidden() and area.panel('xy').window.group.settings_button.isVisible()
    area.dock('xy')
    assert not gear.isHidden()


# ── Cards ────────────────────────────────────────────────────────────────


def _pixel(widget, x, y):
    image = widget.grab().toImage()
    ratio = image.devicePixelRatio()
    return image.pixelColor(int(x * ratio), int(y * ratio))


def _distance(one, other):
    return sum(abs(a - b) for a, b in zip(one.getRgb()[:3], other.getRgb()[:3]))


def test_each_place_is_a_card_with_a_gap_between_cards(area, qapp):
    from PySide6.QtGui import QPalette
    from gui.panel_area import GUTTER, _Gutter

    _panel(area, 'plot', closable=False, floatable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    _settle(qapp)
    plot, xy = _place(area, 'plot'), _place(area, 'xy')
    # The panel lies inside its card's edge.
    for place in (plot, xy):
        assert place.stack.geometry().left() == CARD_INSET
        assert place.width() - place.stack.geometry().right() - 1 == CARD_INSET
    # Between the cards, an even gap, dragged as the splitter.
    splitter = xy.parentWidget()
    assert splitter.handleWidth() == GUTTER
    assert all(isinstance(splitter.handle(i), _Gutter) for i in range(1, splitter.count()))
    left, _top = _left_top(area, xy)
    assert left - (_left_top(area, plot)[0] + plot.width()) == GUTTER
    # The card's outline in the plot tabs' colour; the gap left as the window.
    window = area.palette().color(QPalette.ColorRole.Window)
    middle = area.height() // 2
    assert _distance(_pixel(area, left, middle), xy.outline_color()) <= 30
    assert _distance(_pixel(area, left - GUTTER // 2, middle), window) <= 6


def test_the_grip_mark_is_quieter_than_the_cards_outline(area, qapp):
    from PySide6.QtGui import QPalette
    from gui.panel_area import GRIP_HEIGHT

    _panel(area, 'plot', closable=False, floatable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    _settle(qapp)
    place = _place(area, 'xy')
    grip = place.grip
    assert grip.isVisible()
    mark = _pixel(grip, grip.width() // 2, 2 + GRIP_HEIGHT // 2)
    window = grip.palette().color(QPalette.ColorRole.Window)
    assert 0 < _distance(mark, window) < _distance(place.outline_color(), window)


def test_the_time_plot_stays_in_the_tab(area, qapp):
    _panel(area, 'plot', closable=False, floatable=False)
    area.float_panel('plot')
    assert not area.is_floating('plot')


def test_closing_a_panel_in_a_window_closes_the_window(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    area.open_panel('xy')
    area.float_panel('xy')
    window = area.panel('xy').window
    area.close_panel('xy')
    _settle(qapp)
    assert not area.is_open('xy') and not window.isVisible()
    area.open_panel('xy')
    assert area.is_open('xy') and not area.is_floating('xy')


# ── A configuration ──────────────────────────────────────────────────────


def _three(area):
    _panel(area, 'plot', closable=False, floatable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right', 'share': 0.4})
    _panel(area, 'table', home={'beside': 'xy', 'side': 'bottom', 'share': 0.3})
    _panel(area, 'trace', home={'with': 'table'})


def test_describe_gives_the_tree_with_sizes_as_fractions(area, qapp):
    _three(area)
    for key in ('xy', 'table', 'trace'):
        area.open_panel(key)
    _place(area, 'table').set_current('table')
    _settle(qapp)
    layout = area.describe()
    tree = layout['tree']
    assert tree['split'] == 'row' and len(tree['children']) == 2
    assert tree['children'][0] == {'panels': ['plot'], 'current': 'plot'}
    column = tree['children'][1]
    assert column['split'] == 'column'
    assert column['children'][1] == {'panels': ['table', 'trace'], 'current': 'table'}
    assert abs(sum(tree['sizes']) - 1) < 0.01
    assert abs(tree['sizes'][1] - 0.4) < 0.03
    assert 'windows' not in layout


def test_a_described_layout_is_put_back(area, qapp):
    from gui.panel_area import PanelArea

    _three(area)
    for key in ('xy', 'table', 'trace'):
        area.open_panel(key)
    _settle(qapp)
    layout = area.describe()

    other = PanelArea()
    other.resize(1000, 600)
    for key in ('plot', 'xy', 'table', 'trace'):
        other.add(key, QLabel(key), key.title(), closable=key != 'plot')
    other.restore_layout(layout)
    other.show()
    _settle(qapp)
    again = other.describe()
    assert _shape(again['tree']) == _shape(layout['tree'])
    for a, b in zip(_all_sizes(again['tree']), _all_sizes(layout['tree'])):
        assert abs(a - b) < 0.02
    other.close()
    other.deleteLater()


def _shape(node):
    if 'panels' in node:
        return (tuple(node['panels']), node['current'])
    return (node['split'], tuple(_shape(child) for child in node['children']))


def _all_sizes(node):
    if 'panels' in node:
        return []
    return list(node['sizes']) + [size for child in node['children'] for size in _all_sizes(child)]


def test_panels_a_layout_leaves_out_stay_closed_but_the_plot_opens(area, qapp):
    _three(area)
    area.open_panel('xy')
    area.restore_layout({'tree': {'panels': ['table'], 'current': 'table'}})
    assert area.is_open('table') and area.is_open('plot')
    assert not area.is_open('xy') and not area.is_open('trace')


def test_a_panel_named_in_a_layout_but_added_later_goes_to_its_place(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'edge': 'right'})
    area.restore_layout({'tree': {'split': 'column', 'sizes': [0.7, 0.3], 'children': [
        {'panels': ['plot'], 'current': 'plot'},
        {'panels': ['trace'], 'current': 'trace'}]}})
    _settle(qapp)
    trace = _panel(area, 'trace', home={'edge': 'right'})     # the Trace comes after decode
    area.open_panel('trace')
    _settle(qapp)
    assert _left_top(area, trace)[1] > 0 and _left_top(area, trace)[0] == CARD_INSET  # below
    assert abs(_place(area, 'trace').height() / area.height() - 0.3) < 0.04


def test_a_broken_layout_leaves_the_tab_as_it_is(area, qapp):
    _three(area)
    area.open_panel('xy')
    for broken in (None, [], {'tree': 'x'}, {'tree': {'split': 'row', 'children': 'x'}}):
        area.restore_layout(broken)
    assert area.is_open('plot')
    area.restore_layout({'tree': {'panels': ['nonsense', 7]}})
    assert area.is_open('plot')                  # a tab always keeps its plot


def test_a_window_in_a_layout_opens_where_it_was_on_a_screen_that_is_there(area, qapp):
    from PySide6.QtGui import QGuiApplication

    _panel(area, 'plot', closable=False, floatable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right'})
    room = QGuiApplication.primaryScreen().availableGeometry()
    rect = [room.x() + 50, room.y() + 60, 400, 300]
    area.restore_layout({'tree': {'panels': ['plot'], 'current': 'plot'},
                         'windows': [{'panels': ['xy'], 'rect': rect}]})
    _settle(qapp)
    assert area.is_floating('xy')
    assert area.describe()['windows'][0]['panels'] == ['xy']
    # On no screen at all: docked instead.
    area.restore_layout({'tree': {'panels': ['plot'], 'current': 'plot'},
                         'windows': [{'panels': ['xy'], 'rect': [-90000, -90000, 400, 300]}]})
    assert area.is_open('xy') and not area.is_floating('xy')


def test_layout_keys_names_the_panels_in_the_tab_and_in_windows(qapp):
    from gui.panel_area import layout_keys

    layout = {'tree': {'split': 'row', 'sizes': [0.5, 0.5], 'children': [
        {'panels': ['plot']}, {'panels': ['table', 'trace']}]},
        'windows': [{'panels': ['xy']}]}
    assert layout_keys(layout) == ['plot', 'table', 'trace', 'xy']
    assert layout_keys(None) == [] and layout_keys({'tree': 3}) == []


def test_a_panel_comes_back_where_it_was_moved_to_not_where_it_first_opened(area, qapp):
    plot = _panel(area, 'plot', closable=False)
    xy = _panel(area, 'xy', home={'edge': 'right', 'share': 0.4})
    # A configuration put it below the plot.
    area.restore_layout({'tree': {'split': 'column', 'sizes': [0.7, 0.3], 'children': [
        {'panels': ['plot'], 'current': 'plot'}, {'panels': ['xy'], 'current': 'xy'}]}})
    _settle(qapp)
    area.close_panel('xy')
    area.open_panel('xy')
    _settle(qapp)
    assert _left_top(area, xy)[1] > _left_top(area, plot)[1]      # below again
    assert _left_top(area, xy)[0] == _left_top(area, plot)[0]


def test_restore_keeps_the_shares_though_the_tab_changed_size_meanwhile(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right', 'share': 0.3})
    area.open_panel('xy')
    _settle(qapp)
    before = _place(area, 'xy').width() / area.width()
    area.maximise('plot')
    _settle(qapp)
    area.resize(700, 500)
    _settle(qapp)
    area.restore()
    _settle(qapp)
    assert abs(_place(area, 'xy').width() / area.width() - before) < 0.03


def test_a_maximised_tab_is_described_as_it_lies_when_restored(area, qapp):
    _panel(area, 'plot', closable=False)
    _panel(area, 'xy', home={'beside': 'plot', 'side': 'right', 'share': 0.3})
    area.open_panel('xy')
    _settle(qapp)
    before = area.describe()
    area.maximise('xy')
    _settle(qapp)
    assert area.describe() == before


def _awkward_layout():
    return {'tree': {'split': 'row', 'sizes': [0.3333, 0.6667], 'children': [
        {'panels': ['plot'], 'current': 'plot'},
        {'split': 'column', 'sizes': [0.2857, 0.7143], 'children': [
            {'panels': ['xy'], 'current': 'xy'},
            {'panels': ['table'], 'current': 'table'}]}]}}


def test_a_restored_layout_is_described_as_it_was_given(area, qapp):
    # Measured again in whole pixels the shares would differ in the last
    # digit, and a configuration opened and closed unchanged would ask to be
    # saved.
    _three(area)
    layout = _awkward_layout()
    area.restore_layout(layout)
    _settle(qapp)
    area.resize(913, 577)
    _settle(qapp)
    assert area.describe() == layout


def test_a_dragged_splitter_is_described_as_it_now_is(area, qapp):
    _three(area)
    area.restore_layout(_awkward_layout())
    _settle(qapp)
    root = area._root
    root.setSizes([500, 500])
    root.splitterMoved.emit(500, 1)              # as a drag of its handle does
    _settle(qapp)
    assert area.describe()['tree']['sizes'] == [0.5, 0.5]


def test_a_layouts_own_window_leaves_the_tab_as_described(area, qapp):
    from PySide6.QtGui import QGuiApplication

    _three(area)
    # Docked first, the Trace would split the tab along its foot.
    area.panel('trace').home = {'edge': 'bottom', 'share': 0.3}
    room = QGuiApplication.primaryScreen().availableGeometry()
    layout = _awkward_layout()
    layout['tree']['children'][1]['children'][1]['panels'] = ['table']
    layout['windows'] = [{'panels': ['trace'], 'rect': [room.x() + 40, room.y() + 40, 400, 300]}]
    area.restore_layout(layout)
    _settle(qapp)
    assert area.is_floating('trace')
    assert area.describe()['tree'] == layout['tree']
