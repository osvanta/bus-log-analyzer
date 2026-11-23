# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

import pytest

from PySide6.QtWidgets import QApplication

from gui.signal_tree import SignalTreeWidget


@pytest.fixture()
def signal_tree(qapp):
    widget = SignalTreeWidget()
    widget.set_payload(
        {
            1: {
                "EngineControl": ["EngSpeed", "Throttle"],
                "GearStatus": ["Gear"],
            },
            2: {
                "BatteryStatus": ["Current", "Voltage"],
            },
        }
    )
    yield widget
    widget.close()


def _channels(widget: SignalTreeWidget):
    return [
        widget.tree.topLevelItem(index)
        for index in range(widget.tree.topLevelItemCount())
    ]


def _messages(widget: SignalTreeWidget):
    return [
        channel.child(index)
        for channel in _channels(widget)
        for index in range(channel.childCount())
    ]


def test_message_controls_are_enabled_after_payload_load(signal_tree):
    assert signal_tree.collapse_messages_button.isEnabled()
    assert signal_tree.expand_messages_button.isEnabled()
    assert all(channel.isExpanded() for channel in _channels(signal_tree))
    assert all(message.isExpanded() for message in _messages(signal_tree))


def test_the_search_and_the_tree_are_two_cards_drawn_as_the_panels_are(signal_tree):
    # Feedback item 15: the panel's name over the search box in one card,
    # the tree in another headed by a band with its heading, Collapse and
    # Expand; the same card and band as the plot tab's panels.
    from PySide6.QtWidgets import QFrame
    from gui.panel_area import CARD_INSET, Card, HeaderBand
    from gui.signal_tree import MARGIN

    head, tree = signal_tree.search_card, signal_tree.tree_card
    assert isinstance(head, Card) and isinstance(tree, Card)
    assert isinstance(signal_tree.title_band, HeaderBand)
    assert isinstance(signal_tree.tree_band, HeaderBand)
    assert signal_tree.title_label.text() == "Decoded Signals"
    assert [head.content.itemAt(i).widget() for i in range(head.content.count())] == [
        signal_tree.title_band, signal_tree.search_edit]
    assert [tree.content.itemAt(i).widget() for i in range(tree.content.count())] == [
        signal_tree.tree_band, signal_tree.tree]
    band = signal_tree.tree_band.layout()
    for widget in (signal_tree.tree_title, signal_tree.collapse_messages_button,
                   signal_tree.expand_messages_button):
        assert band.indexOf(widget) >= 0
    # The cards are the edges: no frame of their own inside them.
    assert not signal_tree.search_edit.hasFrame()
    assert signal_tree.tree.frameShape() == QFrame.Shape.NoFrame
    assert signal_tree.tree.isHeaderHidden()

    signal_tree.resize(300, 600)
    signal_tree.show()
    QApplication.processEvents()
    # On its own, an even margin round the cards and a gutter between them.
    from PySide6.QtCore import QPoint
    from gui.panel_area import GUTTER
    head_rect, tree_rect = head.geometry(), tree.geometry()
    head_rect.moveTopLeft(head.mapTo(signal_tree, QPoint(0, 0)))
    tree_rect.moveTopLeft(tree.mapTo(signal_tree, QPoint(0, 0)))
    assert head_rect.left() == tree_rect.left() == MARGIN
    assert head_rect.top() == MARGIN
    assert tree_rect.top() - head_rect.bottom() - 1 == GUTTER
    assert signal_tree.height() - tree_rect.bottom() - 1 == MARGIN
    assert signal_tree.search_edit.width() == signal_tree.tree.width()
    assert signal_tree.tree.mapTo(tree, QPoint(0, 0)).x() == CARD_INSET


def test_the_trees_heading_gives_way_to_collapse_and_expand(signal_tree):
    # Narrow, the heading is cut short with its whole text in the tooltip;
    # it never holds the panel wider, and the buttons stay whole.
    signal_tree.resize(1000, 400)
    signal_tree.show()
    QApplication.processEvents()
    heading = signal_tree.tree_title
    assert heading.shown_text() == "Channels / Messages / Signals"
    narrow = signal_tree.minimumSizeHint().width()
    signal_tree.resize(narrow + 60, 400)
    QApplication.processEvents()
    assert heading.shown_text().endswith("…") and len(heading.shown_text()) > 1
    assert heading.toolTip() == "Channels / Messages / Signals"
    for button in (signal_tree.collapse_messages_button, signal_tree.expand_messages_button):
        assert button.width() >= button.sizeHint().width()
    assert heading.minimumSizeHint().width() <= 1


def test_collapse_operates_in_place_and_keeps_channels_open(signal_tree):
    messages_before = _messages(signal_tree)

    signal_tree.collapse_messages_button.click()

    assert all(channel.isExpanded() for channel in _channels(signal_tree))
    assert _messages(signal_tree) == messages_before
    assert all(not message.isExpanded() for message in _messages(signal_tree))


def test_expand_restores_every_message_across_channels(signal_tree):
    signal_tree.collapse_messages()

    signal_tree.expand_messages_button.click()

    assert all(channel.isExpanded() for channel in _channels(signal_tree))
    assert all(message.isExpanded() for message in _messages(signal_tree))


def test_collapse_preserves_selected_signal(signal_tree):
    signal_item = _channels(signal_tree)[0].child(0).child(0)
    signal_item.setSelected(True)
    selected_keys = signal_tree.selected_signal_keys()

    signal_tree.collapse_messages()

    assert signal_item.isSelected()
    assert signal_tree.selected_signal_keys() == selected_keys


def test_search_temporarily_expands_then_restores_collapsed_mode(signal_tree):
    signal_tree.collapse_messages()

    signal_tree.search_edit.setText("Speed")

    assert [message.text(0) for message in _messages(signal_tree)] == ["EngineControl"]
    assert _messages(signal_tree)[0].isExpanded()

    signal_tree.search_edit.clear()

    assert len(_messages(signal_tree)) == 3
    assert all(not message.isExpanded() for message in _messages(signal_tree))


def test_controls_are_disabled_for_empty_payload(qapp):
    widget = SignalTreeWidget()
    try:
        assert not widget.collapse_messages_button.isEnabled()
        assert not widget.expand_messages_button.isEnabled()
        widget.set_payload({})
        assert not widget.collapse_messages_button.isEnabled()
        assert not widget.expand_messages_button.isEnabled()
    finally:
        widget.close()


def test_generated_signals_have_separate_top_level_group(signal_tree):
    signal_tree.set_generated_signals([
        (
            "CH?::Generate Signals::ScaledSpeed",
            "ScaledSpeed",
            "ScaledSpeed [rpm] = `CH1::EngineControl::EngSpeed` * 100",
        )
    ])

    generated = signal_tree.tree.topLevelItem(0)
    assert generated.text(0) == "Calculated Signals"
    assert generated.isExpanded()
    assert generated.child(0).text(0) == "ScaledSpeed"
    assert signal_tree._item_key(generated.child(0)) == "CH?::Generate Signals::ScaledSpeed"
    assert generated.child(0).font(0).italic()

    signal_tree.collapse_messages()
    assert generated.isExpanded()
    assert generated.child(0).isHidden() is False


def test_generated_signals_participate_in_search(signal_tree):
    signal_tree.set_generated_signals([
        ("CH?::Generate Signals::ScaledSpeed", "ScaledSpeed", "formula")
    ])

    signal_tree.search_edit.setText("Scaled")
    assert signal_tree.tree.topLevelItem(0).text(0) == "Calculated Signals"
    assert signal_tree.tree.topLevelItem(0).childCount() == 1

    signal_tree.search_edit.setText("NoSuchSignal")
    assert all(item.text(0) != "Calculated Signals" for item in _channels(signal_tree))


def test_generated_signal_context_menu_offers_rename(signal_tree):
    key = "CH?::Generate Signals::ScaledSpeed"
    signal_tree.set_generated_signals([(key, "ScaledSpeed", "formula")])
    signal_tree.resize(500, 600)
    signal_tree.show()
    QApplication.processEvents()
    generated_item = signal_tree.tree.topLevelItem(0).child(0)
    requested = []
    signal_tree.generatedRenameRequested.connect(requested.append)
    menu = signal_tree._build_context_menu(
        generated_item,
        key,
        [key],
    )
    rename_action = next(
        action
        for action in menu.actions()
        if action.text() == "Rename calculated signal"
    )

    rename_action.trigger()

    assert requested == [key]
