# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Database Manager dialog — bus-tagged channel assignment.

The dialog is where the bus dimension becomes visible to the user, and where
it can silently go wrong: an assignment that fails to round-trip looks like a
working dialog right up until the measurement decodes against the wrong
database. These tests drive the widgets rather than the model, because that is
the only place the failure shows.
"""
from __future__ import annotations

import pytest

from core.bus_types import BusType, encode_key
from core.channel_config import ChannelConfig
from gui.dbc_manager import DBCManagerDialog, _DBCRow

CAN1 = (BusType.CAN, 1)
CAN2 = (BusType.CAN, 2)
LIN1 = (BusType.LIN, 1)
LIN2 = (BusType.LIN, 2)
ALL_CAN = (BusType.CAN, 0)

_CHANNELS_IN_FILE = [CAN1, CAN2, LIN1]
_IDS_PER_CHANNEL = {
    CAN1: {0x100, 0x200},
    CAN2: {0x300},
    LIN1: {0x20, 0x21},
}


@pytest.fixture()
def dialog(qapp, sample_dbc_path, sample_ldf_path):
    config = ChannelConfig(name="Mixed", channels={
        CAN1: str(sample_dbc_path),
        LIN1: str(sample_ldf_path),
    })
    dlg = DBCManagerDialog(
        channel_config=config,
        channels_in_file=_CHANNELS_IN_FILE,
        ids_per_channel=_IDS_PER_CHANNEL,
    )
    yield dlg
    dlg.close()
    qapp.processEvents()


def test_dropdown_lists_every_bus_with_its_own_fallback(qapp):
    row = _DBCRow(
        dbc_path="x.dbc",
        channels=_CHANNELS_IN_FILE,
        assigned_to=ALL_CAN,
        match_pct=0.0,
        match_text="",
    )
    combo = row.channel_combo
    labels = [combo.itemText(i) for i in range(combo.count())]

    assert labels == ["All CAN", "CAN 1", "CAN 2", "All LIN", "LIN 1"]


def test_row_keeps_the_channel_it_was_given(qapp):
    """Regression: every row used to collapse onto the first dropdown entry.

    Item data was the ``(bus, number)`` tuple, and ``QComboBox.findData()``
    compares wrapped Python objects by *identity*, not equality — so an equal
    but distinct tuple returned -1 and ``max(0, -1)`` selected "All CAN". Both
    the model and the dropdown contents looked correct; only the selection was
    wrong, which meant a LIN database silently ended up assigned to CAN.

    The assigned key is therefore built as a **fresh** tuple rather than reused
    from the channel list. Passing the same object would match by identity and
    the test would pass against the bug — which is precisely how the defect
    survived being written in the first place.
    """
    for bus, number in ((BusType.CAN, 1), (BusType.CAN, 2), (BusType.LIN, 1)):
        expected = (bus, number)
        listed = next(
            key for key in _CHANNELS_IN_FILE if key == expected
        )
        assert expected is not listed, "must be a distinct object to be meaningful"

        row = _DBCRow(
            dbc_path="x.dbc",
            channels=_CHANNELS_IN_FILE,
            assigned_to=expected,
            match_pct=0.0,
            match_text="",
        )
        assert row.assigned_channel() == expected


def test_existing_assignments_round_trip_through_the_dialog(
    dialog, sample_dbc_path, sample_ldf_path
):
    assert dialog.result_config().channels == {
        CAN1: str(sample_dbc_path),
        LIN1: str(sample_ldf_path),
    }


def test_correct_assignments_produce_no_warning(dialog):
    assert dialog.compatibility_warnings() == []


def test_ldf_moved_onto_a_can_channel_warns(dialog, sample_ldf_path):
    ldf_row = next(
        row for row in dialog._rows if row.dbc_path.endswith(".ldf")
    )
    ldf_row.channel_combo.setCurrentIndex(
        ldf_row.channel_combo.findData(encode_key(CAN1))
    )

    assert ldf_row.assigned_channel() == CAN1
    warnings = dialog.compatibility_warnings()
    assert len(warnings) == 1
    assert "LIN" in warnings[0]


def test_channel_banner_names_both_buses(dialog):
    banner = dialog._channel_info.text()
    assert "CAN 1" in banner
    assert "LIN 1" in banner


def test_ldf_match_quality_is_scored_against_lin_frame_ids(dialog):
    """The match bar is reused for LIN unchanged.

    ``_j1939_pgn`` only fires above 0x7FF and LIN frame IDs are 6-bit, so the
    J1939 pass is inert by construction and LIN is scored on exact frame-ID
    matches — which is what the percentage should mean for it.
    """
    percentage, text, best = dialog._compute_match(
        next(row.dbc_path for row in dialog._rows if row.dbc_path.endswith(".ldf"))
    )

    assert percentage == 1.0
    assert text == "2 / 2 IDs"
    assert best == LIN1


def test_assignment_survives_a_measurement_that_declares_no_channels(qapp):
    """Regression: an LDF silently became a CAN assignment.

    A BLF holding no CAN traffic pre-scans to nothing, so the dropdown was
    built from the empty channel list and offered "All CAN" alone.
    ``findData`` returned -1 for the All-LIN assignment and ``max(0, -1)``
    selected the only entry — the dialog then reported, and saved, an
    assignment nobody made.
    """
    row = _DBCRow(
        dbc_path="door.ldf",
        channels=[],
        assigned_to=(BusType.LIN, 0),
        match_pct=0.0,
        match_text="",
    )

    assert row.assigned_channel() == (BusType.LIN, 0)
    labels = [row.channel_combo.itemText(i) for i in range(row.channel_combo.count())]
    assert "All LIN" in labels


def test_a_channel_the_file_never_reported_is_kept_not_dropped(qapp):
    """A saved config can name a channel the current measurement lacks.

    Silently re-homing it would rewrite the user's configuration on the next
    save, so the entry is added rather than replaced by the first item.
    """
    row = _DBCRow(
        dbc_path="x.dbc",
        channels=[CAN1],
        assigned_to=(BusType.CAN, 7),
        match_pct=0.0,
        match_text="",
    )

    assert row.assigned_channel() == (BusType.CAN, 7)


# ── Match scoring ──────────────────────────────────────────────────────────

def _lin_dialog(qapp, lengths):
    """A dialog seeing two LIN channels with identical frame IDs."""
    ids = {LIN1: {0x20, 0x21}, LIN2: {0x20, 0x21}}
    return DBCManagerDialog(
        channel_config=ChannelConfig(),
        channels_in_file=[LIN1, LIN2],
        ids_per_channel=ids,
        lengths_per_channel=lengths,
    )


def test_frame_length_decides_which_lin_channel_a_database_belongs_to(
    qapp, sample_ldf_path, alt_ldf_path
):
    """Regression: frame IDs alone sent the wrong LDF to the wrong channel.

    Every LIN cluster numbers its frames from 0, so two unrelated databases
    routinely declare the same IDs — in the real CANoe log, Door.ldf scores a
    perfect ID match against *both* LIN channels and belongs to only one.
    _compute_match's answer is applied, not merely displayed, so a tie there
    becomes a silently wrong assignment.
    """
    dialog = _lin_dialog(
        qapp,
        # Same IDs on both channels, opposite lengths.
        {LIN1: {0x20: 2, 0x21: 1}, LIN2: {0x20: 1, 0x21: 2}},
    )
    try:
        pct, _text, best = dialog._compute_match(str(sample_ldf_path))
        assert best == LIN1 and pct == 1.0

        pct, _text, best = dialog._compute_match(str(alt_ldf_path))
        assert best == LIN2 and pct == 1.0
    finally:
        dialog.close()


def test_without_observed_lengths_matching_falls_back_to_ids(
    qapp, sample_ldf_path
):
    """CAN pre-scans collect no lengths, and their scoring must not move."""
    dialog = _lin_dialog(qapp, {})
    try:
        pct, text, _best = dialog._compute_match(str(sample_ldf_path))
        assert pct == 1.0
        assert text == "2 / 2 IDs"
    finally:
        dialog.close()


def test_a_wrong_length_lowers_the_score_rather_than_hiding_it(
    qapp, sample_ldf_path
):
    dialog = _lin_dialog(
        qapp,
        {LIN1: {0x20: 7, 0x21: 7}, LIN2: {0x20: 7, 0x21: 7}},
    )
    try:
        pct, text, _best = dialog._compute_match(str(sample_ldf_path))
        assert pct == 0.0
        assert text == "0 / 2 IDs"
    finally:
        dialog.close()
