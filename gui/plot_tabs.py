# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tabs of plots: each tab has its own signals, signal table and plot mode.

The tab strip sits above the signal table and the plot. Each tab holds a
PlotPanel of its own, so switching tabs shows that tab's table and plot as they
were left, without drawing them again. The "+" button adds a tab, a double-click
renames one, and its close button or right-click menu closes it.

The selected tab is outlined, with round top corners and feet that curve out
into a thin line along the foot of the strip, which runs under the table and
the plot from one side to the other, so the tab plainly owns what lies below
it. A tab's close button is a small "x" raised beside its name, like a
superscript, so a click on the name does not close the tab by accident.

Signals dragged from the signal tree onto a tab are plotted in it, and onto
"+" in a new tab; that tab comes on screen. Rows dragged from the signal table
onto another tab, or sent there by "Move to tab" in the signal menu, move
there with their look; the tab on screen stays, unless they go to a new tab,
which takes the plot mode of the tab they came from.

Synchronized tabs, the default, show one time: the tab coming into view takes
over the time range and the cursors of the tab it replaces. Only one tab is
ever on screen, so nothing is updated behind it. Unsynchronized, each tab keeps
the time range and cursors it was left with.

A configuration file, and the plots kept for the next measurement, describe
each tab as describe_tab() does; SavedTabs reads them back. A configuration
saved before tabs describes one plot, which becomes the tab on screen.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import count

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProxyStyle,
    QStackedWidget,
    QStyle,
    QTabBar,
    QToolButton,
    QWidget,
)

from gui.edge_tab import _mix
from gui.plot_widget import ROW_MIME_TYPE, PlotPanel
from gui.signal_tree import SignalTreeWidget

TAB_NAME = 'Tab {}'
MAX_NAME_LENGTH = 40
PLOT_TYPES = ('normal', 'multi_axis', 'stacked', 'multistack')
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


def plot_type(panel: PlotPanel) -> str:
    """The panel's plot mode, as a configuration names it."""
    if panel._multistack_mode:
        return 'multistack'
    if panel._stacked_mode:
        return 'stacked'
    if panel._multi_axis:
        return 'multi_axis'
    return 'normal'


def describe_tab(panel: PlotPanel, name: str) -> dict:
    """A tab as a configuration keeps it: its name, plot mode, data points,
    cursors, and each signal with its look."""
    return {
        'name': name,
        'plot_type': plot_type(panel),
        'show_data_points': panel._show_points,
        'hide_plot_lines': panel._hide_lines,
        'cursor1': panel._cursor1_enabled,
        'cursor2': panel._cursor2_enabled,
        'signals': [_signal_entry(key, panel._items[key]) for key in panel.plotted_keys()],
    }


def _signal_entry(key: str, plotted) -> dict:
    return {
        'key': key,
        'color': plotted.color,
        'visible': plotted.visible,
        'group': plotted.group,
        'axis_visible': plotted.axis_visible,
        'own_axis': plotted.own_axis,
        'multistack_id': plotted.multistack_id,
        'line_style': plotted.line_style,
    }


@dataclass
class SavedTabs:
    """Tabs as a configuration, or the last measurement, left them."""

    tabs: list[dict]
    current: int = 0
    # Saved before tabs: one plot, for the tab on screen.
    single_plot: bool = False
    synchronized: bool | None = None
    # Kept from the last measurement, for the same tabs.
    carried_over: bool = False

    @classmethod
    def from_config(cls, data: dict) -> SavedTabs:
        """The tabs of a configuration file. One saved before tabs holds a
        single plot, in its top-level keys, which old versions also read."""
        tabs = [tab for tab in data.get('tabs') or [] if isinstance(tab, dict)]
        sync = data.get('synchronize_tabs')
        if tabs:
            return cls(tabs, _index(data.get('current_tab'), len(tabs)),
                       synchronized=None if sync is None else bool(sync))
        multi_axis = bool(data.get('multi_axis', False))
        multistack = bool(data.get('multistack', False))
        stacked = bool(data.get('stacked', not multi_axis and not multistack))
        colors = dict(data.get('signal_colors') or {})
        signals = []
        for entry in data.get('signals') or []:
            if isinstance(entry, str):
                entry = {'key': entry}
            if isinstance(entry, dict) and entry.get('key'):
                entry = dict(entry)
                if entry['key'] in colors:
                    entry.setdefault('color', colors[entry['key']])
                signals.append(entry)
        show_points = bool(data.get('show_data_points', False))
        return cls([{
            'plot_type': ('multistack' if multistack else 'multi_axis' if multi_axis
                          else 'stacked' if stacked else 'normal'),
            'show_data_points': show_points,
            'hide_plot_lines': bool(data.get('hide_plot_lines', False)) and show_points,
            'cursor1': bool(data.get('cursor1', False)),
            'cursor2': bool(data.get('cursor2', False)),
            'signals': signals,
        }], single_plot=True)

    @classmethod
    def from_handoff(cls, data: dict) -> SavedTabs:
        """The tabs kept for the next measurement. One kept before tabs is a
        plot mode and its signals, for the tab on screen."""
        tabs = [tab for tab in data.get('tabs') or [] if isinstance(tab, dict)]
        if tabs:
            return cls(tabs, _index(data.get('current_tab'), len(tabs)), carried_over=True)
        return cls([{key: data[key] for key in ('plot_type', 'signals') if key in data}],
                   single_plot=True, carried_over=True)

    def rename_signal(self, old_key: str, new_key: str) -> None:
        for tab in self.tabs:
            for entry in tab.get('signals') or []:
                if isinstance(entry, dict) and entry.get('key') == old_key:
                    entry['key'] = new_key


def signal_entries(tab: dict) -> list[dict]:
    """A saved tab's signals, each with at least its key."""
    return [entry for entry in tab.get('signals') or []
            if isinstance(entry, dict) and entry.get('key')]


def _index(value, count: int) -> int:
    try:
        return min(max(int(value), 0), count - 1)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class TimeView:
    """What synchronized tabs share: the time on screen, and the cursors."""

    time_range: tuple[float, float] | None
    cursors: tuple[bool, float, bool, float]

    @classmethod
    def of(cls, panel: PlotPanel) -> TimeView:
        return cls(panel.time_range(), panel.cursor_state())

    def show_in(self, panel: PlotPanel) -> None:
        if self.time_range is not None and panel.time_range() is not None:
            panel.show_time_range(*self.time_range)
        cursor1, at1, cursor2, at2 = self.cursors
        shown1, _, shown2, _ = panel.cursor_state()
        if shown1 != cursor1:
            panel.set_cursor1_enabled(cursor1)
        if cursor1:
            panel.move_cursor1(at1)
        if shown2 != cursor2:
            panel.set_cursor2_enabled(cursor2)
        if cursor2:
            panel.move_cursor2(at2)


class PlotTabs(QObject):
    """The tab strip, and the stacks that show the current tab's table and plot.

    ``new_panel`` builds the panel for each tab added.
    """

    # The panel of the tab now on screen.
    currentPanelChanged = Signal(object)
    # Signals from the signal tree dropped on a tab: their keys, and the
    # tab's position, or -1 for a new tab.
    signalsDropped = Signal(list, int)
    # Signals to move out of a tab: its panel, their keys, and the position
    # of the tab they go to, or -1 for a new tab.
    signalsMoved = Signal(object, list, int)

    def __init__(self, new_panel: Callable[[], PlotPanel],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._new_panel = new_panel
        self._panels: dict[int, PlotPanel] = {}
        self._ids = count(1)
        self._shown: PlotPanel | None = None
        # The time of the tab shown last, for the next one shown.
        self._shared: TimeView | None = None
        self.synchronized = True

        self.bar = _TabBar()
        self.bar.setMovable(True)
        self.bar.setExpanding(False)
        self.bar.setUsesScrollButtons(True)
        self.bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bar.currentChanged.connect(self._show_tab)
        self.bar.tabCloseRequested.connect(self.close_tab)
        self.bar.tabBarDoubleClicked.connect(self.rename_tab)
        self.bar.customContextMenuRequested.connect(self._tab_menu)
        self.bar.dropped.connect(self._dropped)

        self.add_button = _AddTabButton()
        self.add_button.setText('+')
        self.add_button.setAutoRaise(True)
        self.add_button.setToolTip('New tab')
        self.add_button.setAccessibleName('New tab')
        self.add_button.clicked.connect(lambda: self.add_tab())
        self.add_button.dropped.connect(lambda keys, rows: self._dropped(keys, rows, -1))

        self.strip = _TabStrip()
        layout = QHBoxLayout(self.strip)
        # The line starts a little before the first tab.
        layout.setContentsMargins(STRIP_INDENT, 0, 0, 0)
        layout.setSpacing(2)
        # Down on the line, which the selected tab's feet run into.
        layout.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(self.add_button)
        layout.addStretch(1)

        # The current tab's signal table, and its plot, each in its column.
        self.tables = QStackedWidget()
        self.plots = QStackedWidget()

    # ── The tabs ─────────────────────────────────────────────────────────

    def count(self) -> int:
        return self.bar.count()

    def panels(self) -> list[PlotPanel]:
        """Every tab's panel, in the tabs' order."""
        return [self._panels[self.bar.tabData(i)] for i in range(self.bar.count())]

    def current(self) -> PlotPanel:
        """The panel of the tab on screen."""
        return self._panels[self.bar.tabData(self.bar.currentIndex())]

    def names(self) -> list[str]:
        return [self.bar.tabText(i) for i in range(self.bar.count())]

    def add_tab(self, name: str | None = None) -> PlotPanel:
        """Add a tab with a new, empty panel, and show it."""
        panel = self._new_panel()
        panel.move_targets = lambda: self.other_tabs(panel)
        panel.moveToTabRequested.connect(
            lambda keys, index, source=panel: self.signalsMoved.emit(source, keys, index))
        tab_id = next(self._ids)
        self._panels[tab_id] = panel
        self.tables.addWidget(panel.table_panel)
        self.plots.addWidget(panel)
        # The first tab becomes current as it is added, before it has its id.
        self.bar.blockSignals(True)
        try:
            index = self.bar.addTab(name or self._free_name())
            self.bar.setTabData(index, tab_id)
        finally:
            self.bar.blockSignals(False)
        self.bar.setTabsClosable(self.bar.count() > 1)
        if self.bar.currentIndex() == index:
            self._show_tab(index)
        else:
            self.bar.setCurrentIndex(index)
        return panel

    def other_tabs(self, panel: PlotPanel) -> list[tuple[int, str]]:
        """The tabs other than panel's, as (position, name)."""
        return [(index, self.bar.tabText(index)) for index in range(self.bar.count())
                if self._panels[self.bar.tabData(index)] is not panel]

    def show_neighbour(self, step: int) -> None:
        """Show the next tab (step 1) or the previous one (-1), round the end."""
        if self.bar.count() > 1:
            self.bar.setCurrentIndex((self.bar.currentIndex() + step) % self.bar.count())

    def close_tab(self, index: int) -> None:
        """Close a tab, after asking if signals are plotted in it. The last
        tab stays."""
        if self.bar.count() < 2 or not 0 <= index < self.bar.count():
            return
        tab_id = self.bar.tabData(index)
        panel = self._panels[tab_id]
        plotted = len(panel.plotted_keys())
        if plotted:
            answer = QMessageBox.question(
                self.strip.window(), 'Close tab',
                f'Close {self.bar.tabText(index)} and the {plotted} '
                f'signal{"s" if plotted > 1 else ""} plotted in it?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        # Shows a neighbour first, if this tab was on screen.
        self.bar.removeTab(self._index_of(tab_id))
        del self._panels[tab_id]
        self.tables.removeWidget(panel.table_panel)
        self.plots.removeWidget(panel)
        panel.table_panel.deleteLater()
        panel.deleteLater()
        self.bar.setTabsClosable(self.bar.count() > 1)

    def rename_tab(self, index: int) -> None:
        """Edit a tab's name in place: Enter keeps it, Escape does not."""
        if not 0 <= index < self.bar.count():
            return
        tab_id = self.bar.tabData(index)
        editor = _NameEditor(self.bar)
        editor.setMaxLength(MAX_NAME_LENGTH)
        editor.setText(self.bar.tabText(index))
        editor.selectAll()
        rect = self.bar.tabRect(index).adjusted(TAB_FLARE + 2, 3, -TAB_FLARE - 2, -1)
        rect.setWidth(max(rect.width(), 120))
        editor.setGeometry(rect)

        def finish(keep: bool) -> None:
            name = editor.text().strip()
            at = self._index_of(tab_id)
            if keep and name and at >= 0:
                self.bar.setTabText(at, name)
            editor.deleteLater()

        editor.done.connect(finish)
        editor.show()
        editor.setFocus()

    def _index_of(self, tab_id: int) -> int:
        for index in range(self.bar.count()):
            if self.bar.tabData(index) == tab_id:
                return index
        return -1

    def _free_name(self) -> str:
        names = set(self.names())
        number = self.bar.count() + 1
        while TAB_NAME.format(number) in names:
            number += 1
        return TAB_NAME.format(number)

    def _dropped(self, keys: list, rows: bool, index: int) -> None:
        """Signals dropped on the tab at index, or on "+" (-1): rows of the
        tab on screen move there, signals from the signal tree are added."""
        if rows:
            self.signalsMoved.emit(self.current(), keys, index)
        else:
            self.signalsDropped.emit(keys, index)

    def _tab_menu(self, pos: QPoint) -> None:
        index = self.bar.tabAt(pos)
        menu = QMenu(self.bar)
        if index >= 0:
            menu.addAction('Rename', lambda: self.rename_tab(index))
            close = menu.addAction('Close Tab', lambda: self.close_tab(index))
            close.setEnabled(self.bar.count() > 1)
            menu.addSeparator()
        menu.addAction('New Tab', lambda: self.add_tab())
        menu.exec(self.bar.mapToGlobal(pos))

    # ── Showing a tab, and the time synchronized tabs share ──────────────

    def _show_tab(self, index: int) -> None:
        panel = self._panels.get(self.bar.tabData(index)) if index >= 0 else None
        if panel is None or panel is self._shown:
            return
        leaving = self._shown
        if leaving is not None:
            # An empty tab has no time of its own to hand on.
            view = TimeView.of(leaving)
            if view.time_range is None and self._shared is not None:
                view = replace(view, time_range=self._shared.time_range)
            self._shared = view
        if self.synchronized and self._shared is not None:
            # While it is still hidden, so it is drawn once, at that time.
            self._shared.show_in(panel)
        self.tables.setCurrentWidget(panel.table_panel)
        self.plots.setCurrentWidget(panel)
        self._shown = panel
        self.currentPanelChanged.emit(panel)

    def set_synchronized(self, synchronized: bool) -> None:
        """Whether every tab shows the time of the tab shown before it."""
        self.synchronized = bool(synchronized)

    def show_shared_time(self, panel: PlotPanel) -> None:
        """Show the synchronized time in a tab that has just had its first
        signals plotted, rather than all of their time."""
        if (self.synchronized and self.bar.count() > 1 and self._shared is not None
                and self._shared.time_range is not None):
            panel.show_time_range(*self._shared.time_range)

    def forget_shared_time(self) -> None:
        """Drop the shared time, as for another measurement."""
        self._shared = None


def _dragged_signals(mime) -> tuple[list[str], bool]:
    """The signal keys a drag carries, and whether they are rows of a signal
    table rather than signals from the signal tree."""
    for mime_type, rows in ((SignalTreeWidget.MIME_TYPE, False), (ROW_MIME_TYPE, True)):
        if mime.hasFormat(mime_type):
            payload = bytes(mime.data(mime_type)).decode('utf-8')
            return [key.strip() for key in payload.splitlines() if key.strip()], rows
    return [], False


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


class _TabBar(QTabBar):
    """The tabs, drawn by _TabStyle, which take signals dropped on them. Rows
    of the signal table drop only on a tab other than the one on screen."""

    # The keys dropped, whether they are rows of the signal table, and the tab.
    dropped = Signal(list, bool, int)

    def __init__(self) -> None:
        super().__init__()
        # Kept here: a widget does not own the style it is given.
        self._style = _TabStyle()
        self.setStyle(self._style)
        # The strip draws the line the tabs stand on.
        self.setDrawBase(False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._closable = False
        self.setAcceptDrops(True)
        self._outline = _DropOutline(self, TAB_RADIUS)

    def tabSizeHint(self, index: int) -> QSize:
        width = 2 * TAB_FLARE + TAB_PADDING + self.fontMetrics().horizontalAdvance(
            self.tabText(index))
        if self.tabButton(index, QTabBar.ButtonPosition.RightSide) is not None:
            width += 1 + CLOSE_SIZE + CLOSE_MARGIN
        else:
            width += TAB_PADDING
        return QSize(width, TAB_HEIGHT)

    def setTabsClosable(self, closable: bool) -> None:
        """Give every tab its small "x", or take them away. QTabBar's own
        close buttons are left out: they are as large as the name."""
        self._closable = bool(closable)
        for index in range(self.count()):
            button = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if self._closable and button is None:
                button = _CloseButton(self)
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

    def _drop_tab(self, event) -> int:
        keys, rows = _dragged_signals(event.mimeData())
        index = self.tabAt(event.position().toPoint())
        if not keys or index < 0 or (rows and index == self.currentIndex()):
            return -1
        return index

    def dragEnterEvent(self, event) -> None:
        # A drag move event follows at once, and finds the tab.
        if _dragged_signals(event.mimeData())[0]:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        index = self._drop_tab(event)
        if index < 0:
            self._outline.hide()
            event.ignore()
            return
        self._outline.show_at(self.tab_body(index))
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._outline.hide()

    def dropEvent(self, event) -> None:
        self._outline.hide()
        index = self._drop_tab(event)
        if index < 0:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(*_dragged_signals(event.mimeData()), index)


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
    """The row of tabs, with a thin line along its foot, from one side of the
    table and plot to the other. The selected tab's feet run into it."""

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

    def __init__(self, bar: QTabBar) -> None:
        super().__init__(bar)
        self.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip('Close tab')
        self.setAccessibleName('Close tab')
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


class _AddTabButton(QToolButton):
    """The "+" button: it adds a tab, and takes signals dropped on it into a
    new tab."""

    # The keys dropped, and whether they are rows of the signal table.
    dropped = Signal(list, bool)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self._outline = _DropOutline(self)

    def dragEnterEvent(self, event) -> None:
        if _dragged_signals(event.mimeData())[0]:
            self._outline.show_at(self.rect())
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if _dragged_signals(event.mimeData())[0]:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._outline.hide()

    def dropEvent(self, event) -> None:
        self._outline.hide()
        keys, rows = _dragged_signals(event.mimeData())
        if not keys:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(keys, rows)


class _NameEditor(QLineEdit):
    """A tab's name being edited, in place of the tab's text."""

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
