# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Decoded samples kept in a temporary file instead of the heap.

A SignalStore that has filled its memory budget puts further sample arrays
here. Each array is copied into a memory-mapped region of one temporary file
and the store keeps a NumPy view of that region, so every reader of a series
works unchanged. Pages of a mapped file are backed by the file, not by the
page file: they do not count against the system's commit limit, and Windows
writes them out and drops them when memory is short. A measurement larger
than the memory therefore loads; free disk space is the limit instead.

Space no array holds any more is used again: a series that grew into a new
region leaves its old one, and the parts a decoder kept until it joined them
leave theirs. Before, every copy stayed, and a 5 GB log's sample file held
each sample twice (TODO issue 24).

The file is opened delete-on-close, like the CAN Trace's, and has the same
name pattern, so the start-up sweep in core.raw_frame_store also removes one
left by a crash.
"""

from __future__ import annotations

import bisect
import collections
import mmap
import os
import shutil
import tempfile
import threading
import weakref

import numpy as np

from core.raw_frame_store import _create_data_file

#: One mapping per segment; a larger array gets a segment of its own.
_SEGMENT_BYTES = 256 * 1024 * 1024
#: Free space a new segment must leave on the disk.
_DISK_RESERVE_BYTES = 512 * 1024 * 1024


class SampleFileFull(OSError):
    """The disk has no room for another segment."""


class SampleFile:
    """
    Float64 arrays in one temporary, memory-mapped file.

    Each array is a region of a segment. A region is free again once the
    array returned for it is gone: a slice or other view of an array keeps
    the array as its base, so the array goes only with the last of them.
    """

    def __init__(self, segment_bytes: int = _SEGMENT_BYTES) -> None:
        self._file, self.path = _create_data_file()
        self._fd = self._file.fileno()
        granularity = mmap.ALLOCATIONGRANULARITY
        # Segment offsets must be multiples of the allocation granularity.
        self._granularity = granularity
        self._segment_bytes = -(-segment_bytes // granularity) * granularity
        self._size = 0
        self._segments: list[mmap.mmap] = []
        self._used = 0          # of the last segment
        self._capacity = 0      # of the last segment
        # Free regions as [segment, offset, bytes], in file order.
        self._free: list[list[int]] = []
        # Regions whose array is gone, added by a finalizer on whichever
        # thread let it go and taken into _free under the lock.
        self._released: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._live = 0
        self.closed = False

    @property
    def bytes_stored(self) -> int:
        """Bytes of the arrays still held."""
        with self._lock:
            self._take_released()
            return self._live

    @property
    def file_bytes(self) -> int:
        """The file's size."""
        return self._size

    def store(self, *parts) -> np.ndarray:
        """
        Copy the float64 concatenation of *parts* into the file.

        Returns a writable float64 view of the copy. The view keeps its
        mapping alive, so it stays valid after :meth:`close`.
        """
        arrays = [np.asarray(p, dtype=np.float64).reshape(-1) for p in parts]
        count = sum(len(a) for a in arrays)
        if count == 0:
            return np.empty(0, dtype=np.float64)
        view = self._allocate(count)
        start = 0
        for array in arrays:
            view[start:start + len(array)] = array
            start += len(array)
        return view

    def _allocate(self, count: int) -> np.ndarray:
        nbytes = count * 8
        with self._lock:
            if self.closed:
                raise ValueError("the sample file is closed")
            self._take_released()
            place = self._take_free(nbytes)
            if place is None:
                if not self._segments or self._used + nbytes > self._capacity:
                    self._new_segment(max(self._segment_bytes, nbytes))
                place = (len(self._segments) - 1, self._used)
                self._used += nbytes
            segment, offset = place
            view = np.frombuffer(
                self._segments[segment], dtype=np.float64, count=count, offset=offset
            )
            self._live += nbytes
        weakref.finalize(view, self._released.append, (segment, offset, nbytes)).atexit = False
        return view

    def _take_free(self, nbytes: int) -> tuple[int, int] | None:
        """The smallest free region that holds *nbytes*, or None."""
        best = None
        for i, (_, _, size) in enumerate(self._free):
            if size >= nbytes and (best is None or size < self._free[best][2]):
                best = i
                if size == nbytes:
                    break
        if best is None:
            return None
        segment, offset, size = self._free[best]
        if size == nbytes:
            del self._free[best]
        else:
            self._free[best] = [segment, offset + nbytes, size - nbytes]
        return segment, offset

    def _take_released(self) -> None:
        while self._released:
            segment, offset, nbytes = self._released.popleft()
            if self.closed:
                continue
            self._live -= nbytes
            self._add_free(segment, offset, nbytes)

    def _add_free(self, segment: int, offset: int, nbytes: int) -> None:
        """Mark a region free, joined with free neighbours."""
        free = self._free
        i = bisect.bisect_left(free, [segment, offset, 0])
        if i and free[i - 1][0] == segment and free[i - 1][1] + free[i - 1][2] == offset:
            i -= 1
            free[i][2] += nbytes
        else:
            free.insert(i, [segment, offset, nbytes])
        if (i + 1 < len(free) and free[i + 1][0] == segment
                and free[i][1] + free[i][2] == free[i + 1][1]):
            free[i][2] += free[i + 1][2]
            del free[i + 1]
        if segment == len(self._segments) - 1 and free[i][1] + free[i][2] == self._used:
            # The end of the last segment: back to where new regions start.
            self._used = free[i][1]
            del free[i]

    def _new_segment(self, nbytes: int) -> None:
        capacity = -(-nbytes // self._granularity) * self._granularity
        free = shutil.disk_usage(os.path.dirname(self.path) or tempfile.gettempdir()).free
        if free < capacity + _DISK_RESERVE_BYTES:
            raise SampleFileFull(
                f"{capacity / 1e9:.1f} GB more is needed in "
                f"{os.path.dirname(self.path)}, which has {free / 1e9:.1f} GB free"
            )
        tail = None
        if self._segments and self._used < self._capacity:
            tail = (len(self._segments) - 1, self._used, self._capacity - self._used)
        offset = self._size
        os.ftruncate(self._fd, offset + capacity)
        self._segments.append(mmap.mmap(
            self._fd, capacity, offset=offset, access=mmap.ACCESS_WRITE
        ))
        self._size = offset + capacity
        self._used = 0
        self._capacity = capacity
        if tail is not None:
            # What the previous segment had left takes later, smaller arrays.
            self._add_free(*tail)

    def close(self) -> None:
        """Close the file. Mapped views stay valid; the file goes with the last."""
        with self._lock:
            if self.closed:
                return
            self.closed = True
            self._segments = []
            self._free = []
            self._released.clear()
        try:
            self._file.close()
        except OSError:
            pass


class AppendColumn:
    """
    One typed column appended to its own temporary file, then mapped.

    The CAN Trace's frame index (time, channel, ID, ...) grows by a few bytes
    per frame; written here batch by batch instead of held in the heap, it
    costs no memory while a measurement is read, and once mapped it costs no
    commit while the measurement is open. Same delete-on-close file and name
    as the CAN Trace's data file.
    """

    def __init__(self, dtype) -> None:
        self.dtype = np.dtype(dtype)
        self._file, self.path = _create_data_file()
        self.count = 0
        self._map: mmap.mmap | None = None

    def extend(self, values) -> None:
        column = np.ascontiguousarray(values, dtype=self.dtype).reshape(-1)
        if len(column):
            self._file.write(memoryview(column).cast("B"))
            self.count += len(column)

    def mapped(self) -> np.ndarray:
        """A writable view of the whole column; further extends are refused."""
        if self._map is None and self.count:
            self._file.flush()
            self._map = mmap.mmap(
                self._file.fileno(), self.count * self.dtype.itemsize,
                access=mmap.ACCESS_WRITE,
            )
        if self._map is None:
            return np.empty(0, dtype=self.dtype)
        return np.frombuffer(self._map, dtype=self.dtype, count=self.count)

    def close(self) -> None:
        """Close the file; a mapped view stays valid and the file goes with it."""
        self._map = None
        try:
            self._file.close()
        except OSError:
            pass


class PieceFile:
    """
    Arrays of any numeric type, appended one after another to one temporary
    file, then mapped for reading.

    An MF4 group stores its channels record by record, so reading one channel
    reads the whole group. Copied here a chunk of records at a time, each
    channel's piece of each chunk in turn, any channel is then a few mapped
    reads. Written front to back, the file is never zero-filled first.
    """

    def __init__(self, expected_bytes: int = 0) -> None:
        self._file, self.path = _create_data_file()
        free = shutil.disk_usage(os.path.dirname(self.path) or tempfile.gettempdir()).free
        if free < expected_bytes + _DISK_RESERVE_BYTES:
            self._file.close()
            raise SampleFileFull(
                f"{expected_bytes / 1e9:.1f} GB is needed in {os.path.dirname(self.path)}, "
                f"which has {free / 1e9:.1f} GB free"
            )
        self.nbytes = 0
        self._map: mmap.mmap | None = None

    def append(self, values: np.ndarray) -> tuple[np.dtype, int, int]:
        """Write *values*; return the piece (dtype, byte offset, count)."""
        values = np.ascontiguousarray(values).reshape(-1)
        piece = (values.dtype, self.nbytes, len(values))
        if len(values):
            self._file.write(memoryview(values).cast("B"))
            self.nbytes += values.nbytes
        return piece

    def seal(self) -> None:
        """Map what was written; further appends are refused."""
        self._file.flush()
        if self.nbytes:
            self._map = mmap.mmap(self._file.fileno(), self.nbytes, access=mmap.ACCESS_READ)

    def read(self, pieces) -> np.ndarray:
        """The pieces joined, as a new array of their type."""
        parts = [
            np.frombuffer(self._map, dtype=dtype, count=count, offset=offset)
            for dtype, offset, count in pieces if count
        ]
        if not parts:
            return np.empty(0, dtype=pieces[0][0] if pieces else np.float64)
        return np.concatenate(parts) if len(parts) > 1 else parts[0].copy()

    def close(self) -> None:
        self._map = None
        try:
            self._file.close()
        except OSError:
            pass


def scratch_array(count: int, dtype, heap_limit: int = 64 * 1024 * 1024) -> np.ndarray:
    """
    A writable work array: in the heap when small, file-backed when large.

    For per-frame working arrays of a decode (a sort order, say) that would
    otherwise take memory in proportion to the measurement.
    """
    dtype = np.dtype(dtype)
    if count * dtype.itemsize <= heap_limit:
        return np.empty(count, dtype=dtype)
    column = AppendColumn(dtype)
    column._file.truncate(count * dtype.itemsize)
    column.count = count
    view = column.mapped()
    column.close()
    return view
