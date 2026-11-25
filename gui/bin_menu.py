# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A menu whose rows can carry a waste bin, in one column toward the menu's right
side, clear of the longest text: a click on the bin deletes what the row
stands for, a marker or a view, and takes the row out. The menu stays open on
the rest, so several go in a row of clicks; Delete deletes the highlighted
row's. The bin turns red under the pointer, and its tooltip names what it
deletes.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QMenu, QStyle, QStyleOptionMenuItem, QToolTip, QWidget

from gui.plot_icons import drawing_pixmap


class BinMenu(QMenu):
    """A menu with a waste bin, in a column at its right, on the rows given
    add_bin()."""

    BIN = 16                  # the bin's side, in pixels
    BIN_AREA = 24             # its click target's width; the row's height high
    BIN_GAP = 24              # at least, from the longest text to the bins' column
    BIN_RIGHT = 6             # from the bins' column to the row's right end
    BIN_HOT = '#c62828'       # behind the bin under the pointer, drawn white
    #: Where a row's text starts, from the row's left; None: as the style
    #: draws it, measured once.
    TEXT_LEFT: int | None = None

    def __init__(self, title: str = '', parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._bins: dict[QAction, tuple[Callable[[], None], str]] = {}
        self._hot: QAction | None = None        # the row whose bin is under the pointer
        self._pixmaps: dict[tuple[int, float], QPixmap] = {}
        self._text_left: int | None = self.TEXT_LEFT
        self.setMouseTracking(True)

    # ── Rows with a bin ──────────────────────────────────────────────────

    def add_bin(self, action: QAction, delete: Callable[[], None], tip: str) -> None:
        """Give action's row a bin that calls delete; tip names what it deletes."""
        self._bins[action] = (delete, tip)
        if self.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
            self._make_room()

    def _make_room(self) -> None:
        # A menu's rows are as wide as their texts, or as its least width
        # allows: that width keeps room past the longest text for the gap and
        # the bins' column, so each bin lies inside its row, under its
        # highlight. Set before the rows are laid out: at Polish, as the menu
        # first opens, or as a bin is added.
        if not self._bins:
            return
        style = self.style()
        frame = 2 * (style.pixelMetric(QStyle.PixelMetric.PM_MenuPanelWidth, None, self)
                     + style.pixelMetric(QStyle.PixelMetric.PM_MenuHMargin, None, self))
        margins = self.contentsMargins()
        need = (max(self._text_end(action) for action in self._bins)
                + self.BIN_GAP + self.BIN_AREA + self.BIN_RIGHT)
        least = need + frame + margins.left() + margins.right() + 2
        if self.minimumWidth() < least:
            self.setMinimumWidth(least)

    def bin_rows(self) -> list[QAction]:
        return list(self._bins)

    def text_left(self) -> int:
        """Where the style starts a row's text, from the row's left."""
        if self._text_left is None:
            self._text_left = self._measure_text_left()
        return self._text_left

    def _measure_text_left(self) -> int:
        # A row drawn by the style alone, with no check, icon or key: its
        # first drawn column is where its text starts. The column for checks
        # is kept all the same while the menu has rows that can be checked.
        actions = [action for action in self.actions() if not action.isSeparator()]
        if not actions:
            return 12
        option = QStyleOptionMenuItem()
        self.initStyleOption(option, actions[0])
        row = self.actionGeometry(actions[0])
        option.rect = QRect(0, 0, max(row.width(), 60), max(row.height(), 10))
        option.text = 'I'
        option.checkType = QStyleOptionMenuItem.CheckType.NotCheckable
        option.checked = False
        option.icon = QIcon()
        option.state &= ~QStyle.StateFlag.State_Selected
        image = QImage(option.rect.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self.style().drawControl(QStyle.ControlElement.CE_MenuItem, option, painter, self)
        painter.end()
        for x in range(image.width()):
            if any(image.pixelColor(x, y).alpha() > 0 for y in range(image.height())):
                return x
        return 12

    def _text_end(self, action: QAction) -> int:
        return self.text_left() + self.fontMetrics().horizontalAdvance(action.text())

    def bin_area(self, action: QAction) -> QRect:
        """Action's bin and its click target: in the bins' column at the
        row's right end, so every row's bin lies at the same place."""
        row = self.actionGeometry(action)
        return QRect(row.right() + 1 - self.BIN_RIGHT - self.BIN_AREA, row.top(),
                     self.BIN_AREA, row.height())

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if not self._bins:
            return hint
        # Room past the longest text for the gap and the bins' column.
        need = max(self._text_end(action) for action in self._bins) \
            + self.BIN_GAP + self.BIN_AREA + self.BIN_RIGHT
        return QSize(max(hint.width(), need), hint.height())

    def delete_row(self, action: QAction) -> None:
        """Delete what action's row stands for and take its row out; the menu
        stays open on the rest."""
        entry = self._bins.pop(action, None)
        if entry is None:
            return
        entry[0]()
        self.removeAction(action)
        action.deleteLater()
        self._hot = None
        if self._after_delete():
            # The next row moves up under the pointer: its bin is the one now.
            self._set_hot(self._bin_row_at(self.mapFromGlobal(QCursor.pos())))

    def _after_delete(self) -> bool:
        """After a row went: False when the menu closed."""
        return True

    # ── Under the pointer ────────────────────────────────────────────────

    def _bin_row_at(self, pos) -> QAction | None:
        action = self.actionAt(pos)
        if action in self._bins and self.bin_area(action).contains(pos):
            return action
        return None

    def _set_hot(self, action: QAction | None) -> None:
        if action is not self._hot:
            self._hot = action
            self.update()

    def _bin_pixmap(self, color: QColor) -> QPixmap:
        scale = self.devicePixelRatioF()
        key = (color.rgba(), scale)
        if key not in self._pixmaps:
            self._pixmaps[key] = drawing_pixmap('clear_plot', self.BIN, color, scale)
        return self._pixmaps[key]

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._bins:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for action in self._bins:
            area = self.bin_area(action)
            if action is self._hot:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(self.BIN_HOT))
                square = min(area.width(), area.height()) - 4
                hot = QRectF(0, 0, square, square)
                hot.moveCenter(QRectF(area).center())
                painter.drawRoundedRect(hot, 4, 4)
                color = QColor(Qt.GlobalColor.white)
            else:
                color = self.palette().color(QPalette.ColorRole.Text)
            painter.drawPixmap(area.left() + (area.width() - self.BIN) // 2,
                               area.top() + (area.height() - self.BIN) // 2,
                               self._bin_pixmap(color))
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        self._set_hot(self._bin_row_at(event.position().toPoint()))

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._set_hot(None)

    def mouseReleaseEvent(self, event) -> None:
        action = self._bin_row_at(event.position().toPoint())
        if action is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        event.accept()
        self.delete_row(action)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete and self.activeAction() in self._bins:
            event.accept()
            self.delete_row(self.activeAction())
            return
        super().keyPressEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.Polish:
            # Its font and style are final now: measure again.
            handled = super().event(event)
            self._text_left = self.TEXT_LEFT
            self._make_room()
            return handled
        if event.type() == QEvent.Type.ToolTip:
            action = self._bin_row_at(event.pos())
            if action is not None:
                QToolTip.showText(event.globalPos(), self._bins[action][1], self)
                return True
            if self._bins and self.actionAt(event.pos()) in self._bins:
                QToolTip.hideText()
                return True
        return super().event(event)
