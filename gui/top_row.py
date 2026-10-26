# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The top row's own controls: the File menu and the button of the main step,
Decode.

The row reads left to right in the order of the work. The File menu holds what
is done now and then: a configuration opened or saved, plotted signals
exported. Then come the steps of every session, one click each: Open
Measurement, Databases and Decode. Then what is done with decoded signals,
Calculated Signal and Trace, and Help last, where a menu bar has it.

Decode, which every session turns on, stands out by its icon and bold text,
both in the theme's text colour like the rest of the row, the icon right
beside the word.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QMenu,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QWidget,
)

MAIN_ICON_SIZE = QSize(18, 18)   # as the plot buttons' icons


class FileMenu(QMenu):
    """A configuration opened or saved, signals exported, and what the open
    measurement is."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__('File', parent)
        self.open_configuration_action = self.addAction('Open configuration')
        self.save_configuration_action = self.addAction('Save configuration')
        self.save_configuration_action.setShortcut(QKeySequence('Ctrl+S'))
        self.addSeparator()
        self.export_action = self.addAction('Export plotted signals')
        self.export_action.setShortcut(QKeySequence('Ctrl+E'))
        self.save_decoded_action = self.addAction('Save decoded measurement as MF4')
        self.addSeparator()
        self.info_action = self.addAction('Measurement info')
        self.info_action.setShortcut(QKeySequence('Ctrl+I'))
        # The style leaves a single space between the longest item and its
        # shortcut; a wider menu moves the shortcuts clear of the text.
        self.setMinimumWidth(self.sizeHint().width() + 3 * self.fontMetrics().horizontalAdvance('M'))


class ToolsMenu(QMenu):
    """Jobs on measurement files that need not be open."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__('Tools', parent)
        self.convert_action = self.addAction('Convert files')


class MainStepButton(QToolButton):
    """The main step's button: its icon right beside its bold text.

    Windows 11 leaves some nine pixels between a tool button's icon and its
    text, which set the icon apart from the word. This button draws the two
    itself, next to each other, over the style's own button background, so
    hovering and pressing look as on the other buttons of the row.
    """

    # Around the icon and the text, inside the button.
    PADDING = 6

    def __init__(self, action: QAction, beside: QToolButton,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDefaultAction(action)
        self.setAutoRaise(True)
        self.setIconSize(MAIN_ICON_SIZE)
        # Only the weight is set: the rest of the font still follows the row's.
        bold = QFont()
        bold.setBold(True)
        self.setFont(bold)
        # As tall as beside, a plain text button of the same row.
        self._height = max(beside.sizeHint().height(), MAIN_ICON_SIZE.height() + 2)

    def sizeHint(self) -> QSize:
        icon, text = self.content_rects()
        return QSize(icon.width() + text.width() + 2 * self.PADDING, self._height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def content_rects(self) -> tuple[QRect, QRect]:
        """Where the icon and the text are drawn: side by side, centred.

        The drawing's own margin and the first letter's keep them a few
        pixels apart.
        """
        text_width = self.fontMetrics().horizontalAdvance(self.text()) + 1
        left = max(0, (self.width() - MAIN_ICON_SIZE.width() - text_width) // 2)
        icon = QRect(left, (self.height() - MAIN_ICON_SIZE.height()) // 2,
                     MAIN_ICON_SIZE.width(), MAIN_ICON_SIZE.height())
        return icon, QRect(icon.right() + 1, 0, text_width, self.height())

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        # The style draws the background alone; the icon and text follow.
        option.text = ''
        option.icon = QIcon()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        icon_rect, text_rect = self.content_rects()
        enabled = self.isEnabled()
        self.icon().paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter,
                          QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled)
        group = QPalette.ColorGroup.Active if enabled else QPalette.ColorGroup.Disabled
        painter.setPen(self.palette().color(group, QPalette.ColorRole.ButtonText))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.text())
