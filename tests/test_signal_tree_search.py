# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Wildcards in the signal tree's search box.

A pattern with ``*`` or ``?`` had to match a signal's whole name, so
``veh*speed`` found nothing for ``WheelBasedVehicleSpeed`` and ``speed*`` only
names starting with "speed". Generated signals were searched by another rule,
so one pattern showed different things in the same tree.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import pytest

from gui.signal_tree import SignalTreeWidget, search_pattern


@pytest.fixture()
def tree(qapp):
    widget = SignalTreeWidget()
    widget.set_payload({
        1: {
            "EEC1": ["EngSpeed", "EngTorque", "ActualEngPercentTorque"],
            "CCVS1": ["WheelBasedVehicleSpeed", "CruiseCtrlActive"],
        },
        2: {
            "BatteryStatus": ["Battery_Voltage", "Current", "Temp[1]", "Temp1"],
        },
    })
    widget.set_generated_signals([
        ("CH?::Generate Signals::ScaledSpeed", "ScaledSpeed", "formula"),
    ])
    yield widget
    widget.close()


def _found(widget: SignalTreeWidget, text: str) -> set[str]:
    widget.search_edit.setText(text)
    names = set()
    for channel_index in range(widget.tree.topLevelItemCount()):
        channel = widget.tree.topLevelItem(channel_index)
        for index in range(channel.childCount()):
            child = channel.child(index)
            if child.childCount():  # a message
                names.update(child.child(i).text(0) for i in range(child.childCount()))
            else:  # a generated signal
                names.add(child.text(0))
    return names


def test_a_star_stands_for_any_text_inside_the_name(tree):
    assert _found(tree, "veh*speed") == {"WheelBasedVehicleSpeed"}
    assert _found(tree, "bat*volt") == {"Battery_Voltage"}


def test_the_pattern_can_be_anywhere_in_the_name(tree):
    assert _found(tree, "speed*") == {"EngSpeed", "WheelBasedVehicleSpeed", "ScaledSpeed"}
    assert _found(tree, "eng*torque") == {"EngTorque", "ActualEngPercentTorque"}


def test_a_question_mark_stands_for_one_character(tree):
    assert _found(tree, "eng?peed") == {"EngSpeed"}
    assert _found(tree, "eng??peed") == set()


def test_a_star_alone_finds_every_signal(tree):
    assert len(_found(tree, "*")) == 10


def test_text_without_wildcards_is_found_anywhere_in_any_case(tree):
    assert _found(tree, "SPEED") == {"EngSpeed", "WheelBasedVehicleSpeed", "ScaledSpeed"}
    assert _found(tree, "  torque ") == {"EngTorque", "ActualEngPercentTorque"}


def test_other_characters_mean_themselves(tree):
    # Brackets are not a set of characters to choose from.
    assert _found(tree, "temp[1]*") == {"Temp[1]"}
    assert _found(tree, "y_v") == {"Battery_Voltage"}


def test_clearing_the_search_shows_everything_again(tree):
    _found(tree, "veh*speed")
    assert len(_found(tree, "")) == 10


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_search_is_no_pattern(text):
    assert search_pattern(text) is None


def test_the_search_box_says_wildcards_work(tree):
    assert "*" in tree.search_edit.placeholderText()
    assert "?" in tree.search_edit.placeholderText()
