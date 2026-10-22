# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from core.readers import reader_factory
from core.readers.base import MeasurementReader
from core.signal_store import ChoiceLabels, SampleCache, SignalStore
from core.raw_frame_store import FLAG_LIN, RawFrameStore
from core.bus_types import (
    ALL_CHANNELS_NUMBER,
    BusChannel,
    BusType,
    store_key_prefix,
)
from core.channel_config import ChannelConfig
from core.dbc_decoder import multi_sender_messages, source_address_message_name
from core.signal_store import as_channel_key

# ── Streaming constants ───────────────────────────────────────────────────
_CAN_TREE_INTERVAL     = 10_000   # was 2 000 — build_tree_payload() sorts all signals; every 2k frames was ~250 rebuilds for 500k frames
_CAN_PLOT_INTERVAL     = 25_000   # was 5 000 — partial_ready triggers main-thread redraw; every 5k was 100 redraws
_CAN_PROGRESS_INTERVAL = 10_000
_BULK_PLOT_INTERVAL    = 10
_BULK_PROGRESS_INTERVAL = 50
_RAW_BATCH_SIZE         = 65_536
# A raw-frame log longer than this is grouped by CAN ID chunk by chunk, so
# grouping takes memory per chunk rather than per frame of the log.
_GROUP_CHUNK_ROWS       = 1 << 22
# A CAN ID with more frames than this is decoded in slices of this many.
_DECODE_SLICE_ROWS      = 1 << 20


def _bulk_compute_store_stats(store, rfs) -> None:
    """
    Populate store frame-statistics from the sealed RawFrameStore arrays.

    Replaces the per-frame ``note_frame()`` calls removed from the Pass 1 hot
    loop (Bottleneck 3).  All work is vectorised numpy; no Python loop over
    individual frames.
    """
    import numpy as np
    n = len(rfs)
    if n == 0:
        return

    channels_np = np.frombuffer(rfs.channels,   dtype=np.uint8)
    arb_ids_np  = np.frombuffer(rfs.arb_ids,    dtype=np.uint32)
    dlcs_np     = np.frombuffer(rfs.dlcs,        dtype=np.uint8)
    dirs_np     = np.frombuffer(rfs.directions,  dtype=np.uint8)
    flags_np    = np.frombuffer(rfs.flags,        dtype=np.uint8)

    # Channel set + per-channel frame counts in one counting pass. A channel
    # number can appear on both buses; the bus bit splits the count so
    # "CAN 1" and "LIN 1" are reported separately rather than merged.
    counts = np.zeros(512, dtype=np.int64)
    for start in range(0, n, _GROUP_CHUNK_ROWS):
        end = min(n, start + _GROUP_CHUNK_ROWS)
        is_lin = (flags_np[start:end] & np.uint8(FLAG_LIN)) != 0
        codes = (channels_np[start:end].astype(np.uint16) << 1) | is_lin
        counts += np.bincount(codes, minlength=512)
    unknown = int(counts[255 << 1] + counts[(255 << 1) | 1])
    if unknown:
        store.channels.add(None)
        store.channel_frame_counts[None] = unknown
    for code in np.flatnonzero(counts[:255 << 1]).tolist():
        bus = BusType.LIN if code & 1 else BusType.CAN
        key = (bus, code >> 1)
        store.channels.add(key)
        store.channel_frame_counts[key] = int(counts[code])

    store.total_frames    = n
    store.unmatched_frames = n  # corrected to n - decoded_total by Pass 2

    # First-20-frame diagnostic strings (same format as note_frame)
    _dir_strs = ('Rx', 'Tx', 'Unknown')
    for i in range(min(20, n)):
        ch_byte   = int(channels_np[i])
        ch        = None if ch_byte == 255 else ch_byte
        aid       = int(arb_ids_np[i])
        dlc       = int(dlcs_np[i])
        fl        = int(flags_np[i])
        direction = _dir_strs[min(int(dirs_np[i]), 2)]
        id_hex    = (
            f"0x{aid:08X}" if (bool(fl & 1) or aid > 0x7FF)
            else f"0x{aid:03X}"
        )
        label = store_key_prefix(
            None if ch is None
            else ((BusType.LIN if fl & FLAG_LIN else BusType.CAN), ch)
        )
        store.first_frame_ids.append(
            f"{label} | {id_hex} | DLC={dlc} | {direction}"
        )


def _group_frames(combined):
    """
    Stable-sort frame indices by key; return them with each group's bounds.

    The same result as ``np.argsort(combined, kind='stable')``, which takes
    seconds for a 13-million-frame BLF. A log holds only a few hundred
    distinct keys, so each frame's key is replaced by its rank among them
    first: ranks fit in 16 bits, and NumPy sorts 16-bit keys stably with a
    radix sort in one linear pass.
    """
    import numpy as np
    n = len(combined)
    # Distinct keys from a sample spread over the whole log, then any key
    # the sample missed (a rare frame) from the frames that are not in it.
    sample = np.unique(combined[::max(1, n // 131_072)])
    ranks = np.searchsorted(sample, combined)
    missing = sample[np.minimum(ranks, len(sample) - 1)] != combined
    if missing.any():
        sample = np.union1d(sample, combined[missing])
        ranks = np.searchsorted(sample, combined)
    if len(sample) <= 65_536:
        ranks = ranks.astype(np.uint16)
    sort_idx = np.argsort(ranks, kind='stable')
    counts = np.bincount(ranks, minlength=len(sample))
    boundaries = np.zeros(len(sample) + 1, dtype=np.int64)
    np.cumsum(counts, out=boundaries[1:])
    return sort_idx, boundaries


def _choice_labels(choices, values, count=None):
    """
    Display labels for an enum signal's *values*, or [] for a plain signal.

    *values* is one array, or a list of parts (with their total *count*) that
    the store puts together. Integer choice keys below 65 536 — every DBC seen
    so far — give a ChoiceLabels, which reads each label from the stored
    values when asked (Bottleneck 5: no per-sample Python list). Other keys
    fall back to a per-sample list.
    """
    import numpy as np
    from core.signal_store import ChoiceLabels

    if not choices:
        return []
    try:
        int_keys = {
            k: str(v) for k, v in choices.items()
            if isinstance(k, int) and k >= 0
        }
        max_key = max(int_keys) if int_keys else -1
    except (TypeError, ValueError):
        int_keys = {}
        max_key  = -1

    if int_keys and max_key < 65536:
        lut       = np.empty(max_key + 1, dtype=object)
        lut_valid = np.zeros(max_key + 1, dtype=bool)
        for k, v in int_keys.items():
            lut[k]       = v
            lut_valid[k] = True
        if isinstance(values, list):
            return ChoiceLabels.unbound(lut, lut_valid, count)
        return ChoiceLabels(values, lut, lut_valid)

    # Fallback: Python loop (non-integer / very large keys)
    parts = values if isinstance(values, list) else [values]
    return [
        '' if np.isnan(v)
        else (str(choices[int(v)])
              if int(v) in choices
              else float(v))
        for part in parts
        for v in np.asarray(part, dtype=np.float64)
    ]


def _frame_keys(flags, channels, arb_ids):
    """Each frame's (bus, channel, arbitration ID) as one uint64 key.

    The bus goes in bit 40, above the 8-bit channel at 32 and the 32-bit ID:
    CAN 1 and LIN 1 are different channels that happen to share a number, so
    grouping on the number alone would decode one bus's frames against the
    other's database. Bit 41 is already spoken for by the trace-name encoding.
    """
    import numpy as np
    is_lin = (flags & np.uint8(FLAG_LIN)) != 0
    return (
        (is_lin.astype(np.uint64) << np.uint64(40))
        | (channels.astype(np.uint64) << np.uint64(32))
        | arb_ids.astype(np.uint64)
    )


def _group_frames_chunked(flags, channels, arb_ids, chunk_rows=None):
    """
    What ``_group_frames(_frame_keys(...))`` returns, in bounded memory.

    Two passes over the frame columns, a chunk at a time. The first numbers
    each key in the order it first appears and counts its frames; the second
    turns those numbers into ranks in key order and puts each frame in its
    group's place. The per-frame arrays (the numbers and the sort order) are
    file-backed when large (core.sample_file.scratch_array), so a log of any
    length groups in the memory of one chunk. Frames keep their file order
    within a group.
    """
    import numpy as np
    from core.sample_file import scratch_array

    chunk_rows = chunk_rows or _GROUP_CHUNK_ROWS
    n = len(channels)
    chunks = [(s, min(n, s + chunk_rows)) for s in range(0, n, chunk_rows)]

    # Keys seen so far, sorted, and the number each got when first seen.
    sorted_keys = np.empty(0, dtype=np.uint64)
    number_of_sorted = np.empty(0, dtype=np.int64)
    numbers = scratch_array(n, np.uint32)
    counts = np.zeros(0, dtype=np.int64)
    for start, end in chunks:
        keys = _frame_keys(flags[start:end], channels[start:end], arb_ids[start:end])
        at = np.searchsorted(sorted_keys, keys)
        known = at < len(sorted_keys)
        known[known] = sorted_keys[at[known]] == keys[known]
        if not known.all():
            new_keys = np.unique(keys[~known])
            first_number = len(sorted_keys)
            merged = np.concatenate((sorted_keys, new_keys))
            order = np.argsort(merged, kind='stable')
            sorted_keys = merged[order]
            number_of_sorted = np.concatenate((
                number_of_sorted,
                np.arange(first_number, first_number + len(new_keys), dtype=np.int64),
            ))[order]
            at = np.searchsorted(sorted_keys, keys)
        chunk_numbers = number_of_sorted[at]
        numbers[start:end] = chunk_numbers
        chunk_counts = np.bincount(chunk_numbers, minlength=len(sorted_keys))
        counts = np.concatenate((counts, np.zeros(len(chunk_counts) - len(counts), np.int64)))
        counts += chunk_counts

    # A key's rank is its place in key order.
    rank_of_number = np.empty(len(sorted_keys), dtype=np.int64)
    rank_of_number[number_of_sorted] = np.arange(len(sorted_keys))
    rank_type = np.uint16 if len(sorted_keys) <= 65_536 else np.uint32
    rank_of_number = rank_of_number.astype(rank_type)
    boundaries = np.zeros(len(sorted_keys) + 1, dtype=np.int64)
    np.cumsum(counts[number_of_sorted], out=boundaries[1:])

    sort_idx = scratch_array(n, np.int64)
    cursor = boundaries[:-1].copy()
    for start, end in chunks:
        chunk_ranks = rank_of_number[numbers[start:end]]
        order = np.argsort(chunk_ranks, kind='stable')
        present, first, size = np.unique(
            chunk_ranks[order], return_index=True, return_counts=True
        )
        order += start
        for rank, at, count in zip(present.tolist(), first.tolist(), size.tolist()):
            sort_idx[cursor[rank]:cursor[rank] + count] = order[at:at + count]
            cursor[rank] += count
    return sort_idx, boundaries


class _TimeAxis:
    """
    Normalise channel timestamps, reusing the result for a repeated axis.

    asammdf returns a separate timestamp array for every channel, even for
    the channels of one group that share a time axis. Handing the store the
    same normalised array for those lets it keep one copy per group instead
    of one per channel, which for a large MF4 is most of the timestamp memory.
    """

    __slots__ = ("_source", "_normalised")

    def __init__(self) -> None:
        self._source = None
        self._normalised = None

    def normalise(self, ts_arr, base_ts: float):
        import numpy as np
        source = self._source
        if source is not None and (
            ts_arr is source
            or (len(ts_arr) == len(source) and np.array_equal(ts_arr, source))
        ):
            return self._normalised
        self._source = ts_arr
        self._normalised = ts_arr - base_ts
        return self._normalised


class LoadWorker(QObject):
    """
    Background worker that reads any measurement file and populates a SignalStore.

    Accepts a ChannelConfig (multi-DBC) or a single dbc_path string (legacy).
    The channel config maps CAN channel numbers to DBCDecoder instances.
    A frame on CAN 1 is decoded with CAN-1's decoder; a frame on CAN 2 with
    CAN-2's decoder; if no channel-specific decoder exists, the All-Channels
    fallback is used.
    """

    progress      = Signal(str)
    finished      = Signal(object)
    failed        = Signal(str)
    # Nested payload uses integer/None channel keys, which Qt's QVariantMap
    # conversion cannot represent reliably. Keep it as an opaque Python object.
    tree_update   = Signal(object)
    partial_ready = Signal()

    def __init__(
        self,
        measurement_path: str | Path,
        channel_config: ChannelConfig | str | Path | None = None,
    ) -> None:
        super().__init__()
        self._measurement_path = str(measurement_path)

        # Accept ChannelConfig directly or legacy single-DBC path string
        if isinstance(channel_config, ChannelConfig):
            self._channel_config: ChannelConfig | None = channel_config
        elif channel_config:
            self._channel_config = ChannelConfig.from_single_dbc(str(channel_config))
        else:
            self._channel_config = None

        # Legacy compatibility: first DBC path (for reader_factory)
        self._dbc_path: str | None = None
        if self._channel_config:
            paths = self._channel_config.all_dbc_paths()
            if paths:
                self._dbc_path = paths[0]

    @Slot()
    def run(self) -> None:
        t_start = time.perf_counter()
        try:
            self.progress.emit("Initialising reader...")
            reader: MeasurementReader = reader_factory(
                self._measurement_path, self._dbc_path
            )
            self.progress.emit(f"Source: {reader.source_description}")
            for msg in getattr(reader, "load_messages", []):
                self.progress.emit(msg)
            self._share_reader_decoder(reader)

            if self._channel_config:
                self.progress.emit(self._channel_config.summary())

            store = SignalStore()
            self._live_store = store

            if hasattr(reader, "iter_decoded_channel_arrays"):
                self._run_mdf_bus_arrays(reader, store)
            elif reader.has_raw_frames and hasattr(reader, "iter_frames_only"):
                # Packed raw-frame read followed by one bulk vectorised decode.
                # The GUI receives the completed signal hierarchy in one
                # handoff instead of rebuilding it group by group.
                self._run_can_raw_vectorized(reader, store)
            elif reader.has_raw_frames and hasattr(reader, "iter_with_frames"):
                self._run_can_raw(reader, store)
            elif hasattr(reader, "iter_channel_arrays"):
                self._run_bulk_array(reader, store)
            else:
                self._run_sample_loop(reader, store)

            load_warnings = list(getattr(reader, "load_warnings", ()) or ())
            store.load_warnings = load_warnings
            if load_warnings:
                warning_text = "\n".join(
                    f"  {index}. {warning}"
                    for index, warning in enumerate(load_warnings, 1)
                )
                store.diagnostics_text += (
                    "\n\nPARTIAL LOAD WARNINGS\n" + warning_text
                )
                self.progress.emit(
                    f"Completed with warnings: {len(load_warnings):,} "
                    "channel/group item(s) were skipped."
                )

            elapsed = time.perf_counter() - t_start
            elapsed_str = (
                f"{elapsed:.1f} s" if elapsed < 60
                else f"{elapsed/60:.1f} min ({elapsed:.0f} s)"
            )
            self.progress.emit(
                f"Completed | signals: {len(store._series_by_key):,} | "
                f"samples: {store.total_samples:,} | "
                f"elapsed: {elapsed_str}"
            )
            store.diagnostics_text += f"\n\nFile load time: {elapsed_str}"
            self.progress.emit(store.channel_summary_text())
            self.finished.emit(store)

        except Exception as exc:
            import traceback
            elapsed = time.perf_counter() - t_start
            self.failed.emit(
                f"{exc}\n\n{traceback.format_exc()}\n\nFailed after {elapsed:.1f} s"
            )

    def _share_reader_decoder(self, reader) -> None:
        """
        Give the channel config the database the BLF/ASC reader already parsed.

        Both used to parse the first database: once for the reader, once for
        the channel map, a second or more each for a large DBC.
        """
        from core.dbc_decoder import DBCDecoder
        decoder = getattr(reader, "decoder", None)
        if (
            self._channel_config is not None
            and self._dbc_path
            and isinstance(decoder, DBCDecoder)
            and Path(decoder.dbc_path) == Path(self._dbc_path)
        ):
            self._channel_config._decoder_cache.setdefault(self._dbc_path, decoder)

    # ── CAN-raw path: packed read + bulk vectorised decode (preferred) ───

    def _run_can_raw_vectorized(self, reader, store: SignalStore) -> None:
        """
        Bulk loader for BLF/ASC streams.

        Drain every raw frame once into the packed on-disk RawFrameStore.  The
        complete arrays are then grouped by ``(channel, arbitration_id)`` and
        decoded with NumPy.  The raw store remains available for CAN Trace.

        Each group bulk-inserts via
        :meth:`SignalStore.add_series_bulk` (one C-level memcopy per signal).
        Compared to the old per-frame cantools decode, this skips ~50× of the
        Python work for the common little-endian integer-signal case.  Signal
        tree publication is deliberately deferred until every group is ready,
        avoiding queued sequential tree rebuilds in the GUI.
        """
        import numpy as np
        from core.vectorized_decoder import VectorizedDBC

        cfg = self._channel_config
        if cfg:
            cfg.build_all_decoders()

        # (bus, channel) → decoder, with (bus, 0) as that bus's fallback.
        #
        # This loop is the one that *can* be bus-aware: it reads the bus back
        # out of the raw-frame flags, so a LIN 1 frame finds the LDF and a
        # CAN 1 frame finds the DBC even though both are channel 1. The
        # per-frame loops elsewhere still use can_decoder_map(), which is
        # integer-keyed for the formats that carry CAN only.
        _decoder_map: dict[BusChannel, object | None] = {}
        if cfg:
            _decoder_map = cfg.bus_decoder_map()

        # Choices lookup so SignalStore knows which signals carry display labels.
        choices_lookup: dict[tuple[int | None, str, str], dict] = {}
        for ch, decoder in _decoder_map.items():
            if decoder is None:
                continue
            for (msg_name, sig_name), choices in decoder._choices_cache.items():
                if choices:
                    choices_lookup[(ch, msg_name, sig_name)] = choices
        store.set_choices_lookup(choices_lookup)

        rfs = RawFrameStore()
        store.raw_frame_store = rfs

        # ── Read once: drain packed frames into raw_frame_store ──────────
        self.progress.emit("Bulk reading and packing raw frame arrays...")
        read_started = time.perf_counter()
        base_ts: float | None = None
        index   = 0
        _batched = hasattr(reader, 'iter_raw_batches')
        _fast   = hasattr(reader, 'iter_raw_tuples')

        if _batched:
            batch_number = 0
            for (
                batch_base_ts, batch_ts, batch_ch, batch_ids, batch_dlcs,
                batch_dirs, batch_flags, batch_data,
            ) in reader.iter_raw_batches(_RAW_BATCH_SIZE):
                batch_number += 1
                if base_ts is None:
                    base_ts = float(batch_base_ts)
                    store.base_ts = base_ts
                rfs.append_raw_batch(
                    batch_ts, batch_ch, batch_ids, batch_dlcs,
                    batch_dirs, batch_flags, batch_data,
                )
                index += len(batch_ts)
                if batch_number == 1 or batch_number % 8 == 0 \
                        or len(batch_ts) < _RAW_BATCH_SIZE:
                    self.progress.emit(f"Reading frames: {index:,}...")

        elif _fast:
            # Zero-object fast path (Bottlenecks 1, 3, 4):
            #   • yields tuples instead of RawFrame dataclasses
            #   • append_raw() skips name-table lookup and string direction
            #   • note_frame() removed; stats computed in bulk after seal()
            # Batch metadata conversion and raw-data writes.  A 16K-frame
            # batch turns ~12 million Python append/write calls for a 1.5M
            # frame file into fewer than 100 C-level bulk operations.
            batch_ts: list[float] = []
            batch_ch: list[int] = []
            batch_ids: list[int] = []
            batch_dlcs: list[int] = []
            batch_dirs: list[int] = []
            batch_flags: list[int] = []
            batch_data = bytearray(_RAW_BATCH_SIZE * 64)

            def flush_batch() -> None:
                if not batch_ts:
                    return
                rfs.append_raw_batch(
                    batch_ts, batch_ch, batch_ids, batch_dlcs,
                    batch_dirs, batch_flags, batch_data,
                )
                batch_ts.clear()
                batch_ch.clear()
                batch_ids.clear()
                batch_dlcs.clear()
                batch_dirs.clear()
                batch_flags.clear()

            for ts, ch_byte, arb_id, dlc, dir_int, is_ext, is_fd, data in \
                    reader.iter_raw_tuples():
                index += 1
                if base_ts is None:
                    base_ts = ts
                    store.base_ts = base_ts
                slot = len(batch_ts)
                batch_ts.append(ts - base_ts)
                batch_ch.append(ch_byte)
                batch_ids.append(arb_id & 0xFFFF_FFFF)
                batch_dlcs.append(min(dlc, 255))
                batch_dirs.append(dir_int)
                batch_flags.append((1 if is_ext else 0) | (2 if is_fd else 0))
                offset = slot * 64
                batch_data[offset:offset + 64] = b'\x00' * 64
                data_len = min(len(data), 64)
                if data_len:
                    batch_data[offset:offset + data_len] = data[:data_len]
                if len(batch_ts) == _RAW_BATCH_SIZE:
                    flush_batch()
                if index == 1 or index % _CAN_PROGRESS_INTERVAL == 0:
                    self.progress.emit(f"Reading frames: {index:,}...")
            flush_batch()
        else:
            # Legacy path: RawFrame objects with per-frame note_frame()
            rfs_append = rfs.append
            note_frame = store.note_frame
            for frame in reader.iter_frames_only():
                index += 1
                if base_ts is None:
                    base_ts = frame.timestamp
                    store.base_ts = base_ts
                frame.timestamp -= base_ts
                note_frame(frame)
                rfs_append(
                    timestamp   = frame.timestamp,
                    channel     = frame.channel,
                    arb_id      = frame.arbitration_id,
                    dlc         = frame.dlc,
                    direction   = frame.direction,
                    is_extended = frame.is_extended_id,
                    is_fd       = frame.is_fd,
                    data        = frame.data,
                    frame_name  = '',
                    decoded     = False,
                )
                if index == 1 or index % _CAN_PROGRESS_INTERVAL == 0:
                    self.progress.emit(f"Reading frames: {index:,}...")

        rfs.seal()
        read_elapsed = time.perf_counter() - read_started
        self.progress.emit(
            f"Bulk frame-array read/store complete: {index:,} frames in {read_elapsed:.1f} s"
        )

        if _batched or _fast:
            # Bulk-compute what per-frame note_frame() would have done.
            _bulk_compute_store_stats(store, rfs)
        n = len(rfs)
        if n == 0:
            store.normalize_timestamps(already_normalized=True)
            store.diagnostics_text = (
                store.channel_summary_text() + "\n\n(no frames in file)"
            )
            return

        if not rfs.payload_mapped:
            self.progress.emit(
                "WARNING: mmap unavailable for raw-frame store; "
                "falling back to per-frame decode."
            )
            self._decode_fallback_in_place(rfs, store, _decoder_map)
            return

        # ── One global vectorised decode, followed by one GUI handoff ────
        decode_started = time.perf_counter()

        timestamps_np = np.frombuffer(rfs.timestamps, dtype=np.float64)
        channels_np   = np.frombuffer(rfs.channels,   dtype=np.uint8)
        arb_ids_np    = np.frombuffer(rfs.arb_ids,    dtype=np.uint32)
        dlcs_np       = np.frombuffer(rfs.dlcs,       dtype=np.uint8)
        # Frames' 64-byte payload rows come from rfs.rows(), zero-padded.

        # Writable views into the in-memory metadata so we can flip the
        # "decoded" bit and assign name_ids per group post-hoc.
        flags_np    = np.asarray(memoryview(rfs.flags))
        name_ids_np = np.asarray(memoryview(rfs.name_ids))

        # Build (bus, channel, arb_id) groups; see _frame_keys(). A long log
        # is grouped a chunk at a time, so no array the length of the log is
        # held in memory.
        if n <= _GROUP_CHUNK_ROWS:
            sort_idx, boundaries = _group_frames(
                _frame_keys(flags_np, channels_np, arb_ids_np)
            )
        else:
            sort_idx, boundaries = _group_frames_chunked(
                flags_np, channels_np, arb_ids_np
            )

        vec_dbcs: dict[int, VectorizedDBC] = {}
        decoded_total    = 0
        dlc_short_total  = 0
        dlc_short_names: set[str] = set()
        decoded_groups   = 0
        no_signals_total = 0
        total_groups     = len(boundaries) - 1
        self.progress.emit(
            f"Bulk decoding {n:,} frames across {total_groups:,} CAN ID groups..."
        )

        # Resolve every group's message before decoding any: whether a
        # message gets one series per J1939 source address depends on all of
        # the frame IDs that matched it.
        group_matches: list[tuple | None] = []
        for g in range(total_groups):
            first    = sort_idx[int(boundaries[g])]
            ch_byte  = int(channels_np[first])
            arb_id   = int(arb_ids_np[first])
            bus      = BusType.LIN if flags_np[first] & FLAG_LIN else BusType.CAN
            ch       = None if ch_byte == 255 else ch_byte
            ch_key   = as_channel_key(ch, bus)

            decoder = (
                _decoder_map.get(ch_key)
                or _decoder_map.get((bus, ALL_CHANNELS_NUMBER))
            )
            if decoder is None:
                group_matches.append(None)
                continue

            vec = vec_dbcs.get(id(decoder))
            if vec is None:
                vec = VectorizedDBC(decoder)
                vec_dbcs[id(decoder)] = vec

            candidates = vec.get_candidates(arb_id, is_extended=(arb_id > 0x7FF))
            # Match existing single-decoder behaviour: pick the first candidate.
            group_matches.append(
                (ch_key, arb_id, decoder, vec, candidates[0]) if candidates else None
            )

        split_messages = multi_sender_messages(
            ((match[0], id(match[4])), match[1])
            for match in group_matches if match is not None
        )

        for g in range(total_groups):
            start, end = int(boundaries[g]), int(boundaries[g + 1])
            group_idx  = sort_idx[start:end]

            match = group_matches[g]
            if match is None:
                continue
            ch_key, arb_id, decoder, vec, message = match

            if (ch_key, id(message)) in split_messages:
                msg_name = source_address_message_name(message.name, arb_id & 0xFF)
                msg_id   = arb_id
            else:
                msg_name = message.name
                msg_id   = int(getattr(message, 'frame_id', arb_id))
            msg_dec  = vec.get_message_decoder(message)

            # A CAN ID with more frames than one slice is decoded a slice at
            # a time, so no copy of all its frames' data is held at once.
            slices = [
                sort_idx[s:min(end, s + _DECODE_SLICE_ROWS)]
                for s in range(start, end, _DECODE_SLICE_ROWS)
            ]
            in_parts = len(slices) > 1

            # Frames shorter than the message they matched decode from the
            # zero padding of the fixed 64-byte record rather than failing, so
            # a database meant for a different cluster produces plausible
            # zeros instead of an error. Counting the shortfall is what makes
            # that visible — on LIN especially, where unrelated clusters
            # reuse the same low frame IDs and only the lengths differ.
            expected_length = int(getattr(message, 'length', 0) or 0)
            if expected_length:
                short_rows = sum(
                    int((dlcs_np[group_idx] < expected_length).sum())
                    for group_idx in slices
                )
                if short_rows:
                    dlc_short_total += short_rows
                    dlc_short_names.add(msg_name)

            # In parts: name -> [ext, own time parts or None, value parts].
            # A signal valid in every row shares the slices' time parts.
            parts: dict[str, list] = {}
            shared_ts: list = []
            has_results = False
            failed = False
            for group_idx in slices:
                try:
                    sig_results = msg_dec.decode(rfs.rows(group_idx))
                except Exception:
                    failed = True
                    break
                has_results = has_results or bool(sig_results)
                ts_arr = timestamps_np[group_idx]
                if in_parts:
                    shared_ts.append(store.keep(ts_arr))

                for sig_name, (ext, numeric_arr) in sig_results.items():
                    # Slow-path decode and vectorized multiplexing represent
                    # inactive/failed rows as NaN.  Do not materialize those rows
                    # in SignalStore; asammdf and cantools expose sparse branch
                    # series containing only timestamps where the signal exists.
                    valid_mask = ~np.isnan(numeric_arr)
                    if in_parts:
                        entry = parts.setdefault(sig_name, [ext, None, []])
                        all_valid = bool(valid_mask.all())
                        if not all_valid and entry[1] is None:
                            # Its own time parts from here on, the earlier
                            # (all-valid) slices' shared ones first.
                            entry[1] = list(shared_ts[:-1])
                        if entry[1] is not None:
                            entry[1].append(
                                shared_ts[-1] if all_valid
                                else store.keep(ts_arr[valid_mask])
                            )
                        entry[2].append(store.keep(
                            numeric_arr if all_valid else numeric_arr[valid_mask]
                        ))
                        continue
                    if not valid_mask.any():
                        continue
                    if valid_mask.all():
                        signal_ts = ts_arr
                    else:
                        signal_ts = ts_arr[valid_mask]
                        numeric_arr = numeric_arr[valid_mask]
                    raw_values = _choice_labels(ext.choices, numeric_arr)
                    store.add_series_bulk(
                        channel      = ch_key,
                        message_name = msg_name,
                        message_id   = msg_id,
                        signal_name  = sig_name,
                        unit         = ext.unit,
                        timestamps   = signal_ts,
                        values       = numeric_arr,
                        raw_values   = raw_values,
                        has_labels   = bool(ext.choices),
                    )
            if failed:
                continue

            for sig_name, (ext, own_ts, value_parts) in parts.items():
                count = sum(len(part) for part in value_parts)
                if not count:
                    continue
                store.add_series_bulk(
                    channel      = ch_key,
                    message_name = msg_name,
                    message_id   = msg_id,
                    signal_name  = sig_name,
                    unit         = ext.unit,
                    timestamps   = shared_ts if own_ts is None else own_ts,
                    values       = value_parts,
                    raw_values   = _choice_labels(ext.choices, value_parts, count),
                    has_labels   = bool(ext.choices),
                )
            del parts, shared_ts

            # Mark these frames as decoded in the raw-frame store
            nid = rfs._name_to_id.get(msg_name)
            if nid is None:
                if len(rfs.name_table) <= 65535:
                    nid = len(rfs.name_table)
                    rfs.name_table.append(msg_name)
                    rfs._name_to_id[msg_name] = nid
                else:
                    nid = 0
            for group_idx in slices:
                flags_np[group_idx]    |= np.uint8(4)         # decoded bit
                name_ids_np[group_idx]  = np.uint16(nid)

            # Update decoder stats so diagnostics_text() makes sense.
            # Signal-less groups (e.g. raw UDS diagnostic frames) are counted
            # separately — they are not decode failures.
            group_size = end - start
            if has_results:
                decoder.stats['decode_success'] = (
                    decoder.stats.get('decode_success', 0) + group_size
                )
            elif not message.signals:
                decoder.stats['decoded_no_signals'] = (
                    decoder.stats.get('decoded_no_signals', 0) + group_size
                )
                no_signals_total += group_size

            decoded_total  += group_size
            decoded_groups += 1

        # Override per-frame counters (add_series_bulk is series-oriented and
        # over-counts decoded_frames; bulk stats initially count every frame as
        # unmatched because decoding has not happened yet).
        store.decoded_frames   = decoded_total
        store.unmatched_frames = n - decoded_total
        # Groups are decoded one frame ID at a time, so a signal fed by
        # several IDs holds one sweep per ID until it is reordered.
        store.sort_merged_series()
        decode_elapsed = time.perf_counter() - decode_started
        n_sigs = len(store._series_by_key)
        hint = (
            " (measurement contains only diagnostic / signal-less frames)"
            if n_sigs == 0 and no_signals_total > 0 else ""
        )
        self.progress.emit(
            f"Bulk vectorized decode/import complete: "
            f"{decoded_groups:,}/{total_groups:,} groups | "
            f"signals: {n_sigs:,} | frames: {decoded_total:,}{hint} | "
            f"elapsed: {decode_elapsed:.1f} s"
        )
        if dlc_short_total:
            named = ", ".join(sorted(dlc_short_names)[:5])
            more = (
                f" and {len(dlc_short_names) - 5} more"
                if len(dlc_short_names) > 5 else ""
            )
            self.progress.emit(
                f"WARNING: {dlc_short_total:,} frame(s) were shorter than the "
                f"message they matched ({named}{more}). Those signals decoded "
                "from padding, not from the bus — usually a database assigned "
                "to the wrong channel."
            )

        # Trace tab: needs decoder + config for on-demand signal expansion.
        if cfg:
            rfs.decoder        = cfg.decoder_for(BusType.CAN, None)
            rfs.channel_config = cfg

        # Publish the completed hierarchy once.  Intermediate group updates
        # queued multiple expensive Qt tree rebuilds after large BLF/ASC loads.
        if store.is_tree_dirty():
            self.tree_update.emit(store.build_tree_payload())
        self.partial_ready.emit()

        diag_parts = [store.channel_summary_text()]
        diag_parts.append(
            "\nFirst frame IDs seen in file:\n"
            + ("\n".join(store.first_frame_ids) if store.first_frame_ids else "(none)")
        )
        if cfg:
            for path in cfg.all_dbc_paths():
                dec = cfg._decoder_cache.get(path)
                if dec:
                    diag_parts.append(f"\n\n{dec.diagnostics_text()}")

        store.normalize_timestamps(already_normalized=True)
        store.diagnostics_text = "\n".join(diag_parts)

    # ── Per-frame fallback (used only when mmap unavailable) ─────────────

    def _decode_fallback_in_place(self, rfs, store, decoder_map) -> None:
        """Final-resort: walk the sealed rfs frame-by-frame using cantools."""
        import numpy as np
        from core.models import RawFrame

        n = len(rfs)
        if n == 0:
            return
        timestamps_np = np.frombuffer(rfs.timestamps, dtype=np.float64)
        channels_np   = np.frombuffer(rfs.channels,   dtype=np.uint8)
        arb_ids_np    = np.frombuffer(rfs.arb_ids,    dtype=np.uint32)
        flags_np      = np.frombuffer(rfs.flags,      dtype=np.uint8)
        dlcs_np       = np.frombuffer(rfs.dlcs,       dtype=np.uint8)

        decoded_total = 0
        for i in range(n):
            ch_byte = int(channels_np[i])
            ch      = None if ch_byte == 255 else ch_byte
            decoder = decoder_map.get(ch) or decoder_map.get(None)
            if decoder is None:
                continue
            data = rfs._read_data(i, min(int(dlcs_np[i]), 64))
            frame = RawFrame(
                timestamp=float(timestamps_np[i]),
                channel=ch,
                arbitration_id=int(arb_ids_np[i]),
                is_extended_id=bool(flags_np[i] & 1),
                is_fd=bool(flags_np[i] & 2),
                dlc=int(dlcs_np[i]),
                data=data,
                direction='Unknown',
            )
            samples = decoder.decode_frame(frame)
            if samples:
                store.add_samples_direct(samples)
                decoded_total += 1
        store.decoded_frames   = decoded_total
        store.unmatched_frames = n - decoded_total
        if store.is_tree_dirty():
            self.tree_update.emit(store.build_tree_payload())
        self.partial_ready.emit()
        store.normalize_timestamps(already_normalized=True)
        store.diagnostics_text = store.channel_summary_text()

    # ── CAN-raw path: legacy 1-pass (fallback) ───────────────────────────

    def _run_can_raw(self, reader, store: SignalStore) -> None:
        self.progress.emit("Opening measurement file and starting decode...")
        base_ts: float | None = None
        index = 0

        rfs = RawFrameStore()
        store.raw_frame_store = rfs

        # Pre-build all decoders so the first frame pays no parse cost
        cfg = self._channel_config
        if cfg:
            cfg.build_all_decoders()

        # Pre-compute channel→decoder map so the hot loop pays one dict.get()
        # instead of a Python function call + property access + two dict lookups
        # per frame.  Unknown channels seen mid-file are resolved once and cached.
        # CAN-only, integer-keyed by design — see can_decoder_map().
        _decoder_map: dict[int | None, object | None] = {}
        if cfg:
            _decoder_map = cfg.can_decoder_map()
            _fallback_dec = _decoder_map.get(None)
        else:
            _fallback_dec = None

        # Build (channel, msg_name, sig_name) → choices lookup so the SignalStore
        # only allocates the per-sample raw_values list for label-bearing signals.
        # Frames on unmapped channels use the fallback decoder; the SignalStore
        # falls back to the (None, msg, sig) entry when the channel-specific key
        # is absent.
        choices_lookup: dict[tuple[int | None, str, str], dict] = {}
        for ch, decoder in _decoder_map.items():
            if decoder is None:
                continue
            for (msg_name, sig_name), choices in decoder._choices_cache.items():
                if choices:
                    choices_lookup[(ch, msg_name, sig_name)] = choices
        store.set_choices_lookup(choices_lookup)

        for frame, _legacy_samples in reader.iter_with_frames():
            index += 1
            if base_ts is None:
                base_ts = frame.timestamp
                store.base_ts = base_ts
            frame.timestamp -= base_ts  # normalise to t=0

            store.note_frame(frame)

            # ── Multi-DBC decode: pick decoder by channel ─────────────────
            if cfg:
                ch = frame.channel
                if ch in _decoder_map:
                    decoder = _decoder_map[ch]
                else:
                    # First time seeing this channel — resolve once and cache
                    decoder = cfg.decoder_for(BusType.CAN, ch) or _fallback_dec
                    _decoder_map[ch] = decoder
                samples = decoder.decode_frame(frame) if decoder else []
            else:
                samples = []
            # NOTE: samples already carry frame.timestamp (already normalised above).
            # Do NOT subtract base_ts again here — that would double-normalise.

            # Store every frame (decoded or not)
            rfs.append(
                timestamp   = frame.timestamp,
                channel     = frame.channel,
                arb_id      = frame.arbitration_id,
                dlc         = frame.dlc,
                direction   = frame.direction,
                is_extended = frame.is_extended_id,
                is_fd       = frame.is_fd,
                data        = frame.data,
                frame_name  = samples[0].message_name if samples else '',
                decoded     = bool(samples),
            )
            if samples:
                store.add_samples_direct(samples)
            else:
                store.unmatched_frames += 1

            # Only rebuild the (sorted) tree payload when a new signal has
            # been added since the last emit. After ~10 s of decode the signal
            # set has stabilised and every later interval would be wasted work.
            if index % _CAN_TREE_INTERVAL == 0 and store.is_tree_dirty():
                self.tree_update.emit(store.build_tree_payload())
            if index % _CAN_PLOT_INTERVAL == 0:
                self.partial_ready.emit()
            if index == 1 or index % _CAN_PROGRESS_INTERVAL == 0:
                self.progress.emit(
                    f"Processed {index:,} frames | "
                    f"signals: {len(store._series_by_key):,} | "
                    f"samples: {store.total_samples:,}"
                )

        # Final flush — guarantees the tree reflects everything we saw, even
        # when the last new signal appeared between intervals.
        if store.is_tree_dirty():
            self.tree_update.emit(store.build_tree_payload())

        rfs.seal()

        # Attach the first available decoder to rfs for on-demand signal decode
        if cfg:
            rfs.decoder = cfg.decoder_for(BusType.CAN, None)  # All-channels or first
            rfs.channel_config = cfg              # full config for per-channel decode

        # Diagnostics
        diag_parts = [store.channel_summary_text()]
        diag_parts.append("\nFirst frame IDs seen in file:\n"
                          + ("\n".join(store.first_frame_ids)
                             if store.first_frame_ids else "(none)"))
        if cfg:
            for path in cfg.all_dbc_paths():
                from core.dbc_decoder import DBCDecoder
                dec = cfg._decoder_cache.get(path)
                if dec:
                    diag_parts.append(f"\n\n{dec.diagnostics_text()}")

        store.normalize_timestamps(already_normalized=True)
        store.diagnostics_text = "\n".join(diag_parts)

    def _decode_recovered_mdf_trace(
        self,
        source_store: RawFrameStore,
        store: SignalStore,
        native_base_ts: float | None,
    ) -> float:
        """DBC-decode readable raw groups after native MDF extraction fails."""
        import numpy as np

        source_store.seal()
        frame_count = len(source_store)
        source_timestamps = np.frombuffer(
            source_store.timestamps, dtype=np.float64
        )
        raw_base_ts = (
            float(np.min(source_timestamps)) if frame_count else 0.0
        )

        class _RecoveredRawArrays:
            def iter_raw_batches(self, batch_size):
                channels = np.frombuffer(source_store.channels, dtype=np.uint8)
                arb_ids = np.frombuffer(source_store.arb_ids, dtype=np.uint32)
                dlcs = np.frombuffer(source_store.dlcs, dtype=np.uint8)
                directions = np.frombuffer(
                    source_store.directions, dtype=np.uint8
                )
                flags = np.frombuffer(source_store.flags, dtype=np.uint8)
                for start in range(0, frame_count, batch_size):
                    end = min(frame_count, start + batch_size)
                    yield (
                        raw_base_ts,
                        (source_timestamps[start:end] - raw_base_ts).tolist(),
                        channels[start:end].tolist(),
                        arb_ids[start:end].tolist(),
                        dlcs[start:end].tolist(),
                        directions[start:end].tolist(),
                        flags[start:end].tolist(),
                        source_store.rows(slice(start, end)).tobytes(),
                    )

        self.progress.emit(
            "WARNING: native DBC extraction failed; decoding each readable "
            "raw CAN group with the analyzer's bounded fallback."
        )
        try:
            self._run_can_raw_vectorized(_RecoveredRawArrays(), store)
        finally:
            source_store.close()

        global_base_ts = (
            raw_base_ts
            if native_base_ts is None
            else min(native_base_ts, raw_base_ts)
        )
        shift = raw_base_ts - global_base_ts
        if shift:
            recovered_store = store.raw_frame_store
            np.frombuffer(
                recovered_store.timestamps, dtype=np.float64
            )[:] += shift
            # Series of one group can share a timestamp array; shift it once.
            shifted: set[int] = set()
            for series in store._series_by_key.values():
                if id(series.timestamps) in shifted:
                    continue
                shifted.add(id(series.timestamps))
                series.numpy_timestamps()[:] += shift
        store.base_ts = global_base_ts
        return global_base_ts

    # ── Native asammdf bus decode path ───────────────────────────────────

    def _run_mdf_bus_arrays(self, reader, store: SignalStore) -> None:
        """Native asammdf signal decode plus bulk raw-array CAN Trace import."""
        import numpy as np

        stage_start = time.perf_counter()
        self.progress.emit("Decoding MF4 bus log with asammdf (one pass)...")
        cfg = self._channel_config
        metadata_keys: set[tuple[BusChannel | None, str, str]] | None = None
        trace_message_names: dict[tuple[BusChannel | None, int], str] = {}

        trace_store = (
            RawFrameStore()
            if getattr(reader, "supports_raw_frame_arrays", False)
            else None
        )
        trace_frames = 0

        def on_raw_frame_batch(
            timestamps,
            channels,
            arb_ids,
            dlcs,
            directions,
            flags,
            data_rows,
        ):
            nonlocal trace_frames
            trace_store.append_numpy_batch(
                timestamps,
                channels,
                arb_ids,
                dlcs,
                directions,
                flags,
                data_rows,
            )
            trace_frames += len(timestamps)

        last_progress = [-1]

        def on_extract_progress(current, total):
            if total:
                pct = int(current * 100 / total)
                if pct >= last_progress[0] + 5 or current == total:
                    last_progress[0] = pct
                    self.progress.emit(f"asammdf bus decode: {pct}%")

        def on_metadata_ready(metadata_rows):
            nonlocal metadata_keys
            payload = {}
            metadata_keys = set()
            for (
                channel,
                message_name,
                _message_id,
                signal_name,
                _unit,
            ) in metadata_rows:
                # Normalise exactly as SignalStore does. The metadata tree is
                # compared against the store's finished tree below to decide
                # whether a second full Qt rebuild is needed; if one side kept
                # bare integer channels and the other bus-tagged keys, they
                # would never compare equal and every load would pay for a
                # duplicate rebuild.
                channel = as_channel_key(channel)
                messages = payload.setdefault(channel, {})
                signals = messages.setdefault(message_name, [])
                if signal_name not in signals:
                    signals.append(signal_name)
                metadata_keys.add((channel, message_name, signal_name))
            reader_trace_names = getattr(
                reader, "dbc_trace_message_names", None
            )
            if reader_trace_names is None:
                # Compatibility for alternate/test readers whose metadata is
                # entirely DBC-derived.
                for (
                    channel,
                    message_name,
                    message_id,
                    _signal_name,
                    _unit,
                ) in metadata_rows:
                    trace_message_names.setdefault(
                        (as_channel_key(channel), int(message_id)), message_name
                    )
            else:
                trace_message_names.update(reader_trace_names)
            self.tree_update.emit(payload)
            self.progress.emit(
                f"MF4 signal list ready: {len(metadata_rows):,} signals in "
                f"{time.perf_counter() - stage_start:.1f} s"
            )

        # The MF4 reader leaves the decoded signals in asammdf's decoded file
        # (lazy): they are read the first time each is used, so the decode
        # holds none of them in memory. A reader without that keeps every
        # array until extraction closes. Either way the global first
        # timestamp is found before insertion so every signal shares t=0.
        arrays = []
        lazy = None
        base_ts: float | None = None
        try:
            iterator_kwargs = {
                "progress": on_extract_progress,
                "metadata_ready": on_metadata_ready,
            }
            if trace_store is not None:
                iterator_kwargs["raw_frame_batch"] = on_raw_frame_batch
            open_decoded_sources = getattr(reader, "open_decoded_sources", None)
            if open_decoded_sources is not None:
                lazy = open_decoded_sources(cfg, **iterator_kwargs)
                base_ts = lazy.source.first_timestamp(
                    signal[0] for signal in lazy.signals
                )
            else:
                for meta, ts_arr, num_arr, disp_list in \
                        reader.iter_decoded_channel_arrays(cfg, **iterator_kwargs):
                    if len(ts_arr) == 0:
                        continue
                    arrays.append((meta, ts_arr, num_arr, disp_list))
                    first = float(ts_arr[0])
                    base_ts = first if base_ts is None else min(base_ts, first)
        except Exception as exc:
            if trace_store is not None:
                trace_store.close()
            self.progress.emit(
                "WARNING: asammdf native extraction was unavailable; "
                f"using the analyzer's two-pass fallback ({exc})"
            )
            self._run_can_raw_vectorized(reader, store)
            return

        try:
            self._finish_mdf_bus_arrays(
                reader, store, cfg, stage_start, arrays, lazy, base_ts,
                trace_store, trace_frames, trace_message_names, metadata_keys,
            )
        except BaseException:
            if lazy is not None and store.sample_cache is None:
                lazy.source.close()
            raise

    def _finish_mdf_bus_arrays(
        self, reader, store, cfg, stage_start, arrays, lazy, base_ts,
        trace_store, trace_frames, trace_message_names, metadata_keys,
    ) -> None:
        """The CAN Trace and the signals of _run_mdf_bus_arrays()."""
        import numpy as np

        # Counted as the arrays were: signals with samples.
        signal_count = (
            sum(1 for signal in lazy.signals if signal[6] > 0)
            if lazy is not None else len(arrays)
        )
        extraction_elapsed = time.perf_counter() - stage_start
        self.progress.emit(
            f"asammdf extraction and global array read complete: "
            f"{signal_count:,} signals in "
            f"{extraction_elapsed:.1f} s"
        )

        trace_decoded_frames = 0
        trace_warning = getattr(reader, "raw_trace_error", "")
        if trace_store is not None and trace_warning:
            trace_store.close()
            trace_store = None
            trace_frames = 0
            self.progress.emit(
                f"WARNING: CAN Trace could not be loaded ({trace_warning})"
            )
        if trace_store is not None and trace_frames:
            # The reader appends one channel group at a time, so a file with a
            # group per bus channel arrives channel by channel, not in time
            # order. Sorting here also covers the recovery path below, which
            # rebuilds its trace from this store's row order.
            try:
                trace_store.sort_by_time()
            except Exception as exc:
                self.progress.emit(
                    f"WARNING: CAN Trace left in file order ({exc})"
                )
        if (
            trace_store is not None
            and trace_frames
            and getattr(reader, "dbc_extraction_failed", False)
        ):
            base_ts = self._decode_recovered_mdf_trace(
                trace_store, store, base_ts
            )
            trace_store = None
            trace_frames = 0
        if trace_store is not None and trace_frames:
            # Sealed first: the frame columns are mapped from their files,
            # and the shift and labels below write into them a chunk at a time.
            trace_store.seal()
            timestamps_np = np.frombuffer(trace_store.timestamps, dtype=np.float64)
            trace_base_ts = float(np.min(timestamps_np))
            base_ts = (
                trace_base_ts
                if base_ts is None
                else min(base_ts, trace_base_ts)
            )
            for start in range(0, trace_frames, _GROUP_CHUNK_ROWS):
                timestamps_np[start:start + _GROUP_CHUNK_ROWS] -= base_ts

            # Mark raw rows decoded and attach their message names in one
            # vectorised lookup. This keeps row expansion/search identical to
            # BLF/ASC CAN Trace without walking individual frames in Python.
            if trace_message_names:
                channels_np = np.frombuffer(trace_store.channels, dtype=np.uint8)
                arb_ids_np = np.frombuffer(trace_store.arb_ids, dtype=np.uint32)
                flags_np = np.frombuffer(trace_store.flags, dtype=np.uint8)
                name_ids_np = np.frombuffer(trace_store.name_ids, dtype=np.uint16)

                # Bit 34 carries the bus so a CAN 1 message name is never
                # applied to a LIN 1 frame with the same ID, and vice versa.
                encoded_to_name: dict[int, str] = {}
                for (channel, message_id), message_name in trace_message_names.items():
                    key = as_channel_key(channel)
                    if key is None:
                        channel_byte, is_lin = 255, 0
                    else:
                        bus, number = key
                        channel_byte = int(number) & 0xFF
                        is_lin = 1 if bus is BusType.LIN else 0
                    is_extended = 1 if int(message_id) > 0x7FF else 0
                    encoded = (
                        (is_lin << 41)
                        | (channel_byte << 33)
                        | (is_extended << 32)
                        | (int(message_id) & 0xFFFF_FFFF)
                    )
                    encoded_to_name.setdefault(encoded, message_name)

                if encoded_to_name:
                    encoded_keys = np.asarray(
                        sorted(encoded_to_name), dtype=np.uint64
                    )
                    key_name_ids = np.empty(len(encoded_keys), dtype=np.uint16)
                    for idx, encoded in enumerate(encoded_keys):
                        message_name = encoded_to_name[int(encoded)]
                        name_id = trace_store._name_to_id.get(message_name)
                        if name_id is None:
                            if len(trace_store.name_table) <= 65535:
                                name_id = len(trace_store.name_table)
                                trace_store.name_table.append(message_name)
                                trace_store._name_to_id[message_name] = name_id
                            else:
                                name_id = 0
                        key_name_ids[idx] = name_id

                    # A chunk at a time: no per-frame array of the log's length.
                    for start in range(0, trace_frames, _GROUP_CHUNK_ROWS):
                        end = min(trace_frames, start + _GROUP_CHUNK_ROWS)
                        chunk_flags = flags_np[start:end]
                        frame_keys = (
                            ((chunk_flags.astype(np.uint64) & np.uint64(FLAG_LIN))
                             // np.uint64(FLAG_LIN)) << np.uint64(41)
                        ) | (
                            channels_np[start:end].astype(np.uint64) << np.uint64(33)
                        ) | (
                            (chunk_flags.astype(np.uint64) & np.uint64(1))
                            << np.uint64(32)
                        ) | arb_ids_np[start:end].astype(np.uint64)
                        positions = np.searchsorted(encoded_keys, frame_keys)
                        safe_positions = np.minimum(positions, len(encoded_keys) - 1)
                        decoded_mask = (
                            (positions < len(encoded_keys))
                            & (encoded_keys[safe_positions] == frame_keys)
                        )
                        chunk_flags[decoded_mask] |= np.uint8(4)
                        name_ids_np[start:end][decoded_mask] = key_name_ids[
                            safe_positions[decoded_mask]
                        ]
                        trace_decoded_frames += int(np.count_nonzero(decoded_mask))

            trace_store.seal()
            store.raw_frame_store = trace_store
            _bulk_compute_store_stats(store, trace_store)
            store.decoded_frames = trace_decoded_frames
            store.unmatched_frames = trace_frames - trace_decoded_frames
            if cfg:
                trace_store.decoder = cfg.decoder_for(BusType.CAN, None)
                trace_store.channel_config = cfg
            self.progress.emit(
                f"CAN Trace ready: {trace_frames:,} raw frames "
                "(bulk MDF array extraction)."
            )
        elif trace_store is not None:
            trace_store.close()
            trace_store = None

        if base_ts is None:
            base_ts = 0.0
        store.base_ts = base_ts

        total = signal_count
        import_start = time.perf_counter()
        if lazy is not None:
            self.progress.emit(
                f"Listing {total:,} decoded signals; each is read from the "
                "decoded file when first used..."
            )
            if lazy.signals:
                store.sample_cache = SampleCache(lazy.source, base_ts)
            else:
                lazy.source.close()
            for (group, channel, message_name, message_id, signal_name,
                 unit, sample_count) in lazy.signals:
                store.add_lazy_series(
                    channel=channel,
                    message_name=message_name,
                    message_id=message_id,
                    signal_name=signal_name,
                    unit=unit,
                    group=group,
                    sample_count=sample_count,
                )
        else:
            self.progress.emit(
                f"Bulk importing {total:,} decoded signal arrays into memory..."
            )
        time_axis = _TimeAxis()
        for meta, ts_arr, num_arr, disp_list in arrays:
            channel, message_name, message_id, signal_name, unit = meta
            store.add_series_bulk(
                channel=channel,
                message_name=message_name,
                message_id=message_id,
                signal_name=signal_name,
                unit=unit,
                timestamps=time_axis.normalise(ts_arr, base_ts),
                values=num_arr,
                raw_values=disp_list,
                has_labels=bool(disp_list),
            )

        # add_series_bulk counts one logical insert per signal. When raw MDF
        # frames are present, report actual decoded/unmatched CAN frame counts.
        if trace_frames:
            store.decoded_frames = trace_decoded_frames
            store.unmatched_frames = trace_frames - trace_decoded_frames

        # asammdf yields one group per frame ID; see sort_merged_series().
        store.sort_merged_series()
        import_elapsed = time.perf_counter() - import_start
        self.progress.emit(
            f"Bulk import complete: {total:,} signals | samples: "
            f"{store.total_samples:,} | elapsed: {import_elapsed:.1f} s"
        )

        # Verify the metadata-first hierarchy against the imported arrays.
        if store.is_tree_dirty():
            final_payload = store.build_tree_payload()
            final_keys = {
                (channel, message_name, signal_name)
                for channel, messages in final_payload.items()
                for message_name, signal_names in messages.items()
                for signal_name in signal_names
            }
            # The metadata-first tree is normally identical to the completed
            # store. Avoid queuing a second full Qt tree rebuild in that case.
            # If asammdf advertised an empty/unreadable channel, publish the
            # verified final tree so correctness is unchanged.
            if metadata_keys is None or final_keys != metadata_keys:
                self.tree_update.emit(final_payload)
        self.partial_ready.emit()
        store.normalize_timestamps(already_normalized=True)
        store.diagnostics_text = (
            store.channel_summary_text()
            + f"\n\nSource: {reader.source_description}"
            + f"\n\nDecoded signals: {len(store._series_by_key):,}"
            + "\n\nMF4 decoded directly by asammdf in one pass."
            + (
                f"\nCAN Trace: {store.total_frames:,} raw frames loaded in bulk."
                if store.raw_frame_store is not None
                else "\nCAN Trace was not available from the MDF raw groups."
            )
        )

    # ── Bulk array path (pre-decoded MF4 / MDF / CSV) ────────────────────

    def _run_bulk_array(self, reader, store: SignalStore) -> None:
        import numpy as np
        stage_start = time.perf_counter()
        self.progress.emit("Reading channels (vectorised fast path)...")
        base_ts: float | None = None
        ch_count = 0
        metadata_first = bool(getattr(reader, "metadata_first_arrays", False))

        def on_metadata_ready(metadata_rows):
            messages = {}
            for group_name, channel_name, _unit in metadata_rows:
                signals = messages.setdefault(group_name, [])
                if channel_name not in signals:
                    signals.append(channel_name)
            self.tree_update.emit({None: messages})
            self.progress.emit(
                f"MDF signal list ready: {len(metadata_rows):,} channels in "
                f"{time.perf_counter() - stage_start:.1f} s"
            )

        open_sample_source = getattr(reader, "open_sample_source", None)
        if open_sample_source is not None:
            # MDF: list the channels now and leave every sample in the file.
            # A signal's group is read the first time the signal is used, and
            # the store keeps a bounded number of groups in memory, so the
            # memory a measurement needs no longer follows its size.
            source = open_sample_source()
            try:
                on_metadata_ready(source.metadata_rows())
                base_ts = source.first_timestamp()
                store.base_ts = 0.0 if base_ts is None else base_ts
                store.sample_cache = SampleCache(source, store.base_ts)
            except BaseException:
                source.close()
                raise
            for group_idx, grp_name, channels, cycles in source.groups:
                for _ch_idx, ch_name, unit in channels:
                    ch_count += 1
                    store.add_lazy_series(
                        channel=None, message_name=grp_name, message_id=0,
                        signal_name=ch_name, unit=unit,
                        group=group_idx, sample_count=cycles,
                    )
            array_iter = ()
        elif metadata_first:
            array_iter = reader.iter_channel_arrays(
                metadata_ready=on_metadata_ready,
                batch_all_groups=True,
            )
        else:
            array_iter = reader.iter_channel_arrays()

        time_axis = _TimeAxis()
        for (grp_name, ch_name, unit), ts_arr, num_arr, disp_list in \
                array_iter:
            ch_count += 1
            n = len(ts_arr)
            if n == 0:
                continue
            if base_ts is None:
                base_ts = float(ts_arr[0])
                store.base_ts = base_ts
            ts_norm    = time_axis.normalise(ts_arr, base_ts)
            has_labels = len(disp_list) > 0
            store.add_series_bulk(
                channel=None, message_name=grp_name, message_id=0,
                signal_name=ch_name, unit=unit,
                timestamps=ts_norm, values=num_arr, raw_values=disp_list,
                has_labels=has_labels,
            )
            # Gate tree rebuild on an interval + dirty flag — avoids a full
            # sort of all signals for every channel (Bottleneck 3).
            if not metadata_first and ch_count % _BULK_PLOT_INTERVAL == 0:
                if store.is_tree_dirty():
                    self.tree_update.emit(store.build_tree_payload())
                self.partial_ready.emit()
            if (
                not metadata_first
                and (ch_count == 1 or ch_count % _BULK_PROGRESS_INTERVAL == 0)
            ):
                self.progress.emit(
                    f"Loaded {ch_count:,} channels | samples: {store.total_samples:,}"
                )

        store.sort_merged_series()
        self.tree_update.emit(store.build_tree_payload())
        self.partial_ready.emit()
        if metadata_first:
            self.progress.emit(
                f"Imported {ch_count:,} channels | samples: "
                f"{store.total_samples:,}"
            )
        store.normalize_timestamps(already_normalized=True)
        store.raw_trace_unavailable_reason = getattr(
            reader,
            "raw_trace_unavailable_reason",
            "This pre-decoded measurement does not contain raw CAN frame records.",
        )
        store.diagnostics_text = (
            store.channel_summary_text()
            + f"\n\nSource: {reader.source_description}"
            + f"\n\nChannels loaded: {ch_count:,}"
            + "\n\nNo raw frames — pre-decoded format."
        )

    # ── Fallback sample-by-sample ─────────────────────────────────────────

    def _run_sample_loop(self, reader, store: SignalStore) -> None:
        self.progress.emit("Reading pre-decoded signals...")
        base_ts: float | None = None
        base_ts_set = False
        index = 0
        for sample in reader:
            index += 1
            if not base_ts_set:
                base_ts = sample.timestamp
                store.base_ts = base_ts
                base_ts_set = True
            sample.timestamp -= base_ts
            store.add_samples_direct([sample])
            if index % _CAN_TREE_INTERVAL == 0:
                self.tree_update.emit(store.build_tree_payload())
            if index % _CAN_PLOT_INTERVAL == 0:
                self.partial_ready.emit()
            if index == 1 or index % _CAN_PROGRESS_INTERVAL == 0:
                self.progress.emit(
                    f"Processed {index:,} records | samples: {store.total_samples:,}"
                )
        store.normalize_timestamps(already_normalized=True)
        store.diagnostics_text = (
            store.channel_summary_text()
            + f"\n\nSource: {reader.source_description}"
            + "\n\nNo raw frames — pre-decoded format."
        )
