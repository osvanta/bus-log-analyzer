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

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QWidget,
)

from gui.plot_widget import PlotPanel

TAB_NAME = 'Tab {}'
MAX_NAME_LENGTH = 40
PLOT_TYPES = ('normal', 'multi_axis', 'stacked', 'multistack')


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

        self.bar = QTabBar()
        self.bar.setMovable(True)
        self.bar.setExpanding(False)
        self.bar.setUsesScrollButtons(True)
        self.bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bar.currentChanged.connect(self._show_tab)
        self.bar.tabCloseRequested.connect(self.close_tab)
        self.bar.tabBarDoubleClicked.connect(self.rename_tab)
        self.bar.customContextMenuRequested.connect(self._tab_menu)

        self.add_button = QToolButton()
        self.add_button.setText('+')
        self.add_button.setAutoRaise(True)
        self.add_button.setToolTip('New tab')
        self.add_button.setAccessibleName('New tab')
        self.add_button.clicked.connect(lambda: self.add_tab())

        self.strip = QWidget()
        layout = QHBoxLayout(self.strip)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.bar)
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
        rect = self.bar.tabRect(index)
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
