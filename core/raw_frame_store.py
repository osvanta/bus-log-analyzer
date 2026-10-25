# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
On-disk raw bus frame store (CAN and LIN).

Design goals
------------
* No per-frame Python objects — all per-frame metadata stored in compact
  ``array.array`` buffers (18 bytes/frame total in RAM).
* Raw data bytes stored in a temporary file on disk (64 bytes/frame).
  The file is mmap-based for O(1) random access by frame index.
* No cap on the number of frames — supports 200 MB+ BLF files (3M+ frames)
  with ~54 MB RAM + ~192 MB disk.
* The temp file is opened delete-on-close, so Windows removes it when its last
  handle closes: on ``close()``, on garbage collection, and when the process
  ends, however it ends. Nothing has to run at exit.

Memory layout (in-process)
--------------------------
Per frame, 18 bytes total:
    timestamps  float64  8 B   normalised to t=0
    channels    uint8    1 B   physical channel (1-indexed; 255 = unknown)
    arb_ids     uint32   4 B   CAN arbitration ID / LIN frame ID
    dlcs        uint8    1 B   data length code
    directions  uint8    1 B   0=Rx, 1=Tx, 2=Unknown
    flags       uint8    1 B   bit0=is_extended, bit1=is_fd, bit2=decoded,
                               bit3=is_lin, bit4=error frame, bit5=remote
                               frame, bit6=bit rate switch (CAN FD),
                               bit7=payload kept long
    name_ids    uint16   2 B   index into name_table

name_table: list[str]  — unique message names (~100–500 entries, negligible)
bus_status: list[BusStatus] — what a logger recorded about the bus rather than
            a frame: its own statistics, controller state, error counters,
            overload frames. Rare (none in most files), so a plain list.

Mixed CAN and LIN
-----------------
LIN frames share this store rather than getting one of their own. They fit the
existing layout without widening it: a 6-bit LIN frame ID fits ``arb_ids``, a
LIN payload is at most 8 bytes, and the bus is recorded in a single previously
unused flag bit. Channel numbering is per-bus, so ``CAN 1`` and ``LIN 1`` are
distinct rows distinguished by that bit — which is why the channel filter takes
a ``(bus, number)`` key rather than a bare integer.

Disk layout
-----------
8 bytes of payload per frame, in one file: a classic CAN or LIN frame, whose
bytes past the eighth are all zero, keeps its first 8 bytes there. Any other
frame (CAN FD) is stored, up to its last non-zero 8-byte word, in a second
file, and its 8 bytes say where; ``FLAG_LONG`` marks it. :meth:`RawFrameStore.rows`
puts the 64-byte rows back together. A fixed 64-byte record per frame took
eight times the disk for classic CAN.

Filter interface
----------------
``build_match_mask(needle, channel_filter)`` returns a ``numpy`` bool array
using vectorised ops — no Python loop, no disk access.
For very large stores (> 500 k frames) data-hex search is disabled; all other
search fields (ID, name, channel, direction) remain vectorised.
"""
from __future__ import annotations

import array as _array
import mmap
import os
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import numpy as np

from core.bus_types import BusChannel, BusType, coerce_channel_key


# ── Constants ─────────────────────────────────────────────────────────────
_DATA_BYTES        = 64          # bytes of raw data per frame (CAN FD max)
_SHORT_BYTES       = 8           # bytes kept per frame; see _Payload
_WORDS             = _DATA_BYTES // 8
_DIR_RX,_DIR_TX,_DIR_UNK = 0, 1, 2
_LARGE_STORE_LIMIT = 500_000     # frames; disable data-hex search above this
_SEARCH_CHUNK      = 1 << 22     # frames a search looks at per step

_DIR_STRINGS = ['Rx', 'Tx', 'Unknown']

#: The columns append_raw_batch() receives, in its argument order, and their
#: types; name_ids (uint16) follows them.
_COLUMN_DTYPES = (
    ('timestamps', np.float64), ('channels', np.uint8), ('arb_ids', np.uint32),
    ('dlcs', np.uint8), ('directions', np.uint8), ('flags', np.uint8),
)

_COLUMN_TYPECODES = (
    ('timestamps', 'd'), ('channels', 'B'), ('arb_ids', 'I'), ('dlcs', 'B'),
    ('directions', 'B'), ('flags', 'B'), ('name_ids', 'H'),
)

#: Rows sort_by_time() merges per block, and the most time-ordered runs it
#: merges; a column in more runs than that is sorted in one piece.
_MERGE_BLOCK_ROWS = 1 << 20
_MAX_MERGE_RUNS = 4096

_FILE_PREFIX = 'osvanta_'
_FILE_SUFFIX = '.rawdata'

# Flag bits packed into the uint8 `flags` column.
FLAG_EXTENDED = 1
FLAG_FD       = 2
FLAG_DECODED  = 4
FLAG_LIN      = 8      # bus discriminator; absent means CAN
# Frames that are not messages: kept for what they say about the bus, never decoded.
FLAG_ERROR    = 0x10   # an error frame, as the logger recorded it
FLAG_REMOTE   = 0x20   # a remote (RTR) frame: no data, DLC is the length asked for
FLAG_BRS      = 0x40   # CAN FD frame sent with bit rate switch
FLAG_LONG     = 0x80   # payload kept whole in the overflow file; see _Payload


def _create_data_file():
    """Open a new temp data file that the operating system deletes.

    Deleting it in close() was not enough: the window still holds the last
    measurement's store when the application exits, Python does not collect
    it, and every session left that trace behind in the temp folder. Opened
    with O_TEMPORARY, Windows deletes the file when its last handle closes,
    including when the process crashes or is killed. Elsewhere an open file
    outlives its name, so the name goes at once.

    Returns the buffered file and its path.
    """
    fd, path = tempfile.mkstemp(prefix=_FILE_PREFIX, suffix=_FILE_SUFFIX)
    if hasattr(os, 'O_TEMPORARY'):
        os.close(fd)
        fd = os.open(path, os.O_RDWR | os.O_BINARY | os.O_NOINHERIT | os.O_TEMPORARY)
    else:
        os.unlink(path)
    # A 1 MB write buffer: seal() flushes before it maps the file. Unbuffered,
    # every 64-byte frame was its own write() call, several seconds for a
    # large BLF.
    return os.fdopen(fd, 'w+b', buffering=1 << 20), path


def remove_leftover_files(directory: str | os.PathLike | None = None) -> tuple[int, int]:
    """Delete the data files earlier versions left in the temp folder.

    Returns how many files were deleted and the bytes freed. Another running
    copy of the application is not disturbed: Windows refuses to delete a file
    an earlier version still has open, and a current version's file stays
    usable without its name.
    """
    removed = freed = 0
    folder = Path(directory) if directory is not None else Path(tempfile.gettempdir())
    for path in folder.glob(f'{_FILE_PREFIX}*{_FILE_SUFFIX}'):
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        removed += 1
        freed += size
    return removed, freed


class _Payload:
    """
    Each frame's payload: its 8 bytes, or where its longer payload is.

    A frame whose bytes past the eighth are all zero (classic CAN, LIN) keeps
    its first 8 bytes in the main file. Any other frame (CAN FD) has its
    payload, up to its last non-zero 8-byte word, appended to the overflow
    file, and its 8 bytes hold that record's offset and length (``offset <<
    8 | length``). Written a batch at a time (:meth:`write`), which returns
    the overflowed rows so the caller can set ``FLAG_LONG`` on them; read
    back as 64-byte rows (:meth:`rows`) once :meth:`map` has mapped both
    files.
    """

    def __init__(self) -> None:
        self.short_file, self.short_path = _create_data_file()
        self.long_file, self.long_path = _create_data_file()
        self.count = 0
        self.long_count = 0
        self.long_bytes = 0
        self._short_map: mmap.mmap | None = None
        self._long_map: mmap.mmap | None = None

    def write(self, rows: np.ndarray) -> np.ndarray:
        """Append *rows* ((k, width <= 64) uint8); return the overflowed rows' mask."""
        count, width = rows.shape
        if width == _DATA_BYTES and rows.flags.c_contiguous:
            # Eight 8-byte words a row: any non-zero word past the first.
            words = rows.view('<u8')
            long = (words[:, 1:] != 0).any(axis=1)
            short = words[:, 0].copy()
        else:
            long = (
                rows[:, _SHORT_BYTES:].any(axis=1) if width > _SHORT_BYTES
                else np.zeros(count, dtype=bool)
            )
            first = np.zeros((count, _SHORT_BYTES), dtype=np.uint8)
            first[:, :min(width, _SHORT_BYTES)] = rows[:, :_SHORT_BYTES]
            short = first.view('<u8').reshape(count)
        long_rows = int(np.count_nonzero(long))
        if long_rows:
            overflow = rows[long]
            if width != _DATA_BYTES:
                padded = np.zeros((long_rows, _DATA_BYTES), dtype=np.uint8)
                padded[:, :width] = overflow
                overflow = padded
            # Up to and including the last non-zero 8-byte word of each row:
            # whole words keep this a few operations per batch.
            words = overflow.view('<u8')
            nwords = _WORDS - np.argmax((words != 0)[:, ::-1], axis=1)
            kept = np.arange(_WORDS) < nwords[:, None]
            self.long_file.write(memoryview(np.ascontiguousarray(words[kept])))
            lengths = (nwords * 8).astype(np.uint64)
            offsets = self.long_bytes + np.concatenate(
                ([0], np.cumsum(lengths)[:-1])
            ).astype(np.uint64)
            short[long] = (offsets << np.uint64(8)) | lengths
            self.long_bytes += int(lengths.sum())
            self.long_count += long_rows
        self.short_file.write(memoryview(np.ascontiguousarray(short)))
        self.count += count
        return long

    @property
    def mapped(self) -> bool:
        return self._short_map is not None

    def map(self) -> None:
        """Map both files for reading; writing after this is not supported."""
        if self._short_map is not None or not self.count:
            return
        self.short_file.flush()
        self.long_file.flush()
        self._short_map = mmap.mmap(
            self.short_file.fileno(), self.count * _SHORT_BYTES, access=mmap.ACCESS_READ
        )
        if self.long_bytes:
            self._long_map = mmap.mmap(
                self.long_file.fileno(), self.long_bytes, access=mmap.ACCESS_READ
            )

    def rows(self, indices, flags: np.ndarray) -> np.ndarray:
        """The 64-byte rows of *indices* (array or slice); *flags* are theirs."""
        short = np.frombuffer(
            self._short_map, dtype='<u8', count=self.count
        )[indices]
        out = np.zeros((len(short), _DATA_BYTES), dtype=np.uint8)
        out[:, :_SHORT_BYTES] = short.view(np.uint8).reshape(-1, _SHORT_BYTES)
        long = (np.asarray(flags) & np.uint8(FLAG_LONG)) != 0
        if long.any():
            slots = short[long]
            offsets = (slots >> np.uint64(8)).astype(np.int64)
            lengths = (slots & np.uint64(0xFF)).astype(np.int64)
            # Every kept byte's position in the overflow file, in row order.
            starts = np.cumsum(lengths) - lengths
            positions = (
                np.repeat(offsets - starts, lengths) + np.arange(int(lengths.sum()))
            )
            data = np.frombuffer(self._long_map, dtype=np.uint8, count=self.long_bytes)
            overflow = np.zeros((len(slots), _DATA_BYTES), dtype=np.uint8)
            overflow[np.arange(_DATA_BYTES) < lengths[:, None]] = data[positions]
            out[long] = overflow
        return out

    def close(self) -> None:
        """Close the maps and files; the operating system deletes the files."""
        for name in ('_short_map', '_long_map'):
            view = getattr(self, name)
            setattr(self, name, None)
            if view is not None:
                try:
                    view.close()
                except (BufferError, ValueError):
                    pass    # a live NumPy view: it goes when the view does
        for handle in (self.short_file, self.long_file):
            try:
                handle.close()
            except OSError:
                pass


class BusStatus(NamedTuple):
    """Something a logger recorded about a bus, at one moment.

    *kind* and the *values* it carries:

    ``'statistics'``    the logger's own counts: bus_load (%), std_data,
                        ext_data, std_remote, ext_remote, error_frames,
                        overload_frames (any subset)
    ``'controller'``    controller state: state (text), tec, rec (any subset)
    ``'overload'``      one overload frame
    """
    time_s:  float              # on the frames' time base
    channel: int                # 255 = unknown
    kind:    str
    values:  dict


class RawFrameRecord(NamedTuple):
    """Lightweight record returned by get_window() — no heap allocation needed."""
    time_s:        float
    channel:       int | None     # None when channel field was 255
    arbitration_id: int
    dlc:           int
    direction:     str
    is_extended:   bool
    is_fd:         bool
    decoded:       bool
    data:          bytes          # up to _DATA_BYTES bytes
    frame_name:    str            # from name_table
    # Defaulted so the field can be appended without breaking positional
    # construction in existing callers and tests.
    bus:           BusType = BusType.CAN

    @property
    def channel_key(self) -> BusChannel | None:
        """The frame's bus-tagged channel identity, or None if unknown."""
        return None if self.channel is None else (self.bus, self.channel)


class RawFrameStore:
    """
    Stores every raw CAN frame with near-zero RAM overhead.

    Call ``append()`` during BLF/ASC decode, then ``seal()`` once to open the
    mmap.  After sealing, call ``get_window(indices)`` to retrieve frames by
    index for display.  Call ``close()`` to release the temp file.
    """

    def __init__(self) -> None:
        # ── In-memory compact arrays ──────────────────────────────────────
        self.timestamps:  _array.array = _array.array('d')   # float64
        self.channels:    _array.array = _array.array('B')   # uint8
        self.arb_ids:     _array.array = _array.array('I')   # uint32
        self.dlcs:        _array.array = _array.array('B')   # uint8
        self.directions:  _array.array = _array.array('B')   # uint8
        self.flags:       _array.array = _array.array('B')   # uint8
        self.name_ids:    _array.array = _array.array('H')   # uint16
        self.bus_status:  list[BusStatus] = []

        # ── Name table ────────────────────────────────────────────────────
        self.name_table:  list[str] = ['']    # index 0 = no name
        self._name_to_id: dict[str, int] = {'': 0}

        # ── Decoder reference (set after decode for on-demand signal fetch)
        self.decoder = None   # DBCDecoder | None

        # ── Payload files (see _Payload) ──────────────────────────────────
        self._sealed = False

        # NumPy batches from append_raw_batch(), held until seal(). Growing an
        # array.array one batch at a time reallocates and copies the whole
        # column every few batches: seconds for a 13-million-frame BLF.
        self._pending: list[list] = []
        self._pending_rows = 0

        # Batches from append_raw_batch() go straight to one temporary file
        # per column (core.sample_file.AppendColumn), and seal() maps every
        # column, so the frame index costs no memory however many frames.
        self._file_columns: dict[str, object] | None = None

        # Create the payload files immediately
        self._payload: _Payload | None = _Payload()

    # ── Write phase (during decode) ───────────────────────────────────────

    def append(self, timestamp: float, channel: int | None,
               arb_id: int, dlc: int, direction: str,
               is_extended: bool, is_fd: bool,
               data: bytes, frame_name: str, decoded: bool,
               bus: BusType = BusType.CAN) -> None:
        """Append one frame.  O(1) amortised — no per-frame Python objects."""
        self._materialize()
        self.timestamps.append(timestamp)
        self.channels.append(channel if channel is not None else 255)
        self.arb_ids.append(arb_id & 0xFFFF_FFFF)
        self.dlcs.append(min(dlc, 255))
        self.directions.append(
            _DIR_RX if direction == 'Rx' else
            _DIR_TX if direction == 'Tx' else _DIR_UNK
        )
        flags = (
            (FLAG_EXTENDED if is_extended else 0) |
            (FLAG_FD       if is_fd       else 0) |
            (FLAG_DECODED  if decoded     else 0) |
            (FLAG_LIN      if bus is BusType.LIN else 0)
        )
        if self._write_payload(bytes(data)[:_DATA_BYTES]):
            flags |= FLAG_LONG
        self.flags.append(flags)

        # Name table lookup / insert
        nid = self._name_to_id.get(frame_name)
        if nid is None:
            nid = len(self.name_table)
            if nid > 65535:
                nid = 0   # overflow guard
            else:
                self.name_table.append(frame_name)
                self._name_to_id[frame_name] = nid
        self.name_ids.append(nid)

    def append_raw(self, timestamp: float, channel_byte: int,
                   arb_id: int, dlc: int, direction_int: int,
                   is_extended: bool, is_fd: bool, data) -> None:
        """
        Optimised append for the 2-pass load path.

        Differences from :meth:`append` that eliminate hot-loop overhead:

        * Accepts ``channel_byte`` (uint8, 255=None) — skips None→255 branch.
        * Accepts ``direction_int`` (0/1/2) — skips string comparison.
        * Always ``frame_name=''``, ``decoded=False`` — skips name-table
          lookup (always ``nid=0``) and the decoded-bit shift (Bottleneck 4).
        """
        self._materialize()
        self.timestamps.append(timestamp)
        self.channels.append(channel_byte)
        self.arb_ids.append(arb_id & 0xFFFF_FFFF)
        self.dlcs.append(min(dlc, 255))
        self.directions.append(direction_int)
        flags = (FLAG_EXTENDED if is_extended else 0) | (FLAG_FD if is_fd else 0)
        if self._write_payload(bytes(data[:_DATA_BYTES])):
            flags |= FLAG_LONG
        self.flags.append(flags)
        self.name_ids.append(0)   # always '' for 2-pass path

    def _write_payload(self, data: bytes) -> bool:
        """Store one frame's payload; return whether it overflowed (FLAG_LONG)."""
        row = np.zeros((1, _DATA_BYTES), dtype=np.uint8)
        if data:
            row[0, :len(data)] = np.frombuffer(data, dtype=np.uint8)
        return bool(self._payload.write(row)[0])

    def append_raw_batch(
        self,
        timestamps: list[float],
        channels: list[int],
        arb_ids: list[int],
        dlcs: list[int],
        directions: list[int],
        flags: list[int],
        data_block,
    ) -> None:
        """Append a packed batch from the BLF/ASC hot path.

        Converting Python lists to ``array.array`` is performed by C loops and
        the complete payload block is written with one buffered file call.
        This replaces seven ``array.append`` calls plus one ``write`` call per
        CAN frame. Columns may also be NumPy arrays (the BLF reader's): those
        are held, not copied, until :meth:`seal` joins them into the columns
        in one copy each, so the caller must not modify them afterwards.
        """
        count = len(timestamps)
        if count == 0:
            return
        if not (
            len(channels) == len(arb_ids) == len(dlcs)
            == len(directions) == len(flags) == count
        ):
            raise ValueError("Raw frame batch columns have different lengths")

        # The payload first: rows that overflow get FLAG_LONG in their flags.
        long = self._payload.write(np.frombuffer(
            memoryview(data_block)[:count * _DATA_BYTES], dtype=np.uint8,
        ).reshape(count, _DATA_BYTES))
        if long.any():
            marked = np.asarray(flags, dtype=np.uint8) | (
                long.astype(np.uint8) * np.uint8(FLAG_LONG)
            )
            flags = marked if isinstance(flags, np.ndarray) else marked.tolist()

        columns = [timestamps, channels, arb_ids, dlcs, directions, flags]
        if self._file_columns is not None or (
            not len(self.timestamps) and not self._pending
        ):
            self._write_columns(count, columns)
        elif all(isinstance(values, np.ndarray) for values in columns):
            self._pending.append(columns)
            self._pending_rows += count
        else:
            self._materialize()

            def extend_column(target, values) -> None:
                if isinstance(values, (bytes, bytearray, memoryview)):
                    target.frombytes(values)
                else:
                    target.fromlist(list(values))

            extend_column(self.timestamps, timestamps)
            extend_column(self.channels, channels)
            extend_column(self.arb_ids, arb_ids)
            extend_column(self.dlcs, dlcs)
            extend_column(self.directions, directions)
            extend_column(self.flags, flags)
            self.name_ids.frombytes(bytes(count * self.name_ids.itemsize))

    def _write_columns(self, count: int, columns) -> None:
        """Append one batch to the column files, creating them on first use."""
        from core.sample_file import AppendColumn

        if self._file_columns is None:
            self._file_columns = {
                name: AppendColumn(dtype)
                for name, dtype in (*_COLUMN_DTYPES, ('name_ids', np.uint16))
            }
        for (name, dtype), values in zip(_COLUMN_DTYPES, columns):
            if isinstance(values, (bytes, bytearray, memoryview)):
                values = np.frombuffer(values, dtype=dtype)
            self._file_columns[name].extend(values)
        self._file_columns['name_ids'].extend(np.zeros(count, dtype=np.uint16))

    def _map_columns(self) -> None:
        """Put every column in its file and keep a mapped view of it."""
        from core.sample_file import AppendColumn

        if self._file_columns is None:
            if not len(self.timestamps):
                return
            # Built in memory (one frame at a time, or MDF bus logging):
            # moved to the files now, so the open measurement holds no index.
            self._file_columns = {}
            for name, dtype in (*_COLUMN_DTYPES, ('name_ids', np.uint16)):
                column = AppendColumn(dtype)
                column.extend(np.frombuffer(getattr(self, name), dtype=dtype))
                self._file_columns[name] = column
        for name, column in self._file_columns.items():
            setattr(self, name, column.mapped())
            column.close()
        self._file_columns = None

    def _materialize(self) -> None:
        """Join the NumPy batches held by append_raw_batch() into the columns."""
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        rows, self._pending_rows = self._pending_rows, 0
        targets = (self.timestamps, self.channels, self.arb_ids, self.dlcs,
                   self.directions, self.flags)
        for index, target in enumerate(targets):
            parts = [batch[index] for batch in pending]
            for batch in pending:
                batch[index] = None     # each batch's part goes with its column
            joined = np.concatenate(parts).astype(target.typecode, copy=False)
            del parts
            # frombytes() takes byte-format buffers only.
            target.frombytes(joined.view(np.uint8))
            del joined
        self.name_ids.frombytes(bytes(rows * self.name_ids.itemsize))

    def append_numpy_batch(
        self,
        timestamps,
        channels,
        arb_ids,
        dlcs,
        directions,
        flags,
        data_rows,
    ) -> None:
        """Append column arrays without converting millions of rows to lists.

        This is the MDF bus-logging counterpart of :meth:`append_raw_batch`.
        asammdf exposes a complete ``CAN_DataFrame`` channel group as NumPy
        arrays; copying those arrays directly keeps CAN Trace construction on
        the bulk path and avoids creating a Python object per CAN frame.

        ``data_rows`` may contain 0..64 payload columns; see _Payload for how
        they are kept. They are written in bounded chunks.
        """
        self._materialize()
        count = len(timestamps)
        if count == 0:
            return
        if not (
            len(channels) == len(arb_ids) == len(dlcs)
            == len(directions) == len(flags) == count
        ):
            raise ValueError("Raw frame NumPy columns have different lengths")

        payload = np.asarray(data_rows, dtype=np.uint8)
        if payload.ndim == 1:
            if len(payload) % count:
                raise ValueError("Raw frame payload array has an invalid shape")
            payload = payload.reshape(count, -1)
        if payload.ndim != 2 or payload.shape[0] != count:
            raise ValueError("Raw frame payload array has an invalid shape")

        # The payload first: rows that overflow get FLAG_LONG in their flags.
        width = min(int(payload.shape[1]), _DATA_BYTES)
        long = np.zeros(count, dtype=bool)
        chunk_size = 16_384
        for start in range(0, count, chunk_size):
            end = min(start + chunk_size, count)
            long[start:end] = self._payload.write(payload[start:end, :width])

        columns = []
        for (_name, dtype), values in zip(
            _COLUMN_DTYPES,
            (timestamps, channels, arb_ids, dlcs, directions, flags),
        ):
            column = np.ascontiguousarray(values, dtype=dtype).reshape(-1)
            if len(column) != count:
                raise ValueError("Raw frame NumPy column has an invalid shape")
            columns.append(column)
        if long.any():
            columns[5] = columns[5] | (long.astype(np.uint8) * np.uint8(FLAG_LONG))
        if self._file_columns is not None or not len(self.timestamps):
            # Straight to the column files, as append_raw_batch() does.
            self._write_columns(count, columns)
        else:
            for (name, _dtype), column in zip(_COLUMN_DTYPES, columns):
                getattr(self, name).frombytes(column.tobytes())
            self.name_ids.frombytes(bytes(count * self.name_ids.itemsize))

    def sort_by_time(self) -> bool:
        """
        Put the rows in timestamp order.  Call before :meth:`seal`.

        The MDF bus-logging path appends one channel group at a time, so a
        file with a group per CAN channel would list every CAN 1 frame before
        the first CAN 2 frame -- and the trace dialog's jump-to-time is a
        binary search that assumes time order.  BLF and ASC are appended in
        file order and do not call this.

        The sort is stable, so rows with equal timestamps keep their order.
        An already-ordered store returns ``False`` without touching anything.
        Otherwise the payload records are copied to a new temp file in bounded
        chunks, and only once that has succeeded are the columns swapped in:
        a failure part-way leaves the store exactly as it was.
        """
        if self._sealed:
            raise RuntimeError("sort_by_time() must be called before seal()")
        if self._file_columns is not None:
            return self._merge_by_time()
        self._materialize()
        count = len(self.timestamps)
        timestamps = np.frombuffer(self.timestamps, dtype=np.float64)
        if count < 2 or not (timestamps[1:] < timestamps[:-1]).any():
            return False
        order = np.argsort(timestamps, kind='stable')
        del timestamps          # a live buffer export would pin the column

        payload = _Payload()
        try:
            self._payload.map()
            flags = np.frombuffer(self.flags, dtype=np.uint8)
            chunk_size = 65_536
            for start in range(0, count, chunk_size):
                part = order[start:start + chunk_size]
                payload.write(self._payload.rows(part, flags[part]))
            del flags           # nor may a view of the flags outlive this
            columns = {}
            for name in ('timestamps', 'channels', 'arb_ids', 'dlcs',
                         'directions', 'flags', 'name_ids'):
                column = getattr(self, name)
                reordered = _array.array(column.typecode)
                reordered.frombytes(
                    np.frombuffer(column, dtype=column.typecode)[order].tobytes()
                )
                columns[name] = reordered
        except BaseException:
            payload.close()         # which deletes its files
            raise

        for name, column in columns.items():
            setattr(self, name, column)
        self._payload.close()       # which deletes the unsorted copy
        self._payload = payload
        return True

    def _merge_by_time(self) -> bool:
        """
        :meth:`sort_by_time` for columns in files, in bounded memory.

        The rows arrive as runs already in time order (one per MDF channel
        group, typically). The runs are merged a block at a time: each block
        takes every remaining row up to a time stamp, from every run, and puts
        them in (time, row) order, which is what a stable sort of the whole
        column gives. A column with too many runs to merge is sorted whole.
        """
        from core.sample_file import AppendColumn

        mapped = {name: column.mapped() for name, column in self._file_columns.items()}
        timestamps = mapped['timestamps']
        count = len(timestamps)
        starts = [0]
        for start in range(0, max(count - 1, 0), _MERGE_BLOCK_ROWS):
            stop = min(count, start + _MERGE_BLOCK_ROWS + 1)
            chunk = timestamps[start:stop]
            starts.extend((np.flatnonzero(chunk[1:] < chunk[:-1]) + start + 1).tolist())
        if len(starts) == 1:
            return False
        ends = starts[1:] + [count]
        if len(starts) > _MAX_MERGE_RUNS:
            # Too fragmented to merge: one sort, the order in memory.
            blocks = [np.argsort(timestamps, kind='stable')]
        else:
            blocks = self._merge_blocks(timestamps, starts, ends)

        payload = _Payload()
        sorted_columns = {
            name: AppendColumn(column.dtype) for name, column in mapped.items()
        }
        try:
            self._payload.map()
            flags = mapped['flags']
            for block in blocks:
                for start in range(0, len(block), _MERGE_BLOCK_ROWS):
                    part = block[start:start + _MERGE_BLOCK_ROWS]
                    for name, column in mapped.items():
                        sorted_columns[name].extend(column[part])
                    payload.write(self._payload.rows(part, flags[part]))
        except BaseException:
            payload.close()         # which deletes its files
            for column in sorted_columns.values():
                column.close()
            raise

        del mapped, timestamps, flags
        for column in self._file_columns.values():
            column.close()          # its file goes once nothing maps it
        self._file_columns = sorted_columns
        self._payload.close()       # which deletes the unsorted copy
        self._payload = payload
        return True

    @staticmethod
    def _merge_blocks(timestamps, starts, ends):
        """Yield the merged row order of the time-ordered runs, block by block."""
        heads = list(starts)
        while True:
            active = [r for r in range(len(heads)) if heads[r] < ends[r]]
            if not active:
                return
            step = max(1, _MERGE_BLOCK_ROWS // len(active))
            # No run gives more than *step* rows below this time, unless they
            # tie with it; every row at this time goes into this block.
            limit = min(
                timestamps[min(heads[r] + step, ends[r]) - 1] for r in active
            )
            parts = []
            for r in active:
                run = timestamps[heads[r]:ends[r]]
                taken = int(np.searchsorted(run, limit, side='right'))
                if taken:
                    parts.append(np.arange(heads[r], heads[r] + taken, dtype=np.int64))
                    heads[r] += taken
            block = np.concatenate(parts)
            yield block[np.argsort(timestamps[block], kind='stable')]

    def seal(self) -> None:
        """
        Called once after all frames have been appended.
        Flushes the data file and opens the mmap for random reads.
        Safe to call multiple times.
        """
        if self._sealed:
            return
        self._materialize()
        self._sealed = True
        self._map_columns()
        n = len(self.timestamps)
        if n == 0:
            return
        try:
            self._payload.map()
        except Exception:
            pass    # payload_mapped stays False; the decode falls back

    def __len__(self) -> int:
        written = 0 if self._file_columns is None else self._file_columns['timestamps'].count
        return len(self.timestamps) + self._pending_rows + written

    def close(self) -> None:
        """Release mmap and delete temp file.

        Closing the last handle deletes the file. A map that cannot close yet,
        because a NumPy view of it is still alive, deletes it once the view
        goes.
        """
        if self._payload is not None:
            self._payload.close()
            self._payload = None
        # The mapped columns hold their files open; empty ones let them go.
        for name, typecode in _COLUMN_TYPECODES:
            if not isinstance(getattr(self, name, None), _array.array):
                setattr(self, name, _array.array(typecode))
        if self._file_columns is not None:
            for column in self._file_columns.values():
                column.close()
            self._file_columns = None

    def __del__(self) -> None:
        self.close()

    # ── Random-access read (after seal) ──────────────────────────────────

    def get_window(self, indices) -> list[RawFrameRecord]:
        """
        Return RawFrameRecord objects for the given frame indices.
        indices may be a list, range, or array.array of int frame indices.
        Reads data bytes from the mmap'd temp file.
        """
        result: list[RawFrameRecord] = []
        n = len(self.timestamps)
        indices = [int(idx) for idx in indices if 0 <= idx < n]
        rows = (
            self.rows(np.asarray(indices, dtype=np.int64))
            if indices and self.payload_mapped else None
        )
        for position, idx in enumerate(indices):

            # Mapped metadata, as plain Python numbers
            ts  = float(self.timestamps[idx])
            raw_ch = int(self.channels[idx])
            ch  = raw_ch if raw_ch != 255 else None
            aid = int(self.arb_ids[idx])
            dlc = int(self.dlcs[idx])
            fl  = int(self.flags[idx])
            nid = int(self.name_ids[idx])

            is_extended = bool(fl & FLAG_EXTENDED)
            is_fd       = bool(fl & FLAG_FD)
            decoded     = bool(fl & FLAG_DECODED)
            bus         = BusType.LIN if fl & FLAG_LIN else BusType.CAN
            direction   = _DIR_STRINGS[min(int(self.directions[idx]), 2)]
            name        = self.name_table[nid] if nid < len(self.name_table) else ''

            # Disk data
            data = (
                rows[position, :min(dlc, _DATA_BYTES)].tobytes()
                if rows is not None else b''
            )

            result.append(RawFrameRecord(
                time_s=ts, channel=ch, arbitration_id=aid,
                dlc=dlc, direction=direction,
                is_extended=is_extended, is_fd=is_fd,
                decoded=decoded, data=data, frame_name=name,
                bus=bus,
            ))
        return result

    def _read_data(self, idx: int, length: int) -> bytes:
        """Read up to `length` raw data bytes for frame `idx`."""
        if not self.payload_mapped or not 0 <= idx < len(self.timestamps):
            return b''
        return self.rows(np.asarray([idx], dtype=np.int64))[0, :length].tobytes()

    @property
    def _data_path(self) -> str | None:
        """The payload's main file, while the store is open."""
        return None if self._payload is None else self._payload.short_path

    @property
    def payload_mapped(self) -> bool:
        return self._payload is not None and self._payload.mapped

    def rows(self, indices) -> np.ndarray:
        """
        The 64-byte payload rows of *indices* (an index array or a slice),
        zero past each frame's data, as one ``(k, 64)`` uint8 array.
        """
        flags = np.frombuffer(self.flags, dtype=np.uint8)[indices]
        return self._payload.rows(indices, flags)

    def channel_keys(self) -> list[BusChannel]:
        """
        Return the distinct bus-tagged channels present, in stable order.

        Used to populate the trace dialog's channel filter. The bus comes from
        the flag bit, so ``CAN 1`` and ``LIN 1`` list separately even though
        both store the channel number 1.
        """
        from core.bus_types import sort_key

        if not len(self.timestamps):
            return []
        chs = np.frombuffer(self.channels, dtype=np.uint8)
        fls = np.frombuffer(self.flags, dtype=np.uint8)
        # Pack (bus bit, channel) into one uint16 so np.unique does the
        # reduction in C over the whole store in a single pass.
        packed = chs.astype(np.uint16)
        packed |= ((fls & np.uint8(FLAG_LIN)) != 0).astype(np.uint16) << np.uint16(8)
        keys = [
            (BusType.LIN if value >> 8 else BusType.CAN, value & 0xFF)
            for value in (int(v) for v in np.unique(packed))
            if (value & 0xFF) != 255
        ]
        return sorted(keys, key=sort_key)

    def build_match_mask(
        self,
        needle: str,
        channel_filter: BusChannel | int | None,
    ) -> np.ndarray | None:
        """
        Return a boolean numpy array of length len(self) indicating which
        frames pass the filter.  Returns None when all frames match (no
        filter active) — callers treat None as "full range, zero extra RAM".

        The matching frames themselves come from :meth:`matching_indices`,
        which a caller should prefer: this mask is one byte per frame.
        """
        n = len(self.timestamps)
        if n == 0:
            return np.zeros(0, dtype=bool)
        indices = self.matching_indices(needle, channel_filter)
        if indices is None:
            return None
        mask = np.zeros(n, dtype=bool)
        mask[indices] = True
        return mask

    def matching_indices(
        self,
        needle: str,
        channel_filter: BusChannel | int | None,
    ) -> np.ndarray | None:
        """
        The indices of the frames that pass the filter, or None for all.

        *channel_filter* is a ``(bus, number)`` key: filtering on the number
        alone would show LIN 1 frames when the user asked for CAN 1.

        A frame matches *needle* when its ID (exactly, else as a hex
        substring), frame name, direction, time stamp or — in a store of at
        most 500 k frames — data bytes do. When no frame matches any of
        those, the needle is looked for in each frame's text
        ``"can <channel> <id> <name> <direction> <length>"`` instead.

        Worked out a chunk of frames at a time, and per distinct ID or per
        distinct (ID, name, channel, direction, length) where it is about
        text: a log has a few hundred of those and millions of frames, so
        no Python runs per frame and no array holds one entry per frame
        besides the matches.
        """
        n = len(self.timestamps)
        if n == 0:
            return np.zeros(0, dtype=np.int64)
        if needle == '' and channel_filter is None:
            return None   # sentinel: all match
        chunks = [(start, min(n, start + _SEARCH_CHUNK)) for start in range(0, n, _SEARCH_CHUNK)]

        if channel_filter is not None:
            # A bare integer filter is read as CAN, matching the raw-CAN
            # formats that have no bus dimension to give.
            bus, number = coerce_channel_key(channel_filter)

            def on_channel(start, end):
                chs = np.frombuffer(self.channels, dtype=np.uint8)[start:end]
                fls = np.frombuffer(self.flags, dtype=np.uint8)[start:end]
                is_lin = (fls & np.uint8(FLAG_LIN)) != 0
                return (chs == number) & (is_lin == (bus is BusType.LIN))
        else:
            on_channel = None

        if not needle:
            return np.concatenate([
                np.flatnonzero(on_channel(start, end)) + start for start, end in chunks
            ]).astype(np.int64)

        fields = self._field_test(needle.lower(), n)
        found, any_field = [], False
        for start, end in chunks:
            matched = fields(start, end)
            any_field = any_field or bool(matched.any())
            if on_channel is not None:
                matched &= on_channel(start, end)
            found.append(np.flatnonzero(matched) + start)
        if not any_field:
            # No field matched anywhere: look in each frame's text instead.
            text = self._text_test(needle.lower())
            found = []
            for start, end in chunks:
                matched = text(start, end)
                if on_channel is not None:
                    matched &= on_channel(start, end)
                found.append(np.flatnonzero(matched) + start)
        return np.concatenate(found).astype(np.int64)

    def _distinct_ids(self) -> np.ndarray:
        """The sorted distinct arbitration IDs, worked out once per store."""
        cached = getattr(self, '_distinct_id_cache', None)
        if cached is not None and cached[0] == len(self.timestamps):
            return cached[1]
        aids = np.frombuffer(self.arb_ids, dtype=np.uint32)
        distinct = np.unique(np.concatenate([
            np.unique(aids[start:start + _SEARCH_CHUNK])
            for start in range(0, len(aids), _SEARCH_CHUNK)
        ] or [np.empty(0, dtype=np.uint32)]))
        self._distinct_id_cache = (len(self.timestamps), distinct)
        return distinct

    def _field_test(self, needle_lower: str, n: int):
        """Return ``test(start, end) -> mask`` of the needle against each field."""
        # 1. ID: exactly, else as a hex substring when no ID matches exactly.
        distinct = self._distinct_ids()
        ids = np.empty(0, dtype=np.uint32)
        try:
            hex_val = int(needle_lower.replace(' ', '').lstrip('0x'), 16)
            if np.isin(np.uint64(hex_val), distinct.astype(np.uint64)):
                ids = np.array([hex_val], dtype=np.uint32)
        except (ValueError, OverflowError):
            pass
        if not len(ids) and len(needle_lower) <= 8 and all(
            c in '0123456789abcdef' for c in needle_lower
        ):
            ids = np.array(
                [aid for aid in distinct.tolist() if needle_lower in f'{aid:X}'.lower()],
                dtype=np.uint32,
            )

        # 2. Name, through the name table.
        name_ids = np.array(
            [i for i, nm in enumerate(self.name_table) if needle_lower in nm.lower()],
            dtype=np.uint16,
        )

        # 3. Direction.
        directions = [d for d, text in ((_DIR_RX, 'rx'), (_DIR_TX, 'tx'))
                      if needle_lower in text]

        # 4. Time stamp.
        try:
            ts_val = float(needle_lower)
        except ValueError:
            ts_val = None

        # 5. Data bytes, in a small store only.
        data_needle = None
        hex_needle = needle_lower.replace(' ', '')
        if (n <= _LARGE_STORE_LIMIT and self.payload_mapped and len(needle_lower) >= 2
                and len(hex_needle) >= 2 and len(hex_needle) % 2 == 0
                and all(c in '0123456789abcdef' for c in hex_needle)):
            data_needle = np.frombuffer(bytes.fromhex(hex_needle), dtype=np.uint8)

        def test(start, end):
            mask = np.zeros(end - start, dtype=bool)
            if len(ids):
                aids = np.frombuffer(self.arb_ids, dtype=np.uint32)[start:end]
                mask |= np.isin(aids, ids)
            if len(name_ids):
                nids = np.frombuffer(self.name_ids, dtype=np.uint16)[start:end]
                mask |= np.isin(nids, name_ids)
            if directions:
                dirs = np.frombuffer(self.directions, dtype=np.uint8)[start:end]
                mask |= np.isin(dirs, directions)
            if ts_val is not None:
                ts = np.frombuffer(self.timestamps, dtype=np.float64)[start:end]
                mask |= np.abs(ts - ts_val) < 0.001
            if data_needle is not None and len(data_needle) <= _DATA_BYTES:
                rows = self.rows(slice(start, end))
                width = len(data_needle)
                for offset in range(_DATA_BYTES - width + 1):
                    mask |= np.all(rows[:, offset:offset + width] == data_needle, axis=1)
            return mask

        return test

    def _field_text_test(self, needle_lower: str, distinct: np.ndarray):
        """:meth:`_text_test` for a needle with no space: field by field."""
        everything = needle_lower in 'can'
        channels = [c for c in range(256)
                    if needle_lower in (str(c) if c != 255 else '?')]
        ids = np.array([aid for aid in distinct.tolist() if needle_lower in f'{aid:x}'],
                       dtype=np.uint32)
        names = np.array([i for i, nm in enumerate(self.name_table)
                          if needle_lower in nm.lower()], dtype=np.uint16)
        directions = [d for d in range(3) if needle_lower in _DIR_STRINGS[d].lower()]
        lengths = [length for length in range(256) if needle_lower in str(length)]

        def test(start, end):
            if everything:
                return np.ones(end - start, dtype=bool)
            mask = np.zeros(end - start, dtype=bool)
            if channels:
                mask |= np.isin(np.frombuffer(self.channels, dtype=np.uint8)[start:end], channels)
            if len(ids):
                mask |= np.isin(np.frombuffer(self.arb_ids, dtype=np.uint32)[start:end], ids)
            if len(names):
                mask |= np.isin(np.frombuffer(self.name_ids, dtype=np.uint16)[start:end], names)
            if directions:
                dirs = np.minimum(np.frombuffer(self.directions, dtype=np.uint8)[start:end], 2)
                mask |= np.isin(dirs, directions)
            if lengths:
                mask |= np.isin(np.frombuffer(self.dlcs, dtype=np.uint8)[start:end], lengths)
            return mask

        return test

    def _combinations(self) -> tuple[np.ndarray, np.ndarray]:
        """
        Number each frame's (ID, name, channel, direction, length).

        Returns ``(numbers, keys)``: a number per frame (a file-backed column)
        and each number's packed key — ``id rank << 34 | name id << 18 |
        channel << 10 | direction << 8 | length``. A log has a few thousand
        combinations; worked out once per store, they turn every later text
        search into a table lookup.
        """
        from core.sample_file import AppendColumn

        n = len(self.timestamps)
        cached = getattr(self, '_combination_cache', None)
        if cached is not None and cached[0] == n:
            return cached[1], cached[2]
        distinct = self._distinct_ids()
        numbers: dict[int, int] = {}
        column = AppendColumn(np.uint32)
        for start in range(0, n, _SEARCH_CHUNK):
            end = min(n, start + _SEARCH_CHUNK)
            aids = np.frombuffer(self.arb_ids, dtype=np.uint32)[start:end]
            keys = (
                (np.searchsorted(distinct, aids).astype(np.uint64) << np.uint64(34))
                | (np.frombuffer(self.name_ids, dtype=np.uint16)[start:end].astype(np.uint64)
                   << np.uint64(18))
                | (np.frombuffer(self.channels, dtype=np.uint8)[start:end].astype(np.uint64)
                   << np.uint64(10))
                | (np.minimum(np.frombuffer(self.directions, dtype=np.uint8)[start:end], 2)
                   .astype(np.uint64) << np.uint64(8))
                | np.frombuffer(self.dlcs, dtype=np.uint8)[start:end].astype(np.uint64)
            )
            unique, inverse = np.unique(keys, return_inverse=True)
            chunk_numbers = np.array(
                [numbers.setdefault(key, len(numbers)) for key in unique.tolist()],
                dtype=np.uint32,
            )
            column.extend(chunk_numbers[inverse.reshape(-1)])
        frame_numbers = column.mapped()
        column.close()
        keys = np.fromiter(numbers, dtype=np.uint64, count=len(numbers))
        self._combination_cache = (n, frame_numbers, keys)
        return frame_numbers, keys

    def _text_test(self, needle_lower: str):
        """
        Return ``test(start, end) -> mask``: the needle in each frame's text.

        The text is ``"can <channel> <id hex> <name> <direction> <length>"``.
        A needle without a space cannot reach across the spaces between those
        fields, so it matches where it is in one of them: each field is tested
        over its few distinct values. A needle with a space is tested on the
        text of each distinct combination of the five (_combinations), built
        once per store, never per frame.
        """
        distinct = self._distinct_ids()
        if ' ' not in needle_lower:
            return self._field_text_test(needle_lower, distinct)
        frame_numbers, keys = self._combinations()
        verdicts = np.zeros(len(keys), dtype=bool)
        for number, key in enumerate(keys.tolist()):
            rank, nid, ch, direction, dlc = (
                key >> 34, (key >> 18) & 0xFFFF, (key >> 10) & 0xFF,
                (key >> 8) & 0x3, key & 0xFF,
            )
            ch_str = f'can {ch}' if ch != 255 else 'can ?'
            aid_str = f'{int(distinct[rank]):x}'
            name_str = (self.name_table[nid] if nid < len(self.name_table) else '').lower()
            dir_str = _DIR_STRINGS[direction].lower()
            verdicts[number] = needle_lower in f'{ch_str} {aid_str} {name_str} {dir_str} {dlc}'

        def test(start, end):
            return verdicts[frame_numbers[start:end]]

        return test
