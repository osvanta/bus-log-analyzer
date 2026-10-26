# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The Insert Function window of the calculated-signal dialog: categories,
the functions and operators in the chosen one, and how to use the selected
one, from core.formula_catalog."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.formula_catalog import CATEGORY_ORDER, CatalogEntry, formula_catalog

ALL_CATEGORIES = 'All'


def entry_help_html(entry: CatalogEntry) -> str:
    """How to use one entry: what it does, its arguments, an example, and
    what catches people out."""
    parts = [f'<p><b><code>{escape(entry.signature)}</code></b></p>',
             f'<p>{escape(entry.description)}.</p>']
    if entry.arguments:
        rows = ''.join(
            f'<tr><td style="padding-right:10px"><code>{escape(name)}</code></td>'
            f'<td>{escape(text)}</td></tr>' for name, text in entry.arguments)
        parts.append(f'<table cellspacing="0" cellpadding="1">{rows}</table>')
    parts.append(f'<p><b>Example</b><br><code>{escape(entry.example)}</code></p>')
    if entry.note:
        parts.append(f'<p>{escape(entry.note)}</p>')
    if entry.wraps_selection:
        parts.append('<p><i>Tip: select part of the formula first and it becomes '
                     f'<code>{escape(entry.params[0])}</code>.</i></p>')
    return ''.join(parts)


class InsertFunctionDialog(QDialog):
    """Pick a function, operator or constant to put into a formula. Insert
    (or a double-click) emits insertRequested with it and closes."""

    insertRequested = Signal(object)   # a CatalogEntry

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Insert Function')
        self.resize(820, 440)
        self._entries = formula_catalog()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('Search functions, for example average or limit')
        self.search_edit.setClearButtonEnabled(True)

        self.category_list = QListWidget()
        for name in (ALL_CATEGORIES, *CATEGORY_ORDER):
            if name == ALL_CATEGORIES or any(e.category == name for e in self._entries):
                self.category_list.addItem(name)
        self.category_list.setMaximumWidth(190)

        self.function_list = QListWidget()
        self.function_list.setMaximumWidth(220)

        self.help_view = QTextBrowser()
        self.help_view.setOpenLinks(False)

        lists = QHBoxLayout()
        for title, widget, stretch in (('Category', self.category_list, 0),
                                       ('Function', self.function_list, 0),
                                       ('How to use', self.help_view, 1)):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(widget, stretch=1)
            lists.addLayout(column, stretch)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.insert_button = self.button_box.addButton(
            'Insert', QDialogButtonBox.ButtonRole.AcceptRole)
        self.insert_button.setDefault(True)

        layout = QVBoxLayout(self)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel('Search:'))
        search_row.addWidget(self.search_edit, stretch=1)
        layout.addLayout(search_row)
        layout.addLayout(lists, stretch=1)
        layout.addWidget(self.button_box)

        self.search_edit.textChanged.connect(self._fill_functions)
        self.category_list.currentRowChanged.connect(self._fill_functions)
        self.function_list.currentItemChanged.connect(self._show_help)
        self.function_list.itemDoubleClicked.connect(self._on_double_clicked)
        self.button_box.accepted.connect(self._insert)
        self.button_box.rejected.connect(self.reject)

        self.category_list.setCurrentRow(0)
        self.search_edit.setFocus()

    def current_entry(self) -> CatalogEntry | None:
        item = self.function_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def shown_names(self) -> list[str]:
        return [self.function_list.item(i).text() for i in range(self.function_list.count())]

    @Slot()
    def _fill_functions(self, *_args) -> None:
        """List the entries of the chosen category; while searching, of every
        category, so a match is never hidden by the category chosen."""
        needle = self.search_edit.text()
        current = self.category_list.currentItem()
        category = current.text() if current is not None else ALL_CATEGORIES
        self.function_list.clear()
        for entry in self._entries:
            if needle.strip():
                if not entry.matches(needle):
                    continue
            elif category != ALL_CATEGORIES and entry.category != category:
                continue
            item = QListWidgetItem(entry.name)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(entry.signature)
            self.function_list.addItem(item)
        if self.function_list.count():
            self.function_list.setCurrentRow(0)
        else:
            self._show_help(None)

    @Slot(object)
    def _show_help(self, item, _previous=None) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self.insert_button.setEnabled(entry is not None)
        self.help_view.setHtml(entry_help_html(entry) if entry is not None
                               else '<p>No function matches the search.</p>')

    @Slot(QListWidgetItem)
    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        self.function_list.setCurrentItem(item)
        self._insert()

    @Slot()
    def _insert(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        self.insertRequested.emit(entry)
        self.accept()
