# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A row of buttons that moves the ones that do not fit into a "»" menu.

QToolBar has an overflow menu of its own, but it only opens when the toolbar is
docked in a QMainWindow; the plot buttons sit inside the central panel, beside
the signal table. Without an overflow the row's full width would become the
plot column's minimum width and hold the table narrow.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QMenu,
    QToolButton,
    QWidget,
)


class OverflowButtonRow(QWidget):
    """Buttons laid out left to right; those past the right edge move into a menu.

    The row owns its buttons' visibility: a button is hidden exactly when it
    is in the menu.
    """

    SPACING = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QAbstractButton] = []
        self._overflowed: set[QAbstractButton] = set()
        self._gaps = 0   # width of the space added by add_gap
        self._end_buttons: list[QAbstractButton] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(self.SPACING)
        self._layout.addStretch(1)
        self.more_button = QToolButton(self)
        self.more_button.setText('»')
        self.more_button.setToolTip('More plot buttons')
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setStyleSheet('QToolButton::menu-indicator { image: none; }')
        self.more_menu = QMenu(self.more_button)
        self.more_menu.aboutToShow.connect(self._fill_menu)
        self.more_button.setMenu(self.more_menu)
        self.more_button.hide()
        self._layout.addWidget(self.more_button)

    def add_button(self, button: QAbstractButton) -> None:
        # Before the closing stretch and the menu button.
        self._layout.insertWidget(self._layout.count() - 2, button)
        self._buttons.append(button)
        # Square, and as tall as the buttons it stands in for.
        side = max(self.more_button.minimumHeight(), button.sizeHint().height())
        self.more_button.setMinimumSize(side, side)

    def add_gap(self, width: int) -> None:
        """Leave width pixels more than the usual spacing before the next button."""
        self._layout.insertSpacing(self._layout.count() - 2, width)
        self._gaps += width

    def add_end_button(self, button: QAbstractButton) -> None:
        """Put a button at the right end of the row, where it always stays:
        it never moves into the menu, which opens left of it."""
        self._layout.addWidget(button)
        self._end_buttons.append(button)

    def overflowed_buttons(self) -> list[QAbstractButton]:
        """The buttons currently in the menu, in row order."""
        return [b for b in self._buttons if b in self._overflowed]

    def minimumSizeHint(self) -> QSize:
        # Narrow enough for the overflow button and the end buttons alone, so
        # the row never holds its parent wider than the space it is given.
        return QSize(self._more_width() + self._end_width(),
                     super().minimumSizeHint().height())

    def _end_width(self) -> int:
        # An end button hidden on purpose takes no room.
        return sum(b.sizeHint().width() + self.SPACING
                   for b in self._end_buttons if b.isVisibleTo(self))

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        # LayoutRequest also arrives when a button's text changes its width,
        # e.g. "Cursor 1" becoming "Cursor 1: ON".
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            self._fit()
        return handled

    def _fit(self) -> None:
        widths = [b.sizeHint().width() for b in self._buttons]
        # A gap keeps its place on the row, whichever buttons are shown, and
        # the end buttons theirs.
        available = self.contentsRect().width() - self._gaps - self._end_width()
        if sum(widths) + self.SPACING * (len(widths) - 1) > available:
            available -= self._more_width() + self.SPACING
        fits = 0
        used = -self.SPACING
        for width in widths:
            used += self.SPACING + width
            if used > available:
                break
            fits += 1
        for index, button in enumerate(self._buttons):
            overflowed = index >= fits
            if overflowed != (button in self._overflowed):
                if overflowed:
                    self._overflowed.add(button)
                else:
                    self._overflowed.discard(button)
                button.setVisible(not overflowed)
        self.more_button.setVisible(bool(self._overflowed))

    def _more_width(self) -> int:
        return self.more_button.sizeHint().expandedTo(self.more_button.minimumSize()).width()

    def _fill_menu(self) -> None:
        # Rebuilt on every open so checked and enabled states are current.
        self.more_menu.clear()
        for button in self.overflowed_buttons():
            # An icon-only button is named by its accessible name.
            action = self.more_menu.addAction(button.text() or button.accessibleName())
            if not button.icon().isNull():
                # Its unchecked look even when checked: a checked button's icon
                # may be drawn for the accent fill behind it, which a menu
                # entry does not have.
                action.setIcon(QIcon(button.icon().pixmap(
                    button.iconSize(), self.devicePixelRatioF(),
                    QIcon.Mode.Normal, QIcon.State.Off)))
            action.setCheckable(button.isCheckable())
            action.setChecked(button.isChecked())
            action.setEnabled(button.isEnabled())
            action.triggered.connect(lambda _checked=False, b=button: b.click())
