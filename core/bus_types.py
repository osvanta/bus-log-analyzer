# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Bus type as a first-class part of channel identity.

An MF4 bus-logging file declares which bus each channel belongs to: CAN
channels appear as ``CAN_DataFrame`` groups, LIN channels as ``LIN_Frame``
groups, and each bus numbers its channels independently.  ``CAN 1`` and
``LIN 1`` can therefore both exist in one measurement, which is why a channel
is identified by the pair ``(bus, number)`` rather than by a bare integer.

The bus is always *discovered from the file*, never chosen by the user.  The
user picks a channel ("LIN 1") and the bus rides along, so a bus/database
mismatch cannot be expressed in the first place.

Deliberately free of Qt imports: this module is used from ``core/`` as well as
``gui/``, and nothing in ``core/`` may depend on Qt.
"""
from __future__ import annotations

from enum import Enum


class BusType(str, Enum):
    """A vehicle bus that can appear in a measurement file.

    Values match the bus keys asammdf's ``extract_bus_logging`` accepts
    (``"CAN"`` / ``"LIN"``), so a member can be passed straight through as a
    ``database_files`` key.  Inheriting ``str`` also keeps the pair
    ``(BusType, int)`` sortable and JSON-friendly.
    """

    CAN = "CAN"
    LIN = "LIN"

    def __str__(self) -> str:            # pragma: no cover - trivial
        return self.value


#: A channel identity: which bus, and which channel number on that bus.
#: Channel number 0 is the per-bus "all channels" fallback.
BusChannel = tuple[BusType, int]

#: Channel number meaning "every channel on this bus that has no specific
#: database assigned".  Per-bus: ``(CAN, 0)`` and ``(LIN, 0)`` are independent,
#: so a LIN database can never be applied to CAN extraction.
ALL_CHANNELS_NUMBER = 0

#: Separator for the serialised ``"CAN:1"`` form used in channel config files.
_KEY_SEP = ":"


def coerce_channel_key(
    channel: BusChannel | int | None,
    bus: BusType = BusType.CAN,
) -> BusChannel | None:
    """
    Normalise any channel value to a bus-tagged key.

    A bare integer means CAN. That is not a guess: the formats that carry no
    bus dimension at all — BLF, ASC, raw CAN CSV — are CAN-only, and every
    channel config written before this bus dimension existed was likewise CAN.

    Every helper below coerces through here so a stray integer from an older
    call site, a legacy config, or a test formats and sorts correctly instead
    of raising deep inside a comparison.
    """
    if channel is None:
        return None
    if isinstance(channel, tuple):
        return channel
    return (bus, int(channel))


def all_channels_key(bus: BusType) -> BusChannel:
    """Return the "all channels" fallback key for *bus*."""
    return (bus, ALL_CHANNELS_NUMBER)


def is_all_channels(key: BusChannel) -> bool:
    """Return whether *key* is a per-bus "all channels" fallback."""
    return key[1] == ALL_CHANNELS_NUMBER


def channel_label(key: BusChannel | int | None) -> str:
    """
    Return the user-facing label for a channel key.

    ``(CAN, 1)`` → ``"CAN 1"``, ``(LIN, 1)`` → ``"LIN 1"``,
    ``(CAN, 0)`` → ``"All CAN"``, ``None`` → ``"Unknown"``.

    Every place that used to hardcode ``f"CAN {ch}"`` goes through here, so a
    LIN channel can never be mislabelled as CAN.
    """
    key = coerce_channel_key(key)
    if key is None:
        return "Unknown"
    bus, number = key
    bus_name = bus.value if isinstance(bus, BusType) else str(bus)
    if number == ALL_CHANNELS_NUMBER:
        return f"All {bus_name}"
    return f"{bus_name} {number}"


def store_key_prefix(key: BusChannel | int | None) -> str:
    """
    Return the channel prefix used inside SignalStore signal keys.

    Signal keys (``"CH1::EngineData::RPM"``) are written into saved session
    configs and matched back on load, so the CAN form **must** stay byte
    identical to what shipped before the bus dimension existed — otherwise
    every previously saved plot silently fails to restore. Hence ``CH1`` for
    CAN rather than the nicer ``CAN 1``, which is used for display only.

    ``(CAN, 1)`` → ``"CH1"``, ``(LIN, 1)`` → ``"LIN1"``, ``None`` → ``"CH?"``.
    """
    key = coerce_channel_key(key)
    if key is None:
        return "CH?"
    bus, number = key
    if bus is BusType.CAN or bus == BusType.CAN.value:
        return f"CH{number}"
    bus_name = bus.value if isinstance(bus, BusType) else str(bus)
    return f"{bus_name}{number}"


def encode_key(key: BusChannel) -> str:
    """Serialise a channel key to its ``"CAN:1"`` config-file form."""
    bus, number = key
    bus_name = bus.value if isinstance(bus, BusType) else str(bus)
    return f"{bus_name}{_KEY_SEP}{int(number)}"


def decode_key(raw: str | int, default_bus: BusType = BusType.CAN) -> BusChannel:
    """
    Parse a channel key from a config file.

    Accepts the current ``"CAN:1"`` form and the pre-v3 bare-integer form
    (``"1"`` or ``1``), which predates LIN support and is always CAN.  An
    unrecognised bus name also falls back to *default_bus* rather than raising:
    a config written by a newer version must still open, minus what this
    version cannot represent.
    """
    if isinstance(raw, int):
        return (default_bus, raw)

    text = str(raw).strip()
    if _KEY_SEP in text:
        bus_part, _, number_part = text.partition(_KEY_SEP)
        try:
            bus = BusType(bus_part.strip().upper())
        except ValueError:
            bus = default_bus
        return (bus, int(number_part))

    return (default_bus, int(text))


def sort_key(key: BusChannel | int | None) -> tuple[int, str, int]:
    """
    Ordering for channel keys that tolerates ``None``.

    Unknown-channel signals sort last, mirroring the ``999999`` sentinel the
    integer-keyed code used before the bus dimension existed.
    """
    key = coerce_channel_key(key)
    if key is None:
        return (1, "", 0)
    bus, number = key
    bus_name = bus.value if isinstance(bus, BusType) else str(bus)
    return (0, bus_name, int(number))


__all__ = [
    "BusType",
    "BusChannel",
    "ALL_CHANNELS_NUMBER",
    "coerce_channel_key",
    "all_channels_key",
    "is_all_channels",
    "channel_label",
    "store_key_prefix",
    "encode_key",
    "decode_key",
    "sort_key",
]
