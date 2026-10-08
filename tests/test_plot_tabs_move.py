# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Signals dropped on a tab, and moved from one tab to another.

Signals dragged from the signal tree onto a tab are plotted in it, and onto
"+" in a new tab; that tab comes on screen. Rows dragged from the signal table
onto another tab, or sent there with "Move to tab", move there with their look,
and the tab on screen stays, unless they go to a new tab.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMenu


class _Store:
    """Just enough of a SignalStore to plot from."""

    def __init__(self, duration: float, names: tuple[str, ...]) -> None:
        from core.signal_store import SignalSeries
        ts = array.array('d', (i * duration / 1000 for i in range(1001)))
        vs = array.array('d', (float(i) for i in range(1001)))
        self._series = {name: SignalSeries(None, 'Msg', 1, name, 'km/h', ts, vs)
                        for name in names}

    def get_series(self, key):
        return self._series.get(key)


@pytest.fixture()
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    # Several tabs are a Pro feature, which these tests are about.
    monkeypatch.setattr('gui.plot_tabs.multiple_tabs_allowed', lambda: True)
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    w.store = _Store(20.0, ('Speed', 'Torque', 'Current', 'Voltage'))
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def _tabs(window) -> list[tuple[str, list[str]]]:
    return [(name, panel.plotted_keys())
            for name, panel in zip(window.plot_tabs.names(), window.plot_tabs.panels())]


def _two_tabs(qapp, window):
    """Tab 1: Speed, Torque and Current. "Motor": Voltage. Tab 1 on screen."""
    window.add_signals_to_plot(['Speed', 'Torque', 'Current'])
    window.plot_tabs.add_tab('Motor')
    window.add_signals_to_plot(['Voltage'])
    window.plot_tabs.bar.setCurrentIndex(0)
    qapp.processEvents()
    return window.plot_tabs.panels()


def _mime(keys, rows: bool) -> QMimeData:
    from gui.plot_widget import ROW_MIME_TYPE
    from gui.signal_tree import SignalTreeWidget
    mime = QMimeData()
    mime.setData(ROW_MIME_TYPE if rows else SignalTreeWidget.MIME_TYPE,
                 '\n'.join(keys).encode('utf-8'))
    return mime


_ACTIONS = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction


def _drag_over(widget, at: QPoint, mime: QMimeData) -> bool:
    """Drag mime over widget, to at. True if a drop there would be taken.

    Qt sends the moves and the drop only to the widget that took the drag
    as it entered, as a real drag does."""
    enter = QDragEnterEvent(at, _ACTIONS, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, enter)
    if not enter.isAccepted():
        return False
    move = QDragMoveEvent(at, _ACTIONS, mime, Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, move)
    return move.isAccepted()


def _drop(qapp, widget, at: QPoint, mime: QMimeData) -> bool:
    """Drag mime onto widget, and drop it at. True if the drop was taken."""
    _drag_over(widget, at, mime)
    drop = QDropEvent(QPointF(at), _ACTIONS, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, drop)
    qapp.processEvents()
    return drop.isAccepted()


def _on_tab(window, index: int) -> QPoint:
    return window.plot_tabs.bar.tabRect(index).center()


def test_signals_dropped_on_a_tab_are_plotted_there_and_it_comes_on_screen(qapp, window):
    _two_tabs(qapp, window)
    bar = window.plot_tabs.bar

    assert _drag_over(bar, _on_tab(window, 1), _mime(['Speed', 'Current'], rows=False))
    assert _drop(qapp, bar, _on_tab(window, 1), _mime(['Speed', 'Current'], rows=False))

    assert _tabs(window) == [('Tab 1', ['Speed', 'Torque', 'Current']),
                             ('Motor', ['Voltage', 'Speed', 'Current'])]
    assert window.plot_panel is window.plot_tabs.panels()[1]


def test_signals_dropped_on_plus_open_a_new_tab_with_them(qapp, window):
    _two_tabs(qapp, window)
    plus = window.plot_tabs.add_button

    assert _drag_over(plus, plus.rect().center(), _mime(['Torque'], rows=False))
    assert _drop(qapp, plus, plus.rect().center(), _mime(['Torque'], rows=False))

    assert _tabs(window)[2] == ('Tab 3', ['Torque'])
    assert window.plot_panel is window.plot_tabs.panels()[2]
    assert window.plot_tabs.panels()[0].plotted_keys() == ['Speed', 'Torque', 'Current']


def test_rows_dropped_on_another_tab_move_there_as_they_looked(qapp, window):
    first, motor = _two_tabs(qapp, window)
    first.set_series_color('Torque', '#00ff00')
    first.set_series_line_style('Torque', 'dash')
    first._items['Current'].visible = False
    bar = window.plot_tabs.bar

    # Dropped in the tab's order, whichever way round they were selected.
    assert _drop(qapp, bar, _on_tab(window, 1), _mime(['Current', 'Torque'], rows=True))

    assert _tabs(window) == [('Tab 1', ['Speed']),
                             ('Motor', ['Voltage', 'Torque', 'Current'])]
    torque, current = motor._items['Torque'], motor._items['Current']
    assert (torque.color, torque.line_style) == ('#00ff00', 'dash')
    assert not current.visible
    assert window.plot_panel is first              # the tab on screen stays
    assert window.status_state_label.text() == 'State: Moved 2 signal(s) to Motor'

    # One undo step in each tab.
    motor.undo()
    assert motor.plotted_keys() == ['Voltage']
    first.undo()
    assert first.plotted_keys() == ['Speed', 'Torque', 'Current']


def test_rows_do_not_drop_on_their_own_tab(qapp, window):
    _two_tabs(qapp, window)
    bar = window.plot_tabs.bar

    assert not _drag_over(bar, _on_tab(window, 0), _mime(['Speed'], rows=True))
    assert not _drop(qapp, bar, _on_tab(window, 0), _mime(['Speed'], rows=True))
    assert _drag_over(bar, _on_tab(window, 1), _mime(['Speed'], rows=True))
    assert bar._outline.isVisible()
    QApplication.sendEvent(bar, QDragLeaveEvent())
    assert not bar._outline.isVisible()

    assert _tabs(window) == [('Tab 1', ['Speed', 'Torque', 'Current']),
                             ('Motor', ['Voltage'])]


def test_rows_dropped_on_plus_move_to_a_new_tab_at_the_same_time(qapp, window):
    first, _motor = _two_tabs(qapp, window)
    first.show_time_range(5.0, 8.0)
    plus = window.plot_tabs.add_button

    assert _drop(qapp, plus, plus.rect().center(), _mime(['Torque'], rows=True))

    new = window.plot_tabs.panels()[2]
    assert _tabs(window)[0] == ('Tab 1', ['Speed', 'Current'])
    assert _tabs(window)[2] == ('Tab 3', ['Torque'])
    assert window.plot_panel is new
    x_min, x_max = new.time_range()
    assert (round(x_min, 6), round(x_max, 6)) == (5.0, 8.0)


def test_the_signal_menu_moves_signals_to_the_tab_picked(qapp, window):
    first, motor = _two_tabs(qapp, window)
    window.plot_tabs.add_tab('Brakes & ABS')
    window.plot_tabs.bar.setCurrentIndex(0)
    qapp.processEvents()

    menu = QMenu()
    tabs = first._add_move_to_tab_menu(menu, ['Speed'])
    assert tabs.title() == 'Move to tab' and tabs.isEnabled()
    assert [action.text() for action in tabs.actions()] == [
        'Motor', 'Brakes && ABS', '', 'New tab']

    tabs.actions()[0].trigger()
    qapp.processEvents()

    assert _tabs(window)[:2] == [('Tab 1', ['Torque', 'Current']),
                                 ('Motor', ['Voltage', 'Speed'])]
    assert window.plot_panel is first


def test_the_signal_menu_can_move_signals_to_a_new_tab(qapp, window):
    window.add_signals_to_plot(['Speed', 'Torque'])

    menu = QMenu()
    tabs = window.plot_panel._add_move_to_tab_menu(menu, ['Torque'])
    assert [action.text() for action in tabs.actions()] == ['New tab']
    tabs.actions()[0].trigger()
    qapp.processEvents()

    assert _tabs(window) == [('Tab 1', ['Speed']), ('Tab 2', ['Torque'])]
    assert window.plot_panel is window.plot_tabs.panels()[1]


def test_nothing_selected_leaves_move_to_tab_greyed_out(qapp, window):
    window.add_signals_to_plot(['Speed'])

    menu = QMenu()
    tabs = window.plot_panel._add_move_to_tab_menu(menu, [])

    assert not tabs.isEnabled()


def test_a_signal_the_tab_already_shows_keeps_its_look_there(qapp, window):
    first, motor = _two_tabs(qapp, window)
    window.plot_tabs.bar.setCurrentIndex(1)
    window.add_signals_to_plot(['Speed'])
    motor.set_series_color('Speed', '#123456')
    window.plot_tabs.bar.setCurrentIndex(0)
    qapp.processEvents()

    first.moveToTabRequested.emit(['Speed', 'Torque'], 1)
    qapp.processEvents()

    assert _tabs(window) == [('Tab 1', ['Current']),
                             ('Motor', ['Voltage', 'Speed', 'Torque'])]
    assert motor._items['Speed'].color == '#123456'


def test_signals_sharing_a_multistack_row_share_one_in_the_tab_they_move_to(qapp, window):
    first, motor = _two_tabs(qapp, window)
    window.btn_multistack.setChecked(True)
    first.move_signals_to_stack(['Torque'], first._items['Speed'].multistack_id,
                                confirm_units=False)
    window.plot_tabs.bar.setCurrentIndex(1)
    window.btn_multistack.setChecked(True)
    window.plot_tabs.bar.setCurrentIndex(0)
    qapp.processEvents()

    first.moveToTabRequested.emit(['Speed', 'Torque', 'Current'], 1)
    qapp.processEvents()

    rows = {key: plotted.multistack_id for key, plotted in motor._items.items()}
    assert rows['Speed'] == rows['Torque']
    assert len({rows['Voltage'], rows['Speed'], rows['Current']}) == 3
    window.plot_tabs.bar.setCurrentIndex(1)
    qapp.processEvents()
    assert motor._stacked_row_keys == [['Voltage'], ['Speed', 'Torque'], ['Current']]


def test_signals_moved_to_a_new_tab_keep_the_plot_mode_of_their_tab(qapp, window):
    window.add_signals_to_plot(['Speed', 'Torque', 'Current'])
    first = window.plot_panel
    window.btn_multistack.setChecked(True)
    first.move_signals_to_stack(['Torque'], first._items['Speed'].multistack_id,
                                confirm_units=False)
    qapp.processEvents()

    first.moveToTabRequested.emit(['Speed', 'Torque'], -1)
    qapp.processEvents()

    new = window.plot_panel
    assert new is window.plot_tabs.panels()[1] and new._multistack_mode
    assert window.btn_multistack.isChecked()
    assert new._stacked_row_keys == [['Speed', 'Torque']]
    assert window.status_next_step_label.text() == 'Next: Double-click the tab to rename it'
    # "+" itself still opens a tab the way the application starts.
    window.plot_tabs.add_button.click()
    qapp.processEvents()
    assert window.plot_panel._stacked_mode and not window.plot_panel._multistack_mode
