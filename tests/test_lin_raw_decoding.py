# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""LIN decoding from BLF and ASC.

Motivated by a real Vector CANoe sample: ``LINSystem_1.blf`` holds 7,275 LIN
frames on two channels and no CAN at all, and python-can *skips* LIN objects
rather than rejecting them — so the file loaded as an empty measurement with
no error and nothing to point at.

The parts that can silently go wrong here are all seams, so the tests follow
whole paths rather than single functions:

* the file saying which bus and which channel a frame belongs to,
* that bus surviving into the decoder lookup, so LIN 1 and CAN 1 do not share
  a database,
* the LDF reaching the decode pipeline at all, since cantools cannot read one,
* and the right LDF reaching the right channel, which frame IDs alone cannot
  determine.

``sample_lin.blf`` and ``sample_lin.asc`` carry identical traffic on two LIN
channels, so the two readers can be compared directly:

    LIN 1   0x20 DoorCmd     (10,1) then (30,2)     0x21 DoorStatus  (20) (40)
    LIN 2   0x20 Latch       (7) then (9)           0x21 Seat        (50,3) (60,4)

The channels deliberately share frame IDs with opposite lengths, matching
sample.ldf and sample_alt.ldf respectively.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.bus_types import BusType
from core.channel_config import ChannelConfig
from core.dbc_decoder import DBCDecoder
from core.readers import prescan_measurement, reader_factory
from core.readers.blf_content import blf_bus_content
from core.readers.db_format import ldf_support_available

LIN1 = (BusType.LIN, 1)
LIN2 = (BusType.LIN, 2)


def _require_ldf_support() -> None:
    available, message = ldf_support_available()
    if not available:
        pytest.skip(f"LDF support unavailable: {message}")


@pytest.fixture(params=["lin_blf_path", "lin_asc_path"])
def lin_measurement(request):
    """Both raw containers, so the two parsers cannot drift apart."""
    return request.getfixturevalue(request.param)


def _decode(measurement, databases):
    """Run the real load path and return {signal key: values}."""
    from core.load_worker import LoadWorker

    config = ChannelConfig(name="LIN", channels=databases)
    worker = LoadWorker(str(measurement), config)
    messages: list[str] = []
    result: dict = {}
    worker.progress.connect(messages.append)
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda err: result.setdefault("error", err))
    worker.run()
    if "error" in result:
        raise AssertionError(result["error"])
    store = result["store"]
    return (
        {key: list(series.values) for key, series in store._series_by_key.items()},
        store,
        messages,
    )


# ── The LDF reaches the decoder ────────────────────────────────────────────

def test_an_ldf_loads_as_a_database(sample_ldf_path):
    """cantools cannot read an LDF, so it is converted to DBC first.

    Regression: this used to raise "expected database format 'arxml', 'dbc',
    'kcd', 'sym', 'cdd' or None, but got 'ldf'" — a message about extensions,
    from the library that was never the right one to ask.
    """
    _require_ldf_support()
    decoder = DBCDecoder(str(sample_ldf_path))

    messages = {msg.name: msg for msg in decoder.database.messages}
    assert set(messages) == {"DoorCmd", "DoorStatus"}
    assert messages["DoorCmd"].frame_id == 0x20
    assert messages["DoorCmd"].length == 2
    assert messages["DoorStatus"].length == 1
    # The LDF's encodings survive as a DBC value table, so the enum comes
    # back as its label rather than as a bare 1.
    assert messages["DoorCmd"].decode(bytes((10, 1))) == {
        "WindowPos": 10, "LockState": "Locked"
    }


def test_conversion_keeps_the_value_tables(sample_ldf_path):
    """Enum labels have to survive, or the signal tree loses them silently."""
    _require_ldf_support()
    from core.readers.db_format import ldf_to_dbc_string

    assert "VAL_" in ldf_to_dbc_string(str(sample_ldf_path))


def test_matching_ldf_and_dbc_conversion_is_cached(sample_ldf_path):
    """Runs per load and per match refresh; canmatrix's parse is not cheap."""
    _require_ldf_support()
    from core.readers.db_format import ldf_to_dbc_string

    assert ldf_to_dbc_string(str(sample_ldf_path)) is ldf_to_dbc_string(
        str(sample_ldf_path)
    )


# ── Discovery ──────────────────────────────────────────────────────────────

def test_prescan_finds_both_lin_channels_with_ids_and_lengths(lin_measurement):
    scan = prescan_measurement(str(lin_measurement))

    assert scan.channels == [LIN1, LIN2]
    assert scan.buses() == [BusType.LIN]
    assert scan.ids_per_channel[LIN1] == {0x20, 0x21}
    assert scan.ids_per_channel[LIN2] == {0x20, 0x21}
    # Same IDs, opposite lengths — the only thing telling the two apart.
    assert scan.lengths_per_channel[LIN1] == {0x20: 2, 0x21: 1}
    assert scan.lengths_per_channel[LIN2] == {0x20: 1, 0x21: 2}


def test_lin_channel_numbers_are_not_shifted(lin_measurement):
    """BLF stores LIN channels 1-indexed already.

    The CAN path adds one only to undo python-can's own subtraction, and the
    packed reader bypasses that. Adding one here too would rename every
    channel — LIN 1 would show as LIN 2 — and the error would look like a
    database mismatch rather than an off-by-one.
    """
    scan = prescan_measurement(str(lin_measurement))
    assert [number for _bus, number in scan.channels] == [1, 2]


def test_probe_reports_a_lin_only_blf(lin_blf_path):
    import can

    content = blf_bus_content(lin_blf_path)
    assert content.has_lin
    assert not content.has_can
    assert content.lin_objects == 64
    # The symptom that made this feature necessary.
    assert sum(1 for _ in can.BLFReader(str(lin_blf_path))) == 0


def test_probe_agrees_with_python_can_on_a_can_log(blf_path):
    """Pinned to python-can's own output rather than to a constant, so the
    probe cannot drift into refusing a file that would have loaded."""
    import can

    content = blf_bus_content(blf_path)
    assert content.has_can
    assert not content.has_lin
    assert content.can_objects == sum(1 for _ in can.BLFReader(str(blf_path)))


def test_probe_never_raises_on_files_it_cannot_read(tmp_path, sample_dbc_path):
    """It exists to improve an error message, so it must not become one."""
    garbage = tmp_path / "truncated.blf"
    garbage.write_bytes(b"LOGG" + b"\x90\x00\x00\x00" + b"\x00" * 32)

    for candidate in (garbage, sample_dbc_path, tmp_path / "missing.blf"):
        content = blf_bus_content(candidate)
        assert not (content.has_can or content.has_lin)


# ── Decoding ───────────────────────────────────────────────────────────────

def test_lin_signals_decode_from_both_containers(
    lin_measurement, sample_ldf_path, alt_ldf_path
):
    _require_ldf_support()
    decoded, store, _messages = _decode(
        lin_measurement,
        {LIN1: str(sample_ldf_path), LIN2: str(alt_ldf_path)},
    )

    assert store.channels == {LIN1, LIN2}
    assert decoded["LIN1::DoorCmd::WindowPos"][:2] == [10.0, 30.0]
    assert decoded["LIN1::DoorCmd::LockState"][:2] == [1.0, 2.0]
    assert decoded["LIN1::DoorStatus::MirrorAngle"][:2] == [20.0, 40.0]
    assert decoded["LIN2::Latch::LatchState"][:2] == [7.0, 9.0]
    assert decoded["LIN2::Seat::SeatPos"][:2] == [50.0, 60.0]
    assert decoded["LIN2::Seat::SeatHeat"][:2] == [3.0, 4.0]


def test_each_channel_decodes_against_its_own_database(
    lin_blf_path, sample_ldf_path, alt_ldf_path
):
    """Both channels carry frame 0x20; only the bus-tagged decoder map keeps
    them apart. A single global fallback would decode one with the other's
    database and produce plausible, wrong numbers."""
    _require_ldf_support()
    decoded, _store, _messages = _decode(
        lin_blf_path,
        {LIN1: str(sample_ldf_path), LIN2: str(alt_ldf_path)},
    )

    assert any(key.startswith("LIN1::DoorCmd::") for key in decoded)
    assert not any(key.startswith("LIN1::Latch::") for key in decoded)
    assert any(key.startswith("LIN2::Latch::") for key in decoded)
    assert not any(key.startswith("LIN2::DoorCmd::") for key in decoded)


def test_can_and_lin_on_the_same_channel_and_frame_id_stay_apart(
    mixed_bus_blf_path, sample_ldf_path, low_id_dbc_path
):
    """CAN 1 and LIN 1 both carry frame 0x20 in this file.

    LIN frame IDs are 6-bit and overlap the low CAN range, so the channel
    number and frame ID together are not unique across buses — and the bulk
    decoder groups frames by exactly that pair before choosing a database.
    Without the bus in the grouping key the two merge into one group and
    whichever sorts first decides which database decodes both, producing
    confident nonsense for the other.
    """
    _require_ldf_support()
    decoded, store, _messages = _decode(
        mixed_bus_blf_path,
        {(BusType.CAN, 1): str(low_id_dbc_path), LIN1: str(sample_ldf_path)},
    )

    assert store.channels == {(BusType.CAN, 1), LIN1}
    assert decoded["CH1::CanLow::CanLowValue"][:2] == [12345.0, 12345.0]
    assert decoded["LIN1::DoorCmd::WindowPos"][:2] == [10.0, 10.0]
    # Neither database may reach the other bus.
    assert not any(key.startswith("CH1::DoorCmd") for key in decoded)
    assert not any(key.startswith("LIN1::CanLow") for key in decoded)


def test_blf_and_asc_decode_to_the_same_result(
    lin_blf_path, lin_asc_path, sample_ldf_path, alt_ldf_path
):
    """Two independently written parsers, one expected answer."""
    _require_ldf_support()
    databases = {LIN1: str(sample_ldf_path), LIN2: str(alt_ldf_path)}

    from_blf, _s1, _m1 = _decode(lin_blf_path, databases)
    from_asc, _s2, _m2 = _decode(lin_asc_path, databases)

    assert from_blf == from_asc


def test_the_same_ldf_gives_the_same_names_from_mf4_and_blf(
    lin_blf_path, lin_mf4_path, sample_ldf_path
):
    """MF4 decodes LIN through asammdf and BLF through the DBC conversion.

    Two different routes must produce the same signal keys, or a saved plot
    configuration stops working the moment the same cluster is recorded in a
    different container.
    """
    _require_ldf_support()
    from_blf, _store, _messages = _decode(
        lin_blf_path, {LIN1: str(sample_ldf_path)}
    )

    config = ChannelConfig(channels={LIN1: str(sample_ldf_path)})
    reader = reader_factory(str(lin_mf4_path), str(sample_ldf_path))
    from_mf4 = {
        f"LIN1::{meta[1]}::{meta[3]}": [float(value) for value in values]
        for meta, _ts, values, _display
        in reader.iter_decoded_channel_arrays(config)
    }

    assert set(from_mf4) <= set(from_blf)
    for key, values in from_mf4.items():
        assert from_blf[key][:len(values)] == values


def test_bus_events_are_counted_not_stored(lin_blf_path, sample_ldf_path):
    """Sleep, wakeup and schedule changes are not frames.

    Storing them would put rows in the trace with no payload and inflate every
    frame count; ignoring them entirely would hide that they occurred.
    """
    from core.blf_reader import BLFReaderService

    service = BLFReaderService(lin_blf_path)
    frames = sum(len(batch[1]) for batch in service.iter_raw_batches())

    assert frames == 64
    assert service.lin_event_objects == 3


def test_frames_reach_the_trace_tagged_as_lin(lin_measurement, sample_ldf_path):
    _require_ldf_support()
    _decoded, store, _messages = _decode(
        lin_measurement, {LIN1: str(sample_ldf_path)}
    )
    trace = store.raw_frame_store

    assert len(trace) == 64
    assert trace.channel_keys() == [LIN1, LIN2]
    records = trace.get_window(range(8))
    assert all(record.bus is BusType.LIN for record in records)
    # LIN is never extended-ID and never FD; those bits must stay clear.
    assert not any(record.is_extended or record.is_fd for record in records)


def test_a_short_frame_against_a_longer_message_is_reported(
    lin_blf_path, sample_ldf_path, alt_ldf_path
):
    """Swapping the two databases decodes silently, not loudly.

    A 1-byte frame matched to a 2-byte message reads its second byte from the
    zero padding of the fixed-width record, so the signal comes back as 0
    rather than failing. The count is the only thing that makes it visible.
    """
    _require_ldf_support()
    decoded, _store, messages = _decode(
        lin_blf_path,
        {LIN1: str(alt_ldf_path), LIN2: str(sample_ldf_path)},
    )

    # The wrong database decodes, and produces padding.
    assert decoded["LIN1::Seat::SeatHeat"][:2] == [0.0, 0.0]

    warning = next(
        (m for m in messages if "shorter than the message" in m), None
    )
    assert warning is not None
    assert "32" in warning


# ── Refusals that remain ───────────────────────────────────────────────────

def test_an_ldf_is_still_refused_for_raw_can_csv(tmp_path, sample_ldf_path):
    """CSV has no bus dimension, so an LDF can never apply to one."""
    from core.readers.csv_reader import is_can_bus_logging_csv

    csv_path = tmp_path / "raw.csv"
    csv_path.write_text(
        "TimestampEpoch;BusChannel;ID;DataLength;DataBytes\n"
        "1700000000.0;1;256;8;00 01 02 03 04 05 06 07\n",
        encoding="utf-8",
    )
    assert is_can_bus_logging_csv(str(csv_path)), "fixture must be raw CAN CSV"

    with pytest.raises(ValueError) as excinfo:
        reader_factory(str(csv_path), str(sample_ldf_path))
    assert "LDF" in str(excinfo.value)


def test_a_blf_with_neither_bus_is_refused(tmp_path):
    """python-can skips what it cannot model, so an all-FlexRay log would
    otherwise load as an empty measurement with no error."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_lin_fixture_generator",
        Path(__file__).parent / "fixtures" / "_generate.py",
    )
    generate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generate)

    path = tmp_path / "other_bus.blf"
    generate.generate_other_bus_blf(path)

    with pytest.raises(ValueError) as excinfo:
        reader_factory(str(path), "tests/fixtures/sample.dbc")
    message = str(excinfo.value)
    assert "no CAN or LIN frames" in message


def test_can_measurements_still_load_unchanged(blf_path, asc_path, sample_dbc_path):
    for path in (blf_path, asc_path):
        reader = reader_factory(str(path), str(sample_dbc_path))
        assert reader.has_raw_frames
