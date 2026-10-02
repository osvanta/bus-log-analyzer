# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tests for gui/overflow_row.py and the plot buttons' place in the main window.

The plot buttons sit above the plot only, level with the signal table's
header. Buttons that do not fit the plot's width move into a "»" menu rather
than holding the plot column wide enough to squeeze the table.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest


LABELS = ['Fit to Window', 'Fit Vertical', 'Multi-Axis', 'Stacked',
          'MultiStack', 'Cursor 1', 'Cursor 2', 'Show Data Points']


@pytest.fixture()
def row(qapp):
    from PySide6.QtWidgets import QPushButton, QWidget
    from gui.overflow_row import OverflowButtonRow

    # Hosted in a plain parent, as in the main window: a top-level row would
    # have its layout's minimum width imposed on it as a window.
    host = QWidget()
    host.resize(2000, 200)
    r = OverflowButtonRow(host)
    for label in LABELS:
        button = QPushButton(label)
        button.setCheckable(True)
        r.add_button(button)
    host.show()
    qapp.processEvents()
    yield r
    host.close()
    qapp.processEvents()


def _buttons(row):
    return list(row._buttons)


def _full_width(row):
    widths = [b.sizeHint().width() for b in _buttons(row)]
    return sum(widths) + row.SPACING * (len(widths) - 1)


def _resize(qapp, row, width):
    row.resize(width, row.sizeHint().height())
    qapp.processEvents()


def test_all_buttons_show_when_they_fit(qapp, row):
    _resize(qapp, row, _full_width(row) + 50)

    assert row.overflowed_buttons() == []
    assert all(b.isVisible() for b in _buttons(row))
    assert not row.more_button.isVisible()


def test_buttons_past_the_right_edge_move_into_the_menu(qapp, row):
    _resize(qapp, row, _full_width(row) // 2)

    buttons = _buttons(row)
    overflowed = row.overflowed_buttons()
    assert overflowed, 'half the width must not hold every button'
    # The row keeps a leading run of buttons and hands the tail to the menu.
    assert overflowed == buttons[len(buttons) - len(overflowed):]
    assert all(b.isVisible() for b in buttons[:len(buttons) - len(overflowed)])
    assert not any(b.isVisible() for b in overflowed)
    assert row.more_button.isVisible()
    # What stays on the row, plus the menu button, fits the width without
    # the layout squeezing any button below its natural width.
    shown = [b for b in buttons if b.isVisible()] + [row.more_button]
    assert max(w.geometry().right() for w in shown) < row.width()
    assert all(b.width() >= b.sizeHint().width() for b in shown)


def test_widening_brings_buttons_back(qapp, row):
    _resize(qapp, row, _full_width(row) // 2)
    _resize(qapp, row, _full_width(row) + 50)

    assert row.overflowed_buttons() == []
    assert not row.more_button.isVisible()


def test_row_does_not_hold_its_parent_wide(row):
    # Its minimum is the menu button alone, not the sum of the buttons.
    assert row.minimumSizeHint().width() < _buttons(row)[0].sizeHint().width()


def test_longer_button_text_refits_the_row(qapp, row):
    _resize(qapp, row, _full_width(row))
    assert row.overflowed_buttons() == []

    last = _buttons(row)[-1]
    last.setText(last.text() + ' and much longer')
    qapp.processEvents()

    assert last in row.overflowed_buttons()


def test_menu_entries_mirror_and_click_the_hidden_buttons(qapp, row):
    _resize(qapp, row, _full_width(row) // 2)
    overflowed = row.overflowed_buttons()
    checked, disabled = overflowed[0], overflowed[-1]
    checked.setChecked(True)
    disabled.setEnabled(False)
    clicked = Mock()
    checked.clicked.connect(clicked)

    row.more_menu.aboutToShow.emit()
    actions = row.more_menu.actions()

    assert [a.text() for a in actions] == [b.text() for b in overflowed]
    assert actions[0].isCheckable() and actions[0].isChecked()
    assert not actions[-1].isEnabled()

    actions[0].trigger()

    clicked.assert_called_once()
    assert not checked.isChecked()


def test_reopening_the_menu_does_not_duplicate_entries(qapp, row):
    _resize(qapp, row, _full_width(row) // 2)

    row.more_menu.aboutToShow.emit()
    row.more_menu.aboutToShow.emit()

    assert len(row.more_menu.actions()) == len(row.overflowed_buttons())


# ── Main window placement ────────────────────────────────────────────────


@pytest.fixture()
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))

    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def test_plot_buttons_sit_above_the_plot_not_the_table(window):
    splitter = window.center_splitter
    table_side, plot_side = splitter.widget(0), splitter.widget(1)

    assert table_side is window.plot_panel.table_panel
    assert window.plot_button_row.parentWidget() is plot_side
    assert window.plot_panel.parentWidget() is plot_side
    assert window.plot_button_row.geometry().bottom() < window.plot_panel.geometry().top()
    for button in (window.btn_fit, window.btn_stacked, window.btn_points):
        assert button.parentWidget() is window.plot_button_row


def test_plot_buttons_are_level_with_the_table_header(window):
    from PySide6.QtCore import QPoint

    splitter = window.center_splitter
    row = window.plot_button_row
    table = window.plot_panel.table
    header = table.horizontalHeader()

    def top_and_bottom(widget):
        top = widget.mapTo(splitter, QPoint(0, 0)).y()
        return top, top + widget.height()

    row_top, row_bottom = top_and_bottom(row)
    table_top, _ = top_and_bottom(table)
    _, header_bottom = top_and_bottom(header)

    assert row_top == table_top
    assert row_bottom == header_bottom


def test_narrow_plot_moves_buttons_into_the_menu_not_the_table_aside(qapp, window):
    splitter = window.center_splitter
    total = sum(splitter.sizes())

    splitter.setSizes([total - 300, 300])
    qapp.processEvents()

    # The table gets the space it was given; the buttons give way instead.
    assert splitter.sizes()[1] <= 300
    assert window.plot_button_row.overflowed_buttons()
    assert window.plot_button_row.more_button.isVisible()
