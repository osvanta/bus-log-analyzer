# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
For each frame of a RawFrameStore, the previous frame of the same kind: the
same ID on the same bus and channel, standard or extended alike, and data,
error and remote frames apart. The Trace shows from it the time since that
frame (Δt) and which data bytes changed.

Worked out a chunk at a time, so the Trace can spread it over several turns
of the event loop on a file of millions of frames; the index takes four bytes
a frame.
"""
from __future__ import annotations

import weakref

import numpy as np

from core.raw_frame_store import (FLAG_ERROR, FLAG_EXTENDED, FLAG_LIN,
                                  FLAG_REMOTE, RawFrameStore)

#: Frames looked at per step.
CHUNK = 1 << 19


def frame_keys(store: RawFrameStore, start: int, end: int) -> np.ndarray:
    """One number per frame in ``start:end`` that two frames share exactly
    when they are of the same kind."""
    ids = np.frombuffer(store.arb_ids, dtype=np.uint32)[start:end].astype(np.uint64)
    channels = np.frombuffer(store.channels, dtype=np.uint8)[start:end].astype(np.uint64)
    flags = np.frombuffer(store.flags, dtype=np.uint8)[start:end]
    kind = flags & np.uint8(FLAG_EXTENDED | FLAG_LIN | FLAG_ERROR | FLAG_REMOTE)
    return ids | (channels << np.uint64(32)) | (kind.astype(np.uint64) << np.uint64(40))


class PreviousFrames:
    """The index of each frame's previous frame of the same kind, -1 for the
    first of its kind; built by calling step() until it returns True.

    Holds the store weakly: a measurement closed and dropped half-way stops
    the work instead of being kept alive by it."""

    def __init__(self, store: RawFrameStore) -> None:
        self._store = weakref.ref(store)
        self._count = len(store)
        self._done = 0
        self.previous = np.full(self._count, -1,
                                dtype=np.int32 if self._count < 2 ** 31 else np.int64)
        # The last frame seen of each kind so far: keys sorted, and indices.
        self._keys = np.empty(0, dtype=np.uint64)
        self._last = np.empty(0, dtype=np.int64)

    @property
    def ready(self) -> bool:
        return self._done >= self._count

    @property
    def count(self) -> int:
        return self._count

    @property
    def progress(self) -> float:
        """Share of the frames done, 0 to 1."""
        return self._done / self._count if self._count else 1.0

    def step(self) -> bool:
        """Work out the next chunk; True once every frame is done."""
        if self.ready:
            return True
        store = self._store()
        if store is None or len(store) != self._count:     # closed
            raise RuntimeError('the measurement was closed')
        start = self._done
        end = min(start + CHUNK, self._count)
        keys = frame_keys(store, start, end)
        order = np.argsort(keys, kind='stable')
        ordered = keys[order]
        # In key order, a frame's previous one of its kind is the one before
        # it, when that one has the same key.
        same = np.zeros(len(ordered), dtype=bool)
        same[1:] = ordered[1:] == ordered[:-1]
        previous = np.full(len(ordered), -1, dtype=np.int64)
        previous[1:] = order[:-1] + start
        previous[~same] = -1
        # The first of a kind in this chunk: the last one seen before it.
        first = np.flatnonzero(~same)
        if len(self._keys):
            at = np.searchsorted(self._keys, ordered[first])
            at_clipped = np.minimum(at, len(self._keys) - 1)
            seen = (at < len(self._keys)) & (self._keys[at_clipped] == ordered[first])
            previous[first[seen]] = self._last[at_clipped[seen]]
        self.previous[order + start] = previous
        # Carry the last frame of each kind on to the next chunk.
        last = np.ones(len(ordered), dtype=bool)
        last[:-1] = ordered[1:] != ordered[:-1]
        keys_all = np.concatenate([self._keys, ordered[last]])
        last_all = np.concatenate([self._last, order[last] + start])
        merged = np.lexsort((last_all, keys_all))
        keys_all, last_all = keys_all[merged], last_all[merged]
        keep = np.ones(len(keys_all), dtype=bool)
        keep[:-1] = keys_all[1:] != keys_all[:-1]
        self._keys, self._last = keys_all[keep], last_all[keep]
        self._done = end
        return self.ready

    def build(self) -> np.ndarray:
        """Every chunk at once; the index."""
        while not self.step():
            pass
        return self.previous


#: The index of each open store, worked out once however often the Trace opens.
_INDEXES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def previous_frames(store: RawFrameStore) -> PreviousFrames:
    """The store's index, shared, finished or not."""
    index = _INDEXES.get(store)
    if index is None or index.count != len(store):
        index = _INDEXES[store] = PreviousFrames(store)
    return index


def window_differences(store: RawFrameStore, previous: np.ndarray, indices,
                       rows: np.ndarray | None = None):
    """For the frames *indices*, given the *previous* index: the time since
    the previous frame of the same kind, in seconds (NaN for the first), and
    which data bytes differ from it, as a ``(k, 64)`` bool array (all False
    for the first). A byte past the shorter of the two frames counts as
    changed. *rows* are the frames' payload rows, when already read."""
    indices = np.asarray(indices, dtype=np.int64)
    before = np.asarray(previous[indices], dtype=np.int64)
    known = before >= 0
    times = np.frombuffer(store.timestamps, dtype=np.float64)
    delta = np.full(len(indices), np.nan)
    delta[known] = times[indices[known]] - times[before[known]]
    changed = np.zeros((len(indices), 64), dtype=bool)
    if known.any() and store.payload_mapped:
        now = store.rows(indices) if rows is None else rows
        then = store.rows(before[known])
        # The store keeps each frame's data length (CAN FD codes already
        # turned into bytes), so it bounds the bytes compared.
        dlcs = np.frombuffer(store.dlcs, dtype=np.uint8)
        lengths_now = np.minimum(dlcs[indices[known]], 64).astype(np.int64)
        lengths_then = np.minimum(dlcs[before[known]], 64).astype(np.int64)
        column = np.arange(64)
        inside = column < lengths_now[:, None]
        differs = (now[known] != then) | (column >= lengths_then[:, None])
        # A remote frame carries no data, whatever length it asks for.
        flags = np.frombuffer(store.flags, dtype=np.uint8)[indices[known]]
        inside &= ((flags & np.uint8(FLAG_REMOTE)) == 0)[:, None]
        changed[known] = inside & differs
    return delta, changed
