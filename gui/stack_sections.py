# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
MultiStack: the signal table split into one section per stack.

In MultiStack mode the plot panel's signal table shrinks to its header, and
under it every stack gets a section level with the stack's plot area and as
tall as it, listing only that stack's signals. A section is a view of the
table's own model, filtered to its stack's rows: every value the table holds
(cursor values, unit, statistics, ...) shows in it, and a click on a box
changes the table's own item, so the panel handles it as before. The table's
header stays the one header: its column menu, widths and order apply to every
section, and one scroll bar under the sections scrolls them all sideways.

A thin line across the whole width marks where one stack meets the next, so
each list reads as its stack's. A section with more signals than fit its
height scrolls on its own. Signals of a stack whose signals are all unchecked
have no plot row; they are listed in a last section under the last stack,
level with the time axis.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QItemSelection, QItemSelectionModel, QMimeData, QPointF, \
    QRect, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QApplication, QFrame, QScrollBar, QTableView, \
    QWidget

from gui.signal_tree import SignalTreeWidget

# The table's columns holding a box: the signal's and its axis's.
_BOX_COLUMNS = (0, 6)


class _StackRowsFilter(QSortFilterProxyModel):
    """The table's rows whose signal (column 1's key) is one of *keys*, in
    the table's order."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._keys: frozenset[str] = frozenset()

    def keys(self) -> frozenset[str]:
        return self._keys

    def set_keys(self, keys) -> None:
        keys = frozenset(keys)
        if keys != self._keys:
            self.beginFilterChange()
            self._keys = keys
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # noqa: N802
        index = self.sourceModel().index(source_row, 1, source_parent)
        return index.data(Qt.ItemDataRole.UserRole) in self._keys


class StackSection(QTableView):
    """One stack's part of the signal table: its rows of the table's model."""

    def __init__(self, area: 'StackSectionArea', stack_id: int | None) -> None:
        super().__init__(area)
        self.setObjectName('stackSection')
        self._area = area
        #: The stack it lists, or None for the signals of hidden stacks.
        self.stack_id = stack_id
        table = area.table
        self.filter = _StackRowsFilter(self)
        self.filter.setSourceModel(table.model())
        self.setModel(self.filter)
        self.horizontalHeader().hide()
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(table.verticalHeader().defaultSectionSize())
        self.verticalHeader().setMinimumSectionSize(table.verticalHeader().minimumSectionSize())
        self.horizontalHeader().setMinimumSectionSize(0)
        self.setShowGrid(table.showGrid())
        self.setWordWrap(table.wordWrap())
        self.setEditTriggers(table.editTriggers())
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._delegates = [area.make_delegate(self) for _ in _BOX_COLUMNS]
        for column, delegate in zip(_BOX_COLUMNS, self._delegates):
            self.setItemDelegateForColumn(column, delegate)
        self.selectionModel().selectionChanged.connect(
            lambda *_args: area.section_selection_changed(self))
        # Where a drag would land, as the table shows it.
        self._drop_line = QFrame(self.viewport())
        self._drop_line.setFixedHeight(2)
        self._drop_line.setStyleSheet('background-color: #3d9ef0;')
        self._drop_line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._drop_line.hide()
        self.mirror_columns()

    # ── What it lists ────────────────────────────────────────────────────

    def key_at(self, row: int) -> str | None:
        """The signal on the section's *row*, or None."""
        key = self.filter.index(row, 1).data(Qt.ItemDataRole.UserRole)
        return key if isinstance(key, str) else None

    def keys(self) -> list[str]:
        """The section's signals, top to bottom."""
        return [k for k in (self.key_at(row) for row in range(self.filter.rowCount())) if k]

    def source_rows(self) -> set[int]:
        return {self.filter.mapToSource(self.filter.index(row, 0)).row()
                for row in range(self.filter.rowCount())}

    # ── Looking like the table ──────────────────────────────────────────

    def mirror_columns(self) -> None:
        """The table's column choices, widths, order and sideways scroll."""
        table = self._area.table
        source, own = table.horizontalHeader(), self.horizontalHeader()
        for column in range(table.columnCount()):
            self.setColumnHidden(column, table.isColumnHidden(column))
            if self.columnWidth(column) != table.columnWidth(column):
                self.setColumnWidth(column, table.columnWidth(column))
        for visual in range(table.columnCount()):
            logical = source.logicalIndex(visual)
            if own.visualIndex(logical) != visual:
                own.moveSection(own.visualIndex(logical), visual)
        self.follow_scroll(table.horizontalScrollBar().value())

    def follow_scroll(self, value: int) -> None:
        self.horizontalScrollBar().setValue(value)

    # ── Mouse: boxes as in the table ────────────────────────────────────

    def _box_at(self, pos) -> bool:
        index = self.indexAt(pos.toPoint())
        if not (index.isValid() and index.column() in _BOX_COLUMNS
                and index.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return False
        delegate = self.itemDelegateForColumn(index.column())
        return delegate.hits_box(self.visualRect(index), index, pos)

    # A box applies to every selected signal when the clicked one is among
    # them: the delegate takes the press on a box, so the selection is still
    # whole when the panel looks at it (PlotPanel._checkbox_targets).

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        on_box = self._box_at(event.position())
        panel = self._area.panel
        keep = panel.selected_keys() if on_box else None
        super().mouseReleaseEvent(event)
        if keep is not None:
            # A click on a box leaves the selection as it was.
            panel._restore_selection(keep)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Two quick clicks on a box tick and untick it."""
        index = self.indexAt(event.position().toPoint())
        if (event.button() == Qt.MouseButton.LeftButton and index.isValid()
                and index.column() in _BOX_COLUMNS):
            self.mousePressEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def _show_menu(self, position) -> None:
        panel = self._area.panel
        index = self.indexAt(position)
        key = self.key_at(index.row()) if index.isValid() else None
        keys = panel.selected_keys()
        if key and key not in keys:
            keys = [key]
        panel._show_signal_menu(keys, self.viewport().mapToGlobal(position))

    # ── Drag and drop ───────────────────────────────────────────────────

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        """Drag the selected signals as the table does (the drop puts them
        in plot order)."""
        panel = self._area.panel
        keys = panel.selected_keys()
        if not keys:
            return
        mime = QMimeData()
        mime.setData(panel.table._ROW_REORDER_MIME,
                     QByteArray('\n'.join(keys).encode('utf-8')))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def _accepts(self, mime) -> bool:
        return self.stack_id is not None and (
            mime.hasFormat(self._area.panel.table._ROW_REORDER_MIME)
            or mime.hasFormat(SignalTreeWidget.MIME_TYPE))

    def drop_row(self, y: float) -> int:
        """The section row a drop at *y* lands before (rowCount: at the end)."""
        row = self.rowAt(int(y))
        if row < 0:
            return self.filter.rowCount() if y >= 0 else 0
        rect = self.visualRect(self.filter.index(row, 0))
        return row + 1 if y > rect.center().y() else row

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not self._accepts(event.mimeData()):
            event.ignore()
            self._drop_line.hide()
            return
        event.acceptProposedAction()
        row = self.drop_row(event.position().y())
        if row < self.filter.rowCount():
            y = self.visualRect(self.filter.index(row, 0)).top()
        elif row:
            y = self.visualRect(self.filter.index(row - 1, 0)).bottom()
        else:
            y = 0
        self._drop_line.setGeometry(0, max(0, y - 1), self.viewport().width(), 2)
        self._drop_line.show()
        self._drop_line.raise_()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drop_line.hide()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._drop_line.hide()
        mime = event.mimeData()
        if not self._accepts(mime):
            event.ignore()
            return
        panel = self._area.panel
        row = self.drop_row(event.position().y())
        before = self.key_at(row) if row < self.filter.rowCount() else None
        reorder = panel.table._ROW_REORDER_MIME
        if mime.hasFormat(reorder):
            keys = [k for k in bytes(mime.data(reorder)).decode('utf-8').splitlines() if k]
            event.acceptProposedAction()
            panel.drop_on_stack(keys, self.stack_id, before)
        else:
            payload = bytes(mime.data(SignalTreeWidget.MIME_TYPE)).decode('utf-8')
            keys = [k.strip() for k in payload.splitlines() if k.strip()]
            if not keys:
                event.ignore()
                return
            event.acceptProposedAction()
            panel.signalDroppedToStack.emit(keys, self.stack_id)


class _StackLines(QWidget):
    """The lines between the stacks, drawn over the sections they separate."""

    def __init__(self, area: 'StackSectionArea') -> None:
        super().__init__(area)
        self._area = area
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, event) -> None:  # noqa: N802
        """Each the same whole number of screen pixels thick: a widget one
        pixel tall would cover two or three screen pixels at 225 % scaling,
        depending on where it falls."""
        area = self._area
        if not area._line_ys:
            return
        painter = QPainter(self)
        pen = QPen(area._line_color)
        pen.setCosmetic(True)       # its width in screen pixels
        pen.setWidth(max(1, round(painter.device().devicePixelRatioF())))
        painter.setPen(pen)
        for y in area._line_ys:
            painter.drawLine(QPointF(0, y + 0.5), QPointF(self.width(), y + 0.5))
        painter.end()


class StackSectionArea(QWidget):
    """The sections, each placed where its stack is."""

    def __init__(self, panel, delegate_factory, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName('stackSections')
        self.panel = panel
        self.table = panel.table
        self.make_delegate = delegate_factory
        self._sections: dict[int, StackSection] = {}
        self._hidden: StackSection | None = None
        self._syncing = False
        # One sideways scroll bar for every section, under them all.
        self.scroll_bar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.scroll_bar.hide()
        source = self.table.horizontalScrollBar()
        source.rangeChanged.connect(self._follow_range)
        source.valueChanged.connect(self._follow_scroll)
        self.scroll_bar.valueChanged.connect(source.setValue)
        self.table.horizontalHeader().sectionResized.connect(
            lambda *_args: self.mirror_columns())
        self.table.itemSelectionChanged.connect(self.sync_from_table)
        self._layout: list[tuple[int, list[str], int, int]] = []
        self._hidden_keys: list[str] = []
        # A thin line across the whole width where one stack meets the next,
        # level with the gap between their plots: each list reads as its
        # stack's. The sections meet at the lines, which are drawn over them;
        # coloured by the panel's theme (set_line_color).
        self._line_ys: list[int] = []
        self._line_color = QColor('#808080')
        self._lines = _StackLines(self)

    # ── For the panel ───────────────────────────────────────────────────

    def sections(self) -> dict[int, StackSection]:
        """The section of each stack, by stack ID, top to bottom."""
        return dict(self._sections)

    def hidden_section(self) -> StackSection | None:
        """The section of signals whose stacks are hidden, while there are any."""
        return self._hidden if self._hidden is not None and self._hidden_keys else None

    def set_stacks(self, stacks, hidden_keys) -> None:
        """Lay the sections out: *stacks* is [(stack ID, its signals, top,
        height)] in this widget's coordinates, top to bottom; *hidden_keys*
        the signals of stacks with no plot row."""
        self._layout = [(sid, list(keys), int(top), int(height))
                        for sid, keys, top, height in stacks]
        self._hidden_keys = list(hidden_keys)
        wanted = {sid for sid, *_rest in self._layout}
        for sid in [s for s in self._sections if s not in wanted]:
            self._drop(self._sections.pop(sid))
        for sid, keys, _top, _height in self._layout:
            section = self._sections.get(sid)
            if section is None:
                section = self._sections[sid] = StackSection(self, sid)
            section.filter.set_keys(keys)
        # Top to bottom, as the stacks.
        self._sections = {sid: self._sections[sid] for sid, *_rest in self._layout}
        if self._hidden_keys:
            if self._hidden is None:
                self._hidden = StackSection(self, None)
            self._hidden.filter.set_keys(self._hidden_keys)
        elif self._hidden is not None:
            self._drop(self._hidden)
            self._hidden = None
        self._place()
        self.sync_from_table()

    def clear(self) -> None:
        self.set_stacks([], [])

    def _drop(self, section: StackSection) -> None:
        # Hidden now, deleted later: the click that removed its stack may
        # still be running in it.
        section.hide()
        section.deleteLater()

    def _place(self) -> None:
        width = self.width()
        bar = self.scroll_bar.sizeHint().height() if self.scroll_bar.isVisible() else 0
        bottom = self.height() - bar
        layout = self._layout
        # Where one stack ends and the next begins: the middle of the gap
        # between their plot areas; then the top of the hidden stacks' list.
        lines = [(above_top + above_height + below_top) // 2
                 for (_s, _k, above_top, above_height), (_t, _l, below_top, _h)
                 in zip(layout, layout[1:])]
        if self._hidden is not None and layout:
            lines.append(layout[-1][2] + layout[-1][3] + 2)
        # The sections meet at the lines: each runs from the line above it
        # (the first from right under the header, as the whole table's first
        # row does) to the line below it, so a line closes the one above and
        # opens the one below, with no strip between the line and a row.
        for index, (sid, _keys, top, height) in enumerate(layout):
            upper = 0 if index == 0 else max(0, lines[index - 1])
            lower = min(lines[index] if index < len(lines) else top + height, bottom)
            section = self._sections[sid]
            section.setGeometry(QRect(0, upper, width, max(0, lower - upper)))
            section.setVisible(lower > upper)
        if self._hidden is not None:
            upper = lines[-1] if layout else 0
            self._hidden.setGeometry(QRect(0, upper, width, max(0, bottom - upper)))
            self._hidden.setVisible(bottom > upper)
        self._line_ys = [y for y in lines if 0 <= y < bottom]
        self._lines.setGeometry(0, 0, width, max(0, bottom))
        self._lines.raise_()
        self._lines.update()
        if bar:
            self.scroll_bar.setGeometry(0, bottom, width, bar)

    def set_line_color(self, color) -> None:
        self._line_color = QColor(color)
        self._lines.update()

    def separators(self) -> list[QRect]:
        """Where the lines between the stacks are drawn."""
        return [QRect(0, y, self.width(), 1) for y in self._line_ys]

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.panel._schedule_stack_sections()

    def _all(self) -> list[StackSection]:
        return list(self._sections.values()) + ([self._hidden] if self._hidden else [])

    # ── Columns and sideways scroll ─────────────────────────────────────

    def mirror_columns(self) -> None:
        for section in self._all():
            section.mirror_columns()

    def _follow_range(self, low: int, high: int) -> None:
        self.scroll_bar.setRange(low, high)
        self.scroll_bar.setPageStep(self.table.horizontalScrollBar().pageStep())
        shown = high > low and self.isVisible()
        if shown != self.scroll_bar.isVisible():
            self.scroll_bar.setVisible(shown)
            self._place()

    def _follow_scroll(self, value: int) -> None:
        self.scroll_bar.setValue(value)
        for section in self._all():
            section.follow_scroll(value)

    # ── Selection: the table's, shown in every section ──────────────────

    def section_selection_changed(self, section: StackSection) -> None:
        """A selection made in *section* becomes the table's. With Ctrl or
        Shift held, what other sections had selected stays selected."""
        if self._syncing:
            return
        table = self.table
        chosen = {section.filter.mapToSource(index).row()
                  for index in section.selectionModel().selectedRows()}
        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            kept = {index.row() for index in table.selectionModel().selectedRows()}
            chosen |= kept - section.source_rows()
        selection = QItemSelection()
        model = table.model()
        last = model.columnCount() - 1
        for row in sorted(chosen):
            selection.select(model.index(row, 0), model.index(row, last))
        self._syncing = True
        try:
            selection_model = table.selectionModel()
            current = section.currentIndex()
            if current.isValid():
                selection_model.setCurrentIndex(
                    section.filter.mapToSource(current),
                    QItemSelectionModel.SelectionFlag.NoUpdate)
            selection_model.select(selection,
                                   QItemSelectionModel.SelectionFlag.ClearAndSelect)
        finally:
            self._syncing = False
        self.sync_from_table(skip=section)

    def sync_from_table(self, skip: StackSection | None = None) -> None:
        """Show the table's selection in every section."""
        if self._syncing:
            return
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        self._syncing = True
        try:
            for section in self._all():
                if section is skip:
                    continue
                selection = QItemSelection()
                proxy = section.filter
                last = proxy.columnCount() - 1
                for row in range(proxy.rowCount()):
                    if proxy.mapToSource(proxy.index(row, 0)).row() in selected:
                        selection.select(proxy.index(row, 0), proxy.index(row, last))
                section.selectionModel().select(
                    selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        finally:
            self._syncing = False
