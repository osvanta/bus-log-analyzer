# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
File ▸ Measurement info: what the open measurement is — the file, its
recording, its channels and their databases, what was decoded — and, for
MF4, its header, events and attachments.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from core.bus_types import channel_label, sort_key
from core.measurement_info import FileInfo, save_attachment


def _size(n: int) -> str:
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:,} {unit}' if unit == 'bytes' else f'{n:.1f} {unit}'
        n /= 1024
    return str(n)


def _when(value) -> str:
    return value.strftime('%Y-%m-%d %H:%M:%S') if value else ''


def _table(headers, rows) -> QTableWidget:
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(str(value)))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def general_rows(info: FileInfo, store, channel_config) -> list[tuple[str, str]]:
    """The General tab: the file, the recording, what was decoded."""
    rows = [('File', info.path.name), ('Folder', str(info.path.parent)),
            ('Format', info.format), ('Size', _size(info.size_bytes)),
            ('Modified', _when(info.modified))]
    if info.start:
        rows.append(('Recording started', _when(info.start)))
    if info.stop:
        rows.append(('Recording stopped', _when(info.stop)))
    if store is not None:
        span = _store_span(store)
        if span is not None:
            rows.append(('Duration', f'{span:.3f} s'))
        frames = int(getattr(store, 'total_frames', 0) or 0)
        if frames:
            rows.append(('Frames', f'{frames:,}'))
        rows.append(('Decoded signals', f'{len(store.all_keys()):,}'))
    name = getattr(channel_config, 'name', '') if channel_config is not None else ''
    if name:
        rows.append(('Channel configuration', name))
    return rows


def _store_span(store) -> float | None:
    end = 0.0
    found = False
    for key in store.all_keys():
        series = store.get_series(key)
        if series is not None and len(series.timestamps):
            end = max(end, float(series.timestamps[-1]))
            found = True
    return end if found else None


def channel_rows(store, channel_config) -> list[tuple[str, str, str]]:
    """The Channels tab: each channel, its frames, its database."""
    if store is None:
        return []
    counts = dict(getattr(store, 'channel_frame_counts', {}) or {})
    keys = set(getattr(store, 'channels', set()) or set()) | set(counts)
    rows = []
    for key in sorted((k for k in keys if k is not None), key=sort_key):
        database = ''
        if channel_config is not None:
            path = channel_config.dbc_path_for(*key)
            database = Path(path).name if path else ''
        frames = counts.get(key)
        rows.append((channel_label(key), f'{frames:,}' if frames else '', database))
    return rows


class MeasurementInfoDialog(QDialog):
    def __init__(self, info: FileInfo, store=None, channel_config=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Measurement info')
        self.resize(640, 440)
        self.info = info
        self.tabs = QTabWidget()
        self.general = _table(('Item', 'Value'), general_rows(info, store, channel_config))
        self.tabs.addTab(self.general, 'General')
        channels = channel_rows(store, channel_config)
        if channels:
            self.tabs.addTab(_table(('Channel', 'Frames', 'Database'), channels), 'Channels')
        if info.header:
            self.tabs.addTab(_table(('Item', 'Value'), info.header), 'Header')
        if info.events:
            self.tabs.addTab(_table(('Time s', 'Name', 'Comment'),
                                    [(f'{e.time_s:.6f}', e.name, e.comment) for e in info.events]),
                             f'Events ({len(info.events)})')
        self.attachments = None
        if info.attachments:
            self.attachments = _table(
                ('Name', 'Comment', 'Stored'),
                [(a.file_name, a.comment, 'in the file' if a.embedded else 'linked')
                 for a in info.attachments])
            page = QWidget()
            column = QVBoxLayout(page)
            column.addWidget(self.attachments)
            save = QPushButton('Save attachment…')
            save.clicked.connect(self.save_selected_attachment)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(save)
            column.addLayout(row)
            self.tabs.addTab(page, f'Attachments ({len(info.attachments)})')
        for note in info.notes:
            self.general.insertRow(self.general.rowCount())
            self.general.setItem(self.general.rowCount() - 1, 0, QTableWidgetItem('Note'))
            self.general.setItem(self.general.rowCount() - 1, 1, QTableWidgetItem(note))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton('Copy', QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(self.copy_all)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)

    def as_text(self) -> str:
        """Every tab, as plain text: a title, then its rows tab-separated."""
        lines = []
        for t in range(self.tabs.count()):
            table = self.tabs.widget(t)
            if not isinstance(table, QTableWidget):
                table = table.findChild(QTableWidget)
            lines.append(f'[{self.tabs.tabText(t)}]')
            for r in range(table.rowCount()):
                lines.append('\t'.join(table.item(r, c).text() if table.item(r, c) else ''
                                       for c in range(table.columnCount())))
            lines.append('')
        return '\n'.join(lines).rstrip() + '\n'

    def copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.as_text())

    def save_selected_attachment(self) -> None:
        rows = self.attachments.selectionModel().selectedRows() if self.attachments else []
        index = rows[0].row() if rows else 0
        attachment = self.info.attachments[index]
        target, _ = QFileDialog.getSaveFileName(self, 'Save attachment',
                                                Path(attachment.file_name).name)
        if not target:
            return
        try:
            save_attachment(self.info.path, attachment.index, target)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Attachment not saved', str(exc))

