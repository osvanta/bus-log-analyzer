# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Right-click a signal ▸ Properties: its definition, read-only, laid out like
File ▸ Measurement info — a Signal tab, a Value table tab when it has one, and
Copy for a bug report. The content comes from core.signal_properties.
"""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTabWidget, QVBoxLayout

from core.signal_properties import SignalProperties
from gui.measurement_info import info_table, tabs_as_text


class SignalPropertiesDialog(QDialog):
    def __init__(self, properties: SignalProperties, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f'Properties — {properties.title}')
        self.resize(560, 520)
        self.properties = properties
        self.tabs = QTabWidget()
        self.tabs.addTab(info_table(('Item', 'Value'), properties.rows), 'Signal')
        if properties.value_table:
            self.tabs.addTab(info_table(('Raw', 'Physical', 'Label'), properties.value_table),
                             f'Value table ({len(properties.value_table)})')

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton('Copy', QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(self.copy_all)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)

    def as_text(self) -> str:
        return tabs_as_text(self.tabs)

    def copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.as_text())
