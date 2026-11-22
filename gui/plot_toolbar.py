# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Parts of the plot toolbar: a button that opens a menu, and the plot layouts
by name.

The toolbar keeps one kind of control in one place: the layout in one
picker, the view and measuring buttons a click away, the display choices
that are set once in one menu, and the views that open beside the plot
(XY, Table, CAN Trace) in the Views menu, as panels of the tab
(gui.panel_area).
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

#: Each plot layout as a configuration names it, its name on the picker,
#: and what it is.
LAYOUTS = (
    ('normal', 'Single plot', 'Every signal in one plot, on one axis'),
    ('multi_axis', 'Multi-Axis', 'Every signal in one plot, each on its own axis'),
    ('stacked', 'Stacked', 'A row per signal'),
    ('multistack', 'MultiStack', 'Rows you group: drag signals into a stack to overlay them'),
)
LAYOUT_NAMES = {mode: name for mode, name, _tip in LAYOUTS}
#: After the text of a button that opens a menu.
MENU_MARK = ' ▾'


class MenuButton(QPushButton):
    """A plot button that opens a menu, filled anew at each click so that
    its checks and greyed entries are current."""

    def __init__(self, fill: Callable[[QMenu], None], parent: QWidget | None = None,
                 menu_type: type[QMenu] = QMenu) -> None:
        super().__init__(parent)
        self._fill = fill
        self._menu_type = menu_type
        self.clicked.connect(self.open_menu)

    def menu(self) -> QMenu:
        """The menu as a click would open it."""
        menu = self._menu_type(self)
        menu.setToolTipsVisible(True)
        self._fill(menu)
        return menu

    def open_menu(self) -> None:
        """Under the button, or at the pointer when it is in the "»" menu."""
        at = (self.mapToGlobal(QPoint(0, self.height())) if self.isVisible()
              else QCursor.pos())
        menu = self.menu()
        menu.exec(at)
        menu.deleteLater()
