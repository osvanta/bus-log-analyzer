# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A menu whose rows carry a waste bin after their text (gui.bin_menu), as Go to
marker and Views ▾ have: the menu makes room for the bins whatever its style
leaves after the text, and Delete deletes the highlighted row's.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

_TIGHT = 'QMenu::item { padding: 2px 0px; }'      # no room after the text


def test_the_menu_makes_room_for_a_bin_after_the_longest_text(qapp):
    from gui.bin_menu import BinMenu
    menu = BinMenu()
    menu.setStyleSheet(_TIGHT)
    menu.addAction('Short')
    longest = menu.addAction('A rather longer name')
    menu.add_bin(longest, lambda: None, 'Delete it')
    need = (menu.text_left() + menu.fontMetrics().horizontalAdvance(longest.text())
            + menu.BIN_GAP + menu.BIN_AREA + menu.BIN_RIGHT)
    assert menu.sizeHint().width() >= need
    plain = BinMenu()
    plain.setStyleSheet(_TIGHT)
    plain.addAction('Short')
    plain.addAction('A rather longer name')
    assert plain.sizeHint().width() < need                 # the style left none


def test_the_bins_line_up_in_a_column_at_the_right_clear_of_the_names(qapp):
    # Not each right after its own name (feedback item 12): one column,
    # toward the menu's right end, a clear gap past the longest name.
    from gui.bin_menu import BinMenu
    menu = BinMenu()
    rows = [menu.addAction(name) for name in ('XY plot', 'Histogram', 'XY plot 3')]
    for row in rows:
        menu.add_bin(row, lambda: None, f'Delete {row.text()}')
    menu.addAction('Add XY plot')
    menu.ensurePolished()
    menu.adjustSize()
    menu.show()
    qapp.processEvents()
    areas = [menu.bin_area(row) for row in rows]
    assert len({area.left() for area in areas}) == 1                    # one column
    first = menu.actionGeometry(rows[0])
    assert areas[0].right() == first.right() - menu.BIN_RIGHT            # at the right
    longest = max(menu.text_left() + menu.fontMetrics().horizontalAdvance(row.text())
                  for row in rows)
    assert areas[0].left() - (first.left() + longest) >= menu.BIN_GAP    # clear gap
    menu.hide()


def test_delete_deletes_the_highlighted_rows_thing(qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    from gui.bin_menu import BinMenu
    deleted = []
    menu = BinMenu()
    first = menu.addAction('First')
    second = menu.addAction('Second')
    menu.add_bin(first, lambda: deleted.append('first'), 'Delete First')
    menu.add_bin(second, lambda: deleted.append('second'), 'Delete Second')
    menu.setActiveAction(second)
    QApplication.sendEvent(menu, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                                           Qt.KeyboardModifier.NoModifier))
    assert deleted == ['second'] and menu.bin_rows() == [first]
    assert second not in menu.actions()
