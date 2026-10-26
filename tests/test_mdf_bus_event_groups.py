# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A bus logger writes, beside its CAN_DataFrame and LIN_Frame groups, event
groups that are usually empty: CAN_ErrorFrame, CAN_RemoteFrame and the LIN
error groups. They are bus structure, not decoded signals. Counted as decoded
signals they made a raw-only recording look "mixed": the app then needed no
database, and Decode listed nothing.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from core.readers.mdf_reader import MDFReader


def _group(*names, cycles=10):
    return SimpleNamespace(
        channels=[SimpleNamespace(name=name, channel_type=0) for name in names],
        channel_group=SimpleNamespace(cycles_nr=cycles))


@pytest.fixture(autouse=True)
def _fresh_cache():
    MDFReader._content_cache.clear()
    yield
    MDFReader._content_cache.clear()


def _logger_mf4(path, frames=3):
    """A raw CAN recording as a logger writes it: CAN_DataFrame with frames,
    and empty CAN_ErrorFrame and CAN_RemoteFrame groups beside it."""
    import can
    writer = can.MF4Writer(str(path))
    for i in range(frames):
        writer.on_message_received(can.Message(
            timestamp=1.0 + i, arbitration_id=0x100, data=b'\x01\x02',
            is_extended_id=False, channel=0))
    writer.stop()
    return path


# ── Which groups are bus structure ───────────────────────────────────────


@pytest.mark.parametrize('event', [
    'CAN_DataFrame', 'CAN_ErrorFrame', 'CAN_RemoteFrame', 'CAN_OverloadFrame',
    'LIN_Frame', 'LIN_ChecksumError', 'LIN_ReceiveError', 'LIN_SyncError',
    'LIN_TransmissionError', 'FLX_Frame', 'ETH_Frame'])
def test_every_bus_event_group_is_structure_not_signals(event):
    group = _group('Timestamp', event, f'{event}.BusChannel', f'{event}.ID')
    assert MDFReader._is_bus_event_group(group)
    assert not MDFReader._group_has_decoded_signals(group)


def test_signals_that_only_look_like_an_event_are_still_signals():
    # No <name>.<field> members: an ordinary signal that happens to be named so.
    assert MDFReader._group_has_decoded_signals(_group('time', 'CAN_Speed', 'CAN_Torque'))
    # Members without the structure channel itself.
    assert MDFReader._group_has_decoded_signals(_group('time', 'Engine.Speed', 'Engine.Torque'))
    # A recorder-decoded group.
    assert MDFReader._group_has_decoded_signals(_group('time', 'EngSpeed', 'VehSpeed'))


def test_a_group_with_no_samples_is_not_decoded_content():
    assert not MDFReader._group_has_decoded_signals(_group('time', 'EngSpeed', cycles=0))
    assert MDFReader._group_has_decoded_signals(_group('time', 'EngSpeed', cycles=1))
    no_count = SimpleNamespace(channels=[SimpleNamespace(name='EngSpeed', channel_type=0)])
    assert MDFReader._group_has_decoded_signals(no_count)     # unknown count: keep it


# ── Real files ───────────────────────────────────────────────────────────


def test_a_raw_recording_with_empty_event_groups_is_raw_only(tmp_path):
    from asammdf import MDF
    from core.readers import (database_mandatory_for, dbc_required_for,
                              has_mixed_mdf_content)

    path = _logger_mf4(tmp_path / 'logger.mf4')
    mdf = MDF(str(path), process_bus_logging=False)
    try:   # the case at hand: empty error and remote frame groups are there
        empty = {g.channel_group.acq_name for g in mdf.groups if g.channel_group.cycles_nr == 0
                 for c in g.channels if c.name in ('CAN_ErrorFrame', 'CAN_RemoteFrame')}
        assert empty
    finally:
        mdf.close()

    content = MDFReader.content_info(path)
    assert (content.has_raw_can, content.has_decoded_signals, content.is_mixed) == \
        (True, False, False)
    assert not has_mixed_mdf_content(str(path))
    assert dbc_required_for(str(path)) and database_mandatory_for(str(path))


def test_without_a_database_it_asks_for_one_instead_of_listing_nothing(tmp_path):
    from core.readers import reader_factory
    path = _logger_mf4(tmp_path / 'logger.mf4')
    with pytest.raises(ValueError, match='DBC or ARXML file is required'):
        reader_factory(str(path), None)


def test_a_really_mixed_file_is_still_mixed_and_lists_its_signals(tmp_path):
    from asammdf import MDF, Signal
    from core.readers import has_mixed_mdf_content

    raw = _logger_mf4(tmp_path / 'raw.mf4')
    mixed = tmp_path / 'mixed.mf4'
    mdf = MDF(str(raw), process_bus_logging=False)
    mdf.append([Signal(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0]),
                       name='EngSpeed', unit='rpm')], acq_name='Engine')
    mdf.save(str(mixed), overwrite=True)
    mdf.close()

    content = MDFReader.content_info(mixed)
    assert content.is_mixed and has_mixed_mdf_content(str(mixed))
    with MDF(str(mixed), process_bus_logging=False) as opened:
        listed = MDFReader._channel_metadata(opened)
    assert any(name == 'EngSpeed' for _group, name, _unit in listed)
