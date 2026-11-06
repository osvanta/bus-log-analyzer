# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Right-click a signal ▸ Properties (TODO feature 18): what the database says
about a signal, looked up again from its channel's database; what is known
about a signal the file decoded itself or a calculated one; and why, when no
database defines it.
"""
from __future__ import annotations

import array

import pytest

from core.bus_types import BusType
from core.calculated_signals import CalculatedSignalDefinition
from core.channel_config import ChannelConfig
from core.signal_properties import (
    CALCULATED, DATABASE, FILE, NOT_FOUND, database_message_name, describe_signal, number,
)
from core.signal_store import SignalSeries

EEC1_ID = 0x0CF004FE            # J1939 EEC1 from source address 0xFE in the database

_DBC = """\
VERSION ""

NS_ :

BS_:

BU_: ACC ESC EMS

BO_ 1056 ACC_Ctrl: 8 ACC
 SG_ ACC_ComfBandLower : 16|11@1+ ({factor},-10.23) [-10.23|10.24] "m/s2" ESC
 SG_ ACC_Temp : 39|8@0- (0.5,0) [-64|63.5] "degC" Vector__XXX

BO_ {eec1} EEC1: 8 EMS
 SG_ EngSpeed : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX

BO_ 1100 Muxed: 8 ESC
 SG_ Mux M : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ PageOne m1 : 8|16@1+ (1,0) [0|65535] "" Vector__XXX

CM_ BO_ 1056 "ACC control";
CM_ SG_ 1056 ACC_ComfBandLower "Lower comfort band";
BA_DEF_ BO_ "GenMsgCycleTime" INT 0 10000;
BA_DEF_DEF_ "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 1056 50;
VAL_ 1056 ACC_ComfBandLower 0 "Default" 2047 "Invalid" ;
"""


def _dbc(folder, name="acc.dbc", factor="0.01"):
    path = folder / name
    path.write_text(_DBC.format(factor=factor, eec1=EEC1_ID | 0x80000000), encoding="utf-8")
    return str(path)


def _series(signal, message="ACC_Ctrl", message_id=1056, channel=(BusType.CAN, 1),
            unit="m/s2", samples=3):
    return SignalSeries(channel, message, message_id, signal, unit,
                        array.array("d", range(samples)), array.array("d", [0.0] * samples))


def _describe(series, config, calculated=None):
    return describe_signal(series.key if series else "CH1::X::Y", series, config, calculated)


@pytest.fixture()
def config(tmp_path):
    return ChannelConfig.from_single_dbc(_dbc(tmp_path))


def test_a_database_signal_shows_its_definition(config, tmp_path):
    properties = _describe(_series("ACC_ComfBandLower"), config)
    rows = dict(properties.rows)

    assert properties.source == DATABASE
    assert properties.title == "ACC_ComfBandLower"
    assert rows["Start bit"] == "16"
    assert rows["Length"] == "11 bits"
    assert rows["Byte order"] == "Intel (little endian)"
    assert rows["Type"] == "unsigned integer"
    assert (rows["Factor"], rows["Offset"]) == ("0.01", "-10.23")
    assert (rows["Minimum"], rows["Maximum"]) == ("-10.23", "10.24")
    assert rows["Raw range"] == "0 … 2047 = -10.23 … 10.24"
    assert rows["Unit"] == "m/s2"
    assert rows["Receivers"] == "ESC"
    assert rows["Comment"] == "Lower comfort band"
    assert rows["Message"] == "ACC_Ctrl"
    assert rows["Message ID"] == "0x420 (standard)"
    assert rows["Message length"] == "8 bytes"
    assert rows["Senders"] == "ACC"
    assert rows["Cycle time"] == "50 ms"
    assert rows["Message comment"] == "ACC control"
    assert rows["Database"] == "acc.dbc"
    assert rows["Database folder"] == str(tmp_path)
    assert rows["Assigned to"] == "All CAN"
    assert rows["Channel"] == "CAN 1"
    assert rows["Samples"] == "3"


def test_a_signed_motorola_signal(config):
    rows = dict(_describe(_series("ACC_Temp", unit="degC"), config).rows)

    assert rows["Byte order"] == "Motorola (big endian)"
    assert rows["Type"] == "signed integer"
    assert rows["Raw range"] == "-128 … 127 = -64 … 63.5"
    assert "Value table" not in rows


def test_the_value_table_gives_each_raw_value_its_physical_value(config):
    # Issue 20: raw 0 is "Default", and that is a physical -10.23, not 0.
    properties = _describe(_series("ACC_ComfBandLower"), config)

    assert properties.value_table == [("0", "-10.23", "Default"), ("2047", "10.24", "Invalid")]
    assert dict(properties.rows)["Value table"] == "2 entries"


def test_the_channels_own_database_wins_over_all_channels(tmp_path):
    config = ChannelConfig(channels={
        (BusType.CAN, 0): _dbc(tmp_path, "all.dbc", factor="0.01"),
        (BusType.CAN, 2): _dbc(tmp_path, "can2.dbc", factor="0.02"),
    })

    on_can2 = dict(_describe(_series("ACC_ComfBandLower", channel=(BusType.CAN, 2)), config).rows)
    on_can1 = dict(_describe(_series("ACC_ComfBandLower", channel=(BusType.CAN, 1)), config).rows)

    assert (on_can2["Database"], on_can2["Assigned to"], on_can2["Factor"]) == ("can2.dbc", "CAN 2", "0.02")
    assert (on_can1["Database"], on_can1["Assigned to"], on_can1["Factor"]) == ("all.dbc", "All CAN", "0.01")


def test_a_lin_signal_finds_its_lin_database_and_has_no_can_id_kind(tmp_path):
    config = ChannelConfig(channels={(BusType.LIN, 0): _dbc(tmp_path)})

    rows = dict(_describe(_series("ACC_ComfBandLower", channel=(BusType.LIN, 1)), config).rows)

    assert (rows["Assigned to"], rows["Channel"]) == ("All LIN", "LIN 1")
    assert rows["Message ID"] == "0x420"


def test_a_lin_database_is_not_searched_for_a_can_signal(tmp_path):
    config = ChannelConfig(channels={(BusType.LIN, 0): _dbc(tmp_path)})

    properties = _describe(_series("ACC_ComfBandLower"), config)

    assert properties.source == NOT_FOUND
    assert dict(properties.rows)["Database"] == "No database is assigned to CAN 1"


def test_a_j1939_senders_series_finds_its_message(config):
    series = _series("EngSpeed", message="EEC1 [SA 0x00]", message_id=0x0CF00400, unit="rpm")

    rows = dict(_describe(series, config).rows)

    assert rows["Message"] == "EEC1"
    assert rows["Message ID"] == "0x0CF004FE (extended)"
    assert rows["ID in the measurement"] == "0x0CF00400 (extended)"
    assert database_message_name("EEC1 [SA 0x00]") == "EEC1"


def test_multiplexed_signals_say_under_which_value(config):
    mux = dict(_describe(_series("Mux", message="Muxed", message_id=1100, unit=""), config).rows)
    page = dict(_describe(_series("PageOne", message="Muxed", message_id=1100, unit=""), config).rows)

    assert mux["Multiplexing"] == "multiplexer"
    assert page["Multiplexing"] == "when Mux = 1"


def test_a_message_missing_from_the_database_says_so(config):
    properties = _describe(_series("Nothing", message="Unknown", message_id=0x123), config)

    assert properties.source == NOT_FOUND
    assert dict(properties.rows)["Database"] == "Message Unknown is not in acc.dbc"


def test_a_signal_missing_from_its_message_says_so(config):
    properties = _describe(_series("Nothing"), config)

    assert dict(properties.rows)["Database"] == "Message ACC_Ctrl in acc.dbc has no signal Nothing"


def test_without_a_database(tmp_path):
    properties = _describe(_series("ACC_ComfBandLower"), ChannelConfig())

    assert properties.source == NOT_FOUND
    assert dict(properties.rows)["Database"] == "No database is assigned"


def test_a_database_that_cannot_be_read_gives_the_reason(tmp_path):
    bad = tmp_path / "bad.dbc"
    bad.write_text("BO_ this is not a database", encoding="utf-8")

    properties = _describe(_series("ACC_ComfBandLower"), ChannelConfig.from_single_dbc(str(bad)))

    assert properties.source == NOT_FOUND
    assert dict(properties.rows)["Database"].startswith("bad.dbc could not be read: ")


def test_a_signal_the_file_decoded_itself():
    series = _series("VehicleSpeed", message="CAN_Group", message_id=0, channel=None, unit="km/h")

    properties = _describe(series, ChannelConfig())
    rows = dict(properties.rows)

    assert properties.source == FILE
    assert rows["Source"] == "Decoded in the measurement file, not from a database"
    assert (rows["Group"], rows["Unit"], rows["Samples"]) == ("CAN_Group", "km/h", "3")
    assert properties.value_table == []


def test_a_file_signal_named_as_in_the_database_is_shown_with_a_note(config):
    series = _series("ACC_ComfBandLower", message_id=0, channel=None)

    properties = _describe(series, config)

    assert properties.source == DATABASE
    assert "already decoded" in dict(properties.rows)["Note"]


def test_a_calculated_signal_shows_its_formula(config):
    source = "CH1::ACC_Ctrl::ACC_ComfBandLower"
    definition = CalculatedSignalDefinition("Doubled", f"`{source}` * 2", "m/s2")

    properties = describe_signal(definition.key, None, config, definition)
    rows = dict(properties.rows)

    assert properties.source == CALCULATED
    assert rows["Formula"] == f"`{source}` * 2"
    assert rows["Uses"] == source
    assert rows["Unit"] == "m/s2"


def test_a_key_the_measurement_does_not_have(config):
    properties = describe_signal("CH1::ACC_Ctrl::Gone", None, config)

    assert properties.source == NOT_FOUND
    assert dict(properties.rows)["Signal"] == "Gone"


@pytest.mark.parametrize("value, text", [
    (None, ""), (0.01, "0.01"), (-10.23, "-10.23"), (2047, "2047"),
    (2047 * 0.01 - 10.23, "10.24"), (65535.0, "65535"),
])
def test_numbers_are_written_as_in_the_database(value, text):
    assert number(value) == text
