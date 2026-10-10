# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tests for gui/window_frame.py: the toolbar's row in place of the title bar.

What the row tells Windows about each place on it is tested here. Windows
itself asks only on its own platform, so the frame's native side (the caption
taken off, the maximized window kept on its screen) is checked by hand.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def row(qapp):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QToolBar, QWidget
    from gui.window_frame import TitleRow

    host = QWidget()
    host.resize(1200, 100)
    toolbar = QToolBar('Main')
    for text in ('Open File', 'Open Database', 'Load + Decode'):
        toolbar.addAction(QAction(text, toolbar))
    toolbar.addSeparator()
    toolbar.addAction(QAction('About', toolbar))
    r = TitleRow(toolbar, host)
    r.show_window_buttons(True)
    r.resize(1200, r.sizeHint().height())
    host.show()
    qapp.processEvents()
    yield r
    host.close()
    qapp.processEvents()


def _center(row, widget):
    return widget.mapTo(row, widget.rect().center())


def _free_room(row):
    """A point between the toolbar's end and the minimize button."""
    from PySide6.QtCore import QPoint
    return QPoint((row.toolbar.geometry().right() + row.minimize_button.x()) // 2,
                  row.height() // 2)


def test_each_place_on_the_row_answers_what_the_title_bar_had_there(row):
    from gui.window_frame import HTCAPTION, HTCLIENT, HTMAXBUTTON

    toolbar = row.toolbar
    open_file = toolbar.widgetForAction(toolbar.actions()[0])
    separator = next(c for c in toolbar.children()
                     if c.metaObject().className() == 'QToolBarSeparator')
    assert row.hit_test(_center(row, row.icon)) == HTCAPTION
    assert row.hit_test(_center(row, open_file)) == HTCLIENT
    assert row.hit_test(_center(row, separator)) == HTCAPTION
    assert row.hit_test(_free_room(row)) == HTCAPTION
    assert row.hit_test(_center(row, row.minimize_button)) == HTCLIENT
    assert row.hit_test(_center(row, row.maximize_button)) == HTMAXBUTTON
    assert row.hit_test(_center(row, row.close_button)) == HTCLIENT


def test_a_narrow_row_keeps_its_window_buttons_and_room_to_move_the_window(qapp, row):
    from gui.window_frame import HTCAPTION

    row.resize(420, row.height())
    qapp.processEvents()
    assert all(button.isVisible() for button in row.window_buttons())
    assert row.close_button.geometry().right() == row.width() - 1
    room = row.minimize_button.x() - row.toolbar.geometry().right() - 1
    assert room >= 48
    assert row.hit_test(_free_room(row)) == HTCAPTION


def test_a_widget_at_the_end_sits_before_the_window_buttons(qapp, row):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QToolButton
    from gui.window_frame import HTCAPTION, HTCLIENT

    link = QToolButton()
    link.setText('Help us improve')
    row.add_end_widget(link)
    row.resize(700, row.height())
    qapp.processEvents()

    assert row.toolbar.geometry().right() + 48 < link.x()
    assert link.geometry().right() < row.minimize_button.x()
    assert row.close_button.geometry().right() == row.width() - 1
    assert row.hit_test(_center(row, link)) == HTCLIENT
    # The room between the toolbar and the widget still moves the window.
    room = QPoint((row.toolbar.geometry().right() + link.x()) // 2, row.height() // 2)
    assert row.hit_test(room) == HTCAPTION


def test_the_maximize_button_offers_restore_once_maximized(row):
    row.show_maximized_state(True)
    assert row.maximize_button.accessibleName() == 'Restore'
    row.show_maximized_state(False)
    assert row.maximize_button.accessibleName() == 'Maximize'


def test_the_window_buttons_are_the_height_of_windows_own(row):
    from gui.window_frame import BUTTON_SIZE

    assert row.height() >= BUTTON_SIZE.height()
    for button in row.window_buttons():
        assert button.width() == BUTTON_SIZE.width()
        assert button.height() == row.height()


def test_the_main_window_toolbar_sits_in_the_top_row(qapp):
    from PySide6.QtWidgets import QToolBar
    from gui.main_window import MainWindow

    window = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        assert window.menuWidget() is window.title_row
        assert window.title_row.toolbar.actions()[0].text() == 'Open File'
        # The only toolbar is the row's, none in the main window's own areas.
        assert window.findChildren(QToolBar) == [window.title_row.toolbar]
        assert window.title_row.toolbar.parentWidget() is window.title_row
        # Off Windows' own platform the window keeps its title bar, and the
        # row draws no second set of window buttons.
        assert not window._window_frame.active
        assert not any(b.isVisibleTo(window) for b in window.title_row.window_buttons())
    finally:
        window.deleteLater()
        qapp.processEvents()
