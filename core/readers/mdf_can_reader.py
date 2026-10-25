# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
MDFCANReader — reads ASAM MDF4 bus logging files (.mf4 / .mdf) that store
raw CAN frames using the ``CAN_DataFrame.*`` channel group structure.

python-can's ``MF4Reader`` wraps asammdf internally and yields
``can.Message`` objects — the exact same interface as ``BLFReader`` and
``ASCReader``.  This means the entire existing DBC decode pipeline
(DBCDecoder, LoadWorker, RawFrameStore, DBC Manager) works without any
changes.

Requires both ``asammdf>=7.0`` and the MF4 support in ``python-can>=4.6``.

Usage in ``reader_factory``::

    if MDFReader.is_bus_logging(path):          # fast probe, < 50 ms
        return MDFCANReader(path, DBCDecoder(dbc_path))
    else:
        return MDFReader(path)                   # pre-decoded path

``BusChannel`` field maps to CAN channel numbers directly — 1-indexed
as stored in the MDF file (Vector / ASAM convention is 1-based).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator
import re

import can
import numpy as np

from core.bus_types import BusType
from core.models import RawFrame, DecodedSignalSample
from core.dbc_decoder import (
    DBCDecoder,
    multi_sender_messages,
    source_address_message_name,
)
from core.raw_frame_store import FLAG_BRS, FLAG_ERROR, FLAG_LIN, FLAG_REMOTE
from core.readers.mdf_reader import MDFReader, MDFSampleSource, _channel_failure_text
from core.readers.mdf_recovery import (
    make_bounded_mdf4_copy,
    remove_recovery_copy,
)


class MDFCANReadError(RuntimeError):
    pass


# The ASAM MDF bus-logging groups of CAN frames, and the flag each sets.
_CAN_FRAME_ROOTS = {
    "CAN_DataFrame": 0,
    "CAN_ErrorFrame": FLAG_ERROR,
    "CAN_RemoteFrame": FLAG_REMOTE,
}

#: Raw frame records read per get(): a CAN FD record is up to about 80 bytes.
_TRACE_CHUNK_RECORDS = 1 << 20
#: asammdf's read fragment for DBC extraction; see _bus_decode().
_EXTRACT_FRAGMENT_BYTES = 64 << 20


def _record_chunks(source, group_idx, index, group):
    """Yield the raw records of *group* as Signals, a chunk at a time."""
    cycles = int(
        getattr(getattr(group, "channel_group", None), "cycles_nr", 0) or 0
    )
    if cycles <= 0:
        yield source.get(group=group_idx, index=index, raw=True)
        return
    for offset in range(0, cycles, _TRACE_CHUNK_RECORDS):
        yield source.get(
            group=group_idx,
            index=index,
            raw=True,
            record_offset=offset,
            record_count=min(_TRACE_CHUNK_RECORDS, cycles - offset),
        )


def _bus_logging_keys(mdf) -> dict[str, list[int]]:
    """
    The keys asammdf's ``bus_logging_map`` would have, read a chunk at a time.

    asammdf builds that map while opening a file unless told not to
    (``process_bus_logging=False``), from per-frame arrays of each whole
    bus-logging group: for a long log, memory in proportion to it. Only the
    keys are used here: the CAN bus channels in the data, and (asammdf's own
    LIN layout) the LIN frame IDs of groups without an attached database.
    """
    from asammdf.blocks import v4_constants as v4c

    keys: dict[str, set[int]] = {"CAN": set(), "LIN": set()}
    for group_idx, group in enumerate(getattr(mdf, "groups", ())):
        channel_group = getattr(group, "channel_group", None)
        if channel_group is None or not channel_group.flags & v4c.FLAG_CG_BUS_EVENT:
            continue
        source = getattr(channel_group, "acq_source", None)
        if not source:
            continue
        names = [getattr(channel, "name", "") for channel in group.channels]
        cycles = int(getattr(channel_group, "cycles_nr", 0) or 0)
        reads = []
        if (
            source.bus_type in (v4c.BUS_TYPE_CAN, v4c.BUS_TYPE_OTHER)
            and "CAN_DataFrame" in names
        ):
            reads.append(("CAN", "CAN_DataFrame.BusChannel", None))
        if (
            source.bus_type in (v4c.BUS_TYPE_LIN, v4c.BUS_TYPE_OTHER)
            and "LIN_Frame" in names
            and group.channels[names.index("LIN_Frame")].attachment is None
        ):
            reads.append(("LIN", "LIN_Frame.ID", 0x1FFFFFFF))
        for bus, channel_name, mask in reads:
            for offset in range(0, cycles, _TRACE_CHUNK_RECORDS):
                values = mdf.get(
                    channel_name,
                    group=group_idx,
                    record_offset=offset,
                    record_count=min(_TRACE_CHUNK_RECORDS, cycles - offset),
                    samples_only=True,
                )[0]
                values = np.unique(values)
                if mask is not None:
                    values = np.unique(values.astype("<u4") & mask)
                keys[bus].update(int(value) for value in values)
    return {bus: sorted(found) for bus, found in keys.items()}


#: SampleCache numbers a DBC-decoded group g as DBC_GROUP_BASE + g, apart from
#: the recorder-decoded groups of the same file.
DBC_GROUP_BASE = 1 << 20


class _BusDecodeHandles:
    """What a bus decode hands over: its open decoded MDFs and signal names."""

    def __init__(self, native_source, extracted, recovery_path,
                 j1939_groups, metadata_by_key) -> None:
        self.native_source = native_source
        self.extracted = extracted
        self.recovery_path = recovery_path
        self.j1939_groups = j1939_groups
        self.metadata_by_key = metadata_by_key

    def close(self) -> None:
        for mdf in (self.extracted, self.native_source):
            if mdf is not None:
                try:
                    mdf.close()
                except Exception:
                    pass
        remove_recovery_copy(self.recovery_path)


class BusDecodeSources:
    """
    A decoded bus log, left in its files.

    ``signals`` lists ``(group, channel, message_name, message_id,
    signal_name, unit, sample_count)`` per signal; ``source`` reads the
    groups for a SampleCache and must be closed.
    """

    def __init__(self, source: "_JoinedSampleSource", signals: list) -> None:
        self.source = source
        self.signals = signals


class _JoinedSampleSource:
    """A bus log's recorder-decoded and DBC-decoded groups as one source."""

    def __init__(self, native, dbc, recovery_path) -> None:
        self._native = native
        self._dbc = dbc
        self._recovery_path = recovery_path

    def _part(self, group: int):
        if group >= DBC_GROUP_BASE:
            return self._dbc, group - DBC_GROUP_BASE
        return self._native, group

    def read_groups(self, group_indices, channel_names=None):
        results = {}
        for source, base in ((self._native, 0), (self._dbc, DBC_GROUP_BASE)):
            mine = [
                g - base for g in group_indices
                if (g >= DBC_GROUP_BASE) == (base == DBC_GROUP_BASE)
            ]
            if source is None or not mine:
                continue
            for (group, name), parts in source.read_groups(mine, channel_names).items():
                results[(group + base, name)] = parts
        return results

    def group_bytes(self, group: int) -> int:
        source, local = self._part(group)
        return source.group_bytes(local) if source is not None else 0

    def cycles(self, group: int) -> int:
        source, local = self._part(group)
        return source.cycles(local) if source is not None else 0

    def master(self, group: int, record_offset: int = 0, record_count=None):
        source, local = self._part(group)
        return source.master(local, record_offset, record_count)

    def first_timestamp(self, groups) -> float | None:
        """The earliest first time stamp of *groups*."""
        first = None
        for group in sorted(set(groups)):
            if self.cycles(group) <= 0:
                continue
            try:
                master = self.master(group, 0, 1)
            except Exception:
                continue
            if len(master):
                value = float(master[0])
                first = value if first is None else min(first, value)
        return first

    def close(self) -> None:
        for source in (self._native, self._dbc):
            if source is not None:
                source.close()
        remove_recovery_copy(self._recovery_path)
        self._recovery_path = None


class MDFCANReader:
    """
    Reads an ASAM MDF4 bus logging file and decodes signals with a
    :class:`DBCDecoder`.

    ``python-can`` ``MF4Reader`` yields ``can.Message`` objects, so the
    frame-to-RawFrame conversion is identical to :class:`ASCCANReader`.

    Attributes
    ----------
    source_description : str
    has_raw_frames : bool  — always ``True``
    """

    has_raw_frames: bool = True
    supports_raw_frame_arrays: bool = True

    def __init__(self, mdf_path: str | Path, decoder: DBCDecoder | str | Path) -> None:
        self._path = Path(mdf_path)
        if isinstance(decoder, DBCDecoder) or (
            hasattr(decoder, "decode_frame") and hasattr(decoder, "dbc_path")
        ):
            self._decoder: DBCDecoder | None = decoder
            self._dbc_path = decoder.dbc_path
            decoder_messages = list(decoder.load_messages)
        else:
            self._decoder = None
            self._dbc_path = Path(decoder)
            decoder_messages = []
        fmt = "MF4" if self._path.suffix.lower() == ".mf4" else "MDF"
        self.source_description = (
            f"{fmt} bus log + DBC  ({self._path.name} / {self._dbc_path.name})"
        )
        self.load_warnings: list[str] = []
        self.load_messages: list[str] = [
            f"MDF bus logging: opening {fmt} file (raw CAN frames)…",
            f"DBC: {self._dbc_path.name}",
            "Fast path: asammdf native one-pass bus extraction.",
        ] + decoder_messages

    def _record_channel_error(
        self,
        group_name: str,
        channel_name: str,
        exc: BaseException | str,
    ) -> None:
        warning = _channel_failure_text(group_name, channel_name, exc)
        if warning not in self.load_warnings:
            self.load_warnings.append(warning)

    # ── Protocol iterator ─────────────────────────────────────────────────

    def __iter__(self) -> Iterator[DecodedSignalSample]:
        for _frame, samples in self.iter_with_frames():
            yield from samples

    def _messages(self):
        """Yield python-can messages while centralising MF4 error handling."""
        if not self._path.exists():
            raise MDFCANReadError(f"MDF file not found: {self._path}")

        try:
            reader = can.MF4Reader(str(self._path))
        except AttributeError:
            raise MDFCANReadError(
                "python-can's MF4Reader is not available.\n"
                "Ensure python-can >= 4.6 and asammdf >= 7.0 are installed."
            )
        except Exception as exc:
            raise MDFCANReadError(
                f"Failed to open MDF bus log '{self._path}': {exc}"
            ) from exc

        try:
            with reader:
                for msg in reader:
                    if hasattr(msg, "arbitration_id"):
                        yield msg
        except MDFCANReadError:
            raise
        except Exception as exc:
            raise MDFCANReadError(
                f"Error reading MDF bus log '{self._path}': {exc}"
            ) from exc

    def iter_raw_tuples(self):
        """Yield allocation-light tuples for LoadWorker's vectorized path.

        Decoding is deliberately omitted here.  LoadWorker groups these raw
        frames by channel and arbitration ID, then decodes each group with
        NumPy instead of calling cantools for every individual MF4 frame.
        """
        for msg in self._messages():
            data = bytes(msg.data or b"")
            raw_ch = getattr(msg, "channel", None)
            channel = int(raw_ch) if isinstance(raw_ch, (int, float)) else 255
            is_rx = getattr(msg, "is_rx", None)
            direction = 0 if is_rx is True else 1 if is_rx is False else 2
            yield (
                float(msg.timestamp),
                channel,
                int(msg.arbitration_id),
                int(getattr(msg, "dlc", len(data))),
                direction,
                bool(getattr(msg, "is_extended_id", False)),
                bool(getattr(msg, "is_fd", False)),
                data,
            )

    def iter_frames_only(self) -> Iterator[RawFrame]:
        """Yield raw frames without per-frame DBC decoding."""
        for ts, ch, arb_id, dlc, direction, is_ext, is_fd, data in self.iter_raw_tuples():
            yield RawFrame(
                timestamp=ts,
                channel=None if ch == 255 else ch,
                arbitration_id=arb_id,
                is_extended_id=is_ext,
                is_fd=is_fd,
                dlc=dlc,
                data=data,
                direction=("Rx", "Tx", "Unknown")[direction],
            )

    def iter_decoded_channel_arrays(
        self,
        channel_config,
        progress=None,
        metadata_ready=None,
        raw_frame_batch=None,
    ):
        """Decode signals and expose raw CAN records through bulk arrays."""
        yield from self._bus_decode(
            channel_config, progress, metadata_ready, raw_frame_batch,
            hand_over=False,
        )

    def open_decoded_sources(
        self,
        channel_config,
        progress=None,
        metadata_ready=None,
        raw_frame_batch=None,
    ) -> "BusDecodeSources":
        """
        Decode as :meth:`iter_decoded_channel_arrays` does, but read nothing back.

        asammdf writes its decoded signals to a temporary file of its own. The
        returned sources keep that decoded MDF (and any recorder-decoded
        groups) open for a SampleCache, which reads a signal's group the first
        time the signal is used. The CAN Trace's frames still arrive through
        *raw_frame_batch* before this returns.
        """
        decode = self._bus_decode(
            channel_config, progress, metadata_ready, raw_frame_batch,
            hand_over=True,
        )
        handles = next(decode)
        decode.close()
        try:
            return self._lazy_sources(handles, channel_config)
        except BaseException:
            handles.close()
            raise

    def _lazy_sources(self, handles, channel_config) -> "BusDecodeSources":
        native = (
            MDFSampleSource(handles.native_source, None, self._record_channel_error)
            if handles.native_source is not None else None
        )
        dbc = (
            MDFSampleSource(handles.extracted, None, self._record_channel_error)
            if handles.extracted is not None else None
        )
        handles.native_source = handles.extracted = None   # the sources own them
        signals = []
        for group_idx, group_name, channels, cycles in (native.groups if native else ()):
            for _ch_idx, signal_name, unit in channels:
                signals.append((group_idx, None, group_name, 0, signal_name, unit, cycles))
        j1939_groups = handles.j1939_groups
        for group_idx, _name, channels, cycles in (dbc.groups if dbc else ()):
            if group_idx in j1939_groups and j1939_groups[group_idx] is None:
                continue
            for _ch_idx, signal_name, unit in channels:
                meta = handles.metadata_by_key.get((group_idx, signal_name))
                if meta is None and group_idx in j1939_groups:
                    channel, message_name, message_id = j1939_groups[group_idx]
                elif meta is None:
                    channel, message_name, message_id = self._decoded_group_metadata(
                        dbc._mdf, group_idx, signal_name, channel_config
                    )
                else:
                    channel, message_name, message_id, _signal, unit = meta
                signals.append((
                    DBC_GROUP_BASE + group_idx, channel, message_name,
                    message_id, signal_name, unit, cycles,
                ))
        return BusDecodeSources(
            _JoinedSampleSource(native, dbc, handles.recovery_path), signals
        )

    def _bus_decode(
        self,
        channel_config,
        progress,
        metadata_ready,
        raw_frame_batch,
        hand_over: bool,
    ):
        """
        The decode itself. With *hand_over*, once the signal list is known and
        the CAN Trace is loaded, yield one _BusDecodeHandles and leave the
        decoded MDFs open for the caller; otherwise yield the arrays.
        """
        handed_over = False
        try:
            import asammdf
        except ImportError as exc:
            raise MDFCANReadError("asammdf is required for MF4 bus decoding.") from exc

        # asammdf takes one dict keyed by bus type, so CAN and LIN databases
        # go in together and are extracted in a single pass over the file.
        # Keying by bus is what stops a LIN database being offered to CAN
        # extraction (and vice versa) — the two buses number their channels
        # independently, so a flat list would cross-apply on channel number.
        databases: dict[str, list[tuple[str, int]]] = {}
        if channel_config is not None:
            for bus in (BusType.CAN, BusType.LIN):
                pairs = channel_config.databases_for_bus(bus)
                if pairs:
                    databases[bus.value] = pairs
        if not databases:
            databases = {BusType.CAN.value: [(str(self._dbc_path), 0)]}

        source = native_source = extracted = None
        recovery_path = None
        raw_executor = None
        raw_future = None
        self.raw_trace_error = ""
        self.dbc_extraction_failed = False
        try:
            try:
                source = asammdf.MDF(
                    str(self._path), use_display_names=False,
                    process_bus_logging=False,
                )
            except Exception as original_exc:
                recovery_path, warnings = make_bounded_mdf4_copy(self._path)
                if recovery_path is None:
                    raise original_exc
                self.load_warnings.extend(warnings)
                source = asammdf.MDF(
                    str(recovery_path), use_display_names=False,
                    process_bus_logging=False,
                )
            active_path = recovery_path or self._path
            # asammdf decodes a raw group a fragment at a time, holding a few
            # times the fragment in memory; its default 256 MB fragment made
            # that over a gigabyte. A smaller one costs a little time.
            configure = getattr(source, "configure", None)
            if configure is not None:
                configure(read_fragment_size=_EXTRACT_FRAGMENT_BYTES)

            # A mixed MDF contains two independent signal sources:
            #
            #   1. engineering channels already decoded by the recorder
            #   2. raw CAN_DataFrame groups that can be decoded with the DBC
            #
            # Keep the recorder-decoded channels as the primary source.  The
            # previous routing selected only ``extract_bus_logging()`` whenever
            # a DBC was configured, which made all existing engineering
            # channels disappear.  Use a normal-display-name MDF handle here
            # so these channels have exactly the same names as MDFReader gives
            # them when the same file is loaded without a database.
            native_metadata_rows = []
            try:
                has_native_groups = any(
                    not MDFReader._is_raw_can_group(group)
                    and MDFReader._group_has_decoded_signals(group)
                    for group in source.groups
                )
                if has_native_groups:
                    native_source = asammdf.MDF(
                        str(active_path), process_bus_logging=False
                    )
                    native_metadata_rows = [
                        (None, group_name, 0, signal_name, unit)
                        for group_name, signal_name, unit
                        in MDFReader._channel_metadata(native_source)
                    ]
            except Exception:
                # Do not prevent raw-CAN/DBC decoding when an unusual MDF
                # exposes engineering metadata that cannot be reopened through
                # the regular decoded-channel view.
                if native_source is not None:
                    try:
                        native_source.close()
                    except Exception:
                        pass
                    native_source = None
                native_metadata_rows = []

            if raw_frame_batch is not None:
                # Raw CAN arrays are independent from the extracted decoded
                # MDF. Read them through a second lazy MDF handle so payload
                # materialisation overlaps native DBC extraction instead of
                # becoming another sequential loading phase.
                raw_executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="osvanta-mf4-trace",
                )
                raw_future = raw_executor.submit(
                    self._load_raw_frame_arrays,
                    asammdf,
                    raw_frame_batch,
                )
            # Expand the analyzer's "All Channels" fallback to the concrete bus
            # channels discovered by asammdf.  This prevents a channel-specific
            # database and the fallback database from both decoding the same bus.
            if channel_config is not None:
                try:
                    bus_logging_map = _bus_logging_keys(source)
                    for bus in (BusType.CAN, BusType.LIN):
                        discovered = bus_logging_map.get(bus.value, ())
                        concrete = []
                        for channel in discovered:
                            path = channel_config.dbc_path_for(bus, int(channel))
                            if path:
                                concrete.append((path, int(channel)))
                        if concrete:
                            databases[bus.value] = list(dict.fromkeys(concrete))
                except Exception:
                    pass
            try:
                extracted = source.extract_bus_logging(
                    database_files=databases,
                    ignore_value2text_conversion=False,
                    progress=progress,
                )
            except Exception as exc:
                # Recorder-decoded engineering groups are independent of the
                # raw CAN payload groups. A corrupt raw DataBytes/VLSD block
                # must not discard those readable native signals.
                self._record_channel_error(
                    "DBC extraction", "raw CAN groups", exc
                )
                self.dbc_extraction_failed = True
                extracted = None

            # The hierarchy is available from decoded MDF metadata before any
            # sample arrays are selected. Hand it to the GUI immediately so
            # signal discovery tracks native asammdf extraction time.
            metadata_by_key = {}
            dbc_metadata_rows = []
            native_message_names = {
                message_name for _channel, message_name, _message_id,
                _signal_name, _unit in native_metadata_rows
            }
            j1939_groups = (
                self._j1939_group_identities(extracted, channel_config)
                if extracted is not None else {}
            )
            for group_idx, group in enumerate(
                extracted.groups if extracted is not None else ()
            ):
                if group_idx in j1939_groups and j1939_groups[group_idx] is None:
                    continue
                for ch_idx, decoded_channel in enumerate(group.channels):
                    signal_name = (
                        getattr(decoded_channel, "name", None) or f"Ch{ch_idx}"
                    )
                    if getattr(decoded_channel, "channel_type", -1) == 1:
                        continue
                    if signal_name.lower() in ("time", "t", "timestamps"):
                        continue
                    unit = str(getattr(decoded_channel, "unit", "") or "")
                    if group_idx in j1939_groups:
                        channel, message_name, message_id = j1939_groups[group_idx]
                    else:
                        channel, message_name, message_id = self._decoded_group_metadata(
                            extracted, group_idx, signal_name, channel_config
                        )
                    # Normally DBC signals have a concrete CAN channel and
                    # therefore cannot collide with recorder-decoded CH?
                    # signals.  Keep both visible even when malformed metadata
                    # prevents channel recovery and the acquisition names match.
                    if channel is None and message_name in native_message_names:
                        message_name = f"{message_name} (DBC)"
                    meta = (
                        channel,
                        message_name,
                        message_id,
                        signal_name,
                        unit,
                    )
                    metadata_by_key[(group_idx, signal_name)] = meta
                    dbc_metadata_rows.append(meta)

            # The worker uses only DBC-derived message identities to label raw
            # CAN Trace rows. Native decoded groups are intentionally excluded.
            self.dbc_trace_message_names = {
                (channel, int(message_id)): message_name
                for channel, message_name, message_id, _signal_name, _unit
                in dbc_metadata_rows
            }

            if metadata_ready is not None:
                # Existing decoded channels are intentionally listed first;
                # DBC-derived channels are appended under their CAN channel.
                metadata_ready(native_metadata_rows + dbc_metadata_rows)

            if hand_over:
                if raw_future is not None:
                    try:
                        raw_future.result()
                    except Exception as exc:
                        self.raw_trace_error = str(exc)
                handles = _BusDecodeHandles(
                    native_source, extracted, recovery_path,
                    j1939_groups, metadata_by_key,
                )
                handed_over = True
                yield handles
                return

            # Yield the recorder-decoded arrays first.  Their key layout is the
            # established MDFReader layout: CH? / acquisition-group / signal.
            # DBC-decoded arrays follow under CHn / message / signal, so both
            # sources remain independently selectable even when names match.
            if native_source is not None:
                for old_meta, ts_arr, num_arr, disp_list in MDFReader._iter_arrays(
                    native_source,
                    batch_all_groups=True,
                    channel_error=self._record_channel_error,
                ):
                    group_name, signal_name, unit = old_meta
                    yield (
                        (None, group_name, 0, signal_name, unit),
                        ts_arr,
                        num_arr,
                        disp_list,
                    )

            if extracted is not None:
                for group_idx, old_meta, ts_arr, num_arr, disp_list in \
                        MDFReader._iter_arrays(
                            extracted,
                            include_group_index=True,
                            batch_all_groups=True,
                            channel_error=self._record_channel_error,
                        ):
                    if group_idx in j1939_groups and j1939_groups[group_idx] is None:
                        continue
                    signal_name = old_meta[1]
                    unit = old_meta[2]
                    meta = metadata_by_key.get((group_idx, signal_name))
                    if meta is None and group_idx in j1939_groups:
                        channel, message_name, message_id = j1939_groups[group_idx]
                    elif meta is None:
                        channel, message_name, message_id = self._decoded_group_metadata(
                            extracted, group_idx, signal_name, channel_config
                        )
                    else:
                        channel, message_name, message_id, _name, metadata_unit = meta
                        if not unit:
                            unit = metadata_unit
                    yield (
                        (channel, message_name, message_id, signal_name, unit),
                        ts_arr,
                        num_arr,
                        disp_list,
                    )

            if raw_future is not None:
                try:
                    raw_future.result()
                except Exception as exc:
                    # A trace-only failure must not discard a successful native
                    # signal decode or force the much slower compatibility path.
                    self.raw_trace_error = str(exc)
        except MDFCANReadError:
            raise
        except Exception as exc:
            raise MDFCANReadError(
                f"asammdf bus extraction failed for '{self._path}': {exc}"
            ) from exc
        finally:
            if raw_executor is not None:
                raw_executor.shutdown(wait=True, cancel_futures=False)
            # Handed over, the decoded MDFs and the recovery copy belong to the
            # caller; the raw-frame handle is done with either way.
            for mdf in ((source,) if handed_over else (extracted, native_source, source)):
                if mdf is not None:
                    try:
                        mdf.close()
                    except Exception:
                        pass
            if not handed_over:
                remove_recovery_copy(recovery_path)

    def _load_raw_frame_arrays(self, asammdf, callback) -> int:
        raw_source = None
        try:
            raw_source = asammdf.MDF(
                str(self._path), use_display_names=False,
                process_bus_logging=False,
            )
            total = self._emit_raw_frame_arrays(
                raw_source, callback, self._record_channel_error
            )
            total += self._emit_raw_lin_frame_arrays(
                raw_source, callback, self._record_channel_error
            )
            return total
        finally:
            if raw_source is not None:
                try:
                    raw_source.close()
                except Exception:
                    pass

    @staticmethod
    def _emit_raw_frame_arrays(source, callback, channel_error=None) -> int:
        """Read each MDF ``CAN_DataFrame`` group as one structured array.

        ``CAN_ErrorFrame`` and ``CAN_RemoteFrame`` groups (ASAM MDF bus
        logging) are read the same way, their frames flagged FLAG_ERROR and
        FLAG_REMOTE; neither carries data, and either may lack the ID.
        """
        total = 0
        for group_idx, group in enumerate(source.groups):
            parent_idx = root = None
            for channel_idx, channel in enumerate(group.channels):
                name = getattr(channel, "name", "") or ""
                if name in _CAN_FRAME_ROOTS:
                    parent_idx, root = channel_idx, name
                    break
            if parent_idx is None:
                continue
            kind_flag = _CAN_FRAME_ROOTS[root]
            group_name = MDFReader._group_name(source, group_idx)
            try:
                # A chunk of records at a time: a group can hold every
                # frame of a long log, far more than fits in memory.
                for signal in _record_chunks(source, group_idx, parent_idx, group):
                    samples = np.asarray(signal.samples)
                    field_names = samples.dtype.names or ()

                    def field(suffix, required=True):
                        exact = f"{root}.{suffix}"
                        name = exact if exact in field_names else next(
                            (item for item in field_names if item.endswith(f".{suffix}")),
                            None,
                        )
                        if name is None:
                            if required:
                                raise MDFCANReadError(
                                    f"Raw CAN channel '{exact}' is missing from "
                                    f"group {group_idx}."
                                )
                            return None
                        return samples[name]

                    timestamps = np.asarray(signal.timestamps, dtype=np.float64)
                    count = len(timestamps)
                    data_frame = kind_flag == 0
                    channels = field("BusChannel")
                    arb_ids = field("ID", required=data_frame)
                    if arb_ids is None:
                        arb_ids = np.zeros(count, dtype=np.uint32)
                    data_rows = field("DataBytes", required=data_frame)
                    if data_rows is None or kind_flag == FLAG_REMOTE:
                        data_rows = np.zeros((count, 8), dtype=np.uint8)
                    data_lengths = field("DataLength", required=False)
                    if data_lengths is None:
                        data_lengths = field("DLC", required=data_frame)
                    if data_lengths is None:
                        data_lengths = np.zeros(count, dtype=np.uint8)
                    directions = field("Dir", required=False)
                    if directions is None:
                        directions = np.full(len(samples), 2, dtype=np.uint8)
                    ide = field("IDE", required=False)
                    if ide is None:
                        ide = np.asarray(arb_ids, dtype=np.uint32) > 0x7FF
                    edl = field("EDL", required=False)
                    if edl is None:
                        edl = np.zeros(len(samples), dtype=np.uint8)
                    flags = (
                        (np.asarray(ide, dtype=np.uint8) & np.uint8(1))
                        | ((np.asarray(edl, dtype=np.uint8) & np.uint8(1)) << np.uint8(1))
                        | np.uint8(kind_flag)
                    )
                    brs = field("BRS", required=False)
                    if brs is not None:
                        flags |= (np.asarray(brs, dtype=np.uint8) & np.uint8(1)) * np.uint8(FLAG_BRS)

                    callback(
                        timestamps,
                        channels,
                        arb_ids,
                        data_lengths,
                        directions,
                        flags,
                        data_rows,
                    )
                    total += len(timestamps)
            except Exception as exc:
                if channel_error is None:
                    raise
                channel_error(group_name, root, exc)
        return total

    @staticmethod
    def _emit_raw_lin_frame_arrays(source, callback, channel_error=None) -> int:
        """
        Read each MDF ``LIN_Frame`` group as one structured array.

        LIN frames go into the same trace store as CAN rather than a store of
        their own: a 6-bit LIN ID fits the uint32 arbitration-ID column, a LIN
        payload is at most 8 of the 64 available bytes, and the bus is carried
        by a single previously unused flag bit. Nothing in the record layout
        changes.

        LIN has no equivalent of IDE, EDL or direction — every frame is
        standard-ID, non-FD, and the ASAM LIN_Frame structure has no Dir field,
        so direction is reported as Unknown rather than invented.
        """
        total = 0
        for group_idx, group in enumerate(source.groups):
            parent_idx = None
            for channel_idx, channel in enumerate(group.channels):
                if (getattr(channel, "name", "") or "") == "LIN_Frame":
                    parent_idx = channel_idx
                    break
            if parent_idx is None:
                continue
            group_name = MDFReader._group_name(source, group_idx)
            try:
                # A chunk of records at a time: a group can hold every
                # frame of a long log, far more than fits in memory.
                for signal in _record_chunks(source, group_idx, parent_idx, group):
                    samples = np.asarray(signal.samples)
                    field_names = samples.dtype.names or ()

                    def field(suffix, required=True):
                        exact = f"LIN_Frame.{suffix}"
                        name = exact if exact in field_names else next(
                            (item for item in field_names if item.endswith(f".{suffix}")),
                            None,
                        )
                        if name is None:
                            if required:
                                raise MDFCANReadError(
                                    f"Raw LIN channel '{exact}' is missing from "
                                    f"group {group_idx}."
                                )
                            return None
                        return samples[name]

                    timestamps = np.asarray(signal.timestamps, dtype=np.float64)
                    arb_ids = field("ID")
                    data_rows = field("DataBytes")
                    count = len(timestamps)

                    channels = field("BusChannel", required=False)
                    if channels is None:
                        # Single-bus recorders may omit BusChannel entirely.
                        channels = np.ones(count, dtype=np.uint8)
                    data_lengths = field("DataLength", required=False)
                    if data_lengths is None:
                        data_lengths = field("ReceivedDataByteCount", required=False)
                    if data_lengths is None:
                        data_lengths = np.full(count, 8, dtype=np.uint8)
                    directions = field("Dir", required=False)
                    if directions is None:
                        directions = np.full(count, 2, dtype=np.uint8)

                    flags = np.full(count, FLAG_LIN, dtype=np.uint8)

                    callback(
                        timestamps,
                        channels,
                        arb_ids,
                        data_lengths,
                        directions,
                        flags,
                        data_rows,
                    )
                    total += count
            except Exception as exc:
                if channel_error is None:
                    raise
                channel_error(group_name, "LIN_Frame", exc)
        return total

    @classmethod
    def _j1939_group_identities(cls, extracted, channel_config):
        """
        Identify the J1939 groups asammdf extracted, one per sender.

        asammdf extracts J1939 frames into one group per frame ID and records
        the sender in the group comment (``CAN1 ID=0x18FEF100 CCVS PGN=0xFEF1
        SA=0x0``). Its acquisition source only carries the database ID, and
        its ``acq_name`` prints the SA in decimal behind a ``0x`` prefix, so
        the comment is the reliable record of which frame ID fed the group.

        Returns ``{group_idx: (channel, message_name, frame_id) | None}`` for
        J1939 groups only. ``None`` marks a group the analyzer's own decoder
        would not match: asammdf assigns an undefined sender of a PGN the
        database defines at several source addresses to one of those
        messages, which would put another node's data in its signals. When
        several senders share one message, each gets its own message name.
        """
        identities = {}
        for group_idx, group in enumerate(extracted.groups):
            channel_group = getattr(group, "channel_group", None)
            comment = str(getattr(channel_group, "comment", "") or "")
            match = re.search(
                r"\bID=0x([0-9A-F]+)\b.*\bSA=0x([0-9A-F]+)\b", comment,
                re.IGNORECASE,
            )
            if not match:
                continue
            signal_name = next(
                (
                    decoded_channel.name
                    for decoded_channel in group.channels
                    if getattr(decoded_channel, "channel_type", -1) != 1
                    and decoded_channel.name.lower() not in ("time", "t", "timestamps")
                ),
                None,
            )
            if signal_name is None:
                continue
            channel, message_name, _database_id = cls._decoded_group_metadata(
                extracted, group_idx, signal_name, channel_config
            )
            frame_id = int(match.group(1), 16)
            identity = (channel, message_name, frame_id)
            if channel_config is not None and channel is not None:
                try:
                    decoder = channel_config.decoder_for(*channel)
                except Exception:
                    decoder = None
                if decoder is not None and hasattr(decoder, "candidates_for"):
                    names = {
                        message.name
                        for message in decoder.candidates_for(frame_id, True)
                    }
                    if message_name not in names:
                        identity = None
            identities[group_idx] = identity

        split = multi_sender_messages(
            ((identity[0], identity[1]), identity[2])
            for identity in identities.values() if identity is not None
        )
        for group_idx, identity in identities.items():
            if identity is not None and (identity[0], identity[1]) in split:
                channel, message_name, frame_id = identity
                identities[group_idx] = (
                    channel,
                    source_address_message_name(message_name, frame_id & 0xFF),
                    frame_id,
                )
        return identities

    @staticmethod
    def _decoded_group_metadata(extracted, group_idx, signal_name, channel_config):
        """
        Recover bus/channel/message identity from asammdf group metadata.

        asammdf stamps each extracted group with an acquisition source whose
        path is ``CAN{n}.CAN_DataFrame.ID=0x{id} EXT={bool}`` or
        ``LIN{n}.LIN_Frame.ID=0x{id}``, and gives each signal a display name of
        the form ``{BUS}{n}.{message}.{signal}``.  Both carry the bus, so the
        bus never has to be guessed from the database's file extension — which
        would be wrong anyway, since a DBC legitimately describes a LIN cluster.

        Returns ``((bus, channel) | None, message_name, message_id)``.
        """
        group = extracted.groups[group_idx]
        channel_group = getattr(group, "channel_group", None)
        source = getattr(channel_group, "acq_source", None)
        source_path = str(getattr(source, "path", "") or "")
        acq_name = str(getattr(channel_group, "acq_name", "") or "")
        text = f"{source_path} {acq_name}"

        channel_match = re.search(r"\b(CAN|LIN)(\d+)\b", text, re.IGNORECASE)
        id_match = re.search(r"\bID=0x([0-9A-F]+)", text, re.IGNORECASE)
        channel_key = None
        bus = BusType.CAN
        if channel_match:
            bus = BusType(channel_match.group(1).upper())
            channel_key = (bus, int(channel_match.group(2)))
        message_id = int(id_match.group(1), 16) if id_match else 0

        message_name = ""
        try:
            locations = extracted.channels_db.get(signal_name, ())
            for gi, ci in locations:
                if gi != group_idx:
                    continue
                display_names = getattr(group.channels[ci], "display_names", {}) or {}
                for display_name in display_names:
                    match = re.match(r"(?:CAN|LIN)\d+\.(.+)\.[^.]+$", display_name)
                    if match:
                        message_name = match.group(1)
                        break
                if message_name:
                    break
        except Exception:
            pass

        if not message_name and channel_config is not None and message_id:
            try:
                number = channel_key[1] if channel_key else None
                # An LDF is unreadable by cantools, so building a DBCDecoder
                # for one only raises — and would do so once per signal.
                # asammdf's own display names already named the message in
                # that case; fall through to the synthetic name instead.
                assigned = channel_config.dbc_path_for(bus, number)
                if assigned and Path(assigned).suffix.lower() != ".ldf":
                    decoder = channel_config.decoder_for(bus, number)
                    if decoder is not None:
                        from core.vectorized_decoder import VectorizedDBC
                        candidates = VectorizedDBC(decoder).get_candidates(
                            message_id, is_extended=(message_id > 0x7FF)
                        )
                        if candidates:
                            message_name = candidates[0].name
            except Exception:
                pass

        if not message_name:
            prefix = "LIN_Frame" if bus is BusType.LIN else "CAN_DataFrame"
            message_name = f"{prefix}_{message_id:X}"
        return channel_key, message_name, message_id

    # ── Extended iterator (used by LoadWorker) ────────────────────────────

    def iter_with_frames(self) -> Iterator[tuple[RawFrame, list[DecodedSignalSample]]]:
        """
        Yield (RawFrame, decoded_samples) pairs — identical contract to
        BLFCANReader and ASCCANReader.

        python-can's MF4Reader handles the asammdf call internally.
        ``BusChannel`` is already 1-indexed in the MDF4 bus logging standard
        (unlike BLF/ASC which use 0-indexed and need +1).
        """
        try:
            for msg in self._messages():
                data = bytes(msg.data or b"")
                is_rx = getattr(msg, "is_rx", None)
                direction = (
                    "Rx"      if is_rx is True  else
                    "Tx"      if is_rx is False else
                    "Unknown"
                )

                # MDF bus logging BusChannel is already 1-indexed
                raw_ch = getattr(msg, "channel", None)
                channel = int(raw_ch) if isinstance(raw_ch, (int, float)) else None

                frame = RawFrame(
                    timestamp      = float(msg.timestamp),
                    channel        = channel,
                    arbitration_id = int(msg.arbitration_id),
                    is_extended_id = bool(getattr(msg, "is_extended_id", False)),
                    is_fd          = bool(getattr(msg, "is_fd", False)),
                    dlc            = int(getattr(msg, "dlc", len(data))),
                    data           = data,
                    direction      = direction,
                )
                samples = self.decoder.decode_frame(frame)
                yield frame, samples

        except MDFCANReadError:
            raise
        except Exception as exc:
            raise MDFCANReadError(
                f"Error reading MDF bus log '{self._path}': {exc}"
            ) from exc

    @property
    def decoder(self) -> DBCDecoder:
        if self._decoder is None:
            self._decoder = DBCDecoder(self._dbc_path)
        return self._decoder
