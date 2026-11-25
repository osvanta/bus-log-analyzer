# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Panels side by side in one plot tab.

A plot tab shows its time plot and, beside or below it, the views opened from
Views ▾: the XY plot, the Table and the CAN Trace. Each of them is a panel,
the time plot too. Splitters tile the panels, and panels put in one place
share it as tabs. A panel can fill the tab (maximise) and give the room back
(restore), close and open again where it was, and move into a window of its
own, on a second monitor say, and dock back where it came from.

Each place is drawn as a card: a thin rounded outline in the plot tabs'
colour, a little room inside it, and an even gap between one card and the
next, where the splitters are dragged. A panel's own header row is a
HeaderBand across the top of its card. Card draws the same card round a
widget beside the panels: the signal table, level with the time plot's card.

A panel's title is drawn as the plot tabs are (gui.tab_drawing). A title row
costs room, as a window's caption would, so a panel alone in its place shows
its title only while the pointer rests on the panel's top edge, where a short
mark shows that it can; the title folds away once the pointer leaves.
set_titles_shown() keeps every title in view instead. Panels sharing a place
always show their tabs, which are how you switch between them, and a panel
alone in the whole tab shows no title at all, so a tab with only its time
plot looks as it always did. A panel may hand over a label of its own row
for its name, shown while its title is not.

describe() gives the arrangement as a configuration keeps it, and
restore_layout() puts it back. Sizes are kept as fractions of the room, so an
arrangement made on a large screen fits a small one.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.edge_tab import _mix
from gui.tab_drawing import _OUTLINE, STRIP_INDENT, TAB_HEIGHT, TabBar, _NameEditor, _TabStrip

SIDES = ('left', 'right', 'top', 'bottom')

#: How long the pointer rests on a panel's top edge before its title shows,
#: and how long after it leaves before the title folds: as the rail did.
PEEK_DELAY = 0.12
FOLD_DELAY = 0.35
#: How long a title clicked out by its mark, or shown by the keyboard, stays.
HOLD_DELAY = 2.5
#: The top edge: this far above a panel and this far into it.
EDGE_ABOVE = 8
EDGE_BELOW = 4
_POLL_MS = 60
_TOLERANCE = 1e-6            # seconds, against rounding in the times compared
#: The mark on a panel's top edge that says a title can be shown there.
GRIP_WIDTH = 36
GRIP_HEIGHT = 3
#: A title's buttons: their size, the room between them and after the last.
STRIP_BUTTON_SIZE = 20
_STRIP_SPACING = 1
_STRIP_RIGHT = 2
#: The room a title's maximise, undock and close take at its right end. A
#: panel's gear goes just left of them, in its title and in its own row alike.
TITLE_BUTTONS_ROOM = 3 * STRIP_BUTTON_SIZE + 3 * _STRIP_SPACING + _STRIP_RIGHT
#: A place's card: its outline's rounding, how far inside its edge the panel
#: starts (the 1-px outline and the room inside it), and the gap between
#: two cards, where the splitter between them is dragged.
CARD_RADIUS = 6
CARD_INSET = 3
GUTTER = 6
#: The grip mark's colour, quieter than the outline.
_GRIP = _OUTLINE * 0.6
#: A header band's shade off the card, and its hairline's.
_BAND = 0.05
_BAND_LINE = _OUTLINE * 0.5

# How a place shows its title.
ROW = 'row'          # a title row above the panel, always
OVERLAY = 'overlay'  # over the panel's top, while the pointer is at its edge
NONE = 'none'        # none: the panel is alone in the tab


@dataclass
class _Panel:
    key: str
    title: str
    widget: QWidget
    closable: bool
    floatable: bool
    # Where the panel goes when opened again: {'with': key}, {'beside': key,
    # 'side': side, 'share': fraction}, or {'edge': side, 'share': fraction}.
    home: dict = field(default_factory=dict)
    name_label: QLabel | None = None
    window: PanelWindow | None = None
    # The panel's own window as it was last left, for the next time.
    window_rect: QRect | None = None
    # Renames the panel: from its menu, or a double-click on its name.
    rename: Callable[[], None] | None = None
    # Opens the panel's settings under the button given: its title's gear.
    settings: Callable[[QWidget], None] | None = None
    # The panel's own gear, in its own row: shown while its title is not.
    settings_button: QWidget | None = None
    # Deletes the panel for good (a view the user added): from its menu.
    delete: Callable[[], None] | None = None


def _horizontal(side: str) -> bool:
    return side in ('left', 'right')


def _orientation(side: str) -> Qt.Orientation:
    return Qt.Orientation.Horizontal if _horizontal(side) else Qt.Orientation.Vertical


def _first(side: str) -> bool:
    return side in ('left', 'top')


class _Gutter(QSplitterHandle):
    """The gap between two cards: nothing drawn, dragged to resize them."""

    def paintEvent(self, event) -> None:
        pass


class _Splitter(QSplitter):
    def createHandle(self) -> QSplitterHandle:
        return _Gutter(self.orientation(), self)


def _new_splitter(orientation: Qt.Orientation) -> QSplitter:
    splitter = _Splitter(orientation)
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(GUTTER)
    return splitter


def gutter_splitter(orientation: Qt.Orientation) -> QSplitter:
    """A splitter whose handle is the gap between two cards, as between the
    panels: GUTTER wide, drawn as nothing, dragged to resize them. The
    signal table and the plots beside it are split by one."""
    return _new_splitter(orientation)


class HeaderBand(QWidget):
    """A panel's header: a band across the top of its card, at least as high
    as a title, a shade off the card, with a hairline under it. Its row holds
    the panel's name, a dim reading and the panel's controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(TAB_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        painter = QPainter(self)
        painter.fillRect(self.rect(), _mix(window, text, _BAND))
        painter.fillRect(0, self.height() - 1, self.width(), 1, _mix(window, text, _BAND_LINE))
        painter.end()


def _fractions(sizes: list[int]) -> list[float]:
    total = sum(sizes)
    if total <= 0:
        return [round(1 / len(sizes), 4)] * len(sizes) if sizes else []
    return [round(size / total, 4) for size in sizes]


def _weights(fractions: list[float]) -> list[int]:
    """Splitter sizes from fractions. setSizes() keeps their proportions, so
    they need not add up to the splitter's size, which a hidden one lacks."""
    return [max(1, int(round(fraction * 10000))) for fraction in fractions]


class _StripButton(QAbstractButton):
    """A small button at the right end of a title: maximise, restore, own
    window or close. Drawn in the title's colours, round under the mouse,
    like a tab's "x"."""

    SIZE = STRIP_BUTTON_SIZE
    TIPS = {'settings': 'Settings',
            'maximise': 'Maximise: fill the tab (Ctrl+Shift+Enter)',
            'restore': 'Restore: give the room back (Ctrl+Shift+Enter)',
            'undock': 'Undock: move this panel into a window of its own',
            'dock': 'Dock back: put this panel back in its place in the tab',
            'close': 'Close this panel (Ctrl+F4)'}

    def __init__(self, kind: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.setToolTip(self.TIPS[kind])
        self.setAccessibleName(self.TIPS[kind].split(':')[0].split(' (')[0])
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.SIZE, self.SIZE)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        hovered = self.underMouse() or self.isDown()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_mix(window, text, 0.28 if self.isDown() else 0.16))
            painter.drawRoundedRect(area, 4, 4)
        pen = QPen(QColor(text) if hovered else _mix(window, text, 0.62), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        c = area.center()
        if self.kind == 'settings':
            # A gear: a hub, a ring and eight teeth.
            painter.drawEllipse(c, 1.6, 1.6)
            painter.drawEllipse(c, 4.2, 4.2)
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            for step in range(8):
                angle = step * math.pi / 4
                dx, dy = math.cos(angle), math.sin(angle)
                painter.drawLine(QPointF(c.x() + 4.2 * dx, c.y() + 4.2 * dy),
                                 QPointF(c.x() + 6.4 * dx, c.y() + 6.4 * dy))
        elif self.kind == 'maximise':
            painter.drawRoundedRect(QRectF(c.x() - 4.5, c.y() - 4.5, 9, 9), 1, 1)
        elif self.kind == 'restore':
            painter.drawRoundedRect(QRectF(c.x() - 4.5, c.y() - 2.5, 7, 7), 1, 1)
            painter.drawPolyline([QPointF(c.x() - 2.5, c.y() - 2.5), QPointF(c.x() - 2.5, c.y() - 4.5),
                                  QPointF(c.x() + 4.5, c.y() - 4.5), QPointF(c.x() + 4.5, c.y() + 2.5),
                                  QPointF(c.x() + 2.5, c.y() + 2.5)])
        elif self.kind == 'undock':
            # A box with an arrow leaving it, up and right.
            painter.drawRoundedRect(QRectF(c.x() - 4.5, c.y() - 1.5, 6, 6), 1, 1)
            painter.drawPolyline([QPointF(c.x() - 1, c.y() - 4.5), QPointF(c.x() + 4.5, c.y() - 4.5),
                                  QPointF(c.x() + 4.5, c.y() + 1)])
            painter.drawLine(QPointF(c.x() + 4.5, c.y() - 4.5), QPointF(c.x(), c.y()))
        elif self.kind == 'dock':
            # The same box with the arrow coming back into it.
            painter.drawRoundedRect(QRectF(c.x() - 4.5, c.y() - 1.5, 6, 6), 1, 1)
            painter.drawLine(QPointF(c.x() + 4.5, c.y() - 4.5), QPointF(c.x() - 1, c.y() + 1))
            painter.drawPolyline([QPointF(c.x() - 1, c.y() - 3), QPointF(c.x() - 1, c.y() + 1),
                                  QPointF(c.x() + 3, c.y() + 1)])
        else:
            arm = 3.5
            painter.drawLine(QPointF(c.x() - arm, c.y() - arm), QPointF(c.x() + arm, c.y() + arm))
            painter.drawLine(QPointF(c.x() - arm, c.y() + arm), QPointF(c.x() + arm, c.y() - arm))


class _GroupStrip(_TabStrip):
    """A place's title: its tabs, drawn as the plot tabs are, and its
    buttons. Over the panel it is filled, so the panel does not show through."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.overlay = False
        self.setFixedHeight(TAB_HEIGHT)

    def paintEvent(self, event) -> None:
        if self.overlay:
            painter = QPainter(self)
            painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Window))
            painter.end()
        super().paintEvent(event)


class _Grip(QWidget):
    """The short mark on a panel's top edge: a title can be shown there.
    A click shows it for a while."""

    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        # Larger than the mark, so it is easy to hit.
        self.setFixedSize(GRIP_WIDTH + 16, GRIP_HEIGHT + 7)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Show this panel’s title')

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        palette = self.palette()
        color = _mix(palette.color(QPalette.ColorRole.Window),
                     palette.color(QPalette.ColorRole.WindowText), _GRIP)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF((self.width() - GRIP_WIDTH) / 2, 2, GRIP_WIDTH, GRIP_HEIGHT),
                                GRIP_HEIGHT / 2, GRIP_HEIGHT / 2)


def card_outline_color(widget: QWidget) -> QColor:
    """A card's outline colour in *widget*'s palette: the plot tabs' colour."""
    palette = widget.palette()
    return _mix(palette.color(QPalette.ColorRole.Window),
                palette.color(QPalette.ColorRole.WindowText), _OUTLINE)


def paint_card(widget: QWidget) -> None:
    """Draw *widget* as a card: a thin rounded outline along its edge."""
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(card_outline_color(widget), 1)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            CARD_RADIUS, CARD_RADIUS)
    painter.end()


class Card(QWidget):
    """A card as the panels' places are drawn, for a widget beside them: the
    same outline, and its content as far inside it. Its layout, content,
    takes the widgets."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(CARD_INSET, CARD_INSET, CARD_INSET, CARD_INSET)
        self.content.setSpacing(0)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        paint_card(self)


class PanelGroup(QWidget):
    """One place in the tab: one panel, or several shown one at a time
    under their tabs, drawn as a card."""

    def __init__(self, area: PanelArea, in_window: bool = False) -> None:
        super().__init__()
        self._area = area
        self.in_window = in_window
        self._keys: list[str] = []
        self._mode = NONE
        self.peeked = False

        self.strip = _GroupStrip(self)
        self.bar = TabBar(close_tip='Close panel')
        self.bar.setExpanding(False)
        self.bar.setUsesScrollButtons(True)
        self.bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bar.currentChanged.connect(self._show_index)
        self.bar.tabCloseRequested.connect(lambda index: self._area.close_panel(self._keys[index], user=True))
        self.bar.customContextMenuRequested.connect(self._bar_menu)
        self.bar.tabBarDoubleClicked.connect(
            lambda index: self._area.rename(self._keys[index]) if 0 <= index < len(self._keys)
            else None)
        self.settings_button = _StripButton('settings', self.strip)
        self.max_button = _StripButton('maximise', self.strip)
        # Undock in the tab, Dock back in the panel's own window: ✕ closes
        # in both, so moving a panel and closing it are never one button.
        self.window_button = _StripButton('dock' if in_window else 'undock', self.strip)
        self.close_button = _StripButton('close', self.strip)
        self.settings_button.clicked.connect(
            lambda: self._area.open_settings(self.current(), self.settings_button))
        self.max_button.clicked.connect(lambda: self._area.maximise(self.current()))
        self.window_button.clicked.connect(self._move)
        self.close_button.clicked.connect(lambda: self._area.close_panel(self.current(), user=True))
        row = QHBoxLayout(self.strip)
        row.setContentsMargins(STRIP_INDENT, 0, _STRIP_RIGHT, 0)
        row.setSpacing(_STRIP_SPACING)
        row.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        for button in (self.settings_button, self.max_button, self.window_button,
                       self.close_button):
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.strip.customContextMenuRequested.connect(
            lambda pos: self._area.show_panel_menu(self.current(), self.strip.mapToGlobal(pos)))

        self.stack = QStackedWidget()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(CARD_INSET, CARD_INSET, CARD_INSET, CARD_INSET)
        self._layout.setSpacing(0)
        self._layout.addWidget(self.stack)
        self.strip.hide()

        self.grip = _Grip(self)
        self.grip.clicked.connect(lambda: self._area.peek(self, hold=HOLD_DELAY))
        self.grip.hide()

    # ── Its panels ───────────────────────────────────────────────────────

    def keys(self) -> list[str]:
        return list(self._keys)

    def current(self) -> str | None:
        index = self.bar.currentIndex()
        return self._keys[index] if 0 <= index < len(self._keys) else None

    def add(self, key: str, widget: QWidget, title: str) -> None:
        self._keys.append(key)
        self.stack.addWidget(widget)
        self.bar.blockSignals(True)
        try:
            self.bar.addTab(title)
        finally:
            self.bar.blockSignals(False)
        widget.show()
        self.set_current(key)

    def take(self, key: str) -> None:
        """Take a panel out; its widget is the caller's to place."""
        index = self._keys.index(key)
        widget = self.stack.widget(index)
        self._keys.pop(index)
        self.stack.removeWidget(widget)
        self.bar.blockSignals(True)
        try:
            self.bar.removeTab(index)
        finally:
            self.bar.blockSignals(False)
        if self._keys:
            self._show_index(self.bar.currentIndex())

    def set_current(self, key: str) -> None:
        index = self._keys.index(key)
        self.bar.setCurrentIndex(index)
        self._show_index(index)

    def set_title(self, key: str, title: str) -> None:
        self.bar.setTabText(self._keys.index(key), title)

    def _show_index(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
        self.update_buttons()

    # ── Its title ────────────────────────────────────────────────────────

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Show the title as a row above the panel, over its top while the
        pointer is at its edge, or not at all."""
        if mode != self._mode:
            if self._mode == ROW:
                self._layout.removeWidget(self.strip)
            self._mode = mode
            self.strip.overlay = mode == OVERLAY
            if mode == ROW:
                self._layout.insertWidget(0, self.strip)
                self.strip.show()
            else:
                self.strip.setVisible(mode == OVERLAY and self.peeked)
            self._place_overlay()
        self.bar.setTabsClosable(len(self._keys) > 1)
        self.grip.setVisible(mode == OVERLAY and not self.peeked)
        self.update_buttons()

    def set_peeked(self, peeked: bool) -> None:
        self.peeked = peeked
        if self._mode == OVERLAY:
            self.strip.setVisible(peeked)
            self.grip.setVisible(not peeked)
            if peeked:
                self._place_overlay()
                self.strip.raise_()

    def _place_overlay(self) -> None:
        if self._mode == OVERLAY:
            # Over the top of the panel, inside the card: over its header band.
            self.strip.setGeometry(CARD_INSET, CARD_INSET,
                                   max(self.width() - 2 * CARD_INSET, 0), TAB_HEIGHT)
            self.strip.raise_()
        self.grip.move((self.width() - self.grip.width()) // 2, 0)
        self.grip.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_overlay()

    def outline_color(self) -> QColor:
        return card_outline_color(self)

    def paintEvent(self, event) -> None:
        # The card: a thin rounded outline in the plot tabs' colour.
        paint_card(self)

    def update_buttons(self) -> None:
        key = self.current()
        panel = self._area.panel(key) if key else None
        if panel is None:
            return
        self.settings_button.setVisible(panel.settings is not None)
        self.max_button.set_kind('restore' if self._area.maximised() == key else 'maximise')
        self.max_button.setVisible(not self.in_window)
        self.window_button.setVisible(panel.floatable)
        self.close_button.setVisible(panel.closable)

    def _move(self) -> None:
        """Undock the panel shown, or, in its own window, dock it back."""
        key = self.current()
        if key is None:
            return
        if self.in_window:
            self._area.dock(key)
        else:
            self._area.float_panel(key)

    def _bar_menu(self, pos: QPoint) -> None:
        index = self.bar.tabAt(pos)
        key = self._keys[index] if 0 <= index < len(self._keys) else self.current()
        self._area.show_panel_menu(key, self.bar.mapToGlobal(pos))


class PanelWindow(QWidget):
    """A panel in a window of its own. Owned by the main window, it stays in
    front of it and goes with it. Its title row has Dock back, which puts the
    panel back in its place in the tab, and ✕, which closes the panel.
    Closing the window by its frame docks the panel back: nothing is lost."""

    def __init__(self, area: PanelArea, key: str) -> None:
        super().__init__(area.window(), Qt.WindowType.Window)
        self._area = area
        self.key = key
        self._docking = False
        self.group = PanelGroup(area, in_window=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.group)

    def closeEvent(self, event) -> None:
        if self._docking:
            super().closeEvent(event)
            return
        event.ignore()
        self._area.dock(self.key)


class PanelArea(QWidget):
    """The panels of one plot tab, tiled by splitters."""

    #: The user closed a panel: by its ✕, its menu, or Ctrl+F4. Its key.
    panelClosed = Signal(str)
    #: A panel opened, closed, moved into its own window or docked back.
    panelsChanged = Signal()
    #: The user renamed a panel by edit_title(): its key and new title.
    panelRenamed = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panels: dict[str, _Panel] = {}
        self._closed: list[str] = []        # the last closed, last
        self._titles_shown = False
        self._maximised: str | None = None
        self._hidden: list[QWidget] = []
        self._saved_sizes: dict[QSplitter, list[int]] = {}
        # The fractions a restored layout gave each splitter, described back
        # as given until the user drags it or the places change. Measured
        # again in whole pixels they would differ in the last digit, and a
        # configuration opened and closed unchanged would ask to be saved.
        self._given: dict[QSplitter, list[float]] = {}
        # Where panels not here yet go once they are: from a restored layout.
        self._layout_homes: dict[str, dict] = {}
        #: The title of a panel's own window, from the panel's: set by the
        #: owner to name the plot tab too.
        self.window_title: Callable[[str], str] = lambda title: title

        self._root = _new_splitter(Qt.Orientation.Horizontal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._root)

        # The pointer is followed while a title can be shown at an edge.
        self._peek_group: PanelGroup | None = None
        self._candidate: PanelGroup | None = None
        self._candidate_since = 0.0
        self._left_at: float | None = None
        self._hold_until = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(lambda: self.follow_pointer(QCursor.pos(), time.monotonic()))

    # ── Panels ───────────────────────────────────────────────────────────

    def add(self, key: str, widget: QWidget, title: str, *, closable: bool = True,
            floatable: bool = True, home: dict | None = None,
            name_label: QLabel | None = None,
            rename: Callable[[], None] | None = None,
            settings: Callable[[QWidget], None] | None = None,
            settings_button: QWidget | None = None,
            delete: Callable[[], None] | None = None) -> None:
        """Make a panel of widget, closed until open_panel() unless it is the first.
        home says where it opens; a restored layout's place wins. rename, if
        given, renames it: offered in its menu and by a double-click on its
        name (edit_title() edits it in place). settings, if given, opens its
        settings under a gear in its title, just left of the title's other
        buttons; settings_button is its own gear, at the same place in its own
        row, shown while its title is not. delete, if given, deletes it for
        good, offered in its menu below Close."""
        panel = _Panel(key, title, widget, closable, floatable,
                       dict(self._layout_homes.pop(key, None) or home or {'edge': 'right'}),
                       name_label, rename=rename, settings=settings,
                       settings_button=settings_button, delete=delete)
        self._panels[key] = panel
        if name_label is not None:
            name_label.setText(title)
            if rename is not None:
                name_label.setToolTip('Double-click to rename')
                name_label.installEventFilter(self)
        widget.setParent(self)
        widget.hide()
        if not self._groups():
            self.open_panel(key)
        else:
            self._refresh()

    def remove(self, key: str) -> QWidget | None:
        """Forget a panel and give its widget back, out of the tab."""
        panel = self._panels.get(key)
        if panel is None:
            return None
        if self.is_open(key):
            self.close_panel(key)
        if key in self._closed:
            self._closed.remove(key)
        del self._panels[key]
        panel.widget.setParent(None)
        self._refresh()
        return panel.widget

    def panel(self, key: str) -> _Panel | None:
        return self._panels.get(key)

    def keys(self) -> list[str]:
        return list(self._panels)

    def title(self, key: str) -> str:
        return self._panels[key].title

    def set_title(self, key: str, title: str) -> None:
        panel = self._panels[key]
        panel.title = title
        group = self._group_of(key) or (panel.window.group if panel.window else None)
        if group is not None:
            group.set_title(key, title)
        if panel.window is not None:
            panel.window.setWindowTitle(self.window_title(title))
        if panel.name_label is not None:
            panel.name_label.setText(title)

    # ── Names ────────────────────────────────────────────────────────────

    def open_settings(self, key: str | None, anchor: QWidget | None = None) -> None:
        """Open a panel's settings, under anchor if given."""
        panel = self._panels.get(key) if key else None
        if panel is not None and panel.settings is not None:
            panel.settings(anchor)

    def rename(self, key: str) -> None:
        """Rename a panel as its owner asked to, if it can be."""
        panel = self._panels.get(key)
        if panel is not None and panel.rename is not None:
            panel.rename()

    def edit_title(self, key: str, max_length: int = 40) -> None:
        """Edit a panel's title in place, over its name or its tab: Enter
        keeps it, Escape does not. panelRenamed says what it became."""
        panel = self._panels.get(key)
        if panel is None:
            return
        label = panel.name_label
        if label is not None and label.isVisible():
            parent, rect = label, label.rect()
        else:
            group = self._group_of(key) or (panel.window.group if panel.window else None)
            if group is None:
                return
            if group.mode() == OVERLAY:
                self.peek(group, hold=math.inf)    # the tab is where the name is
            if not group.strip.isVisible():
                return
            bar = group.bar
            parent, rect = bar, bar.tabRect(group.keys().index(key))
        editor = _NameEditor(parent)
        editor.setMaxLength(max_length)
        editor.setText(panel.title)
        editor.selectAll()
        editor.setGeometry(rect.x(), rect.y(), max(rect.width(), 140), rect.height())

        def finish(keep: bool) -> None:
            name = ' '.join(editor.text().split())[:max_length]
            editor.hide()
            editor.deleteLater()
            self._hold_until = 0.0
            if keep and name and name != panel.title and key in self._panels:
                self.set_title(key, name)
                self.panelRenamed.emit(key, name)

        editor.done.connect(finish)
        editor.show()
        editor.setFocus()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonDblClick:
            for key, panel in self._panels.items():
                if panel.name_label is watched:
                    self.rename(key)
                    return True
        return super().eventFilter(watched, event)

    def is_open(self, key: str) -> bool:
        panel = self._panels.get(key)
        return panel is not None and (panel.window is not None or self._group_of(key) is not None)

    def is_floating(self, key: str) -> bool:
        panel = self._panels.get(key)
        return panel is not None and panel.window is not None

    def has_windows(self) -> bool:
        return any(panel.window is not None for panel in self._panels.values())

    def open_panel(self, key: str) -> None:
        """Show a panel where it was last, or where it belongs."""
        panel = self._panels[key]
        if self.is_open(key):
            return
        self.restore()
        if key in self._closed:
            self._closed.remove(key)
        home = panel.home or {}
        beside = self._group_of(home.get('with')) if home.get('with') else None
        if beside is not None:
            beside.add(key, panel.widget, panel.title)
        else:
            group = self._new_group(key)
            target = self._group_of(home.get('beside')) if home.get('beside') else None
            side = home.get('side') if home.get('side') in SIDES else 'right'
            share = home.get('share')
            share = min(max(float(share), 0.05), 0.95) if isinstance(share, (int, float)) else 0.4
            if not self._groups(but=group):
                self._root.addWidget(group)
            elif target is not None:
                whole = home.get('whole')
                self._split_beside(target, group, side, share,
                                   whole if isinstance(whole, (int, float)) and 0 < whole < 1 else None)
            else:
                edge = home.get('edge') if home.get('edge') in SIDES else 'right'
                self._add_at_edge(group, edge, share)
        self._refresh()
        self.panelsChanged.emit()

    def close_panel(self, key: str, user: bool = False) -> None:
        """Close a panel; open_panel() brings it back where it was."""
        panel = self._panels.get(key)
        if panel is None or not self.is_open(key):
            return
        if user and not panel.closable:
            return
        if panel.window is not None:
            self._end_window(panel)
        else:
            self.restore()
            panel.home = self._home_of(key)
            self._take_out(key)
        self._closed.append(key)
        self._refresh()
        self.panelsChanged.emit()
        if user:
            self.panelClosed.emit(key)

    def reopen_last(self) -> str | None:
        """Open the panel closed last; its key, or None."""
        while self._closed:
            key = self._closed[-1]
            if key in self._panels:
                self.open_panel(key)
                return key
            self._closed.pop()
        return None

    def closed_keys(self) -> list[str]:
        return [key for key in self._closed if key in self._panels]

    # ── Maximise, restore ────────────────────────────────────────────────

    def maximised(self) -> str | None:
        return self._maximised

    def maximise(self, key: str | None) -> None:
        """Let a panel fill the tab, or, if it does, give the room back."""
        if key is None:
            return
        if self._maximised == key:
            self.restore()
            return
        self.restore()
        group = self._group_of(key)
        if group is None or len(self._groups()) < 2:
            return
        group.set_current(key)
        # Hidden panels have no size: describe() takes these meanwhile.
        self._saved_sizes = {splitter: splitter.sizes() for splitter in self._splitters()}
        self._hidden = []
        widget: QWidget = group
        while widget is not self._root:
            parent = widget.parentWidget()
            for index in range(parent.count()):
                other = parent.widget(index)
                if other is not widget and not other.isHidden():
                    other.hide()
                    self._hidden.append(other)
            widget = parent
        self._maximised = key
        self._refresh()

    def restore(self) -> None:
        if self._maximised is None:
            return
        self._maximised = None
        # Shown again, they take their sizes back: a splitter keeps them.
        for widget in self._hidden:
            widget.show()
        self._hidden, self._saved_sizes = [], {}
        self._refresh()

    # ── A window of its own ──────────────────────────────────────────────

    def float_panel(self, key: str | None, rect: QRect | None = None) -> None:
        """Move a panel into a window of its own."""
        panel = self._panels.get(key) if key else None
        if panel is None or panel.window is not None or not panel.floatable:
            return
        if self.is_open(key):
            self.restore()
            size = self._group_of(key).size()
            panel.home = self._home_of(key)
            self._take_out(key)
        else:
            # Straight from closed, as a configuration's window: the places
            # in the tab stay as they are.
            size = QSize(480, 320)
            if key in self._closed:
                self._closed.remove(key)
        window = PanelWindow(self, key)
        window.group.add(key, panel.widget, panel.title)
        # Its title row holds Dock back and ✕, always in view.
        window.group.set_mode(ROW)
        window.setWindowTitle(self.window_title(panel.title))
        panel.window = window
        window.setGeometry(rect or panel.window_rect or self._new_window_rect(size))
        window.show()
        window.raise_()
        self._refresh()
        self.panelsChanged.emit()

    def dock(self, key: str) -> None:
        """Put a panel in its own window back where it came from."""
        panel = self._panels.get(key)
        if panel is None or panel.window is None:
            return
        self._end_window(panel)
        self.open_panel(key)

    def dock_all(self) -> None:
        for key, panel in list(self._panels.items()):
            if panel.window is not None:
                self.dock(key)

    def _end_window(self, panel: _Panel) -> None:
        window = panel.window
        panel.window = None
        panel.window_rect = window.geometry()
        window.group.take(panel.key)
        panel.widget.setParent(self)
        panel.widget.hide()
        window._docking = True
        window.close()
        window.deleteLater()

    def _new_window_rect(self, size: QSize) -> QRect:
        size = QSize(max(size.width(), 420), max(size.height(), 280))
        corner = self.mapToGlobal(QPoint(self.width() // 4, self.height() // 6))
        rect = QRect(corner, size)
        screen = QGuiApplication.screenAt(corner) or QGuiApplication.primaryScreen()
        if screen is not None:
            room = screen.availableGeometry()
            rect.moveLeft(max(room.left(), min(rect.left(), room.right() - rect.width())))
            rect.moveTop(max(room.top(), min(rect.top(), room.bottom() - rect.height())))
        return rect

    # ── Titles ───────────────────────────────────────────────────────────

    def set_titles_shown(self, shown: bool) -> None:
        """Every title in view, or each shown only at its panel's edge."""
        self._titles_shown = bool(shown)
        self._refresh()

    def titles_shown(self) -> bool:
        return self._titles_shown

    def peek(self, group: PanelGroup | None, hold: float = 0.0) -> None:
        """Show a title at its panel's top edge; with hold, for that long."""
        self._set_peek(group)
        self._hold_until = time.monotonic() + hold if group is not None else 0.0

    def peek_key(self, key: str, hold: float = HOLD_DELAY) -> None:
        group = self._group_of(key)
        if group is not None and group.mode() == OVERLAY:
            self.peek(group, hold)

    def peeked(self) -> PanelGroup | None:
        return self._peek_group

    def follow_pointer(self, position: QPoint, now: float) -> None:
        """Show a title after the pointer rests PEEK_DELAY at its panel's top
        edge, and fold it FOLD_DELAY after the pointer leaves edge and title."""
        if QApplication.activePopupWidget() is not None:
            return                     # a menu from the title is open
        hit = None
        for group in self._groups():
            if group.mode() != OVERLAY or not group.isVisible():
                continue
            top_left = group.mapToGlobal(QPoint(0, 0))
            below = TAB_HEIGHT if group is self._peek_group else EDGE_BELOW
            if (top_left.x() <= position.x() < top_left.x() + group.width()
                    and top_left.y() - EDGE_ABOVE <= position.y() < top_left.y() + below):
                hit = group
                break
        if hit is not None and hit is self._peek_group:
            self._left_at = None
            self._candidate = None
            return
        if hit is None:
            self._candidate = None
        elif hit is not self._candidate:
            self._candidate, self._candidate_since = hit, now
        elif now - self._candidate_since >= PEEK_DELAY - _TOLERANCE:
            self._candidate = None
            self._set_peek(hit)
            return
        if self._peek_group is not None and now >= self._hold_until:
            if self._left_at is None:
                self._left_at = now
            elif now - self._left_at >= FOLD_DELAY - _TOLERANCE:
                self._set_peek(None)

    def _set_peek(self, group: PanelGroup | None) -> None:
        if self._peek_group is not None and self._peek_group is not group:
            try:
                self._peek_group.set_peeked(False)
            except RuntimeError:
                pass              # gone with a layout change
        self._peek_group = group
        self._left_at = None
        if group is not None:
            group.set_peeked(True)

    # ── Menu ─────────────────────────────────────────────────────────────

    def panel_menu(self, key: str | None) -> QMenu:
        menu = QMenu(self)
        panel = self._panels.get(key) if key else None
        if panel is None:
            return menu
        if panel.window is None:
            menu.addAction('Restore' if self._maximised == key else 'Maximise',
                           lambda: self.maximise(key)).setEnabled(len(self._groups()) > 1)
        if panel.floatable:
            if panel.window is None:
                menu.addAction('Undock', lambda: self.float_panel(key))
            else:
                menu.addAction('Dock back', lambda: self.dock(key))
        if panel.settings is not None:
            menu.addAction('Settings…', lambda: self.open_settings(key))
        if panel.rename is not None:
            menu.addAction('Rename…', lambda: self.rename(key))
        menu.addSeparator()
        menu.addAction('Close', lambda: self.close_panel(key, user=True)).setEnabled(panel.closable)
        if panel.delete is not None:
            menu.addAction('Delete', panel.delete).setToolTip(
                'Delete it for good: unlike Close, it is not kept to open again')
        return menu

    def show_panel_menu(self, key: str | None, at: QPoint) -> None:
        if key is None:
            return
        menu = self.panel_menu(key)
        hold, self._hold_until = self._hold_until, math.inf
        try:
            menu.exec(at)
        finally:
            self._hold_until = hold
            menu.deleteLater()

    # ── The keyboard ─────────────────────────────────────────────────────

    def key_of(self, widget: QWidget | None) -> str | None:
        """The panel a widget is in, if it is in one of these."""
        widgets = {id(panel.widget): key for key, panel in self._panels.items()}
        while widget is not None:
            if id(widget) in widgets:
                return widgets[id(widget)]
            widget = widget.parentWidget()
        return None

    def focused_key(self) -> str | None:
        return self.key_of(QApplication.focusWidget())

    # ── A configuration ──────────────────────────────────────────────────

    def describe(self) -> dict:
        """The arrangement: the tree of places with their sizes as
        fractions, and the panels in windows of their own."""
        # A tab with one place is that place, not a split of one.
        top = self._root.widget(0) if self._root.count() == 1 else self._root
        layout = {'tree': self._describe(top)}
        windows = []
        for key, panel in self._panels.items():
            if panel.window is None:
                continue
            rect = panel.window.geometry()
            screen = panel.window.screen()
            entry = {'panels': [key], 'rect': [rect.x(), rect.y(), rect.width(), rect.height()]}
            if screen is not None:
                entry['screen'] = screen.name()
            windows.append(entry)
        if windows:
            layout['windows'] = windows
        return layout

    def _describe(self, widget: QWidget) -> dict:
        if isinstance(widget, PanelGroup):
            return {'panels': widget.keys(), 'current': widget.current()}
        sizes = self._saved_sizes.get(widget) or widget.sizes()
        given = self._given.get(widget)
        return {'split': 'row' if widget.orientation() == Qt.Orientation.Horizontal else 'column',
                'sizes': given if given is not None and len(given) == widget.count()
                else _fractions(sizes),
                'children': [self._describe(widget.widget(index)) for index in range(widget.count())]}

    def restore_layout(self, layout: dict) -> None:
        """Arrange the panels as describe() gave them. Panels it names that
        are not here yet go to their place once they come; panels it does not
        name stay closed, but one that cannot close stays open."""
        if not isinstance(layout, dict) or not isinstance(layout.get('tree'), dict):
            return
        self.restore()
        self.dock_all()
        for key in list(self._panels):
            if self.is_open(key):
                self._take_out(key)
        self._closed = []
        tree = layout['tree']
        built = self._build(tree)
        if built is not None:
            if isinstance(built, QSplitter):
                self._replace_root(built)
            else:
                self._root.addWidget(built)
        for key, panel in self._panels.items():
            if not self.is_open(key):
                place = self._home_from_tree(tree, key)
                if place:
                    panel.home = place
                if not panel.closable:
                    self.open_panel(key)
                else:
                    self._closed.append(key)
        self._layout_homes = {key: home for key in _tree_keys(tree)
                              if key not in self._panels
                              and (home := self._home_from_tree(tree, key))}
        for entry in layout.get('windows') or []:
            self._restore_window(entry)
        self._refresh()
        self.panelsChanged.emit()

    def _restore_window(self, entry) -> None:
        if not isinstance(entry, dict):
            return
        keys = [key for key in entry.get('panels') or [] if key in self._panels]
        rect = entry.get('rect')
        if not keys or not (isinstance(rect, list) and len(rect) == 4):
            return
        rect = QRect(*[int(value) for value in rect])
        screens = QGuiApplication.screens()
        named = [screen for screen in screens if screen.name() == entry.get('screen')]
        on = [screen for screen in (named or screens)
              if screen.availableGeometry().intersects(rect)]
        key = keys[0]
        if on:
            self.float_panel(key, rect)
        elif not self.is_open(key):
            self.open_panel(key)              # its screen is gone: docked instead

    def _build(self, node) -> QWidget | None:
        if not isinstance(node, dict):
            return None
        if 'panels' in node:
            keys = [key for key in node.get('panels') or []
                    if isinstance(key, str) and key in self._panels and not self.is_open(key)]
            if not keys:
                return None
            group = PanelGroup(self)
            for key in keys:
                group.add(key, self._panels[key].widget, self._panels[key].title)
            if node.get('current') in keys:
                group.set_current(node['current'])
            return group
        children = node.get('children')
        if not isinstance(children, list):
            return None
        sizes = node.get('sizes') if isinstance(node.get('sizes'), list) else []
        built = []
        for index, child in enumerate(children):
            widget = self._build(child)
            if widget is not None:
                size = sizes[index] if index < len(sizes) else None
                built.append((widget, float(size) if isinstance(size, (int, float)) and size > 0
                              else 1.0 / len(children)))
        if not built:
            return None
        if len(built) == 1:
            return built[0][0]
        splitter = _new_splitter(Qt.Orientation.Horizontal if node.get('split') == 'row'
                                 else Qt.Orientation.Vertical)
        for widget, _size in built:
            splitter.addWidget(widget)
        splitter.setSizes(_weights([size for _widget, size in built]))
        self._given[splitter] = [round(size, 4) for _widget, size in built]
        splitter.splitterMoved.connect(lambda *_args, s=splitter: self._given.pop(s, None))
        return splitter

    def _home_from_tree(self, tree: dict, key: str) -> dict | None:
        """Where a panel lies in a described tree, as a home beside panels
        that are here."""
        path = _path_to(tree, key)
        if path is None:
            return None
        group = path[-1][0]
        others = [other for other in group.get('panels') or []
                  if other != key and other in self._panels]
        if others:
            return {'with': others[0]}
        for node, index in reversed(path[:-1]):
            children = node['children']
            sizes = node.get('sizes') if isinstance(node.get('sizes'), list) else []
            for step in (-1, 1):
                at = index + step
                if 0 <= at < len(children):
                    near = [k for k in _tree_keys(children[at]) if k in self._panels]
                    if near:
                        horizontal = node.get('split') == 'row'
                        side = (('right' if step < 0 else 'left') if horizontal
                                else ('bottom' if step < 0 else 'top'))
                        own = sizes[index] if index < len(sizes) else 0
                        other = sizes[at] if at < len(sizes) else 0
                        share = own / (own + other) if own and other else 0.4
                        home = {'beside': near[0], 'side': side, 'share': share}
                        if own and len(sizes) == len(children):
                            home['whole'] = own / sum(sizes)
                        return home
        return None

    # ── The tree ─────────────────────────────────────────────────────────

    def _groups(self, but: QWidget | None = None) -> list[PanelGroup]:
        found: list[PanelGroup] = []

        def walk(widget: QWidget) -> None:
            if isinstance(widget, PanelGroup):
                if widget is not but:
                    found.append(widget)
            elif isinstance(widget, QSplitter):
                for index in range(widget.count()):
                    walk(widget.widget(index))
        walk(self._root)
        return found

    def _splitters(self) -> list[QSplitter]:
        found: list[QSplitter] = []

        def walk(widget: QWidget) -> None:
            if isinstance(widget, QSplitter):
                found.append(widget)
                for index in range(widget.count()):
                    walk(widget.widget(index))
        walk(self._root)
        return found

    def card_of(self, key: str) -> PanelGroup | None:
        """The card (place) a panel is in here; None while it is closed or
        in a window of its own."""
        return self._group_of(key)

    def _group_of(self, key: str | None) -> PanelGroup | None:
        if key is None:
            return None
        for group in self._groups():
            if key in group.keys():
                return group
        return None

    def _new_group(self, key: str) -> PanelGroup:
        group = PanelGroup(self)
        panel = self._panels[key]
        group.add(key, panel.widget, panel.title)
        return group

    def _home_of(self, key: str) -> dict:
        """Where a panel is now, to come back to."""
        group = self._group_of(key)
        if group is None:
            return dict(self._panels[key].home)
        others = [other for other in group.keys() if other != key]
        if others:
            return {'with': others[0]}
        parent = group.parentWidget()
        index = parent.indexOf(group)
        if not isinstance(parent, QSplitter) or parent.count() < 2:
            return dict(self._panels[key].home)
        at = index - 1 if index > 0 else 1
        near = self._keys_in(parent.widget(at))
        sizes = parent.sizes()
        own, other = sizes[index], sizes[at]
        horizontal = parent.orientation() == Qt.Orientation.Horizontal
        side = (('right' if at < index else 'left') if horizontal
                else ('bottom' if at < index else 'top'))
        share = own / (own + other) if own > 0 and other > 0 else 0.4
        whole = own / sum(sizes) if own > 0 else None
        if not near:
            return {'edge': side}
        home = {'beside': near[0], 'side': side, 'share': share}
        if whole is not None:
            home['whole'] = whole
        return home

    def _keys_in(self, widget: QWidget) -> list[str]:
        if isinstance(widget, PanelGroup):
            return widget.keys()
        if isinstance(widget, QSplitter):
            return [key for index in range(widget.count()) for key in self._keys_in(widget.widget(index))]
        return []

    def _take_out(self, key: str) -> None:
        """Take a docked panel out of its place, and the place with it if it
        is left empty."""
        group = self._group_of(key)
        if group is None:
            return
        panel = self._panels[key]
        group.take(key)
        panel.widget.setParent(self)
        panel.widget.hide()
        if not group.keys():
            self._remove_group(group)

    def _split_beside(self, target: PanelGroup, group: PanelGroup, side: str, share: float,
                      whole: float | None = None) -> None:
        """Put group on a side of target: share is its part of the two, or,
        in a row of more, whole its part of the row, the others giving way
        alike, so a panel reopened gets the size it had."""
        self._given.clear()
        parent = target.parentWidget()
        index = parent.indexOf(target)
        sizes = parent.sizes()
        if parent.orientation() == _orientation(side):
            at = index if _first(side) else index + 1
            total = sum(sizes)
            if whole is not None and total > 0 and len(sizes) > 1:
                new = max(1, int(round(total * whole)))
                sizes = [max(1, int(round(size * (total - new) / total))) for size in sizes]
            else:
                own = sizes[index] or 1000
                new = max(1, int(own * share))
                sizes[index] = max(1, own - new)
            parent.insertWidget(at, group)
            sizes.insert(at, new)
            parent.setSizes(sizes)
        else:
            # A splitter the other way takes target's place, then target and
            # group. insertWidget, unlike replaceWidget, hands the new
            # splitter to Qt, so Python's collector cannot take it, panels and all.
            splitter = _new_splitter(_orientation(side))
            parent.insertWidget(index, splitter)
            pair = [group, target] if _first(side) else [target, group]
            for widget in pair:
                splitter.addWidget(widget)
                widget.show()
            splitter.setSizes(_weights([share, 1 - share] if _first(side) else [1 - share, share]))
            parent.setSizes(sizes)

    def _add_at_edge(self, group: PanelGroup, side: str, share: float) -> None:
        self._given.clear()
        root = self._root
        if root.orientation() != _orientation(side):
            if root.count() < 2:
                root.setOrientation(_orientation(side))
            else:
                new_root = _new_splitter(_orientation(side))
                self._replace_root(new_root)
                new_root.addWidget(root)
                root.show()
                root = new_root
        sizes = root.sizes()
        total = sum(sizes) or 1000 * max(1, len(sizes))
        new = max(1, int(total * share))
        sizes = [max(1, int(size * (total - new) / total)) for size in sizes] if sum(sizes) else [
            max(1, int((total - new) / len(sizes)))] * len(sizes)
        at = 0 if _first(side) else root.count()
        root.insertWidget(at, group)
        sizes.insert(at, new)
        root.setSizes(sizes)

    def _replace_root(self, splitter: QSplitter) -> None:
        old = self._root
        # Parented first, so it is Qt's to keep, not Python's to collect.
        splitter.setParent(self)
        self.layout().replaceWidget(old, splitter)
        old.setParent(None)
        if old is not splitter and old.count() == 0:
            old.deleteLater()
        self._root = splitter
        splitter.show()

    def _remove_group(self, group: PanelGroup) -> None:
        self._given.clear()
        if self._peek_group is group:
            self._peek_group = None
        if self._candidate is group:
            self._candidate = None
        parent = group.parentWidget()
        group.hide()
        group.setParent(None)
        group.deleteLater()
        if isinstance(parent, QSplitter):
            self._collapse(parent)

    def _collapse(self, splitter: QSplitter) -> None:
        """A splitter left with one child gives its place to that child."""
        if splitter is self._root:
            if splitter.count() == 1 and isinstance(splitter.widget(0), QSplitter):
                inner = splitter.widget(0)
                sizes = inner.sizes()
                splitter.setOrientation(inner.orientation())
                for child in [inner.widget(index) for index in range(inner.count())]:
                    splitter.addWidget(child)
                    child.show()
                inner.setParent(None)
                inner.deleteLater()
                splitter.setSizes(sizes)
            return
        if splitter.count() != 1:
            return
        child = splitter.widget(0)
        parent = splitter.parentWidget()
        index = parent.indexOf(splitter)
        sizes = parent.sizes()
        parent.insertWidget(index, child)
        child.show()
        splitter.setParent(None)
        splitter.deleteLater()
        parent.setSizes(sizes)
        if isinstance(child, QSplitter) and child.orientation() == parent.orientation():
            self._merge(parent, child)

    def _merge(self, parent: QSplitter, child: QSplitter) -> None:
        """Lay a child splitter's widgets into its parent of the same way."""
        index = parent.indexOf(child)
        outer, inner = parent.sizes(), child.sizes()
        share, total = outer[index], sum(inner) or len(inner)
        widgets = [child.widget(i) for i in range(child.count())]
        for offset, widget in enumerate(widgets):
            parent.insertWidget(index + offset, widget)
            widget.show()
        child.setParent(None)
        child.deleteLater()
        parent.setSizes(outer[:index] + [max(1, int(share * size / total)) for size in inner]
                        + outer[index + 1:])

    # ── Showing the titles ───────────────────────────────────────────────

    def _refresh(self) -> None:
        groups = self._groups()
        alone = len(groups) == 1 and len(groups[0].keys()) == 1
        for group in groups:
            if self._maximised is not None and self._maximised in group.keys():
                mode = ROW
            elif alone:
                mode = NONE            # the tab looks as it did before panels
            elif len(group.keys()) > 1 or self._titles_shown:
                mode = ROW
            else:
                mode = OVERLAY
            group.set_mode(mode)
        if self._peek_group is not None and (self._peek_group not in groups
                                             or self._peek_group.mode() != OVERLAY):
            self._peek_group = None
        for key, panel in self._panels.items():
            group = self._group_of(key)
            # Where its title row shows, the title names it and has its gear.
            own_row = group is not None and group.mode() != ROW
            for widget in (panel.name_label, panel.settings_button):
                if widget is not None:
                    widget.setVisible(own_row)
        self._watch_pointer()

    def _watch_pointer(self) -> None:
        wanted = self.isVisible() and any(group.mode() == OVERLAY for group in self._groups())
        if wanted and not self._timer.isActive():
            self._timer.start()
        elif not wanted and self._timer.isActive():
            self._timer.stop()
            self._set_peek(None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._watch_pointer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._watch_pointer()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.PaletteChange:
            for group in self._groups():
                group.update()
        return super().event(event)


def layout_keys(layout) -> list[str]:
    """Every panel a described layout shows, in the tab or in a window."""
    if not isinstance(layout, dict):
        return []
    keys = _tree_keys(layout.get('tree'))
    for entry in layout.get('windows') or []:
        if isinstance(entry, dict):
            keys += [key for key in entry.get('panels') or [] if isinstance(key, str)]
    return keys


def _tree_keys(node) -> list[str]:
    if not isinstance(node, dict):
        return []
    if 'panels' in node:
        return [key for key in node.get('panels') or [] if isinstance(key, str)]
    return [key for child in node.get('children') or [] for key in _tree_keys(child)]


def _path_to(node, key: str, path: list | None = None) -> list | None:
    """The nodes from the top of a described tree down to the place holding
    key, each with the index of the child taken."""
    path = path or []
    if not isinstance(node, dict):
        return None
    if 'panels' in node:
        return path + [(node, -1)] if key in (node.get('panels') or []) else None
    for index, child in enumerate(node.get('children') or []):
        found = _path_to(child, key, path + [(node, index)])
        if found is not None:
            return found
    return None
