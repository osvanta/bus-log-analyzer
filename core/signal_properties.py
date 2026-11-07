# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Right-click a signal ▸ Properties: what defines it.

A decoded signal remembers its channel, message and name, not its database, so
its definition is looked up again in the database the channel configuration
assigns to its channel (the same lookup the CAN Trace uses): bits, scaling,
range, value table and message. A signal the measurement file decoded itself,
or a calculated one, gets what is known about it instead, and a signal no
database defines gets the reason.

Deliberately free of Qt imports: nothing in ``core/`` may depend on Qt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.bus_types import BusType, channel_label, coerce_channel_key

#: What a signal's values come from.
DATABASE = "database"
FILE = "file"
CALCULATED = "calculated"
NOT_FOUND = "not found"

# The suffix the decode gives one sender's series of a J1939 message that
# several ECUs send (core.dbc_decoder.source_address_message_name).
_SOURCE_ADDRESS = re.compile(r" \[SA 0x[0-9A-Fa-f]{2}\]$")


@dataclass
class SignalProperties:
    """The Properties window's content: Item/Value rows and the value table."""
    title: str
    source: str
    rows: list[tuple[str, str]] = field(default_factory=list)
    # (raw, physical, label) for each entry of the signal's value table.
    value_table: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class _Definition:
    path: str
    assigned_to: str
    message: object
    signal: object


def database_message_name(message_name: str) -> str:
    """The database's name for a message, without a J1939 sender suffix."""
    return _SOURCE_ADDRESS.sub("", message_name)


def number(value) -> str:
    """A database number as written: 0.01, -10.23, 2047; '' for none."""
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    rounded = round(float(value), 9)
    return str(int(rounded)) if rounded.is_integer() else repr(rounded)


def describe_signal(key: str, series, channel_config, calculated=None) -> SignalProperties:
    """
    Describe the signal stored under *key*.

    *series* is its SignalSeries (None if the open measurement has none),
    *channel_config* the ChannelConfig its databases come from, *calculated*
    its CalculatedSignalDefinition when it is a calculated signal. Reading
    the definition never reads the signal's samples.
    """
    if calculated is not None:
        return _calculated(calculated, series)
    if series is None:
        name = key.rsplit("::", 1)[-1]
        return SignalProperties(name, NOT_FOUND, [
            ("Signal", name),
            ("Source", "Not in the open measurement"),
        ])
    definition, reason = _find_definition(channel_config, series)
    if definition is not None:
        return _from_database(series, definition)
    if not series.message_id:
        return _from_file(series)
    return SignalProperties(series.signal_name, NOT_FOUND, _measurement_rows(series) + [
        ("Source", "Not found in a database"),
        ("Database", reason),
    ])


def _find_definition(channel_config, series) -> tuple[_Definition | None, str]:
    """Return the signal's definition, or None and why there is none."""
    if channel_config is None or channel_config.is_empty():
        return None, "No database is assigned"
    channel = coerce_channel_key(series.channel)
    message_name = database_message_name(series.message_name)
    reasons = []
    for assignment, path in _candidates(channel_config, channel):
        try:
            decoder = channel_config.decoder_for(*assignment)
            database = decoder.database
        except Exception as exc:        # a database that cannot be read
            reasons.append(f"{Path(path).name} could not be read: {exc}")
            continue
        message = _message(database, message_name)
        if message is None:
            reasons.append(f"Message {message_name} is not in {Path(path).name}")
            continue
        signal = next((s for s in message.signals if s.name == series.signal_name), None)
        if signal is None:
            reasons.append(
                f"Message {message_name} in {Path(path).name} "
                f"has no signal {series.signal_name}"
            )
            continue
        return _Definition(path, channel_label(assignment), message, signal), ""
    if not reasons:
        where = channel_label(channel) if channel is not None else "any channel"
        return None, f"No database is assigned to {where}"
    return None, "; ".join(reasons)


def _candidates(channel_config, channel):
    """
    The (assignment, path) pairs to look in, in order: the database assigned
    to the signal's channel, its bus's All-Channels database, then every other
    database on that bus (any bus when the channel is unknown).
    """
    channels = channel_config.channels
    preferred = []
    if channel is not None:
        bus, number_ = channel
        preferred = [(bus, number_), (bus, 0)]
    seen_paths = set()
    ordered = [key for key in preferred if key in channels]
    ordered += [key for key in channels
                if key not in ordered and (channel is None or key[0] == channel[0])]
    for assignment in ordered:
        path = channels[assignment]
        if path in seen_paths:
            continue
        seen_paths.add(path)
        yield assignment, path


def _message(database, name: str):
    """The database message called *name*, including an ARXML contained PDU."""
    for message in database.messages:
        if message.name == name:
            return message
        for contained in getattr(message, "contained_messages", None) or ():
            if contained.name == name:
                return contained
    return None


def _from_database(series, definition: _Definition) -> SignalProperties:
    message, signal = definition.message, definition.signal
    scale = float(signal.scale)
    offset = float(signal.offset)
    rows = [
        ("Source", "Database"),
        ("Signal", signal.name),
        ("Unit", signal.unit or ""),
        ("Start bit", str(signal.start)),
        ("Length", f"{signal.length} bits"),
        ("Byte order", "Intel (little endian)" if signal.byte_order == "little_endian"
         else "Motorola (big endian)"),
        ("Type", "float" if signal.is_float
         else "signed integer" if signal.is_signed else "unsigned integer"),
        ("Factor", number(scale)),
        ("Offset", number(offset)),
        ("Minimum", number(signal.minimum)),
        ("Maximum", number(signal.maximum)),
    ]
    if not signal.is_float:
        low, high = _raw_range(signal.length, signal.is_signed)
        rows.append(("Raw range", f"{low} … {high} = {number(low * scale + offset)} "
                                  f"… {number(high * scale + offset)}"))
    multiplexing = _multiplexing(signal)
    if multiplexing:
        rows.append(("Multiplexing", multiplexing))
    initial = getattr(signal, "raw_initial", None)
    if initial is not None:
        rows.append(("Initial value (raw)", number(initial)))
    if signal.receivers:
        rows.append(("Receivers", ", ".join(signal.receivers)))
    if signal.comment:
        rows.append(("Comment", str(signal.comment)))
    choices = dict(signal.choices or {})
    if choices:
        rows.append(("Value table", f"{len(choices)} entries"))

    lin = _is_lin(series.channel)
    rows += [
        ("Message", message.name),
        ("Message ID", _frame_id(message.frame_id, message.is_extended_frame, lin)),
    ]
    if series.message_id and series.message_id != message.frame_id:
        rows.append(("ID in the measurement",
                     _frame_id(series.message_id, series.message_id > 0x7FF, lin)))
    rows.append(("Message length", f"{message.length} bytes"))
    if getattr(message, "is_fd", False):
        rows.append(("CAN FD", "yes"))
    if message.senders:
        rows.append(("Senders", ", ".join(message.senders)))
    if message.cycle_time:
        rows.append(("Cycle time", f"{message.cycle_time} ms"))
    if message.comment:
        rows.append(("Message comment", str(message.comment)))

    rows += [
        ("Database", Path(definition.path).name),
        ("Database folder", str(Path(definition.path).parent)),
        ("Assigned to", definition.assigned_to),
    ]
    rows += _measurement_rows(series)[-2:]          # its channel and samples
    if not series.message_id and message.frame_id:
        rows.append(("Note", "The measurement file holds this signal already "
                             "decoded; the database's signal of the same name "
                             "is shown"))
    table = [(str(raw), number(raw * scale + offset), str(label))
             for raw, label in sorted(choices.items())]
    return SignalProperties(signal.name, DATABASE, rows, table)


def _raw_range(length: int, signed: bool) -> tuple[int, int]:
    if signed:
        return -(1 << (length - 1)), (1 << (length - 1)) - 1
    return 0, (1 << length) - 1


def _multiplexing(signal) -> str:
    if signal.is_multiplexer:
        return "multiplexer"
    if signal.multiplexer_ids:
        values = ", ".join(str(v) for v in signal.multiplexer_ids)
        return f"when {signal.multiplexer_signal} = {values}"
    return ""


def _is_lin(channel) -> bool:
    channel = coerce_channel_key(channel)
    return channel is not None and channel[0] == BusType.LIN


def _frame_id(frame_id: int, extended: bool, lin: bool = False) -> str:
    if lin:                                     # LIN has no standard/extended
        return f"0x{frame_id:02X}"
    if extended:
        return f"0x{frame_id:08X} (extended)"
    return f"0x{frame_id:03X} (standard)"


def _measurement_rows(series) -> list[tuple[str, str]]:
    """Name, message, unit, then the channel and samples, which stay last."""
    return [
        ("Signal", series.signal_name),
        ("Message", series.message_name),
        ("Unit", series.unit or ""),
        ("Channel", channel_label(series.channel) if series.channel is not None else ""),
        ("Samples", f"{series.sample_count:,}"),
    ]


def _from_file(series) -> SignalProperties:
    rows = [("Source", "Decoded in the measurement file, not from a database")]
    rows += [("Group" if item == "Message" else item, value)
             for item, value in _measurement_rows(series)]
    return SignalProperties(series.signal_name, FILE, rows)


def _calculated(definition, series) -> SignalProperties:
    from core.calculated_signals import CalculatedSignalError, parse_formula
    rows = [
        ("Source", "Calculated signal"),
        ("Signal", definition.name),
        ("Formula", definition.formula),
        ("Unit", definition.unit or ""),
    ]
    try:
        references = parse_formula(definition.formula).references
    except CalculatedSignalError:
        references = ()
    if references:
        rows.append(("Uses", ", ".join(references)))
    if series is not None:
        rows.append(("Samples", f"{series.sample_count:,}"))
    return SignalProperties(definition.name, CALCULATED, rows)
