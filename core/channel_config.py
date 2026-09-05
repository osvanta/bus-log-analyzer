# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
ChannelConfig — maps bus channels to database file paths (.dbc, .arxml, .ldf).

This is the persistent "vehicle / project configuration" layer, kept
separate from session config (signals, plot layout, measurement path).

Saved as a standalone JSON file (.osvanta_ch):

    {
        "type": "osvanta_channel_config",
        "version": 3,
        "name": "Truck ECU Setup",
        "channels": {
            "CAN:1": "/path/to/Powertrain.dbc",
            "CAN:2": "/path/to/Chassis.arxml",
            "LIN:1": "/path/to/Doors.ldf",
            "CAN:0": "/path/to/FallbackAllCan.dbc"   <- 0 = "All CAN"
        }
    }

Version history: 1 = DBC-only paths; 2 = DBC or ARXML paths; 3 = keys gain a
bus prefix so CAN and LIN channels of the same number stop colliding.  A v1/v2
file has bare integer keys and predates LIN support, so every one of its
channels loads as CAN.  Only version 3 is ever written.

Files written before the Osvanta rename carry the ".canscope_ch" extension
and a "canscope_channel_config" type. Both are still accepted on load; only
the new names are ever written.

Channel number 0 is the per-bus "All Channels" fallback: ``(CAN, 0)`` and
``(LIN, 0)`` are independent, so a LIN database is never pushed into CAN
extraction.

Usage in LoadWorker:
    channel_config.decoder_for(BusType.CAN, channel)  -> DBCDecoder | None
    channel_config.can_decoder_map()                  -> {int | None: decoder}
"""
from __future__ import annotations

import json
from pathlib import Path

from core.bus_types import (
    ALL_CHANNELS_NUMBER,
    BusChannel,
    BusType,
    all_channels_key,
    channel_label,
    decode_key,
    encode_key,
    is_all_channels,
    sort_key,
)

# ── Sentinel: channel number 0 = "All Channels" on a given bus ───────────
ALL_CHANNELS_KEY = ALL_CHANNELS_NUMBER


class ChannelConfig:
    """
    Maps bus channels to database file paths (.dbc, .arxml or .ldf).

    Attributes
    ----------
    name : str
        Human-readable label (e.g. "Truck ECU v2").
    channels : dict[BusChannel, str]
        ``{(bus, channel_num): database_absolute_path}``.  Channel number
        ``ALL_CHANNELS_NUMBER`` (0) is that bus's fallback for unassigned
        channels.
    """

    FILE_EXTENSION = ".osvanta_ch"
    # Pre-rename names. Read, never written.
    LEGACY_FILE_EXTENSION = ".canscope_ch"

    CONFIG_TYPE = "osvanta_channel_config"
    LEGACY_TYPE = "canscope_channel_config"

    #: Written by save(); load() accepts 1, 2 and 3.
    CONFIG_VERSION = 3

    def __init__(
        self,
        name: str = "Unnamed Configuration",
        channels: dict[BusChannel, str] | None = None,
    ) -> None:
        self.name = name
        # (bus, channel) → absolute database path string
        self.channels: dict[BusChannel, str] = dict(channels or {})

    # ── Factory methods ───────────────────────────────────────────────────

    @classmethod
    def from_single_dbc(cls, dbc_path: str) -> "ChannelConfig":
        """Compatibility helper: single-DBC workflow → apply to all CAN channels."""
        return cls(
            name=Path(dbc_path).stem,
            channels={all_channels_key(BusType.CAN): str(dbc_path)},
        )

    @classmethod
    def load(cls, path: str | Path) -> "ChannelConfig":
        """Load a channel config (.osvanta_ch, or a legacy .canscope_ch)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("type") not in (cls.CONFIG_TYPE, cls.LEGACY_TYPE):
            raise ValueError(f"Not a channel config file: {path}")
        return cls(
            name=str(data.get("name", "Unnamed")),
            channels=cls._decode_channels(data.get("channels", {})),
        )

    @staticmethod
    def _decode_channels(raw: dict) -> dict[BusChannel, str]:
        """
        Parse the ``channels`` mapping from any config version.

        v1/v2 keys are bare integers written before LIN support existed, so
        ``decode_key`` reads them as CAN.  Keys that cannot be parsed at all
        are skipped rather than aborting the load — one bad entry must not
        cost the user the rest of a working configuration.
        """
        channels: dict[BusChannel, str] = {}
        for key, value in raw.items():
            try:
                channels[decode_key(key)] = str(value)
            except (ValueError, TypeError):
                continue
        return channels

    def save(self, path: str | Path) -> None:
        """Save to a .osvanta_ch JSON file."""
        data = {
            "type": self.CONFIG_TYPE,
            "version": self.CONFIG_VERSION,
            "name": self.name,
            "channels": {
                encode_key(key): value for key, value in self.channels.items()
            },
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── Query helpers ─────────────────────────────────────────────────────

    def dbc_path_for(self, bus: BusType, channel: int | None) -> str | None:
        """
        Return the database path assigned to *channel* on *bus*, or that bus's
        All-Channels fallback, or None if neither is configured.

        The fallback is deliberately per-bus: a LIN database configured as
        "All LIN" must never be handed to CAN extraction.
        """
        if channel is not None:
            key = (bus, int(channel))
            if key in self.channels:
                return self.channels[key]
        return self.channels.get(all_channels_key(bus))

    def decoder_for(self, bus: BusType, channel: int | None):
        """
        Return a warm DBCDecoder for *channel* on *bus*, or None.

        Decoders are created lazily and cached — the same database file shared
        across channels reuses one decoder instance (saves RAM + parse time).
        """
        path = self.dbc_path_for(bus, channel)
        if not path:
            return None
        return self._decoder_for_path(path)

    def _decoder_for_path(self, path: str):
        if path not in self._decoder_cache:
            from core.dbc_decoder import DBCDecoder
            self._decoder_cache[path] = DBCDecoder(path)
        return self._decoder_cache[path]

    def can_decoder_map(self) -> dict[int | None, object | None]:
        """
        Return the CAN-only, integer-keyed ``{channel: decoder}`` view.

        The raw-CAN decode loops (BLF, ASC, raw-CAN CSV) match decoders against
        ``RawFrame.channel``, which is a plain integer with no bus dimension —
        those formats carry CAN only.  Handing them the bus-tagged
        :attr:`channels` mapping directly would fill the lookup with tuple keys
        that no integer channel can ever match, and decoding would silently
        yield nothing instead of raising.  ``None`` maps to the All-CAN
        fallback decoder, matching the convention those loops already use.

        Call :meth:`build_all_decoders` first; entries whose database failed to
        parse come back as ``None``, exactly as the previous inline lookup did.
        """
        cache = self._decoder_cache
        decoder_map: dict[int | None, object | None] = {}
        for (bus, number), path in self.channels.items():
            if bus != BusType.CAN:
                continue
            channel = None if number == ALL_CHANNELS_NUMBER else number
            decoder_map[channel] = cache.get(path)
        return decoder_map

    def bus_decoder_map(self) -> dict[BusChannel, object | None]:
        """
        Return the bus-tagged ``{(bus, channel): decoder}`` view.

        The counterpart to :meth:`can_decoder_map`, for the one loop that does
        know which bus a frame came from: the bulk vectorised decode, which
        reads the bus out of the raw-frame flags.  ``(bus, 0)`` is that bus's
        All-Channels fallback, so CAN and LIN each get their own — a single
        global fallback would hand an LDF to CAN extraction.

        Call :meth:`build_all_decoders` first; entries whose database failed to
        parse come back as ``None``.
        """
        cache = self._decoder_cache
        return {key: cache.get(path) for key, path in self.channels.items()}

    def build_all_decoders(self) -> dict[str, object]:
        """
        Pre-build and warm all decoders. Called before decode loop starts
        so the first frame doesn't pay the database parse cost.
        Returns {database_path: DBCDecoder}.
        """
        for path in set(self.channels.values()):
            if path not in self._decoder_cache:
                from core.dbc_decoder import DBCDecoder
                self._decoder_cache[path] = DBCDecoder(path)
        return dict(self._decoder_cache)

    def is_empty(self) -> bool:
        return not self.channels

    def buses(self) -> list[BusType]:
        """Return the bus types this config assigns a database to."""
        seen: dict[BusType, None] = {}
        for bus, _number in self.channels:
            seen.setdefault(bus, None)
        return list(seen)

    def has_bus(self, bus: BusType) -> bool:
        """Return whether any channel on *bus* has a database assigned."""
        return any(key_bus == bus for key_bus, _number in self.channels)

    def all_dbc_paths(self, bus: BusType | None = None) -> list[str]:
        """Return deduplicated database paths, optionally for one bus only."""
        return list(dict.fromkeys(
            path for key, path in self.channels.items()
            if bus is None or key[0] == bus
        ))

    def databases_for_bus(self, bus: BusType) -> list[tuple[str, int]]:
        """
        Return ``(path, bus_channel)`` pairs for *bus*, shaped for asammdf's
        ``extract_bus_logging(database_files=...)`` argument.  Channel 0 there
        means "any channel on this bus", which matches this module's
        All-Channels fallback exactly.
        """
        return [
            (path, int(number))
            for (key_bus, number), path in self.channels.items()
            if key_bus == bus
        ]

    def assigned_channels(self, bus: BusType | None = None) -> list[BusChannel]:
        """Return channel keys with a specific assignment (excluding fallbacks)."""
        return [
            key for key in self.channels
            if not is_all_channels(key) and (bus is None or key[0] == bus)
        ]

    def summary(self) -> str:
        """Human-readable summary for the Log tab."""
        lines = [f'Channel config: "{self.name}"']
        for key in sorted(self.channels, key=sort_key):
            lines.append(
                f"  {channel_label(key)} → {Path(self.channels[key]).name}"
            )
        return "\n".join(lines)

    # ── Internal ──────────────────────────────────────────────────────────

    @property
    def _decoder_cache(self) -> dict:
        # Stored on instance to survive across multiple decode runs in the
        # same session without re-parsing the database file.
        try:
            return self.__decoder_cache
        except AttributeError:
            self.__decoder_cache: dict = {}
            return self.__decoder_cache

    def invalidate_cache(self) -> None:
        """Force decoder re-creation (e.g. after a database changes on disk)."""
        try:
            del self.__decoder_cache
        except AttributeError:
            pass
