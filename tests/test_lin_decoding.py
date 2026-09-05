# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""End-to-end LIN decoding from MF4 bus-logging files.

Covers the whole path rather than each piece in isolation, because the parts
that can silently go wrong are the seams: the file declaring which bus a
channel belongs to, that bus surviving into the decoded metadata, and the
database being routed to the right bus. A unit test of any single stage would
still pass with the buses crossed.

The measurement (``sample_lin.mf4``) and the database (``sample.ldf``) are
built to match, so decoded values can be asserted by hand:

    0x20 DoorCmd     payload (10, 1) then (30, 2)
    0x21 DoorStatus  payload (20, 0) then (40, 0)
"""
from __future__ import annotations

import pytest

from core.bus_types import BusType
from core.channel_config import ChannelConfig
from core.readers import (
    database_mandatory_for,
    dbc_required_for,
    prescan_measurement,
    reader_factory,
)
from core.readers.db_format import (
    compatibility_warning,
    database_message_ids,
    db_format_label,
    is_database_file,
    ldf_support_available,
)
from core.readers.mdf_reader import MDFReader

LIN1 = (BusType.LIN, 1)


def _require_ldf_support() -> None:
    available, message = ldf_support_available()
    if not available:
        pytest.skip(f"LDF support unavailable: {message}")


# ── Database format handling ───────────────────────────────────────────────

def test_ldf_is_a_recognised_database_format(sample_ldf_path):
    assert is_database_file(str(sample_ldf_path))
    assert db_format_label(str(sample_ldf_path)) == "LDF"


def test_ldf_frames_and_signals_load_through_canmatrix(sample_ldf_path):
    """The LDF must actually parse, not merely be accepted by extension."""
    _require_ldf_support()
    ids = database_message_ids(str(sample_ldf_path))
    assert ids == {0x20, 0x21}


def test_dbc_on_a_lin_channel_is_allowed_without_warning(sample_dbc_path):
    """LIN layouts are routinely distributed as DBC — this must stay silent."""
    assert compatibility_warning(str(sample_dbc_path), BusType.LIN) is None


def test_ldf_on_a_can_channel_warns_but_does_not_block(sample_ldf_path):
    warning = compatibility_warning(str(sample_ldf_path), BusType.CAN)
    assert warning is not None
    assert "LIN" in warning


# ── File classification ────────────────────────────────────────────────────

def test_lin_mf4_is_classified_as_raw_bus_not_decoded_signals(lin_mf4_path):
    """Regression: LIN_Frame groups used to read as pre-decoded signals.

    ``content_info`` only special-cased ``CAN_DataFrame``, so a LIN group fell
    through to "has decoded signals" and the file opened with no database,
    listing raw LIN_Frame columns as if they were engineering signals.
    """
    content = MDFReader.content_info(str(lin_mf4_path))

    assert content.has_raw_lin
    assert not content.has_raw_can
    assert not content.has_decoded_signals
    assert content.bus_types() == [BusType.LIN]
    assert MDFReader.is_bus_logging(str(lin_mf4_path))


def test_lin_mf4_wants_a_database_but_still_opens_without_one(lin_mf4_path):
    """A raw LIN file is readable without a database; raw CAN is not.

    The LIN_Frame columns are ordinary MDF channels, so the file opens and a
    database only upgrades them into decoded signals. Blocking the load would
    remove behaviour that works today.
    """
    assert dbc_required_for(str(lin_mf4_path)) is True
    assert database_mandatory_for(str(lin_mf4_path)) is False

    reader = reader_factory(str(lin_mf4_path), None)
    assert type(reader).__name__ == "MDFReader"


# ── Pre-scan ───────────────────────────────────────────────────────────────

def test_prescan_discovers_lin_channels_and_frame_ids(lin_mf4_path):
    """python-can cannot see LIN at all, so this exercises the asammdf path."""
    scan = prescan_measurement(str(lin_mf4_path))

    assert scan.channels == [LIN1]
    assert scan.buses() == [BusType.LIN]
    assert scan.ids_per_channel[LIN1] == {0x20, 0x21}


# ── Decoding ───────────────────────────────────────────────────────────────

def _decode(lin_mf4_path, ldf_path):
    config = ChannelConfig(name="LIN", channels={LIN1: str(ldf_path)})
    reader = reader_factory(str(lin_mf4_path), str(ldf_path))
    return {
        (meta[0], meta[1], meta[3]): list(values)
        for meta, _timestamps, values, _display
        in reader.iter_decoded_channel_arrays(config)
    }


def test_lin_signals_decode_with_an_ldf(lin_mf4_path, sample_ldf_path):
    _require_ldf_support()
    decoded = _decode(lin_mf4_path, sample_ldf_path)

    assert (LIN1, "DoorCmd", "WindowPos") in decoded
    assert decoded[(LIN1, "DoorCmd", "WindowPos")] == [10.0, 30.0]
    assert decoded[(LIN1, "DoorStatus", "MirrorAngle")] == [20.0, 40.0]


def test_decoded_lin_metadata_carries_the_bus_not_just_the_number(
    lin_mf4_path, sample_ldf_path
):
    """The bus must come from the file, never from the database extension.

    asammdf stamps the group's acquisition source with ``LIN{n}.LIN_Frame...``;
    recovering the bus from there is what makes a DBC-on-LIN assignment work
    and keeps CAN 1 and LIN 1 distinguishable.
    """
    _require_ldf_support()
    decoded = _decode(lin_mf4_path, sample_ldf_path)

    channels = {key[0] for key in decoded}
    assert channels == {LIN1}
    assert all(isinstance(key[0], tuple) for key in decoded)


def test_lin_message_names_come_from_the_database(lin_mf4_path, sample_ldf_path):
    """Named messages prove the LDF was applied, not just the frames counted."""
    _require_ldf_support()
    decoded = _decode(lin_mf4_path, sample_ldf_path)

    message_names = {key[1] for key in decoded}
    assert message_names == {"DoorCmd", "DoorStatus"}
    # The synthetic fallback name appears only when the database did not match.
    assert not any(name.startswith("LIN_Frame_") for name in message_names)


def test_lin_database_is_not_applied_to_can_extraction(lin_mf4_path, sample_ldf_path):
    """An All-LIN fallback must never reach CAN, and vice versa.

    A single global fallback would hand the LDF to CAN extraction; per-bus
    fallbacks are what prevent that.
    """
    config = ChannelConfig(channels={(BusType.LIN, 0): str(sample_ldf_path)})

    assert config.databases_for_bus(BusType.LIN) == [(str(sample_ldf_path), 0)]
    assert config.databases_for_bus(BusType.CAN) == []
    assert config.dbc_path_for(BusType.CAN, 1) is None


# ── CAN Trace ──────────────────────────────────────────────────────────────

def test_lin_frames_reach_the_shared_trace_store(lin_mf4_path, sample_ldf_path):
    """LIN frames join the existing trace rather than getting a store of
    their own, tagged by the bus flag bit so they stay distinguishable."""
    _require_ldf_support()
    from core.raw_frame_store import RawFrameStore

    config = ChannelConfig(channels={LIN1: str(sample_ldf_path)})
    reader = reader_factory(str(lin_mf4_path), str(sample_ldf_path))
    store = RawFrameStore()

    def on_batch(timestamps, channels, ids, dlcs, directions, flags, data):
        store.append_numpy_batch(
            timestamps, channels, ids, dlcs, directions, flags, data
        )

    try:
        for _ in reader.iter_decoded_channel_arrays(
            config, raw_frame_batch=on_batch
        ):
            pass
        store.seal()

        assert len(store) == 4
        assert store.channel_keys() == [LIN1]

        records = store.get_window(range(len(store)))
        assert all(record.bus is BusType.LIN for record in records)
        assert [record.arbitration_id for record in records] == [
            0x20, 0x21, 0x20, 0x21
        ]
        # LIN is never extended-ID and never FD; those bits must stay clear.
        assert not any(record.is_extended or record.is_fd for record in records)
    finally:
        store.close()
