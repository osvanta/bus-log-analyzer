# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.asammdf_guard import install as _install_asammdf_guard
from core.bus_types import BusChannel, BusType, sort_key
from core.readers.base import MeasurementReader, UnsupportedFormatError

# Before any reader touches asammdf: some measurements made it overrun memory.
_install_asammdf_guard()


# ── Formats that always require DBC (regardless of file content) ──────────
CAN_RAW_SUFFIXES = {'.blf', '.asc'}

# ── All supported measurement file suffixes ───────────────────────────────
ALL_SUFFIXES = {'.blf', '.asc', '.mf4', '.mdf', '.csv'}

# Maximum frames to read during a lightweight pre-scan (keeps it < 1 s)
_PRESCAN_LIMIT = 50_000


@dataclass(frozen=True, slots=True)
class PrescanResult:
    """
    Bus-tagged result of a lightweight channel pre-scan.

    A dataclass rather than a wider tuple on purpose: the previous
    ``(channels, ids_per_channel)`` pair was already unpacked positionally at
    every call site, and adding a third and fourth element would have been
    silently mis-unpacked wherever one was missed.
    """

    channels: list[BusChannel] = field(default_factory=list)
    ids_per_channel: dict[BusChannel, set[int]] = field(default_factory=dict)
    # {channel: {frame id: observed payload length}}, populated for LIN only.
    #
    # LIN clusters all start numbering at 0, so two unrelated databases
    # routinely declare the same frame IDs and an ID-only comparison cannot
    # tell them apart — while their frame lengths usually can. CAN is left out
    # deliberately: its IDs are already distinctive, and scoring CAN on DLC
    # would move match percentages that users have been reading for a while.
    lengths_per_channel: dict[BusChannel, dict[int, int]] = field(
        default_factory=dict
    )

    def __bool__(self) -> bool:
        return bool(self.channels)

    def buses(self) -> list[BusType]:
        """Return the bus types discovered, in a stable order."""
        seen: dict[BusType, None] = {}
        for bus, _number in self.channels:
            seen.setdefault(bus, None)
        return list(seen)


def prescan_measurement(
    path: str,
    progress: Callable[[str], None] | None = None,
) -> PrescanResult:
    """
    Lightweight pre-scan: read up to *_PRESCAN_LIMIT* raw frames from *path*
    and return the bus-tagged channels and frame IDs without decoding signals.

    Works for BLF, ASC, MDF bus logging (CAN and LIN), and raw CAN CSV
    exports. Pre-decoded CSV/MDF files return an empty result (channels come
    from decoded signals instead).

    Typically finishes in < 1 second even for 100 MB+ files.
    """
    suffix = Path(path).suffix.lower()
    ids_per_channel: dict[BusChannel, set[int]] = {}

    if suffix == '.csv':
        from core.readers.csv_reader import (
            is_can_bus_logging_csv,
            prescan_can_bus_logging_csv,
        )
        if not is_can_bus_logging_csv(path):
            return PrescanResult()
        if progress:
            progress("Scanning measurement for channels…")
        try:
            csv_channels, csv_ids = prescan_can_bus_logging_csv(
                path, _PRESCAN_LIMIT
            )
        except Exception:
            return PrescanResult()
        # Raw CAN CSV exports carry CAN only.
        return PrescanResult(
            channels=[(BusType.CAN, int(ch)) for ch in csv_channels],
            ids_per_channel={
                (BusType.CAN, int(ch)): set(ids) for ch, ids in csv_ids.items()
            },
        )

    if suffix not in CAN_RAW_SUFFIXES and suffix not in ('.mf4', '.mdf'):
        return PrescanResult()

    is_mdf = suffix in ('.mf4', '.mdf')
    content = None
    if is_mdf:
        from core.readers.mdf_reader import MDFReader
        content = MDFReader.content_info(path)
        if not content.has_raw_bus:
            return PrescanResult()

    if progress:
        progress("Scanning measurement for channels…")

    # CAN comes from python-can's readers, which handle BLF, ASC and the
    # CAN_DataFrame groups of an MF4 uniformly.
    if not is_mdf or content.has_raw_can:
        can_channels: set[int] = set()
        can_ids: dict[int, set[int]] = {}
        try:
            _prescan_can_messages(path, suffix, can_channels, can_ids)
        except Exception:
            pass
        for channel in can_channels:
            ids_per_channel.setdefault((BusType.CAN, channel), set()).update(
                can_ids.get(channel, ())
            )

    lengths_per_channel: dict[BusChannel, dict[int, int]] = {}

    # LIN never comes from python-can — its MF4Reader understands only
    # CAN_DataFrame groups, and its BLF and ASC readers skip LIN objects and
    # lines outright.
    if is_mdf:
        if content.has_raw_lin:
            try:
                _prescan_lin_frames(path, ids_per_channel, lengths_per_channel)
            except Exception:
                pass
    else:
        try:
            _prescan_lin_raw(path, ids_per_channel, lengths_per_channel)
        except Exception:
            pass

    return PrescanResult(
        channels=sorted(ids_per_channel, key=sort_key),
        ids_per_channel=ids_per_channel,
        lengths_per_channel=lengths_per_channel,
    )


def _prescan_lin_raw(
    path: str,
    ids_per_channel: dict[BusChannel, set[int]],
    lengths_per_channel: dict[BusChannel, dict[int, int]],
) -> None:
    """Collect LIN channels, frame IDs and lengths from a BLF or ASC log.

    Reads through the same packed-batch path the loader uses, so what the
    Database Manager offers and what actually decodes cannot drift apart.
    """
    suffix = Path(path).suffix.lower()
    if suffix == '.blf':
        from core.blf_reader import BLFReaderService
        batches = BLFReaderService(path).iter_raw_batches()
    elif suffix == '.asc':
        from core.readers.asc_can_reader import ASCCANReader
        batches = ASCCANReader.__new__(ASCCANReader)
        batches._path = Path(path)
        batches = batches.iter_raw_batches()
    else:
        return

    from core.raw_frame_store import FLAG_LIN

    seen = 0
    for _base_ts, timestamps, channels, arb_ids, dlcs, _dirs, flags, _data in batches:
        for index in range(len(timestamps)):
            if not flags[index] & FLAG_LIN:
                continue
            key = (BusType.LIN, int(channels[index]))
            frame_id = int(arb_ids[index])
            ids_per_channel.setdefault(key, set()).add(frame_id)
            # Last observation wins; a frame's length does not vary within a
            # cluster, and disagreement means the log is not what it claims.
            lengths_per_channel.setdefault(key, {})[frame_id] = int(dlcs[index])
        seen += len(timestamps)
        if seen >= _PRESCAN_LIMIT:
            break


def _prescan_lin_frames(
    path: str,
    ids_per_channel: dict[BusChannel, set[int]],
    lengths_per_channel: dict[BusChannel, dict[int, int]] | None = None,
) -> None:
    """Collect LIN channels, frame IDs and lengths from ``LIN_Frame`` groups."""
    import asammdf
    import numpy as np

    from core.readers.mdf_reader import MDFReader

    mdf = asammdf.MDF(str(path))
    try:
        for group_idx, group in enumerate(mdf.groups):
            if not MDFReader._is_raw_lin_group(group):
                continue
            parent_idx = next(
                (
                    idx for idx, channel in enumerate(group.channels)
                    if (getattr(channel, "name", "") or "") == "LIN_Frame"
                ),
                None,
            )
            if parent_idx is None:
                continue
            # record_count bounds the read the same way _PRESCAN_LIMIT bounds
            # the python-can loop, so a huge recording stays sub-second.
            signal = mdf.get(
                group=group_idx,
                index=parent_idx,
                raw=True,
                record_count=_PRESCAN_LIMIT,
            )
            samples = np.asarray(signal.samples)
            names = samples.dtype.names or ()

            def field_named(suffix):
                exact = f"LIN_Frame.{suffix}"
                if exact in names:
                    return samples[exact]
                match = next(
                    (n for n in names if n.endswith(f".{suffix}")), None
                )
                return samples[match] if match else None

            frame_ids = field_named("ID")
            if frame_ids is None:
                continue
            buses = field_named("BusChannel")
            if buses is None:
                buses = np.ones(len(frame_ids), dtype=np.uint8)

            data_lengths = field_named("DataLength")
            frame_ids = np.asarray(frame_ids, dtype=np.uint32) & 0x1FFFFFFF
            buses = np.asarray(buses)
            for bus_number in np.unique(buses):
                key = (BusType.LIN, int(bus_number))
                mask = buses == bus_number
                selected = frame_ids[mask]
                ids_per_channel.setdefault(key, set()).update(
                    int(value) for value in np.unique(selected)
                )
                if lengths_per_channel is None or data_lengths is None:
                    continue
                selected_lengths = np.asarray(data_lengths)[mask]
                lengths = lengths_per_channel.setdefault(key, {})
                for frame_id, length in zip(selected, selected_lengths):
                    lengths[int(frame_id)] = int(length)
    finally:
        try:
            mdf.close()
        except Exception:
            pass


def _prescan_can_messages(
    path: str,
    suffix: str,
    channels: set[int],
    ids_per_channel: dict[int, set[int]],
) -> None:
    """Iterate raw CAN messages from *path* using python-can readers."""
    import can

    if suffix == '.blf':
        reader = can.BLFReader(str(path))
    elif suffix == '.asc':
        reader = can.ASCReader(str(path))
    else:
        reader = can.MF4Reader(str(path))

    is_mdf = suffix in ('.mf4', '.mdf')
    count = 0
    with reader:
        for msg in reader:
            if not hasattr(msg, 'arbitration_id'):
                continue
            raw_ch = getattr(msg, 'channel', None)
            if is_mdf:
                ch = int(raw_ch) if isinstance(raw_ch, (int, float)) else None
            else:
                ch = (int(raw_ch) + 1) if isinstance(raw_ch, (int, float)) else None
            if ch is not None:
                channels.add(ch)
                ids_per_channel.setdefault(ch, set()).add(
                    int(msg.arbitration_id)
                )
            count += 1
            if count >= _PRESCAN_LIMIT:
                break


def dbc_required_for(path: str) -> bool:
    """
    Return True when the measurement file needs a database to decode signals.

    For .blf / .asc: always True.
    For .mf4 / .mdf: True only for raw-only bus recordings (CAN or LIN). Mixed
        MDFs containing existing decoded signals can load those signals without
        a database, so they return False.
    For .csv: True for raw CAN-frame exports; False for decoded signal CSV.

    "Required to decode signals" is not the same as "required to open the
    file" — see :func:`database_mandatory_for`.
    """
    suffix = Path(path).suffix.lower()
    if suffix in CAN_RAW_SUFFIXES:
        return True
    if suffix in ('.mf4', '.mdf'):
        from core.readers.mdf_reader import MDFReader
        content = MDFReader.content_info(path)
        return content.has_raw_bus and not content.has_decoded_signals
    if suffix == '.csv':
        from core.readers.csv_reader import is_can_bus_logging_csv
        return is_can_bus_logging_csv(path)
    return False


def database_mandatory_for(path: str) -> bool:
    """
    Return True when :func:`reader_factory` refuses the file without a database.

    Raw CAN has no readable form without a database — python-can's readers
    yield frames, not signals — so those formats hard-fail. A raw *LIN* MDF is
    different: its ``LIN_Frame`` columns are ordinary MDF channels, so the file
    still opens and shows them, and a database only upgrades those columns into
    decoded signals. Callers use this to decide whether to block a load or
    merely suggest configuring a database.
    """
    suffix = Path(path).suffix.lower()
    if suffix in CAN_RAW_SUFFIXES:
        return True
    if suffix == '.csv':
        from core.readers.csv_reader import is_can_bus_logging_csv
        return is_can_bus_logging_csv(path)
    if suffix in ('.mf4', '.mdf'):
        from core.readers.mdf_reader import MDFReader
        content = MDFReader.content_info(path)
        return content.has_raw_can and not content.has_decoded_signals
    return False


def has_mixed_mdf_content(path: str) -> bool:
    """Return whether an MDF contains decoded signals and raw CAN frames."""
    if Path(path).suffix.lower() not in ('.mf4', '.mdf'):
        return False
    from core.readers.mdf_reader import MDFReader
    return MDFReader.content_info(path).is_mixed


def _reject_ldf_database(measurement_path: str, dbc_path: str) -> None:
    """Refuse an LDF for a format that carries no LIN traffic.

    BLF, ASC and MF4 all decode LIN, so an LDF is legitimate there. A raw CAN
    CSV export is not — it has no bus dimension at all — and without this the
    LDF would be loaded, matched against nothing, and reported as a database
    that simply does not fit the file.
    """
    if Path(dbc_path).suffix.lower() != '.ldf':
        return
    measurement = Path(measurement_path)
    raise ValueError(
        f"'{Path(dbc_path).name}' is an LDF (LIN description file), which "
        f"cannot be used with '{measurement.name}'.\n"
        "LDF databases apply to LIN traffic, and this format carries CAN only.\n"
        "Assign a DBC or ARXML instead."
    )


def _reject_unreadable_blf(measurement_path: str) -> None:
    """Refuse a BLF holding neither CAN nor LIN traffic.

    python-can yields CAN objects and skips every other type, so a log made
    entirely of some other bus reads as an empty file rather than an
    unsupported one — no error, and nothing to point at. Naming the emptiness
    is the difference between an unsupported measurement and an apparently
    broken application.
    """
    from core.readers.blf_content import blf_bus_content
    content = blf_bus_content(measurement_path)
    if content.has_can or content.has_lin or content.truncated:
        return
    raise ValueError(
        f"'{Path(measurement_path).name}' contains no CAN or LIN frames.\n"
        "Vector logs can also carry FlexRay, Ethernet, MOST and other buses, "
        "which this application does not decode."
    )


def reader_factory(
    measurement_path: str,
    dbc_path: str | None = None,
) -> MeasurementReader:
    """
    Return the appropriate MeasurementReader for *measurement_path*.

    MDF4 / MDF routing
    ------------------
    A metadata-only probe distinguishes decoded, raw-only, and mixed MDFs:
    - Raw-only MDF → ``MDFCANReader`` and a database is required.
    - Decoded MDF → ``MDFReader`` without a database.
    - Mixed MDF without a database → ``MDFReader`` (decoded signals win).
    - Mixed MDF with a database → ``MDFCANReader`` combines existing
      decoded channels with DBC-decoded CAN signals and raw CAN Trace.

    Raises
    ------
    UnsupportedFormatError  – file extension is not recognised
    ValueError              – DBC or ARXML required for format but not supplied
    """
    suffix = Path(measurement_path).suffix.lower()

    if suffix == '.blf':
        # Checked before the database, because a BLF holding no decodable bus
        # is unreadable whichever database is chosen. Reporting the database
        # first would send the user looking for a different DBC for a file
        # that has no frames to decode with it.
        _reject_unreadable_blf(measurement_path)
        if not dbc_path:
            raise ValueError(
                "A DBC, ARXML or LDF file is required for BLF files."
            )
        from core.readers.blf_can_reader import BLFCANReader
        from core.dbc_decoder import DBCDecoder
        return BLFCANReader(measurement_path, DBCDecoder(dbc_path))

    if suffix == '.asc':
        if not dbc_path:
            raise ValueError(
                "A DBC, ARXML or LDF file is required for ASC files."
            )
        from core.readers.asc_can_reader import ASCCANReader
        from core.dbc_decoder import DBCDecoder
        return ASCCANReader(measurement_path, DBCDecoder(dbc_path))

    if suffix in ('.mf4', '.mdf'):
        from core.readers.mdf_reader import MDFReader
        content = MDFReader.content_info(measurement_path)
        if content.has_raw_bus and (
            not content.has_decoded_signals or dbc_path
        ):
            # Raw-only, or mixed with an explicitly configured database.  For
            # mixed files MDFCANReader preserves the native decoded channels
            # and appends the database-decoded raw-bus signals.
            if not dbc_path:
                if content.has_raw_can:
                    raise ValueError(
                        "This MDF file contains raw CAN bus frames.\n"
                        "A DBC or ARXML file is required for signal decoding.\n"
                        "Please configure channel → database mapping via 'Open Database'."
                    )
                # LIN-only with no database: the LIN_Frame columns are still
                # readable as ordinary MDF channels, so open the file rather
                # than refusing it. Assigning an LDF or DBC on the next load
                # turns those columns into decoded signals.
                return MDFReader(measurement_path)
            from core.readers.mdf_can_reader import MDFCANReader
            return MDFCANReader(measurement_path, dbc_path)
        else:
            # Decoded-only, or mixed with no database: prefer existing signals.
            return MDFReader(measurement_path)

    if suffix == '.csv':
        from core.readers.csv_reader import (
            CSVRawCANReader,
            CSVSignalReader,
            is_can_bus_logging_csv,
        )
        if is_can_bus_logging_csv(measurement_path):
            if not dbc_path:
                raise ValueError(
                    "This CSV file contains raw CAN bus frames.\n"
                    "A DBC or ARXML file is required for signal decoding.\n"
                    "Please configure channel → database mapping via 'Open Database'."
                )
            _reject_ldf_database(measurement_path, dbc_path)
            from core.dbc_decoder import DBCDecoder
            return CSVRawCANReader(
                measurement_path, DBCDecoder(dbc_path)
            )
        return CSVSignalReader(measurement_path)

    raise UnsupportedFormatError(
        f"Unsupported measurement file format: '{suffix}'. "
        f"Supported: {', '.join(sorted(ALL_SUFFIXES))}"
    )


__all__ = [
    "MeasurementReader",
    "UnsupportedFormatError",
    "PrescanResult",
    "reader_factory",
    "dbc_required_for",
    "database_mandatory_for",
    "has_mixed_mdf_content",
    "prescan_measurement",
    "CAN_RAW_SUFFIXES",
    "ALL_SUFFIXES",
]
