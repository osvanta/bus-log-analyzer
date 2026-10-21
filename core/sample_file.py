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

The file is opened delete-on-close, like the CAN Trace's, and has the same
name pattern, so the start-up sweep in core.raw_frame_store also removes one
left by a crash.
"""

from __future__ import annotations

import mmap
import os
import shutil
import tempfile

import numpy as np

from core.raw_frame_store import _create_data_file

#: One mapping per segment; a larger array gets a segment of its own.
_SEGMENT_BYTES = 256 * 1024 * 1024
#: Free space a new segment must leave on the disk.
_DISK_RESERVE_BYTES = 512 * 1024 * 1024


class SampleFileFull(OSError):
    """The disk has no room for another segment."""


class SampleFile:
    """Append-only float64 arrays in one temporary, memory-mapped file."""

    def __init__(self, segment_bytes: int = _SEGMENT_BYTES) -> None:
        self._file, self.path = _create_data_file()
        self._fd = self._file.fileno()
        granularity = mmap.ALLOCATIONGRANULARITY
        # Segment offsets must be multiples of the allocation granularity.
        self._granularity = granularity
        self._segment_bytes = -(-segment_bytes // granularity) * granularity
        self._size = 0
        self._segment: mmap.mmap | None = None
        self._used = 0
        self._capacity = 0
        self.bytes_stored = 0
        self.closed = False

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
        self.bytes_stored += view.nbytes
        return view

    def _allocate(self, count: int) -> np.ndarray:
        nbytes = count * 8
        if self._segment is None or self._used + nbytes > self._capacity:
            self._new_segment(max(self._segment_bytes, nbytes))
        view = np.frombuffer(
            self._segment, dtype=np.float64, count=count, offset=self._used
        )
        self._used += -(-nbytes // 8) * 8
        return view

    def _new_segment(self, nbytes: int) -> None:
        if self.closed:
            raise ValueError("the sample file is closed")
        capacity = -(-nbytes // self._granularity) * self._granularity
        free = shutil.disk_usage(os.path.dirname(self.path) or tempfile.gettempdir()).free
        if free < capacity + _DISK_RESERVE_BYTES:
            raise SampleFileFull(
                f"{capacity / 1e9:.1f} GB more is needed in "
                f"{os.path.dirname(self.path)}, which has {free / 1e9:.1f} GB free"
            )
        offset = self._size
        os.ftruncate(self._fd, offset + capacity)
        self._segment = mmap.mmap(
            self._fd, capacity, offset=offset, access=mmap.ACCESS_WRITE
        )
        self._size = offset + capacity
        self._used = 0
        self._capacity = capacity

    def close(self) -> None:
        """Close the file. Mapped views stay valid; the file goes with the last."""
        if self.closed:
            return
        self.closed = True
        self._segment = None
        try:
            self._file.close()
        except OSError:
            pass
