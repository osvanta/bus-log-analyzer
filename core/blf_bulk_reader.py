# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Read a BLF into packed frame columns with NumPy instead of a Python loop.

The per-object reader in :mod:`core.blf_reader` unpacks one object at a time:
about 5 µs a frame, a minute for a 13-million-frame log.  This reader produces
the same rows from whole decompressed blocks at once:

1. The main thread walks the file's containers and hands groups of them, about
   8 MB uncompressed, to worker threads.
2. A worker decompresses its group, finds every ``LOBJ`` signature, and reads
   each object's header, body and payload with one gather per field.  zlib and
   NumPy both release the GIL, so the workers run in parallel.
3. The main thread follows the object chain through each block exactly as
   python-can does, from wherever the previous block's last object ended, and
   parses the one object that straddles each block boundary on its own.

A signature inside a payload is only a candidate.  The chain check rejects it:
each object must start where the previous one ended, give or take the padding
python-can's eight-byte search allows.  When the check fails, the walk falls
back to following the candidates one at a time, which is still exact.

``tests/test_blf_bulk_reader.py`` compares every row against the per-object
reader on files built to break it: payloads holding ``LOBJ``, objects split
across containers, every frame type and both header versions.
"""
from __future__ import annotations

import os
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import can
import numpy as np
from can.io import blf as _blf
from can.util import CAN_FD_DLC

# The LIN layout is core.blf_reader's, verified there against a real CANoe log.
from core.blf_reader import (
    _LIN1_CHANNEL, _LIN1_DATA, _LIN1_DLC, _LIN1_ID, _LIN1_MIN_BODY,
    _LIN2_CHANNEL, _LIN2_DATA, _LIN2_DIR, _LIN2_DLC, _LIN2_ID, _LIN2_MIN_BODY,
    _LIN_EVENT_TYPES, _LIN_MESSAGE, _LIN_MESSAGE2,
)
from core.raw_frame_store import FLAG_EXTENDED, FLAG_FD, FLAG_LIN

_CAN_MESSAGE = _blf.CAN_MESSAGE
_CAN_MESSAGE2 = _blf.CAN_MESSAGE2
_CAN_ERROR_EXT = _blf.CAN_ERROR_EXT
_CAN_FD_MESSAGE = _blf.CAN_FD_MESSAGE
_CAN_FD_MESSAGE_64 = _blf.CAN_FD_MESSAGE_64

# Uncompressed bytes a worker parses in one go. Small enough to stay mostly in
# the CPU cache while it is scanned, large enough that NumPy's per-call cost
# is lost in the work.
_BLOCK_BYTES = 8 << 20

# Zero bytes after each block, so a fixed-width read that starts on the last
# candidate never runs off the end. The widest is a LIN_MESSAGE2 body, 123
# bytes from a body that starts at most 40 bytes after the signature.
_PAD = 256
_PAD_BYTES = bytes(_PAD)

_SIGNATURE = int.from_bytes(b"LOBJ", "little")
_BASE_SIZE = 16

# The 32 bytes from an object's signature: base header, then the fields that
# the v1 and v2 object headers share (time flags at 16, timestamp at 24).
_HEADER = np.dtype({
    "names": ["header_size", "header_version", "size", "type", "time_flags", "timestamp"],
    "formats": ["<u2", "<u2", "<u4", "<u4", "<u4", "<u8"],
    "offsets": [4, 6, 8, 12, 16, 24],
    "itemsize": 32,
})

# Object bodies, at the offsets python-can's structs unpack them from.
_CAN_BODY = np.dtype({
    "names": ["channel", "flags", "dlc", "can_id"],
    "formats": ["<u2", "u1", "u1", "<u4"],
    "offsets": [0, 2, 3, 4],
    "itemsize": _blf.CAN_MSG_STRUCT.size,
})
_ERROR_BODY = np.dtype({
    "names": ["channel", "dlc", "can_id"],
    "formats": ["<u2", "u1", "<u4"],
    "offsets": [0, 10, 16],
    "itemsize": _blf.CAN_ERROR_EXT_STRUCT.size,
})
_FD_BODY = np.dtype({
    "names": ["channel", "flags", "dlc", "can_id", "fd_flags", "valid_bytes"],
    "formats": ["<u2", "u1", "u1", "<u4", "u1", "u1"],
    "offsets": [0, 2, 3, 4, 13, 14],
    "itemsize": _blf.CAN_FD_MSG_STRUCT.size,
})
_FD64_BODY = np.dtype({
    "names": ["channel", "dlc", "valid_bytes", "can_id", "fd_flags",
              "direction", "ext_data_offset"],
    "formats": ["u1", "u1", "u1", "<u4", "<u4", "u1", "u1"],
    "offsets": [0, 1, 2, 4, 12, 34, 35],
    "itemsize": _blf.CAN_FD_MSG_64_STRUCT.size,
})
_LIN2_BODY = np.dtype({
    "names": ["channel", "frame_id", "dlc", "direction"],
    "formats": ["<u2", "u1", "u1", "u1"],
    "offsets": [_LIN2_CHANNEL, _LIN2_ID, _LIN2_DLC, _LIN2_DIR],
    "itemsize": _LIN2_MIN_BODY,
})
_LIN1_BODY = np.dtype({
    "names": ["channel", "frame_id", "dlc"],
    "formats": ["<u2", "u1", "u1"],
    "offsets": [_LIN1_CHANNEL, _LIN1_ID, _LIN1_DLC],
    "itemsize": _LIN1_DLC + 1,
})

# Where each body's payload starts.
_CAN_PAYLOAD = 8
_ERROR_PAYLOAD = 24
_FD_PAYLOAD = 20
_FD64_PAYLOAD = _blf.CAN_FD_MSG_64_STRUCT.size

# python-can's dlc2len(): the table, then 64 for every DLC above 15.
_DLC_TO_LENGTH = np.array(list(CAN_FD_DLC) + [64] * (256 - len(CAN_FD_DLC)),
                          dtype=np.uint8)

# What each object is, as a small code, so one lookup replaces a comparison
# per object type. Codes are only given to objects with a v1 or v2 header;
# python-can skips every other version unread.
_NONE, _CAN, _ERROR, _FD, _FD64, _LIN2, _LIN1, _EVENT = range(8)
_KIND_BY_TYPE = np.zeros(256, dtype=np.uint8)
_KIND_BY_TYPE[[_CAN_MESSAGE, _CAN_MESSAGE2]] = _CAN
_KIND_BY_TYPE[_CAN_ERROR_EXT] = _ERROR
_KIND_BY_TYPE[_CAN_FD_MESSAGE] = _FD
_KIND_BY_TYPE[_CAN_FD_MESSAGE_64] = _FD64
_KIND_BY_TYPE[_LIN_MESSAGE2] = _LIN2
_KIND_BY_TYPE[_LIN_MESSAGE] = _LIN1
_KIND_BY_TYPE[list(_LIN_EVENT_TYPES)] = _EVENT

# Body bytes python-can unpacks with a struct, by kind. LIN bodies are sliced,
# which never fails, so they need nothing beyond the object.
_STRUCT_BYTES = np.zeros(8, dtype=np.int64)
_STRUCT_BYTES[_CAN] = _CAN_BODY.itemsize
_STRUCT_BYTES[_ERROR] = _ERROR_BODY.itemsize
_STRUCT_BYTES[_FD] = _FD_BODY.itemsize
_STRUCT_BYTES[_FD64] = _FD64_BODY.itemsize

# Row n keeps the first n payload bytes and clears the rest.
_PAYLOAD = np.dtype((np.void, 64))
_PAYLOAD_MASKS = np.where(
    np.arange(64)[None, :] < np.arange(65)[:, None], 0xFF, 0
).astype(np.uint8).view(_PAYLOAD).ravel()


def _records(data: bytes, dtype: np.dtype) -> np.ndarray:
    """A record of *dtype* starting at every byte of *data*.

    Indexing this with object offsets reads a whole struct per object in one
    C-level gather, without the per-row cost of a 2-D sliding window.
    """
    return np.ndarray(
        shape=(len(data) - dtype.itemsize + 1,),
        dtype=dtype,
        buffer=data,
        strides=(1,),
    )


def _gather(data: bytes, dtype: np.dtype, offsets: np.ndarray) -> np.ndarray:
    """Read one *dtype* struct at each offset.

    Gathered as opaque bytes and only then viewed as the struct: NumPy copies
    a structured record field by field, a plain one in one move, and that is
    over twice as fast.
    """
    return _records(data, np.dtype((np.void, dtype.itemsize)))[offsets].view(dtype)


@dataclass
class _Frames:
    """Frame columns in file order. Timestamps are absolute seconds."""

    timestamps: np.ndarray
    channels: np.ndarray
    arb_ids: np.ndarray
    dlcs: np.ndarray
    directions: np.ndarray
    flags: np.ndarray
    payload: np.ndarray          # (n, 64) uint8, zero past each frame's data

    def __len__(self) -> int:
        return len(self.timestamps)

    def select(self, rows) -> "_Frames":
        return _Frames(
            self.timestamps[rows], self.channels[rows], self.arb_ids[rows],
            self.dlcs[rows], self.directions[rows], self.flags[rows],
            self.payload[rows],
        )

    @staticmethod
    def join(parts: list["_Frames"]) -> "_Frames":
        if len(parts) == 1:
            return parts[0]
        return _Frames(*(
            np.concatenate([getattr(part, name) for part in parts])
            for name in ("timestamps", "channels", "arb_ids", "dlcs",
                         "directions", "flags", "payload")
        ))


@dataclass
class _Scan:
    """Every ``LOBJ`` candidate in one buffer, parsed as if it were real.

    Which candidates are real is decided afterwards by :func:`_follow`, from
    where the walk enters the buffer; candidates it rejects are simply never
    selected.
    """

    data: bytes                  # the buffer, followed by _PAD zero bytes
    limit: int                   # bytes of real data
    starts: np.ndarray           # candidate offsets, ascending
    ends: np.ndarray             # starts + object size
    sizes: np.ndarray
    complete: np.ndarray         # every byte python-can reads is in the buffer
    frame_rows: np.ndarray       # candidates before each one that are frames
    events: np.ndarray           # candidate is a LIN bus event
    frames: _Frames              # one row per frame candidate


def _find_signatures(data: bytes, limit: int) -> np.ndarray:
    """Offsets of every ``LOBJ`` that lies wholly inside the first *limit* bytes."""
    if limit < 4:
        return np.empty(0, dtype=np.int64)
    first = np.flatnonzero(np.frombuffer(data, dtype=np.uint8, count=limit - 3) == 0x4C)
    words = _records(data, np.dtype("<u4"))[first]
    return first[words == _SIGNATURE].astype(np.int64, copy=False)


def _scan(data: bytes, start_timestamp: float) -> _Scan:
    """Parse every candidate object in *data*, which ends with _PAD zeros."""
    limit = len(data) - _PAD
    starts = _find_signatures(data, limit)

    header = _gather(data, _HEADER, starts)
    sizes = header["size"].astype(np.int64)
    ends = starts + sizes
    version = header["header_version"]
    v2 = version == 2
    versioned = (version == 1) | v2
    body = starts + np.where(v2, _BASE_SIZE + _blf.OBJ_HEADER_V2_STRUCT.size,
                             _BASE_SIZE + _blf.OBJ_HEADER_V1_STRUCT.size)
    kind = np.where(versioned, _KIND_BY_TYPE[np.minimum(header["type"], 255)], _NONE)
    # LIN frames too short for their layout are skipped, as the per-object
    # reader does, and are then neither frames nor events.
    body_length = ends - body
    kind[(kind == _LIN2) & (body_length < _LIN2_MIN_BODY)] = _NONE
    kind[(kind == _LIN1) & (body_length < _LIN1_MIN_BODY)] = _NONE

    # The last byte python-can reads for each object: the object's own end,
    # unless a malformed size makes a header or struct read run past it. A
    # read past the end of the data stops the parse until more arrives.
    header_end = np.where(versioned, body, starts + _BASE_SIZE)
    complete = np.maximum(ends, header_end + _STRUCT_BYTES[kind]) <= limit

    is_frame = (kind != _NONE) & (kind != _EVENT)
    frame_rows = np.zeros(len(starts) + 1, dtype=np.int64)
    np.cumsum(is_frame, out=frame_rows[1:])
    total = int(frame_rows[-1])

    channels = np.empty(total, dtype=np.uint16)
    arb_ids = np.empty(total, dtype=np.uint32)
    dlcs = np.empty(total, dtype=np.uint8)
    directions = np.empty(total, dtype=np.uint8)
    flags = np.empty(total, dtype=np.uint8)
    payload_start = np.empty(total, dtype=np.int64)
    payload_length = np.empty(total, dtype=np.int64)
    columns = (channels, arb_ids, dlcs, directions, flags,
               payload_start, payload_length)

    def place(rows, *values) -> None:
        if len(rows) == total:
            # Every frame is of this kind, so rows is 0..total-1.
            for column, value in zip(columns, values):
                column[:] = value
        else:
            for column, value in zip(columns, values):
                column[rows] = value

    def can_flags(can_id, fd=None):
        flag = ((can_id >> 31) * FLAG_EXTENDED).astype(np.uint8)
        if fd is not None:
            flag |= (fd != 0).astype(np.uint8) * np.uint8(FLAG_FD)
        return flag

    for code in np.unique(kind).tolist():
        if code in (_NONE, _EVENT):
            continue
        candidates = np.flatnonzero(kind == code)
        rows = frame_rows[candidates]
        offsets = body[candidates]
        if code == _CAN:
            fields = _gather(data, _CAN_BODY, offsets)
            can_id = fields["can_id"]
            dlc = fields["dlc"]
            place(rows, fields["channel"], can_id & 0x1FFF_FFFF, dlc,
                  fields["flags"] & _blf.DIR, can_flags(can_id),
                  offsets + _CAN_PAYLOAD, np.minimum(dlc, 8))
        elif code == _ERROR:
            fields = _gather(data, _ERROR_BODY, offsets)
            can_id = fields["can_id"]
            dlc = fields["dlc"]
            place(rows, fields["channel"], can_id & 0x1FFF_FFFF, dlc, 0,
                  can_flags(can_id), offsets + _ERROR_PAYLOAD, np.minimum(dlc, 8))
        elif code == _FD:
            fields = _gather(data, _FD_BODY, offsets)
            can_id = fields["can_id"]
            place(rows, fields["channel"], can_id & 0x1FFF_FFFF,
                  _DLC_TO_LENGTH[fields["dlc"]], fields["flags"] & _blf.DIR,
                  can_flags(can_id, fields["fd_flags"] & 0x1),
                  offsets + _FD_PAYLOAD, np.minimum(fields["valid_bytes"], 64))
        elif code == _FD64:
            fields = _gather(data, _FD64_BODY, offsets)
            can_id = fields["can_id"]
            ext_offset = fields["ext_data_offset"].astype(np.int64)
            # python-can: min(valid, (ext_data_offset or obj_size) -
            # header_size - struct size), sliced from the data that is there
            # and zero-padded back up to valid; a negative length is nothing.
            available = (
                np.where(ext_offset != 0, ext_offset, sizes[candidates])
                - header["header_size"][candidates]
                - _FD64_PAYLOAD
            )
            start = offsets + _FD64_PAYLOAD
            length = np.minimum(np.minimum(fields["valid_bytes"], available),
                                limit - start)
            place(rows, fields["channel"], can_id & 0x1FFF_FFFF,
                  _DLC_TO_LENGTH[fields["dlc"]], fields["direction"] != 0,
                  can_flags(can_id, fields["fd_flags"] & 0x1000),
                  start, np.clip(length, 0, 64))
        elif code == _LIN2:
            fields = _gather(data, _LIN2_BODY, offsets)
            dlc = np.minimum(fields["dlc"], 8)
            place(rows, fields["channel"], fields["frame_id"] & 0x3F, dlc,
                  np.minimum(fields["direction"], 2), FLAG_LIN,
                  offsets + _LIN2_DATA, dlc)
        elif code == _LIN1:
            fields = _gather(data, _LIN1_BODY, offsets)
            dlc = np.minimum(fields["dlc"], 8)
            place(rows, fields["channel"], fields["frame_id"] & 0x3F, dlc,
                  2, FLAG_LIN, offsets + _LIN1_DATA, dlc)

    frame_header = header[is_frame]
    factor = np.where(frame_header["time_flags"] == _blf.TIME_TEN_MICS, 1e-5, 1e-9)
    timestamps = frame_header["timestamp"].astype(np.float64) * factor + start_timestamp

    # Read 64 bytes for every frame, then clear what lies past its data.
    payload = _gather(data, _PAYLOAD, payload_start).view(np.uint8).reshape(-1, 64)
    if total and int(payload_length.min()) < 64:
        words = payload.view("<u8")
        words &= _PAYLOAD_MASKS[payload_length].view("<u8").reshape(-1, 8)

    frames = _Frames(
        timestamps,
        np.minimum(channels, 255).astype(np.uint8),
        arb_ids, dlcs, directions, flags, payload,
    )
    return _Scan(data, limit, starts, ends, sizes, complete, frame_rows,
                 kind == _EVENT, frames)


def _follow(scan: _Scan, pos: int) -> tuple[_Frames, int, int]:
    """Walk the object chain from *pos* the way python-can does.

    Returns the frames of every complete object on the chain, the offset the
    walk stopped at (where the next block's data must be appended), and how
    many LIN bus events it passed.
    """
    starts = scan.starts
    count = len(starts)
    first = int(np.searchsorted(starts, pos))

    # The fast path: every candidate from here is a real object. Then each
    # object starts within four bytes of the last one's end, which is exactly
    # where python-can's search window would find it.
    if first < count and starts[first] - pos <= 4:
        complete = scan.complete[first:]
        stop = first + (len(complete) if complete.all() else int(np.argmin(complete)))
        # Each object to its successor, including the incomplete one the walk
        # stops at, whose start must still be where python-can looks for it.
        last = min(stop, count - 1)
        gaps = starts[first + 1:last + 1] - scan.ends[first:last]
        chained = (
            bool((scan.sizes[first:stop] >= _BASE_SIZE).all())
            and bool(((gaps >= 0) & (gaps <= 4)).all())
        )
        if chained and stop == count:
            # Nothing follows the last object, as python-can only accepts
            # when fewer than eight bytes remain to search.
            chained = int(scan.ends[stop - 1]) + 8 > scan.limit
        if chained:
            consumed = int(scan.ends[stop - 1]) if stop > first else pos
            rows = slice(int(scan.frame_rows[first]), int(scan.frame_rows[stop]))
            events = int(np.count_nonzero(scan.events[first:stop]))
            return scan.frames.select(rows), consumed, events

    # A candidate inside some payload: follow the chain one object at a time.
    begin = starts.tolist()
    end = scan.ends.tolist()
    size = scan.sizes.tolist()
    complete = scan.complete.tolist()
    following = np.searchsorted(starts, scan.ends).tolist()
    chain: list[int] = []
    index = first
    while True:
        if index >= count or begin[index] > pos + 4:
            if pos + 8 > scan.limit:
                break
            raise _blf.BLFParseError("Could not find next object")
        if not complete[index]:
            break
        if size[index] < _BASE_SIZE:
            raise _blf.BLFParseError("Object smaller than its own header")
        chain.append(index)
        pos = end[index]
        index = following[index]
    chain_array = np.asarray(chain, dtype=np.int64)
    frame_rows = scan.frame_rows[:-1][chain_array]
    is_frame = scan.frame_rows[chain_array + 1] > frame_rows
    events = int(np.count_nonzero(scan.events[chain_array]))
    return scan.frames.select(frame_rows[is_frame]), pos, events


def _decompress_block(containers: list[tuple[int, memoryview]],
                      start_timestamp: float) -> _Scan:
    """Worker: inflate a group of containers and parse every candidate."""
    pieces = [
        zlib.decompressobj().decompress(body) if method == _blf.ZLIB_DEFLATE else body
        for method, body in containers
    ]
    pieces.append(_PAD_BYTES)
    return _scan(b"".join(pieces), start_timestamp)


def _container_groups(reader: can.BLFReader) -> Iterator[list[tuple[int, memoryview]]]:
    """Group the file's log containers into blocks of about _BLOCK_BYTES.

    Reads the top level exactly as python-can's ``BLFReader.__iter__`` does,
    skipping objects that are not containers and containers it cannot inflate.
    """
    unpack_base = _blf.OBJ_HEADER_BASE_STRUCT.unpack
    unpack_container = _blf.LOG_CONTAINER_STRUCT.unpack_from
    container_size = _blf.LOG_CONTAINER_STRUCT.size
    # Read in large chunks and slice containers out of memory: one small
    # read per header, body and padding would be ~30,000 calls, each giving
    # up the GIL to the busy workers and waiting to get it back.
    source = _ChunkedFile(reader.file)
    group: list[tuple[int, memoryview]] = []
    group_bytes = 0
    while True:
        data = source.read(_BASE_SIZE)
        if not data:
            break
        signature, _, _, obj_size, obj_type = unpack_base(data)
        if signature != b"LOBJ":
            raise _blf.BLFParseError()
        # As python-can's file.read(): a size of 15 reads everything that
        # is left (read(-1)), and a smaller one is an error.
        obj_data = source.read(obj_size - _BASE_SIZE)
        source.read(obj_size % 4)
        if obj_type != _blf.LOG_CONTAINER:
            continue
        method, uncompressed = unpack_container(obj_data)
        if method not in (_blf.NO_COMPRESSION, _blf.ZLIB_DEFLATE):
            _blf.LOG.warning("Unknown compression method (%d)", method)
            continue
        group.append((method, obj_data[container_size:]))
        group_bytes += uncompressed
        if group_bytes >= _BLOCK_BYTES:
            yield group
            group = []
            group_bytes = 0
    if group:
        yield group


class _ChunkedFile:
    """``read(n)`` over a file, served from 32 MB chunks as memoryviews."""

    _CHUNK = 32 << 20

    def __init__(self, file) -> None:
        self._file = file
        self._chunk = memoryview(b"")
        self._pos = 0

    def read(self, size: int) -> memoryview:
        if size < -1:
            raise ValueError("read length must be non-negative or -1")
        available = len(self._chunk) - self._pos
        if size < 0 or size > available:
            rest = self._chunk[self._pos:]
            more = self._file.read(-1 if size < 0 else max(size - available, self._CHUNK))
            self._chunk = memoryview(bytes(rest) + more) if rest else memoryview(more)
            self._pos = 0
            available = len(self._chunk)
            if size < 0:
                size = available
        size = min(size, available)
        view = self._chunk[self._pos:self._pos + size]
        self._pos += size
        return view


class BulkBLFReader:
    """Yield a BLF's frames as NumPy columns, block by block.

    The rows are those of the per-object reader,
    :meth:`core.blf_reader.BLFReaderService.iter_raw_batches_per_object`; see
    the module docstring for how.
    """

    def __init__(self, path: str | Path, workers: int | None = None) -> None:
        self.path = Path(path)
        self.workers = workers or max(1, min(8, os.cpu_count() or 1))
        # LIN bus events (sleep, wakeup, errors, schedule changes) passed by
        # the last complete pass. Counted, not stored: they are not frames.
        self.lin_event_objects = 0

    def iter_frames(self) -> Iterator[_Frames]:
        """Yield frame columns block by block, in file order."""
        reader = can.BLFReader(str(self.path))
        executor = ThreadPoolExecutor(
            max_workers=self.workers, thread_name_prefix="blf-block"
        )
        try:
            start_timestamp = reader.start_timestamp
            pending: deque = deque()
            carry = b""
            events = 0
            groups = _container_groups(reader)
            # Start with two blocks in flight and widen to the worker count,
            # so a caller that only wants the first frames (the channel
            # pre-scan) does not wait for a dozen blocks it will never read.
            in_flight = 2
            exhausted = False
            while True:
                while not exhausted and len(pending) < in_flight:
                    group = next(groups, None)
                    if group is None:
                        exhausted = True
                        break
                    pending.append(
                        executor.submit(_decompress_block, group, start_timestamp)
                    )
                if not pending:
                    break
                in_flight = min(in_flight * 2, self.workers + 2)
                scan = pending.popleft().result()

                parts: list[_Frames] = []
                pos = 0
                if carry:
                    carry, pos, carried_events, carried = self._straddle(
                        carry, scan, start_timestamp
                    )
                    events += carried_events
                    if len(carried):
                        parts.append(carried)
                    if carry:
                        # One object spans the whole block; keep collecting.
                        continue
                frames, consumed, block_events = _follow(scan, pos)
                events += block_events
                if len(frames):
                    parts.append(frames)
                carry = scan.data[consumed:scan.limit]
                if parts:
                    yield _Frames.join(parts)
            self.lin_event_objects = events
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            reader.stop()

    @staticmethod
    def _straddle(carry: bytes, scan: _Scan, start_timestamp: float):
        """Parse the object that runs from the last block into *scan*.

        Returns what is still carried (non-empty only when the object is not
        finished even with all of *scan*), where the walk resumes in *scan*,
        the LIN events passed and the frames found.
        """
        # Enough of this block to finish the object, if its size is readable
        # yet; the signature may follow up to four bytes of padding.
        at = carry.find(b"LOBJ", 0, 8)
        if at >= 0 and len(carry) >= at + _BASE_SIZE:
            size = int.from_bytes(carry[at + 8:at + 12], "little")
            head = max(at + size - len(carry), 0) + _PAD
        else:
            head = 4096
        while True:
            head = min(head, scan.limit)
            joined = carry + scan.data[:head]
            small = _scan(joined + _PAD_BYTES, start_timestamp)
            frames, consumed, events = _follow(small, 0)
            if consumed > len(carry):
                # Finished; the walk resumes in this block where it stopped.
                return b"", consumed - len(carry), events, frames
            if head == scan.limit:
                # The object is not finished even by the end of this block.
                return joined[consumed:], 0, events, frames
            head *= 2
