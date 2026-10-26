# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from core.bus_types import BusType
from core.models import DecodedSignalSample
from core.readers.mdf_recovery import (
    make_bounded_mdf4_copy,
    remove_recovery_copy,
)


#: Upper bound on the record bytes one ``mdf.select()`` reads at once when
#: the caller asks for groups to be batched. Selecting every group together
#: is fastest for a small file, but it holds the whole decoded measurement
#: in memory while the store copies it, and a 5 GB MF4 ran out of memory.
#: A group larger than this is still read on its own.
_SELECT_BATCH_BYTES = 256 * 1024 * 1024

#: The record bytes one pass of a group copy reads at once (_GroupCopy).
_COPY_CHUNK_BYTES = 4 * 1024 * 1024
#: A group copy starts this long after the read that asked for it, so the
#: signal just read is drawn first, and rests this long between chunks, so
#: drawing and reading keep going while it runs.
_COPY_DELAY_S = 0.5
_COPY_REST_S = 0.002


class MDFImportError(RuntimeError):
    """Raised when asammdf is not installed."""


class MDFReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MDFContentInfo:
    """Structural content discovered from MDF channel metadata."""

    has_raw_can: bool = False
    has_decoded_signals: bool = False
    has_raw_lin: bool = False

    @property
    def has_raw_bus(self) -> bool:
        """Whether the file carries raw bus frames of any supported bus type."""
        return self.has_raw_can or self.has_raw_lin

    @property
    def is_mixed(self) -> bool:
        return self.has_raw_bus and self.has_decoded_signals

    def bus_types(self) -> list[BusType]:
        """Return the raw bus types present, in a stable order."""
        buses = []
        if self.has_raw_can:
            buses.append(BusType.CAN)
        if self.has_raw_lin:
            buses.append(BusType.LIN)
        return buses


def _is_text(arr) -> bool:
    """Return True when *arr* holds string/bytes samples (enum/text channel)."""
    if not hasattr(arr, "dtype"):
        return False
    if arr.dtype.kind in ("U", "S"):
        return True
    if arr.dtype.kind == "O" and len(arr) > 0:
        first = arr.flat[0]
        return isinstance(first, (str, bytes, bytearray, np.bytes_))
    return False


def _decode_str_arr(arr) -> list[str]:
    """Convert a string/bytes numpy array to a plain Python list of str."""
    return [
        v.decode("utf-8", errors="replace").strip()
        if isinstance(v, (bytes, bytearray, np.bytes_))
        else (v.strip() if isinstance(v, str) else str(v))
        for v in arr
    ]


def _channel_failure_text(
    group_name: str,
    channel_name: str,
    exc: BaseException | str,
) -> str:
    reason = " ".join(str(exc).split()) or type(exc).__name__
    if len(reason) > 320:
        reason = reason[:317] + "..."
    return f"Skipped channel '{group_name} / {channel_name}': {reason}"


class LazyTextValues:
    """Sequence view that decodes MDF byte labels only when they are displayed.

    Native bus extraction can produce millions of fixed-width byte labels.
    Turning all of them into Python strings during load is both slow and
    memory-heavy; keeping the NumPy array and decoding indexed values preserves
    the same UI/export text without delaying signal discovery.
    """

    __slots__ = ("_values",)

    def __init__(self, values) -> None:
        self._values = values

    def __len__(self) -> int:
        return len(self._values)

    def __bool__(self) -> bool:
        return len(self._values) > 0

    @staticmethod
    def _decode(value) -> str:
        if isinstance(value, (bytes, bytearray, np.bytes_)):
            return value.decode("utf-8", errors="replace").strip()
        if isinstance(value, str):
            return value.strip()
        return str(value)

    def __getitem__(self, index):
        value = self._values[index]
        if isinstance(index, slice):
            return [self._decode(item) for item in value]
        return self._decode(value)

    def __iter__(self):
        return (self._decode(value) for value in self._values)


class MDFReader:
    """
    Reads ASAM MDF version 3 (.mdf) and version 4 (.mf4) measurement files.

    Signals are already decoded to engineering units — **no database required**.

    Performance design
    ------------------
    The default iterator yields one :class:`DecodedSignalSample` per timestamp.
    This is correct but slow: millions of Python object allocations for large
    MDF files.

    ``iter_channel_arrays()`` is the fast path used by :class:`LoadWorker`.
    It yields one ``(meta, ts_arr, num_arr, disp_list)`` tuple **per channel**
    using vectorised numpy — no per-sample Python loop, no Python objects until
    the final list-comprehension for display values.  The LoadWorker calls
    :meth:`~core.signal_store.SignalStore.add_series_bulk` which does a single
    C-level memcopy into ``array.array`` storage.

    Batch I/O (Bottleneck 1)
    ------------------------
    asammdf stores channels in "channel groups" that share a single compressed
    data block on disk.  The old code called ``mdf.get()`` once per channel,
    which decompressed the block each time — O(channels_per_group) wasted work.
    The new code uses ``mdf.select()`` to batch-fetch all channels from the same
    group in a single file pass, then a second ``mdf.select(raw=True)`` pass for
    enum channels only.  Typical speedup: 5–20× for groups with many channels.

    Enum / text channels (Bottleneck 5)
    ------------------------------------
    ``raw=False`` → string labels (display / cursor table value).
    ``raw=True``  → integer keys  (numeric / plotted as step function).
    Enum channels in the same group are batched into one ``select(raw=True)``
    call instead of individual ``get(raw=True)`` calls.

    Numeric disp_list (Bottleneck 2)
    ---------------------------------
    Pure numeric channels yield ``disp_list=[]`` and the LoadWorker passes
    ``has_labels=False``, skipping the O(n) Python list allocation entirely.

    MDF content cache (Bottleneck 4)
    --------------------------------
    Metadata classification is cached by resolved path so repeated calls
    from ``dbc_required_for()``, ``reader_factory()``, and ``prescan_measurement()``
    open the file header only once.

    Memory
    ------
    asammdf uses lazy / memory-mapped channel loading internally.
    Yielding one channel at a time with explicit ``del`` bounds peak heap RAM
    to ~1 channel group, or one batch of groups of at most
    ``_SELECT_BATCH_BYTES`` when groups are batched, regardless of file size.

    Attributes
    ----------
    has_raw_frames : bool  — always ``False``
    has_channel_arrays : bool — always ``True``; signals the LoadWorker
        to take the vectorised fast path.
    """

    has_raw_frames:    bool = False
    has_channel_arrays: bool = True
    metadata_first_arrays: bool = True
    raw_trace_unavailable_reason: str = (
        "This is a pre-decoded MDF file. It contains signal channels but no "
        "CAN_DataFrame or LIN_Frame records (frame ID, DLC, direction, and "
        "payload bytes), so an authentic bus trace cannot be reconstructed. "
        "Load the original bus-logged MF4/MDF, BLF, or ASC file to view "
        "CAN Trace."
    )

    # Cache the complete classification so dbc_required_for(), reader_factory(),
    # the GUI notice, and pre-scan share one metadata-only MDF open.
    _content_cache: dict[str, MDFContentInfo] = {}

    def __init__(self, mdf_path: str | Path) -> None:
        try:
            import asammdf  # noqa: F401
        except ImportError as exc:
            raise MDFImportError(
                "The 'asammdf' package is required to read MF4/MDF files.\n"
                "Install it with:  pip install asammdf>=7.0"
            ) from exc

        self._path = Path(mdf_path)
        if not self._path.exists():
            raise MDFReadError(f"MDF file not found: {self._path}")

        fmt = "MF4" if self._path.suffix.lower() == ".mf4" else "MDF"
        self.source_description = f"{fmt}  ({self._path.name}) — asammdf"
        self.load_warnings: list[str] = []
        content = self.content_info(self._path)
        self.load_messages: list[str] = [
            f"asammdf: opening {fmt} file…",
            "No database required — signals are pre-decoded.",
            "Using metadata-first global channel-array fast path.",
        ]
        bus_names = " and ".join(bus.value for bus in content.bus_types())
        if content.is_mixed:
            self.load_messages.insert(
                2,
                f"Mixed MDF: loading existing decoded signals; embedded raw "
                f"{bus_names} frames require a database and a reload.",
            )
            self.raw_trace_unavailable_reason = (
                f"This MDF contains both decoded signals and raw {bus_names} "
                "frames. Osvanta Bus Log Analyzer loaded the existing decoded signals without a "
                "database, so the embedded raw frames are not available in "
                f"CAN Trace. Configure a database for the {bus_names} channels "
                "and load the measurement again to decode and view those frames."
            )
        elif content.has_raw_bus:
            # Raw bus groups with no database configured. The file still opens
            # and its raw frame columns stay visible — assigning a database
            # turns them into decoded signals on the next load.
            self.load_messages.insert(
                1,
                f"Raw {bus_names} frames are listed as their structural "
                f"columns. Assign a database to the {bus_names} channels and "
                "reload to decode them into signals.",
            )

    # ── Protocol iterator (fallback, not used by LoadWorker fast path) ────

    def __iter__(self) -> Iterator[DecodedSignalSample]:
        """Compatibility fallback — LoadWorker uses iter_channel_arrays()."""
        for meta, ts_arr, num_arr, disp_list in self.iter_channel_arrays():
            grp_name, ch_name, unit = meta
            for ts, num, disp in zip(ts_arr, num_arr, disp_list):
                yield DecodedSignalSample(
                    timestamp=float(ts), channel=None, message_id=0,
                    message_name=grp_name, signal_name=ch_name,
                    value=disp, unit=unit,
                    is_extended_id=False, direction="Unknown",
                    numeric_value=float(num),
                )

    # ── Fast path: one tuple per channel, vectorised ──────────────────────

    def iter_channel_arrays(
        self,
        metadata_ready=None,
        batch_all_groups: bool = False,
    ):
        """
        Yield ``((grp_name, ch_name, unit), ts_arr, num_arr, disp_list)``
        one entry per channel.

        All arrays are float64 ndarrays.  ``disp_list`` is a Python list of
        display values (strings for enum channels, empty list for numeric).
        Timestamps are **not** yet normalised — LoadWorker subtracts base_ts.

        LoadWorker requests one global ``mdf.select()`` across all channel
        groups. Other callers default to the bounded per-group path.
        """
        mdf, recovery_path = self._open()
        try:
            if metadata_ready is not None:
                metadata_ready(self._channel_metadata(mdf))
            yield from self._iter_arrays(
                mdf,
                batch_all_groups=batch_all_groups,
                channel_error=self._record_channel_error,
            )
        finally:
            try:
                mdf.close()
            except Exception:
                pass
            remove_recovery_copy(recovery_path)

    def open_sample_source(self) -> "MDFSampleSource":
        """
        Open the file for lazy reading: channel list now, samples on request.

        The returned source keeps the file open until it is closed. LoadWorker
        hands it to a SampleCache, so Decode reads no samples at all and a
        signal's channel group is read the first time the signal is used.
        """
        mdf, recovery_path = self._open()
        try:
            return MDFSampleSource(mdf, recovery_path, self._record_channel_error)
        except Exception:
            try:
                mdf.close()
            finally:
                remove_recovery_copy(recovery_path)
            raise

    def _open(self):
        """Open the file, or a bounded copy of a damaged one; see asammdf_guard."""
        try:
            import asammdf
        except ImportError as exc:
            raise MDFImportError("asammdf not installed.") from exc

        recovery_path = None
        try:
            try:
                mdf = asammdf.MDF(str(self._path), process_bus_logging=False)
            except Exception as original_exc:
                recovery_path, warnings = make_bounded_mdf4_copy(self._path)
                if recovery_path is None:
                    raise original_exc
                self.load_warnings.extend(warnings)
                mdf = asammdf.MDF(str(recovery_path), process_bus_logging=False)
        except Exception as exc:
            remove_recovery_copy(recovery_path)
            raise MDFReadError(
                f"Failed to open MDF file '{self._path}': {exc}"
            ) from exc
        return mdf, recovery_path

    def _record_channel_error(
        self,
        group_name: str,
        channel_name: str,
        exc: BaseException | str,
    ) -> None:
        warning = _channel_failure_text(group_name, channel_name, exc)
        if warning not in self.load_warnings:
            self.load_warnings.append(warning)

    @staticmethod
    def _channel_metadata(mdf):
        """Return the complete pre-decoded hierarchy without reading samples."""
        rows = []
        for group_idx, group in enumerate(mdf.groups):
            if MDFReader._is_raw_can_group(group):
                continue
            group_name = MDFReader._group_name(mdf, group_idx)
            for ch_idx, channel in enumerate(group.channels):
                channel_name = getattr(channel, "name", None) or f"Ch{ch_idx}"
                if getattr(channel, "channel_type", -1) == 1:
                    continue
                if channel_name.lower() in ("time", "t", "timestamps"):
                    continue
                unit = str(getattr(channel, "unit", "") or "")
                rows.append((group_name, channel_name, unit))
        return rows

    # ── Internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _has_frame_channel(group, prefix: str) -> bool:
        """Return whether *group* carries an ASAM raw frame structure *prefix*."""
        dotted = f"{prefix}."
        for channel in getattr(group, "channels", ()):
            name = str(getattr(channel, "name", "") or "")
            if name == prefix or name.startswith(dotted):
                return True
        return False

    @staticmethod
    def _is_raw_can_group(group) -> bool:
        """Return whether *group* contains an ASAM raw CAN frame structure."""
        return MDFReader._has_frame_channel(group, "CAN_DataFrame")

    @staticmethod
    def _is_raw_lin_group(group) -> bool:
        """Return whether *group* contains an ASAM raw LIN frame structure."""
        return MDFReader._has_frame_channel(group, "LIN_Frame")

    @staticmethod
    def _group_has_decoded_signals(group) -> bool:
        # A raw bus group is structure, not engineering data. Its members
        # (LIN_Frame.ID, LIN_Frame.DataBytes, …) are ordinary channels and
        # would otherwise be counted as decoded signals, which is what made a
        # LIN-only file look pre-decoded and load without a database.
        if MDFReader._is_raw_can_group(group) or MDFReader._is_raw_lin_group(group):
            return False
        for channel in getattr(group, "channels", ()):
            if getattr(channel, "channel_type", -1) == 1:
                continue
            name = str(getattr(channel, "name", "") or "")
            if name.lower() in ("time", "t", "timestamp", "timestamps"):
                continue
            return True
        return False

    @staticmethod
    def content_info(mdf_path: str | Path) -> MDFContentInfo:
        """Classify an MDF as raw-only, decoded-only, mixed, or empty."""
        resolved = str(Path(mdf_path).resolve())
        cached = MDFReader._content_cache.get(resolved)
        if cached is not None:
            return cached

        has_raw_can = False
        has_raw_lin = False
        has_decoded_signals = False
        try:
            import asammdf
            recovery_path = None
            try:
                mdf = asammdf.MDF(str(mdf_path), process_bus_logging=False)
            except Exception as original_exc:
                recovery_path, _warnings = make_bounded_mdf4_copy(mdf_path)
                if recovery_path is None:
                    raise original_exc
                mdf = asammdf.MDF(str(recovery_path), process_bus_logging=False)
            try:
                for group in mdf.groups:
                    if MDFReader._is_raw_can_group(group):
                        has_raw_can = True
                    elif MDFReader._is_raw_lin_group(group):
                        has_raw_lin = True
                    elif MDFReader._group_has_decoded_signals(group):
                        has_decoded_signals = True
                    if has_raw_can and has_raw_lin and has_decoded_signals:
                        break
            finally:
                try:
                    mdf.close()
                except Exception:
                    pass
                remove_recovery_copy(recovery_path)
        except Exception:
            pass

        result = MDFContentInfo(has_raw_can, has_decoded_signals, has_raw_lin)
        MDFReader._content_cache[resolved] = result
        return result

    @staticmethod
    def is_bus_logging(mdf_path: str | Path) -> bool:
        """
        Probe an MDF file to determine if it contains raw bus frames
        (ASAM MDF bus logging format) rather than pre-decoded signals.

        Bus logging MDF files store frames as ``CAN_DataFrame.*`` or
        ``LIN_Frame.*`` channels per the ASAM MDF bus logging standard.  The
        probe reads only channel group metadata — no sample data loaded.
        Cost: < 50 ms on first call; subsequent calls for the same path return
        the cached result instantly.

        Returns True when the file contains raw frames of either bus type.
        Callers use :meth:`content_info` to distinguish raw-only from mixed
        files, and :meth:`bus_types_in_file` to learn which buses are present.
        """
        return MDFReader.content_info(mdf_path).has_raw_bus

    @staticmethod
    def bus_types_in_file(mdf_path: str | Path) -> list[BusType]:
        """Return which raw bus types *mdf_path* actually contains."""
        return MDFReader.content_info(mdf_path).bus_types()

    @staticmethod
    def _iter_arrays(
        mdf,
        include_group_index: bool = False,
        batch_all_groups: bool = False,
        channel_error=None,
    ):
        """
        Core vectorised channel iterator using ``mdf.select()`` for batch I/O.

        Strategy per channel group:
          1. Scan channel metadata (no data load) to build the select spec list.
          2. ``mdf.select(all_channels, raw=False)`` — one file pass for the
             entire group; returns engineering values (numbers or strings).
          3. Identify enum/text channels from the returned dtype.
          4. ``mdf.select(enum_channels, raw=True)`` — one file pass for raw
             integer keys of enum channels only.  (Skipped for all-numeric groups.)
          5. Yield one tuple per channel; delete arrays to free memory early.

        Falls back to individual ``mdf.get()`` calls if ``select()`` raises.
        """
        # ── Phase 1: collect channel specs per group (metadata only) ─────
        # groups_channels: group_idx → [(ch_idx, ch_name), ...]
        groups_channels: dict[int, list[tuple[int, str]]] = {}
        for group_idx in range(len(mdf.groups)):
            group   = mdf.groups[group_idx]
            # For a mixed MDF loaded without a database, expose only existing
            # engineering signals. Raw frame fields are not plot channels.
            if MDFReader._is_raw_can_group(group):
                continue
            ch_list = []
            for ch_idx in range(len(group.channels)):
                ch      = group.channels[ch_idx]
                ch_name = getattr(ch, "name", None) or f"Ch{ch_idx}"
                if getattr(ch, "channel_type", -1) == 1:
                    continue
                if ch_name.lower() in ("time", "t", "timestamps"):
                    continue
                ch_list.append((ch_idx, ch_name))
            if ch_list:
                groups_channels[group_idx] = ch_list

        # Selecting many groups together avoids hundreds of select()
        # dispatches and lets enum labels remain lazy. The groups are taken
        # in batches of bounded size so a large file is never decoded whole.
        if batch_all_groups:
            for batch in MDFReader._select_batches(mdf, groups_channels):
                yield from MDFReader._iter_arrays_all_groups(
                    mdf, batch, include_group_index, channel_error
                )
            return

        yield from MDFReader._iter_groups(
            mdf, groups_channels, include_group_index, channel_error
        )

    @staticmethod
    def _group_sample_bytes(group) -> int:
        """Estimate the bytes one group's samples and timestamps take."""
        channel_group = getattr(group, "channel_group", None)
        try:
            cycles = int(getattr(channel_group, "cycles_nr", 0) or 0)
            record = int(getattr(channel_group, "samples_byte_nr", 0) or 0)
            record += int(getattr(channel_group, "invalidation_bytes_nr", 0) or 0)
        except (TypeError, ValueError):
            return 0
        # Each decoded group also carries a float64 time stamp per cycle.
        return max(0, cycles * (record + 8))

    @staticmethod
    def _select_batches(mdf, groups_channels):
        """Split *groups_channels* into consecutive batches of bounded size."""
        batch: dict[int, list[tuple[int, str]]] = {}
        batch_bytes = 0
        for group_idx, ch_list in groups_channels.items():
            size = MDFReader._group_sample_bytes(mdf.groups[group_idx])
            if batch and batch_bytes + size > _SELECT_BATCH_BYTES:
                yield batch
                batch = {}
                batch_bytes = 0
            batch[group_idx] = ch_list
            batch_bytes += size
        if batch:
            yield batch

    @staticmethod
    def _iter_groups(mdf, groups_channels, include_group_index, channel_error):
        """Read *groups_channels* one group at a time (bounded per-group path)."""
        # ── Phase 2: batch-fetch one group at a time ──────────────────────
        for group_idx, ch_list in groups_channels.items():
            grp_name     = MDFReader._group_name(mdf, group_idx)
            select_specs = [(ch_name, group_idx, ch_idx)
                            for ch_idx, ch_name in ch_list]

            # One file-block read for all channels in this group (raw=False).
            try:
                sigs: list = mdf.select(select_specs, raw=False)
            except Exception:
                # Fall back to per-channel get on select failure.
                sigs = []
                for ch_idx, ch_name in ch_list:
                    try:
                        sigs.append(
                            mdf.get(ch_name, group=group_idx,
                                    index=ch_idx, raw=False)
                        )
                    except Exception as exc:
                        sigs.append(None)
                        if channel_error is not None:
                            channel_error(grp_name, ch_name, exc)

            # Classify each channel as enum or numeric.
            enum_mask = []
            for i, sig in enumerate(sigs):
                if sig is None:
                    enum_mask.append(False)
                    continue
                try:
                    # Materialise both arrays here so a lazy/block-reference
                    # failure is isolated to this channel.
                    _ = sig.timestamps
                    enum_mask.append(_is_text(sig.samples))
                except Exception as exc:
                    sigs[i] = None
                    enum_mask.append(False)
                    if channel_error is not None:
                        channel_error(grp_name, ch_list[i][1], exc)

            # One file-block read for all enum channels in this group (raw=True).
            # Batched to avoid double-decompress per enum channel (Bottleneck 5).
            raw_map: dict[int, object] = {}   # position → raw Signal
            enum_positions = [i for i, is_e in enumerate(enum_mask) if is_e]
            if enum_positions:
                raw_specs = [
                    (ch_list[i][1], group_idx, ch_list[i][0])
                    for i in enum_positions
                ]
                try:
                    raw_sigs = mdf.select(raw_specs, raw=True)
                    for j, raw_sig in enumerate(raw_sigs):
                        raw_map[enum_positions[j]] = raw_sig
                except Exception:
                    # Fallback: individual get for each enum channel.
                    for i in enum_positions:
                        ch_idx, ch_name = ch_list[i]
                        try:
                            raw_map[i] = mdf.get(
                                ch_name, group=group_idx,
                                index=ch_idx, raw=True
                            )
                        except Exception:
                            pass

            # ── Phase 3: yield one channel at a time ─────────────────────
            for i, (sig, (_ch_idx, ch_name)) in enumerate(zip(sigs, ch_list)):
                if sig is None:
                    continue

                ts_arr  = sig.timestamps
                eng_arr = sig.samples
                unit    = str(getattr(sig, "unit", "") or "")

                if ts_arr is None or eng_arr is None:
                    if channel_error is not None:
                        channel_error(
                            grp_name, ch_name,
                            "timestamps or samples are unavailable",
                        )
                    continue
                if len(ts_arr) == 0:
                    continue
                if len(ts_arr) != len(eng_arr):
                    if channel_error is not None:
                        channel_error(
                            grp_name, ch_name,
                            f"timestamp/sample length mismatch "
                            f"({len(ts_arr)} != {len(eng_arr)})",
                        )
                    continue

                ts_arr = np.asarray(ts_arr, dtype=np.float64)

                if enum_mask[i]:
                    # ── Enum / text channel ───────────────────────────────
                    raw_sig = raw_map.get(i)
                    raw_int = None
                    if raw_sig is not None:
                        raw_int = raw_sig.samples
                        if raw_int is None or len(raw_int) != len(ts_arr):
                            raw_int = None

                    if raw_int is not None:
                        try:
                            num_arr = np.asarray(raw_int, dtype=np.float64)
                        except (TypeError, ValueError):
                            num_arr = np.arange(len(ts_arr), dtype=np.float64)
                    else:
                        num_arr = np.zeros(len(ts_arr), dtype=np.float64)

                    disp_list = _decode_str_arr(eng_arr)

                else:
                    # ── Pure numeric channel ──────────────────────────────
                    try:
                        num_arr   = np.asarray(eng_arr, dtype=np.float64)
                        disp_list = []  # has_labels=False skips raw_values alloc
                    except (TypeError, ValueError):
                        # Cast failed — treat as text (missed by dtype probe).
                        num_arr   = np.arange(len(ts_arr), dtype=np.float64)
                        disp_list = _decode_str_arr(eng_arr)

                item = ((grp_name, ch_name, unit), ts_arr, num_arr, disp_list)
                if include_group_index:
                    yield (group_idx, *item)
                else:
                    yield item

                # Explicit delete so GC can reclaim before the next channel.
                del ts_arr, num_arr, eng_arr, disp_list

            # Release the batch of Signal objects before the next group.
            del sigs

    @staticmethod
    def _iter_arrays_all_groups(
        mdf, groups_channels, include_group_index, channel_error=None
    ):
        """Read the groups in *groups_channels* with two shared selects."""
        flat_channels = [
            (group_idx, ch_idx, ch_name)
            for group_idx, ch_list in groups_channels.items()
            for ch_idx, ch_name in ch_list
        ]
        select_specs = [
            (ch_name, group_idx, ch_idx)
            for group_idx, ch_idx, ch_name in flat_channels
        ]

        try:
            sigs = mdf.select(select_specs, raw=False)
        except Exception:
            # Retain the established per-group/get fallback for unusual MDFs.
            yield from MDFReader._iter_groups(
                mdf, groups_channels, include_group_index, channel_error
            )
            return

        enum_mask = []
        for i, sig in enumerate(sigs):
            if sig is None:
                enum_mask.append(False)
                continue
            try:
                _ = sig.timestamps
                enum_mask.append(_is_text(sig.samples))
            except Exception as exc:
                sigs[i] = None
                enum_mask.append(False)
                if channel_error is not None:
                    group_idx, _ch_idx, ch_name = flat_channels[i]
                    channel_error(
                        MDFReader._group_name(mdf, group_idx), ch_name, exc
                    )
        enum_positions = [i for i, is_enum in enumerate(enum_mask) if is_enum]
        raw_map: dict[int, object] = {}
        if enum_positions:
            raw_specs = [select_specs[i] for i in enum_positions]
            try:
                raw_sigs = mdf.select(raw_specs, raw=True)
                raw_map.update(zip(enum_positions, raw_sigs))
            except Exception:
                for i in enum_positions:
                    ch_name, group_idx, ch_idx = select_specs[i]
                    try:
                        raw_map[i] = mdf.get(
                            ch_name, group=group_idx, index=ch_idx, raw=True
                        )
                    except Exception:
                        pass

        group_names = {
            group_idx: MDFReader._group_name(mdf, group_idx)
            for group_idx in groups_channels
        }
        for i, (sig, (group_idx, _ch_idx, ch_name)) in enumerate(
            zip(sigs, flat_channels)
        ):
            if sig is None:
                continue
            ts_arr = sig.timestamps
            eng_arr = sig.samples
            if (
                ts_arr is None
                or eng_arr is None
                or len(ts_arr) == 0
                or len(ts_arr) != len(eng_arr)
            ):
                if channel_error is not None and (
                    ts_arr is None
                    or eng_arr is None
                    or (
                        ts_arr is not None
                        and eng_arr is not None
                        and len(ts_arr) != len(eng_arr)
                    )
                ):
                    reason = (
                        "timestamps or samples are unavailable"
                        if ts_arr is None or eng_arr is None
                        else f"timestamp/sample length mismatch "
                             f"({len(ts_arr)} != {len(eng_arr)})"
                    )
                    channel_error(group_names[group_idx], ch_name, reason)
                continue

            ts_arr = np.asarray(ts_arr, dtype=np.float64)
            unit = str(getattr(sig, "unit", "") or "")
            if enum_mask[i]:
                raw_sig = raw_map.get(i)
                raw_int = getattr(raw_sig, "samples", None)
                if raw_int is not None and len(raw_int) == len(ts_arr):
                    try:
                        num_arr = np.asarray(raw_int, dtype=np.float64)
                    except (TypeError, ValueError):
                        num_arr = np.arange(len(ts_arr), dtype=np.float64)
                else:
                    num_arr = np.zeros(len(ts_arr), dtype=np.float64)
                disp_values = LazyTextValues(eng_arr)
            else:
                try:
                    num_arr = np.asarray(eng_arr, dtype=np.float64)
                    disp_values = []
                except (TypeError, ValueError):
                    num_arr = np.arange(len(ts_arr), dtype=np.float64)
                    disp_values = LazyTextValues(eng_arr)

            item = (
                (group_names[group_idx], ch_name, unit),
                ts_arr,
                num_arr,
                disp_values,
            )
            # Drop this batch's reference so the consumer holds the only copy.
            sigs[i] = None
            raw_map.pop(i, None)
            if include_group_index:
                yield (group_idx, *item)
            else:
                yield item

    @staticmethod
    def _group_name(mdf, group_idx: int) -> str:
        try:
            grp = mdf.groups[group_idx]
            acq = getattr(grp, "channel_group", None)
            if acq:
                name = getattr(acq, "acq_name", None)
                if name:
                    return str(name)
            src = getattr(grp, "source", None)
            if src:
                name = getattr(src, "name", None)
                if name:
                    return str(name)
        except Exception:
            pass
        return f"Group_{group_idx}"

    @staticmethod
    def _written_bus_channel(mdf, group_idx: int):
        """The bus channel of a group this application wrote (core.mf4_writer),
        as ``(BusType, number)``; None for any other group.

        Only groups whose acquisition source names this application: MF4s
        decoded by other tools record a bus channel too, but their signals
        keep the ``CH?`` keys they always had, so plot configurations saved
        on them still restore.
        """
        from core.mf4_writer import SOURCE_NAME

        try:
            source = mdf.groups[group_idx].channel_group.acq_source
            if source is None or getattr(source, "name", "") != SOURCE_NAME:
                return None
            path = str(getattr(source, "path", "") or "")
        except Exception:
            return None
        for prefix, bus in (("CAN", BusType.CAN), ("LIN", BusType.LIN)):
            number = path[len(prefix):]
            if path.startswith(prefix) and number.isdigit() and int(number) < 255:
                return bus, int(number)
        return None


class MDFSampleSource:
    """
    An open MDF that reads channel groups on request, for a SampleCache.

    ``groups`` lists the readable groups as ``(group_idx, group_name,
    [(ch_idx, ch_name, unit), ...], cycles)`` — the same channels the eager
    path reads, found from metadata alone. The file stays open, and a
    recovery copy stays on disk, until :meth:`close`.
    """

    def __init__(self, mdf, recovery_path, channel_error=None) -> None:
        self._mdf = mdf
        self._recovery_path = recovery_path
        self._channel_error = channel_error
        self.groups: list[tuple[int, str, list[tuple[int, str, str]], int]] = []
        # Group index → bus channel, for groups this application wrote.
        self.group_channels: dict[int, tuple[BusType, int]] = {}
        for group_idx, group in enumerate(mdf.groups):
            if MDFReader._is_raw_can_group(group):
                continue
            channels = []
            for ch_idx, channel in enumerate(group.channels):
                ch_name = getattr(channel, "name", None) or f"Ch{ch_idx}"
                if getattr(channel, "channel_type", -1) == 1:
                    continue
                if ch_name.lower() in ("time", "t", "timestamps"):
                    continue
                channels.append(
                    (ch_idx, ch_name, str(getattr(channel, "unit", "") or ""))
                )
            if channels:
                cycles = int(
                    getattr(getattr(group, "channel_group", None), "cycles_nr", 0)
                    or 0
                )
                self.groups.append(
                    (group_idx, MDFReader._group_name(mdf, group_idx), channels, cycles)
                )
                bus_channel = MDFReader._written_bus_channel(mdf, group_idx)
                if bus_channel is not None:
                    self.group_channels[group_idx] = bus_channel
        self._channels = {g[0]: g[2] for g in self.groups}
        # One thread at a time uses the MDF: the reader's (plotting, a
        # calculation) or a group copy's.
        self._lock = threading.RLock()
        # Groups read one channel at a time are copied in the background
        # after their first read (_GroupCopy), one at a time so the copies
        # take no more than one thread from plotting: the copy once made, or
        # None where a group could not be copied; the groups waiting.
        self._copies: dict[int, _GroupCopy | None] = {}
        self._copying: list[int] = []
        self._copier: threading.Thread | None = None
        self._closing = threading.Event()

    def metadata_rows(self) -> list[tuple[str, str, str]]:
        """``(group_name, channel_name, unit)`` per channel, for the signal tree."""
        return [
            (group_name, ch_name, unit)
            for _idx, group_name, channels, _cycles in self.groups
            for _ch_idx, ch_name, unit in channels
        ]

    def group_bytes(self, group_idx: int) -> int:
        """Memory one group takes once read: a float64 per channel and time."""
        for idx, _name, channels, cycles in self.groups:
            if idx == group_idx:
                return cycles * (len(channels) + 1) * 8
        return 0

    def master(self, group_idx: int, record_offset: int = 0, record_count=None):
        """A group's time stamps, raw; *record_count* None reads to the end."""
        with self._lock:
            return np.asarray(
                self._mdf.get_master(
                    group_idx, record_offset=record_offset, record_count=record_count
                ),
                dtype=np.float64,
            )

    def cycles(self, group_idx: int) -> int:
        for idx, _name, _channels, cycles in self.groups:
            if idx == group_idx:
                return cycles
        return 0

    def first_timestamp(self) -> float | None:
        """The first time stamp of the first group with samples."""
        for group_idx, _name, _channels, cycles in self.groups:
            if cycles <= 0:
                continue
            try:
                with self._lock:
                    master = self._mdf.get_master(group_idx, record_offset=0, record_count=1)
            except Exception:
                continue
            if master is not None and len(master):
                return float(master[0])
        return None

    def read_groups(self, group_indices, channel_names=None):
        """
        Read *group_indices* in one pass.

        Returns ``{(group_idx, channel_name): [(timestamps, values,
        display_values, unit), ...]}``; a list, because one group can hold two
        channels of the same name. *channel_names* limits the read to those
        channels, for a group too large to read whole. Such a group is then
        copied in the background (_GroupCopy), and once the copy is made its
        channels come from it.
        """
        results: dict[tuple[int, str], list] = {}
        to_copy = []
        if channel_names is not None:
            remaining = []
            for group_idx in group_indices:
                copy = self._copies.get(group_idx)
                if copy is not None and copy.read(channel_names, results):
                    continue
                remaining.append(group_idx)
                if group_idx not in self._copies and group_idx not in self._copying:
                    to_copy.append(group_idx)
            group_indices = remaining
        groups_channels = {}
        for group_idx in group_indices:
            channels = [
                (ch_idx, ch_name)
                for ch_idx, ch_name, _unit in self._channels.get(group_idx, ())
                if channel_names is None or ch_name in channel_names
            ]
            if channels:
                groups_channels[group_idx] = channels
        if groups_channels:
            with self._lock:
                for group_idx, (_grp, ch_name, unit), ts, values, display in (
                    MDFReader._iter_arrays_all_groups(
                        self._mdf, groups_channels, True, self._channel_error
                    )
                ):
                    results.setdefault((group_idx, ch_name), []).append(
                        (ts, values, display, unit)
                    )
        for group_idx in to_copy:
            self._start_copy(group_idx)
        return results

    def _start_copy(self, group_idx: int) -> None:
        if self._closing.is_set():
            return
        with self._lock:
            self._copying.append(group_idx)
            if self._copier is None:
                self._copier = threading.Thread(
                    target=self._copy_groups, name="mdf-group-copy", daemon=True
                )
                self._copier.start()

    def _copy_groups(self) -> None:
        while not self._closing.wait(_COPY_DELAY_S):
            with self._lock:
                if not self._copying:
                    self._copier = None
                    return
                group_idx = self._copying[0]
            self._copy_group(group_idx)
            with self._lock:
                self._copying.remove(group_idx)
        with self._lock:
            self._copier = None

    def _copy_group(self, group_idx: int) -> None:
        copy = None
        try:
            group = self._mdf.groups[group_idx].channel_group
            record = int(getattr(group, "samples_byte_nr", 0) or 0) + int(
                getattr(group, "invalidation_bytes_nr", 0) or 0
            )
            copy = _GroupCopy(
                self._select_chunk, record, group_idx,
                self._channels.get(group_idx, ()), self.cycles(group_idx),
            )
        except Exception:
            # No room on the disk, a layout the copy does not take, or the
            # file closed: the group stays read one channel at a time.
            copy = None
        if self._closing.is_set() and copy is not None:
            copy.close()
            copy = None
        self._copies[group_idx] = copy

    def _select_chunk(self, specs, record_offset: int, record_count: int):
        """One chunk of records for a group copy, between the reader's reads."""
        if self._closing.wait(_COPY_REST_S):
            raise _CopyCancelled
        with self._lock:
            if self._closing.is_set() or self._mdf is None:
                raise _CopyCancelled
            return self._mdf.select(
                specs, raw=True, record_offset=record_offset,
                record_count=record_count, copy_master=False,
            )

    def close(self) -> None:
        self._closing.set()
        copier = self._copier
        if copier is not None and copier is not threading.current_thread():
            copier.join()
        for copy in self._copies.values():
            if copy is not None:
                copy.close()
        self._copies = {}
        with self._lock:
            mdf, self._mdf = self._mdf, None
        if mdf is None:
            return
        try:
            mdf.close()
        except Exception:
            pass
        remove_recovery_copy(self._recovery_path)


class _CopyCancelled(Exception):
    """The measurement closed while one of its groups was being copied."""


class _GroupCopy:
    """
    One channel group, copied channel by channel into a temporary file.

    A group stores its channels record by record, so asammdf reads the whole
    group for any one channel. A group too large to keep in memory was read
    that way for every channel plotted: seconds a channel for a group of a
    gigabyte or more, and as long again for the next one. After the first
    such read, a background thread (MDFSampleSource._copy_group) copies every
    channel's raw samples, a few megabytes of records at a time (small enough
    to stay in the processor's cache while each channel is taken out), into a
    PieceFile; a channel is then its pieces through its conversion, exactly
    what ``select(raw=False)`` computes. Raw samples keep the copy about the
    size of the group in the file, not eight bytes a value.

    *select(specs, record_offset, record_count)* reads one chunk of raw
    channels. Only plain numeric channels are copied: anything else raises
    from the constructor, or leaves the channel out, and is read as before.
    """

    def __init__(self, select, record_bytes: int, group_idx: int, channels,
                 cycles: int) -> None:
        self._group_idx = group_idx
        self._all = list(channels)                                   # idx, name, unit
        self._copied: list[tuple[int, str, object, str]] = []      # idx, name, conversion, unit
        self._pieces: dict[object, list] = {}
        self._file = None
        if cycles <= 0 or not channels:
            raise ValueError("nothing to copy")
        chunk = max(1, _COPY_CHUNK_BYTES // max(1, record_bytes))
        specs = [(None, group_idx, ch_idx) for ch_idx, _name, _unit in channels]
        try:
            for offset in range(0, cycles, chunk):
                count = min(chunk, cycles - offset)
                sigs = select(specs, offset, count)
                if self._file is None:
                    self._start(channels, sigs, cycles)
                timestamps = np.asarray(sigs[0].timestamps, dtype=np.float64)
                if len(timestamps) != count:
                    raise ValueError("the group has fewer records than it says")
                self._pieces["time"].append(self._file.append(timestamps))
                for position, ch_idx in self._positions:
                    samples = sigs[position].samples
                    if len(samples) != count or samples.dtype != self._dtypes[ch_idx]:
                        raise ValueError("a channel's samples do not match its records")
                    self._pieces[ch_idx].append(self._file.append(samples))
                del sigs
            self._file.seal()
        except BaseException:
            self.close()
            raise

    def _start(self, channels, sigs, cycles: int) -> None:
        from core.sample_file import PieceFile

        self._positions: list[tuple[int, int]] = []
        self._dtypes: dict[int, np.dtype] = {}
        for position, ((ch_idx, ch_name, _unit), sig) in enumerate(zip(channels, sigs)):
            samples = getattr(sig, "samples", None)
            if (
                isinstance(samples, np.ndarray) and samples.ndim == 1
                and samples.dtype.kind in "biuf" and samples.dtype.itemsize <= 8
                and samples.dtype.names is None
            ):
                self._positions.append((position, ch_idx))
                self._dtypes[ch_idx] = samples.dtype
                self._pieces[ch_idx] = []
                self._copied.append(
                    (ch_idx, ch_name, sig.conversion, str(getattr(sig, "unit", "") or ""))
                )
        if not self._copied:
            raise ValueError("no channel of the group can be copied")
        self._pieces["time"] = []
        size = cycles * (8 + sum(dtype.itemsize for dtype in self._dtypes.values()))
        self._file = PieceFile(size)

    def read(self, channel_names, results) -> bool:
        """
        Add the arrays of the channels named in *channel_names* to *results*,
        as ``MDFSampleSource.read_groups`` returns them. False, adding
        nothing, when one of them was not copied.
        """
        wanted = [c for c in self._all if c[1] in channel_names]
        copied = [c for c in self._copied if c[1] in channel_names]
        if len(copied) != len(wanted) or self._file is None:
            return False
        found: dict[tuple[int, str], list] = {}
        timestamps = None
        for ch_idx, ch_name, conversion, unit in copied:
            try:
                raw = self._file.read(self._pieces[ch_idx])
                values, display = _physical_arrays(raw, conversion)
            except Exception:
                return False
            if timestamps is None:
                timestamps = self._file.read(self._pieces["time"])
            found.setdefault((self._group_idx, ch_name), []).append(
                (timestamps, values, display, unit)
            )
        for key, parts in found.items():
            results.setdefault(key, []).extend(parts)
        return True

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _physical_arrays(raw: np.ndarray, conversion):
    """
    A channel's values and display values from its raw samples, as
    ``_iter_arrays_all_groups`` makes them from ``select(raw=False)``.
    """
    eng = conversion.convert(raw) if conversion else raw
    if _is_text(eng):
        # Text through a conversion: the raw number, and the text to show.
        return np.asarray(raw, dtype=np.float64), LazyTextValues(eng)
    try:
        return np.asarray(eng, dtype=np.float64), []
    except (TypeError, ValueError):
        return np.arange(len(eng), dtype=np.float64), LazyTextValues(eng)
