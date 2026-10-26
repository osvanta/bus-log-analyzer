# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The plotted signals as a table: one row per moment any of them has a sample,
one column per signal, each cell the signal's latest value at that moment.

The model is virtual: a cell looks its value up when the view draws it (a
binary search in the signal's timestamps), so a table of millions of rows
holds nothing but the time column. A value carried forward from an earlier
sample is grey; one sampled at that row's time is in the text colour.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QTableView,
    QVBoxLayout, QWidget,
)

from core.export import _MAX_GRID_POINTS, _column_headers
from core.signal_store import SignalSeries

#: A held value's share of the way from the text colour to the background.
_HELD_SHADE = 0.45


class TabularModel(QAbstractTableModel):
    """Time, then one column per signal, on the union of their timestamps."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: list[SignalSeries] = []
        self._headers: list[str] = []
        self._times = np.empty(0, dtype=np.float64)
        self._stamps: list[np.ndarray] = []
        self.truncated = False
        self.held_color = None

    def set_series(self, series: list[SignalSeries]) -> None:
        self.beginResetModel()
        self._series = list(series)
        self._headers = _column_headers(self._series)
        self._stamps = [np.asarray(s.numpy_timestamps(), dtype=np.float64) for s in self._series]
        grids = [ts for ts in self._stamps if ts.size]
        times = np.unique(np.concatenate(grids)) if grids else np.empty(0, dtype=np.float64)
        # As export does: a union past the cap would cost gigabytes.
        self.truncated = times.size > _MAX_GRID_POINTS
        self._times = times[:_MAX_GRID_POINTS]
        self.endResetModel()

    @property
    def times(self) -> np.ndarray:
        return self._times

    def row_at(self, t: float) -> int | None:
        """The last row at or before *t*; the first row if *t* is before it."""
        if not self._times.size:
            return None
        return max(int(np.searchsorted(self._times, t, side='right')) - 1, 0)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else int(self._times.size)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._series) + 1

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if section == 0:
                return 'Time s'
            series = self._series[section - 1]
            name = self._headers[section - 1]
            return f'{name} [{series.unit}]' if series.unit else name
        if role == Qt.ItemDataRole.ToolTipRole and section > 0:
            return self._series[section - 1].key
        return None

    def _sample(self, row: int, col: int) -> int | None:
        ts = self._stamps[col - 1]
        index = int(np.searchsorted(ts, self._times[row], side='right')) - 1
        return index if index >= 0 else None

    def cell_text(self, row: int, col: int) -> str:
        if col == 0:
            return f'{self._times[row]:.6f}'
        index = self._sample(row, col)
        if index is None:
            return ''
        value = self._series[col - 1].display_value_at(index)
        if isinstance(value, str):
            return value
        try:
            return f'{float(value):.6g}'
        except (TypeError, ValueError):
            return str(value)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.cell_text(row, col)
        if role == Qt.ItemDataRole.TextAlignmentRole and col > 0:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and col > 0 and self.held_color is not None:
            sample = self._sample(row, col)
            if sample is not None and self._stamps[col - 1][sample] != self._times[row]:
                return self.held_color
        return None


class TabularView(QWidget):
    """The table, with a "Go to time" box. A click on a row asks for Cursor 1
    there (*timeClicked*); select_time() follows Cursor 1 without asking."""

    timeClicked = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = TabularModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(
            self.table.fontMetrics().height() + 6)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.clicked.connect(self._on_clicked)

        self.go_to = QLineEdit()
        self.go_to.setPlaceholderText('Go to time, s')
        self.go_to.setMaximumWidth(140)
        self.go_to.returnPressed.connect(self._on_go_to)
        self.summary = QLabel()
        # It wraps, so a narrow table beside the plot is not held wider by it.
        self.summary.setWordWrap(True)
        top = QHBoxLayout()
        top.setContentsMargins(4, 2, 4, 2)
        top.addWidget(self.go_to)
        top.addWidget(self.summary, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, activated=self.copy_selection)

    def set_series(self, series: list[SignalSeries]) -> None:
        palette = self.palette()
        text, base = palette.text().color(), palette.base().color()
        self.model.held_color = QColor(
            *(round(t + (b - t) * _HELD_SHADE) for t, b in (
                (text.red(), base.red()), (text.green(), base.green()),
                (text.blue(), base.blue()))))
        self.model.set_series(series)
        rows = self.model.rowCount()
        note = (f'{rows:,} rows, the first {rows:,} only' if self.model.truncated
                else f'{rows:,} rows')
        self.summary.setText(f'  {note} · grey: value held from an earlier sample'
                             if rows else '  Plot signals to see them as a table.')
        self.table.resizeColumnsToContents()

    def select_time(self, t: float) -> None:
        """Select and show the row at time *t* (without asking for a cursor)."""
        row = self.model.row_at(t)
        if row is None:
            return
        index = self.model.index(row, 0)
        self.table.selectRow(row)
        self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _on_clicked(self, index) -> None:
        if index.isValid():
            self.timeClicked.emit(float(self.model.times[index.row()]))

    def _on_go_to(self) -> None:
        try:
            t = float(self.go_to.text().replace(',', '.'))
        except ValueError:
            return
        row = self.model.row_at(t)
        if row is None:
            return
        self.select_time(t)
        self.timeClicked.emit(float(self.model.times[row]))

    def copy_selection(self) -> None:
        """The selected rows, tab-separated with the header, for a spreadsheet."""
        rows = sorted({index.row() for index in self.table.selectionModel().selectedIndexes()})
        if not rows:
            return
        columns = range(self.model.columnCount())
        lines = ['\t'.join(str(self.model.headerData(c, Qt.Orientation.Horizontal))
                           for c in columns)]
        lines += ['\t'.join(self.model.cell_text(r, c) for c in columns) for r in rows]
        QGuiApplication.clipboard().setText('\n'.join(lines))
