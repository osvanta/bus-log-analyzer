# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The plot tabs' drawing, for everything in the window drawn as a plot tab.

The selected tab is outlined, with round top corners and feet that curve out
into a thin line along the foot of its strip, so the tab plainly owns what
lies below it. Another tab is plain, and lit under the mouse. A tab's close
button is a small "x" raised beside its name, like a superscript, so a click
on the name does not close the tab by accident.

The plot tabs (gui.plot_tabs) and the panels inside a tab (gui.panel_area)
both draw their tabs with this, so every tab in the window looks alike.
"""
from __future__ import annotations

from math import ceil

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QLineEdit,
    QProxyStyle,
    QStyle,
    QTabBar,
    QWidget,
)

from gui.edge_tab import _mix

# The outline of the tab, or of "+", that a drag would drop on.
DROP_TARGET_COLOR = '#3d9ef0'

# The shape of a tab, in pixels. Each foot curves out over TAB_FLARE at either
# side of the tab, inside the room the tab takes in the strip.
TAB_HEIGHT = 28
TAB_FLARE = 7
TAB_RADIUS = 10
TAB_PADDING = 12           # between a tab's side and its name
CLOSE_SIZE = 12            # the "x", raised beside the name
CLOSE_MARGIN = 6           # between the "x" and the tab's side
STRIP_INDENT = 8           # the line along the strip, before the first tab
# How far each colour lies from the window colour toward its text colour.
_OUTLINE = 0.36            # the selected tab's outline, and the line along the strip
_HOVER = 0.08              # another tab under the mouse
_NAME = 0.62               # another tab's name
_CLOSE = 0.5               # another tab's "x"


class _DropOutline(QFrame):
    """Outlines the place a drag would drop on."""

    def __init__(self, parent: QWidget, radius: int = 3) -> None:
        super().__init__(parent)
        self.setObjectName('dropOutline')
        self.setStyleSheet(
            f'#dropOutline {{ border: 2px solid {DROP_TARGET_COLOR}; '
            f'border-radius: {radius}px; }}')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def show_at(self, rect) -> None:
        self.setGeometry(rect)
        self.show()
        self.raise_()


class TabBar(QTabBar):
    """Tabs drawn by _TabStyle, each with a small "x" while they can close."""

    def __init__(self, close_tip: str = 'Close tab') -> None:
        super().__init__()
        # Kept here: a widget does not own the style it is given.
        self._style = _TabStyle()
        self.setStyle(self._style)
        # The strip draws the line the tabs stand on.
        self.setDrawBase(False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._closable = False
        self._close_tip = close_tip

    def tabSizeHint(self, index: int) -> QSize:
        # The name as drawn: on a scaled screen it is a fraction of a pixel
        # wider than fontMetrics() says, and with no room for that fraction
        # Qt shortens it with "…" ("Drive" became "Dri…" at 225 %).
        name = ceil(QFontMetricsF(self.font()).horizontalAdvance(self.tabText(index)))
        width = 2 * TAB_FLARE + TAB_PADDING + name
        if self.tabButton(index, QTabBar.ButtonPosition.RightSide) is not None:
            width += 1 + CLOSE_SIZE + CLOSE_MARGIN
        else:
            width += TAB_PADDING
        return QSize(width, TAB_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        # Qt keeps room for its scroll arrows, 127 px, even beside one short
        # tab, which left "+" 75 px away from it. No wider than its tabs, the
        # bar keeps "+" beside the last tab; once the tabs are wider than
        # Qt's minimum, they scroll as before.
        size = super().minimumSizeHint()
        size.setWidth(min(size.width(), self.sizeHint().width()))
        return size

    def setTabsClosable(self, closable: bool) -> None:
        """Give every tab its small "x", or take them away. QTabBar's own
        close buttons are left out: they are as large as the name."""
        self._closable = bool(closable)
        for index in range(self.count()):
            button = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if self._closable and button is None:
                button = _CloseButton(self, self._close_tip)
                button.clicked.connect(lambda _checked=False, b=button: self._close(b))
                self.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)
            elif not self._closable and button is not None:
                self.setTabButton(index, QTabBar.ButtonPosition.RightSide, None)
                button.deleteLater()

    def tabsClosable(self) -> bool:
        return self._closable

    def _close(self, button: QAbstractButton) -> None:
        for index in range(self.count()):
            if self.tabButton(index, QTabBar.ButtonPosition.RightSide) is button:
                self.tabCloseRequested.emit(index)
                return

    def tab_body(self, index: int) -> QRect:
        """The tab itself, inside its feet."""
        return self.tabRect(index).adjusted(TAB_FLARE, 2, -TAB_FLARE, 0)


def _tab_outline(left: float, right: float, top: float, base: float) -> QPainterPath:
    """A tab's outline: up from the line on a curving foot, round the top
    corners, and down on to the line again."""
    path = QPainterPath(QPointF(left - TAB_FLARE, base))
    path.quadTo(left, base, left, base - TAB_FLARE)
    path.lineTo(left, top + TAB_RADIUS)
    path.quadTo(left, top, left + TAB_RADIUS, top)
    path.lineTo(right - TAB_RADIUS, top)
    path.quadTo(right, top, right, top + TAB_RADIUS)
    path.lineTo(right, base - TAB_FLARE)
    path.quadTo(right, base, right + TAB_FLARE, base)
    return path


class _TabStyle(QProxyStyle):
    """Draws the tabs. The selected one is outlined and filled with the
    window's colour, which covers the strip's line under it; another tab is
    plain, and lit under the mouse. Places each tab's "x" high beside its name.

    A tab dragged to another place is drawn here too, so it keeps its look.
    """

    def drawControl(self, element, option, painter, widget=None) -> None:
        if element == QStyle.ControlElement.CE_TabBarTab:
            self._draw_tab(option, painter)
            return
        super().drawControl(element, option, painter, widget)

    def subElementRect(self, element, option, widget=None) -> QRect:
        if element == QStyle.SubElement.SE_TabBarTabText:
            return self._name_rect(option)
        if element == QStyle.SubElement.SE_TabBarTabRightButton:
            rect = option.rect
            return QRect(rect.right() + 1 - TAB_FLARE - CLOSE_MARGIN - CLOSE_SIZE,
                         rect.top() + 5, CLOSE_SIZE, CLOSE_SIZE)
        return super().subElementRect(element, option, widget)

    def pixelMetric(self, metric, option=None, widget=None) -> int:
        # The selected tab stays level with the others.
        if metric in (QStyle.PixelMetric.PM_TabBarTabShiftVertical,
                      QStyle.PixelMetric.PM_TabBarTabShiftHorizontal):
            return 0
        return super().pixelMetric(metric, option, widget)

    @staticmethod
    def _name_rect(option) -> QRect:
        rect = option.rect
        left = rect.left() + TAB_FLARE + TAB_PADDING
        if option.rightButtonSize.isEmpty():
            right = rect.right() + 1 - TAB_FLARE - TAB_PADDING
        else:
            right = rect.right() - TAB_FLARE - CLOSE_MARGIN - CLOSE_SIZE
        return QRect(left, rect.top() + 3, max(0, right - left), rect.height() - 3)

    def _draw_tab(self, option, painter: QPainter) -> None:
        window = option.palette.color(QPalette.ColorRole.Window)
        text = option.palette.color(QPalette.ColorRole.WindowText)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect
        # Pixel centres, so a one-pixel line is sharp.
        left = rect.left() + TAB_FLARE + 0.5
        right = rect.right() + 0.5 - TAB_FLARE
        top = rect.top() + 2.5
        base = rect.bottom() + 0.5
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            outline = _tab_outline(left, right, top, base)
            inside = QPainterPath(outline)
            inside.lineTo(right + TAB_FLARE, base + 0.5)
            inside.lineTo(left - TAB_FLARE, base + 0.5)
            inside.closeSubpath()
            painter.fillPath(inside, window)
            painter.strokePath(outline, QPen(_mix(window, text, _OUTLINE), 1))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            lit = QPainterPath()
            lit.addRoundedRect(QRectF(left, top, right - left, base - top - 3),
                               TAB_RADIUS, TAB_RADIUS)
            painter.fillPath(lit, _mix(window, text, _HOVER))
        painter.setPen(text if selected else _mix(window, text, _NAME))
        # A "&" in a name is shown as it is, not as a shortcut.
        painter.drawText(self._name_rect(option),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                         | Qt.TextFlag.TextSingleLine, option.text)
        painter.restore()


class _TabStrip(QWidget):
    """The row of tabs, with a thin line along its foot, from one side of
    what the tabs switch to the other. The selected tab's feet run into it."""

    def paintEvent(self, event) -> None:
        palette = self.palette()
        color = _mix(palette.color(QPalette.ColorRole.Window),
                     palette.color(QPalette.ColorRole.WindowText), _OUTLINE)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 1))
        y = self.height() - 0.5
        painter.drawLine(QPointF(0, y), QPointF(self.width(), y))


class _CloseButton(QAbstractButton):
    """A tab's "x": small, round under the mouse, and raised beside the name
    like a superscript."""

    def __init__(self, bar: QTabBar, tip: str = 'Close tab') -> None:
        super().__init__(bar)
        self.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self) -> QSize:
        return QSize(CLOSE_SIZE, CLOSE_SIZE)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def _on_selected_tab(self) -> bool:
        bar = self.parentWidget()
        return (isinstance(bar, QTabBar)
                and bar.tabButton(bar.currentIndex(), QTabBar.ButtonPosition.RightSide) is self)

    def paintEvent(self, event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        hovered = self.underMouse() or self.isDown()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect())
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_mix(window, text, 0.28 if self.isDown() else 0.16))
            painter.drawEllipse(area.adjusted(0.5, 0.5, -0.5, -0.5))
        color = QColor(text) if hovered or self._on_selected_tab() else _mix(window, text, _CLOSE)
        pen = QPen(color, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        middle, arm = area.center(), 2.5
        painter.drawLine(QPointF(middle.x() - arm, middle.y() - arm),
                         QPointF(middle.x() + arm, middle.y() + arm))
        painter.drawLine(QPointF(middle.x() - arm, middle.y() + arm),
                         QPointF(middle.x() + arm, middle.y() - arm))


class _NameEditor(QLineEdit):
    """A name being edited, in place of the name shown."""

    # True when the name typed is to be kept.
    done = Signal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._ended = False

    def _end(self, keep: bool) -> None:
        if not self._ended:
            self._ended = True
            self.done.emit(keep)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._end(True)
        elif event.key() == Qt.Key.Key_Escape:
            self._end(False)
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._end(True)
