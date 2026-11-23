# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

import re

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QAction, QDrag, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.bus_types import channel_label, sort_key, store_key_prefix
from gui.panel_area import GUTTER, Card, HeaderBand
from gui.tab_drawing import TAB_HEIGHT

#: Room round the two cards where nothing lines them up: the window's margin.
MARGIN = 6


def search_pattern(text: str) -> re.Pattern[str] | None:
    """What the search box finds, or None when it is empty.

    The text can be anywhere in a name, in any case. ``*`` stands for any
    run of characters and ``?`` for one, so ``veh*speed`` finds
    ``WheelBasedVehicleSpeed``; every other character means itself.
    """
    text = text.strip()
    if not text:
        return None
    regex = ''.join(
        '.*' if char == '*' else '.' if char == '?' else re.escape(char)
        for char in text
    )
    return re.compile(regex, re.IGNORECASE | re.DOTALL)


class SignalTree(QTreeWidget):
    MIME_TYPE = "application/x-blfviewer-signal-key"

    def startDrag(self, supportedActions: Qt.DropActions) -> None:  # pragma: no cover - Qt hook
        items = self.selectedItems()
        keys = []
        for item in items:
            key = item.data(0, Qt.ItemDataRole.UserRole)
            if key:
                keys.append(str(key))
        if not keys:
            return
        mime = QMimeData()
        mime.setData(self.MIME_TYPE, "\n".join(keys).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class _Heading(QLabel):
    """A heading in a band: one line, cut short with "…" where the room ends,
    the whole of it in its tooltip. It never holds the panel wider."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        return QSize(1, super().minimumSizeHint().height())

    def shown_text(self) -> str:
        """The text as it fits."""
        return self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                             max(self.contentsRect().width(), 1))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), int(self.alignment()), self.shown_text())
        painter.end()


class SignalTreeWidget(QWidget):
    """The decoded signals in two cards, drawn as the plot tab's panels are
    (gui.panel_area): the head, the panel's name over the search box; and
    the tree, headed by a band with its heading, Collapse and Expand. In the
    window the head is the dock's title row, so the name still drags the
    panel, and the window lines both cards up with the plot area
    (place_cards)."""

    signalActivated = Signal(list)
    generatedRenameRequested = Signal(str)
    generatedEditRequested = Signal(str)
    generatedDeleteRequested = Signal(str)
    propertiesRequested = Signal(str)
    MIME_TYPE = SignalTree.MIME_TYPE
    _GENERATED_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._payload: dict[int | None, dict[str, list[str]]] = {}
        self._generated_signals: list[tuple[str, str, str]] = []
        self._messages_expanded = True

        self.search_edit = QLineEdit()
        self.search_edit.setFrame(False)
        # As high as its card leaves it, so the card fits the room the
        # window gives it.
        self.search_edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Preferred)
        self.search_edit.setPlaceholderText("Search signals... (* and ? wildcards)")
        self.search_edit.setToolTip(
            "Finds the text anywhere in a signal name.\n"
            "* stands for any text, ? for one character:\n"
            "veh*speed finds WheelBasedVehicleSpeed."
        )

        self.collapse_messages_button = QToolButton()
        self.collapse_messages_button.setAutoRaise(True)
        self.collapse_messages_button.setText("Collapse")
        self.collapse_messages_button.setToolTip(
            "Collapse all messages — show message names only"
        )
        self.collapse_messages_button.setAccessibleName("Collapse all messages")
        self.collapse_messages_button.setEnabled(False)
        self.collapse_messages_button.clicked.connect(self.collapse_messages)

        self.expand_messages_button = QToolButton()
        self.expand_messages_button.setAutoRaise(True)
        self.expand_messages_button.setText("Expand")
        self.expand_messages_button.setToolTip(
            "Expand all messages — show signals"
        )
        self.expand_messages_button.setAccessibleName("Expand all messages")
        self.expand_messages_button.setEnabled(False)
        self.expand_messages_button.clicked.connect(self.expand_messages)

        self.tree = SignalTree()
        self.tree.setFrameShape(QFrame.Shape.NoFrame)   # its card is its edge
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.setDragEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        # The head: a card with the panel's name in its band, the search box
        # under it.
        self.title_label = QLabel("Decoded Signals")
        self.title_band = HeaderBand()
        title_row = QHBoxLayout(self.title_band)
        title_row.setContentsMargins(6, 0, 6, 0)
        title_row.addWidget(self.title_label)
        title_row.addStretch(1)
        self.search_card = Card()
        self.search_card.content.addWidget(self.title_band)
        self.search_card.content.addWidget(self.search_edit, stretch=1)
        self.head = QWidget()
        self._head_layout = QVBoxLayout(self.head)
        self._head_layout.setSpacing(0)
        self._head_layout.addWidget(self.search_card)

        # The tree: a card headed by a band with its heading, then Collapse
        # and Expand at the band's right end, as a panel's controls are.
        self.tree_title = _Heading("Channels / Messages / Signals")
        self.tree_band = HeaderBand()
        band_row = QHBoxLayout(self.tree_band)
        band_row.setContentsMargins(6, 0, 2, 0)
        band_row.setSpacing(2)
        band_row.addWidget(self.tree_title, stretch=1)
        band_row.addWidget(self.collapse_messages_button)
        band_row.addWidget(self.expand_messages_button)
        self.tree_card = Card()
        self.tree_card.content.addWidget(self.tree_band)
        self.tree_card.content.addWidget(self.tree, stretch=1)
        # The room above and below the tree's card: the window sets it so the
        # card lines up with the plot area's cards.
        self.body = QWidget()
        self._body_layout = QVBoxLayout(self.body)
        self._body_layout.setSpacing(0)
        self._tree_gap = QWidget()
        self._below = QWidget()
        self._body_layout.addWidget(self._tree_gap)
        self._body_layout.addWidget(self.tree_card, stretch=1)
        self._body_layout.addWidget(self._below)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.head)
        layout.addWidget(self.body, stretch=1)
        self.place_cards()

        self.search_edit.textChanged.connect(self.apply_filter)

        self._collapse_shortcut = QShortcut(QKeySequence("Ctrl+Shift+-"), self)
        self._collapse_shortcut.activated.connect(self.collapse_messages)
        self._expand_shortcut = QShortcut(QKeySequence("Ctrl+Shift++"), self)
        self._expand_shortcut.activated.connect(self.expand_messages)

    def place_cards(self, left: int = MARGIN, right: int = MARGIN, head_top: int = MARGIN,
                    head_height: int = 0, tree_gap: int = GUTTER, below: int = MARGIN,
                    band_height: int = 0) -> None:
        """Where the two cards lie: the room at either side of them and
        above the head's card, the head card's height (0: its own), the gap
        above the tree's card and the room below it, and the height of the
        tree's band (0: its own). The window lines the cards up with the plot
        area's through this; on its own the panel has an even margin."""
        def margins(layout, *values) -> None:
            now = layout.contentsMargins()
            if (now.left(), now.top(), now.right(), now.bottom()) != values:
                layout.setContentsMargins(*values)

        def height(widget, value: int, minimum: int) -> None:
            if value > 0:
                if (widget.minimumHeight(), widget.maximumHeight()) != (value, value):
                    widget.setFixedHeight(value)
            elif (widget.minimumHeight(), widget.maximumHeight()) != (minimum, 16777215):
                widget.setMinimumHeight(minimum)
                widget.setMaximumHeight(16777215)    # QWIDGETSIZE_MAX

        margins(self._head_layout, left, head_top, right, 0)
        margins(self._body_layout, left, 0, right, 0)
        height(self.search_card, head_height, 0)
        for gap, value in ((self._tree_gap, tree_gap), (self._below, below)):
            if (gap.minimumHeight(), gap.maximumHeight()) != (value, value):
                gap.setFixedHeight(max(0, value))
        height(self.tree_band, band_height, TAB_HEIGHT)

    def set_payload(self, payload: dict[int | None, dict[str, list[str]]]) -> None:
        self._payload = payload
        self.rebuild_tree()

    def set_generated_signals(self, signals: list[tuple[str, str, str]]) -> None:
        """Set ``(key, name, tooltip)`` rows shown under Calculated Signals."""
        self._generated_signals = list(signals)
        self.rebuild_tree()

    def rebuild_tree(self) -> None:
        self.tree.clear()
        pattern = search_pattern(self.search_edit.text())

        generated_root = QTreeWidgetItem(["Calculated Signals"])
        generated_root.setData(0, self._GENERATED_ROLE, True)
        for key, name, tooltip in self._generated_signals:
            if pattern and not pattern.search(f"{name} {tooltip}"):
                continue
            signal_item = QTreeWidgetItem([name])
            signal_item.setData(0, Qt.ItemDataRole.UserRole, key)
            signal_item.setData(0, self._GENERATED_ROLE, True)
            signal_item.setToolTip(0, tooltip)
            generated_font = signal_item.font(0)
            generated_font.setItalic(True)
            signal_item.setFont(0, generated_font)
            generated_root.addChild(signal_item)
        if generated_root.childCount():
            self.tree.addTopLevelItem(generated_root)
            generated_root.setExpanded(True)

        for channel, message_map in sorted(
            self._payload.items(), key=lambda item: sort_key(item[0])
        ):
            # Display text and signal key deliberately differ. The key is
            # persisted in saved configs and must keep its historical "CH1"
            # form for CAN, while the tree shows the friendlier "CAN 1" so a
            # measurement carrying both buses is readable.
            display_text = channel_label(channel)
            key_prefix = store_key_prefix(channel)
            channel_item = QTreeWidgetItem([display_text])
            added_channel = False
            for message_name, signals in sorted(message_map.items()):
                message_item = QTreeWidgetItem([message_name])
                added_message = False
                for signal_name in sorted(signals):
                    if pattern and not pattern.search(signal_name):
                        continue
                    signal_item = QTreeWidgetItem([signal_name])
                    signal_item.setData(0, Qt.ItemDataRole.UserRole, f"{key_prefix}::{message_name}::{signal_name}")
                    signal_item.setToolTip(0, "Double-click, right-click, Ctrl/Shift-select, or drag this signal to the plot area")
                    message_item.addChild(signal_item)
                    added_message = True
                if added_message:
                    channel_item.addChild(message_item)
                    added_channel = True
            if added_channel:
                self.tree.addTopLevelItem(channel_item)
                channel_item.setExpanded(True)
        # Keep the accepted initial load behaviour: channels and messages are
        # expanded after the payload is built.  A search temporarily expands
        # its matching messages; clearing it restores the user's chosen mode.
        if pattern or self._messages_expanded:
            self.tree.expandToDepth(1)

        has_messages = any(
            not self.tree.topLevelItem(index).data(0, self._GENERATED_ROLE)
            for index in range(self.tree.topLevelItemCount())
        )
        self.collapse_messages_button.setEnabled(has_messages)
        self.expand_messages_button.setEnabled(has_messages)

    def apply_filter(self) -> None:
        self.rebuild_tree()

    def collapse_messages(self) -> None:
        """Show channel/message names while hiding all signal children."""
        self._set_messages_expanded(False)

    def expand_messages(self) -> None:
        """Expand every message under every visible channel."""
        self._set_messages_expanded(True)

    def _set_messages_expanded(self, expanded: bool) -> None:
        """Update existing message items in place; never rebuild the payload."""
        self._messages_expanded = expanded
        scroll_bar = self.tree.verticalScrollBar()
        old_scroll_value = scroll_bar.value()

        self.tree.setUpdatesEnabled(False)
        try:
            for channel_index in range(self.tree.topLevelItemCount()):
                channel_item = self.tree.topLevelItem(channel_index)
                channel_item.setExpanded(True)
                if channel_item.data(0, self._GENERATED_ROLE):
                    continue
                for message_index in range(channel_item.childCount()):
                    channel_item.child(message_index).setExpanded(expanded)
        finally:
            self.tree.setUpdatesEnabled(True)

        scroll_bar.setValue(min(old_scroll_value, scroll_bar.maximum()))
        self.tree.viewport().update()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        keys = self.selected_signal_keys() or [self._item_key(item)]
        keys = [key for key in keys if key]
        if keys:
            self.signalActivated.emit(keys)

    def _show_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        key = self._item_key(item)
        keys = self.selected_signal_keys()
        if not key and not keys:
            return
        if key and key not in keys:
            keys = [key]
        menu = self._build_context_menu(item, key, keys)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _build_context_menu(
        self,
        item: QTreeWidgetItem | None,
        key: str | None,
        keys: list[str],
    ) -> QMenu:
        menu = QMenu(self.tree)
        plot_action = QAction(f"Plot selected signal(s) ({len(keys)})", self.tree)
        plot_action.triggered.connect(lambda: self.signalActivated.emit(keys))
        menu.addAction(plot_action)
        if key and item is not None and item.data(0, self._GENERATED_ROLE):
            menu.addSeparator()
            rename_action = QAction("Rename calculated signal", self.tree)
            rename_action.triggered.connect(
                lambda _checked=False, generated_key=key: self.generatedRenameRequested.emit(generated_key)
            )
            menu.addAction(rename_action)
            edit_action = QAction("Edit calculated signal", self.tree)
            edit_action.triggered.connect(
                lambda _checked=False, generated_key=key: self.generatedEditRequested.emit(generated_key)
            )
            menu.addAction(edit_action)
            delete_action = QAction("Delete calculated signal", self.tree)
            delete_action.triggered.connect(
                lambda _checked=False, generated_key=key: self.generatedDeleteRequested.emit(generated_key)
            )
            menu.addAction(delete_action)
        # The signal under the mouse, or the one selected signal.
        target = key or (keys[0] if len(keys) == 1 else None)
        if target:
            menu.addSeparator()
            properties_action = QAction("Properties…", self.tree)
            properties_action.triggered.connect(
                lambda _checked=False, signal_key=target: self.propertiesRequested.emit(signal_key)
            )
            menu.addAction(properties_action)
        return menu

    def selected_signal_keys(self) -> list[str]:
        keys: list[str] = []
        for item in self.tree.selectedItems():
            key = self._item_key(item)
            if key:
                keys.append(key)
        return keys

    @staticmethod
    def _item_key(item: QTreeWidgetItem | None) -> str | None:
        if item is None:
            return None
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return None
        return str(key)
