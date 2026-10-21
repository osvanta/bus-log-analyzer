# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

import array as _array
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from core.bus_types import (
    BusChannel,
    BusType,
    channel_label,
    coerce_channel_key,
    sort_key,
    store_key_prefix,
)
from core.models import RawFrame
from core.models import DecodedSignalSample


#: Normalise a channel value to a bus-tagged key. Re-exported from
#: core.bus_types so callers that already import from the store keep working
#: and there is only one definition of "a bare integer means CAN".
as_channel_key = coerce_channel_key


def _byte_view(arr) -> memoryview:
    """Return *arr* as float64 bytes, without copying when it already is."""
    return memoryview(np.ascontiguousarray(arr, dtype=np.float64)).cast("B")


def _heap_copy(arr) -> _array.array:
    """A growable float64 copy of *arr* in the heap."""
    copy = _array.array("d")
    copy.frombytes(_byte_view(arr))
    return copy


class ChoiceLabels:
    """
    The display labels of an enum signal, read from its numeric values.

    One label per sample, as the decoder's lookup table gives it: the choice
    text where the value has one, the value itself where it has none, and ''
    where the value is NaN. Nothing is kept per sample, so the labels cost no
    memory however long the signal, and they follow the values wherever the
    store keeps them.
    """

    __slots__ = ("_values", "_lut", "_valid")

    _CHUNK = 65_536

    def __init__(self, values, lut: np.ndarray, valid: np.ndarray) -> None:
        self._values = values
        self._lut = lut          # object array: label per integer value
        self._valid = valid      # bool array: whether that value has a label

    def rebase(self, values) -> "ChoiceLabels":
        """The same choices over *values*."""
        return ChoiceLabels(values, self._lut, self._valid)

    def same_choices(self, other) -> bool:
        return (
            isinstance(other, ChoiceLabels)
            and len(other._lut) == len(self._lut)
            and bool(np.array_equal(other._valid, self._valid))
            and all(
                a == b for a, b, ok in zip(other._lut, self._lut, self._valid) if ok
            )
        )

    def _labels(self, numeric) -> np.ndarray:
        numeric = np.asarray(numeric, dtype=np.float64)
        nan_mask = np.isnan(numeric)
        with np.errstate(invalid="ignore"):
            int_arr = numeric.astype(np.int64)
        max_key = len(self._lut) - 1
        safe_idx = np.clip(int_arr, 0, max_key)
        in_range = ~nan_mask & (int_arr >= 0) & (int_arr <= max_key)
        valid = self._valid[safe_idx]
        result = np.empty(len(numeric), dtype=object)
        result[nan_mask] = ''
        result[~nan_mask & ~in_range] = numeric[~nan_mask & ~in_range]
        result[in_range & ~valid] = numeric[in_range & ~valid]
        has_val = in_range & valid
        if has_val.any():
            result[has_val] = self._lut[safe_idx[has_val]]
        return result

    def __len__(self) -> int:
        return len(self._values)

    def __bool__(self) -> bool:
        return len(self._values) > 0

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._labels(self._values[index]).tolist()
        value = self._values[index]   # IndexError as a list would
        return self._labels(np.array([value], dtype=np.float64))[0]

    def __iter__(self):
        for start in range(0, len(self._values), self._CHUNK):
            yield from self._labels(self._values[start:start + self._CHUNK]).tolist()

    def __array__(self, dtype=None, copy=None):
        labels = self._labels(self._values)
        return labels if dtype in (None, object) else labels.astype(dtype)


@dataclass(slots=True)
class SignalSeries:
    channel: BusChannel | None
    message_name: str
    message_id: int
    signal_name: str
    unit: str
    # array.array('d') uses ~3× less memory than a Python float list and
    # appends in amortised O(1) without Python object overhead per element.
    timestamps: _array.array = field(default_factory=lambda: _array.array('d'))
    values: _array.array     = field(default_factory=lambda: _array.array('d'))
    # raw_values is populated only for signals that carry display labels
    # (DBC enum/choice signals, MDF text channels). For ordinary numeric
    # signals it stays empty — consumers fall back to values[idx].
    raw_values: Sequence[object] = field(default_factory=list)
    has_labels: bool = False

    @property
    def key(self) -> str:
        return (
            f"{store_key_prefix(self.channel)}"
            f"::{self.message_name}::{self.signal_name}"
        )

    @property
    def sample_count(self) -> int:
        """Number of samples, known without reading them for a lazy series."""
        return len(self.timestamps)

    @property
    def latest_value(self) -> object:
        if self.raw_values:
            return self.raw_values[-1]
        if len(self.values):
            return self.values[-1]
        return ""

    def display_value_at(self, idx: int) -> object:
        """
        Display value for sample *idx*.
        For label-bearing signals returns the cached label; for plain numeric
        signals returns the float (kept in `values` to save RAM).
        """
        if self.has_labels and 0 <= idx < len(self.raw_values):
            return self.raw_values[idx]
        if 0 <= idx < len(self.values):
            return self.values[idx]
        return ""

    def numpy_timestamps(self) -> np.ndarray:
        """Return timestamps as a numpy float64 array (zero-copy if possible)."""
        return np.frombuffer(self.timestamps, dtype=np.float64)

    def numpy_values(self) -> np.ndarray:
        """Return values as a numpy float64 array (zero-copy if possible)."""
        return np.frombuffer(self.values, dtype=np.float64)


# The slots SignalSeries keeps its samples in. LazySignalSeries puts
# properties in front of them and reaches the storage through these.
_TIMESTAMPS = SignalSeries.__dict__["timestamps"]
_VALUES = SignalSeries.__dict__["values"]
_RAW_VALUES = SignalSeries.__dict__["raw_values"]
_HAS_LABELS = SignalSeries.__dict__["has_labels"]


class LazySignalSeries(SignalSeries):
    """
    A series whose samples stay in the measurement file until first read.

    Reading ``timestamps``, ``values``, ``raw_values`` or ``has_labels`` asks
    the store's :class:`SampleCache` to read the series' channel group. The
    cache keeps recently read groups up to a memory budget and forgets the
    oldest, which puts their series back in the file: the next read loads
    them again. Memory therefore follows what is in use, not the file size.

    Assigning one of the sample fields detaches the series from the cache,
    so it then behaves as an ordinary in-memory series.
    """

    __slots__ = ("_cache", "_groups", "_sample_count")

    def __init__(
        self,
        channel: BusChannel | None,
        message_name: str,
        message_id: int,
        signal_name: str,
        unit: str,
        cache: "SampleCache",
        group: int,
        sample_count: int,
    ) -> None:
        self.channel = channel
        self.message_name = message_name
        self.message_id = message_id
        self.signal_name = signal_name
        self.unit = unit
        _TIMESTAMPS.__set__(self, None)
        _VALUES.__set__(self, None)
        _RAW_VALUES.__set__(self, None)
        _HAS_LABELS.__set__(self, False)
        self._cache: SampleCache | None = cache
        # Channel groups holding this series, in file order. More than one
        # when several groups carry a channel of the same group and name.
        self._groups: list[int] = [group]
        self._sample_count = sample_count

    @property
    def is_loaded(self) -> bool:
        return _TIMESTAMPS.__get__(self) is not None

    @property
    def sample_count(self) -> int:
        return self._sample_count

    def _field(self, slot):
        value = slot.__get__(self)
        cache = self._cache
        if cache is None:
            return _empty_field(slot) if value is None else value
        if value is None:
            # Read and returned under the cache's lock: the group may be
            # forgotten again by another thread straight after.
            return cache.load_field(self, slot)
        cache.touch(self)
        return value

    @property
    def timestamps(self):
        return self._field(_TIMESTAMPS)

    @timestamps.setter
    def timestamps(self, value) -> None:
        self._detach()
        _TIMESTAMPS.__set__(self, value)

    @property
    def values(self):
        return self._field(_VALUES)

    @values.setter
    def values(self, value) -> None:
        self._detach()
        _VALUES.__set__(self, value)

    @property
    def raw_values(self):
        return self._field(_RAW_VALUES)

    @raw_values.setter
    def raw_values(self, value) -> None:
        self._detach()
        _RAW_VALUES.__set__(self, value)

    @property
    def has_labels(self) -> bool:
        if self._cache is not None and not self.is_loaded:
            self._cache.load_field(self, _TIMESTAMPS)
        return _HAS_LABELS.__get__(self)

    @has_labels.setter
    def has_labels(self, value: bool) -> None:
        _HAS_LABELS.__set__(self, value)

    def _detach(self) -> None:
        """Keep the samples in memory for good: the cache no longer owns them."""
        if self._cache is not None:
            self._cache.detach(self)


#: Fallbacks for SampleCache's budget when the physical memory is unknown.
_CACHE_BUDGET_MIN = 256 * 1024 * 1024
_CACHE_BUDGET_MAX = 2 * 1024 * 1024 * 1024


def default_cache_budget() -> int:
    """An eighth of physical memory, between 256 MB and 2 GB.

    Only a cost/speed trade: a group forgotten is read again when next used,
    which for a typical group takes a fraction of a second.
    """
    total = 0
    try:
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            total = int(status.ullTotalPhys)
    except (AttributeError, OSError):
        pass
    if not total:
        try:
            import os
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (AttributeError, ValueError, OSError):
            return _CACHE_BUDGET_MIN
    return max(_CACHE_BUDGET_MIN, min(_CACHE_BUDGET_MAX, total // 8))


class SampleCache:
    """
    Reads lazy series from their measurement file, keeping a bounded amount.

    *source* reads channel groups: ``read_groups(group_indices)`` returns
    ``{(group_idx, channel_name): [(timestamps, values, display_values,
    unit), ...]}`` with raw (not yet normalised) timestamps, and ``close()``
    releases the file. Every read is one *load*: the groups a series needs,
    plus every other series those groups complete. Loads are forgotten oldest
    first once their samples pass *budget* bytes; the newest load is always
    kept, however large.

    Thread-safe: the GUI thread plots while a calculation thread reads.
    """

    def __init__(self, source, base_ts: float, budget: int | None = None) -> None:
        import threading
        import weakref

        self._source = source
        self._base_ts = base_ts
        self.budget = default_cache_budget() if budget is None else budget
        self._lock = threading.RLock()
        self._series_by_group: dict[int, list[LazySignalSeries]] = defaultdict(list)
        # Insertion order is age: oldest load first.
        self._loads: dict[int, tuple[list[LazySignalSeries], int]] = {}
        self._load_of: dict[int, int] = {}   # id(series) -> its load's number
        self._next_load = 0
        self.resident_bytes = 0
        self.reads = 0
        self._last_ts: tuple | None = None
        self._bounds: dict[int, tuple[float, float]] = {}
        # The file stays open until the store is closed or collected.
        self._finalizer = weakref.finalize(self, _close_source, source)

    # ── Registration (load worker) ────────────────────────────────────────

    def register(self, series: LazySignalSeries) -> None:
        for group in series._groups:
            self._series_by_group[group].append(series)

    def add_group(self, series: LazySignalSeries, group: int, sample_count: int) -> None:
        """Extend *series* with another group's channel of the same name."""
        series._groups.append(group)
        series._sample_count += sample_count
        self._series_by_group[group].append(series)

    # ── Reading ──────────────────────────────────────────────────────────

    def load_field(self, series: LazySignalSeries, slot):
        with self._lock:
            value = slot.__get__(series)
            if value is not None:
                return value
            if self._source is None or series._cache is not self:
                # The measurement was closed: its signals have nothing left.
                return _empty_field(slot)
            self._load(series)
            return slot.__get__(series)

    def _load(self, wanted: LazySignalSeries) -> None:
        groups = set(wanted._groups)
        # Reading a whole group costs one pass for all its channels. A group
        # too large for a quarter of the budget is read one channel at a
        # time instead, so no file layout can make a single read unbounded.
        group_bytes = getattr(self._source, "group_bytes", None)
        only = None
        if group_bytes is not None and (
            sum(group_bytes(g) for g in groups) > self.budget // 4
        ):
            only = {wanted.signal_name}
        # A group's other series may also need groups of their own; read only
        # what this one needs and fill every series the read completes.
        results = self._source.read_groups(sorted(groups), only)
        self.reads += 1
        filled: list[LazySignalSeries] = []
        arrays: dict[int, int] = {}   # id -> bytes; a shared time axis counts once
        seen: set[int] = set()
        for group in sorted(groups):
            for series in self._series_by_group.get(group, ()):
                if id(series) in seen or series._cache is not self:
                    continue
                seen.add(id(series))
                if series.is_loaded or not groups.issuperset(series._groups):
                    continue
                if only is not None and series is not wanted:
                    continue
                for array in self._fill(series, results):
                    arrays[id(array)] = array.nbytes
                filled.append(series)
        size = sum(arrays.values())
        number = self._next_load
        self._next_load += 1
        self._loads[number] = (filled, size)
        for series in filled:
            self._load_of[id(series)] = number
        self.resident_bytes += size
        self._evict(keep=number)

    def _fill(self, series: LazySignalSeries, results) -> tuple[np.ndarray, np.ndarray]:
        """Give *series* its samples from *results*; return its two arrays."""
        parts = []
        unit = None
        for group in series._groups:
            parts.extend(results.get((group, series.signal_name), ()))
        if not parts:
            ts = np.empty(0, dtype=np.float64)
            vs = np.empty(0, dtype=np.float64)
            raw: Sequence[object] = []
        elif len(parts) == 1:
            ts, vs, raw, unit = parts[0]
            ts = self._normalised(ts)
        else:
            ts = np.concatenate([self._normalised(p[0]) for p in parts])
            vs = np.concatenate([np.asarray(p[1], dtype=np.float64) for p in parts])
            raw = []
            if any(len(p[2]) for p in parts):
                for p in parts:
                    raw.extend(p[2] if len(p[2]) else [float(v) for v in p[1]])
            unit = parts[0][3]
            if len(ts) > 1 and np.any(np.diff(ts) < 0):
                order = np.argsort(ts, kind="stable")
                ts, vs = ts[order], vs[order]
                if raw:
                    raw = [raw[i] for i in order.tolist()]
        vs = np.asarray(vs, dtype=np.float64)
        _TIMESTAMPS.__set__(series, ts)
        _VALUES.__set__(series, vs)
        _RAW_VALUES.__set__(series, raw if len(raw) else [])
        _HAS_LABELS.__set__(series, bool(len(raw)))
        if unit:
            series.unit = unit
        series._sample_count = len(ts)
        return ts, vs

    def _normalised(self, ts) -> np.ndarray:
        # Channels of one group share their time axis, but asammdf hands each
        # its own copy. Keep one normalised array for all of them: remember
        # the last source array and its result.
        last = self._last_ts
        if last is not None and (
            last[0] is ts or (len(last[0]) == len(ts) and np.array_equal(last[0], ts))
        ):
            return last[1]
        normalised = np.asarray(ts, dtype=np.float64) - self._base_ts
        self._last_ts = (ts, normalised)
        return normalised

    def _evict(self, keep: int) -> None:
        self._last_ts = None
        for number in list(self._loads):
            if self.resident_bytes <= self.budget:
                break
            if number == keep:
                continue
            filled, size = self._loads.pop(number)
            self.resident_bytes -= size
            for series in filled:
                self._load_of.pop(id(series), None)
                _TIMESTAMPS.__set__(series, None)
                _VALUES.__set__(series, None)
                _RAW_VALUES.__set__(series, None)

    def touch(self, series: LazySignalSeries) -> None:
        """Mark *series*' load as the most recently used."""
        with self._lock:
            number = self._load_of.get(id(series))
            if number is not None and number in self._loads:
                self._loads[number] = self._loads.pop(number)

    def detach(self, series: LazySignalSeries) -> None:
        with self._lock:
            if series._cache is not self:
                return
            if not series.is_loaded and self._source is not None:
                self._load(series)
            number = self._load_of.pop(id(series), None)
            if number is not None and number in self._loads:
                # Its values leave the budget; a time axis it shares stays.
                filled, size = self._loads[number]
                own = _VALUES.__get__(series).nbytes
                filled = [s for s in filled if s is not series]
                self._loads[number] = (filled, size - own)
                self.resident_bytes -= own
            for group in series._groups:
                members = self._series_by_group.get(group)
                if members and series in members:
                    members.remove(series)
            series._cache = None

    # ── Time axes without reading samples ─────────────────────────────────

    def lazy_groups(self) -> list[int]:
        with self._lock:
            return sorted(
                group for group, members in self._series_by_group.items()
                if any(s._cache is self for s in members)
            )

    def group_bounds(self, group: int) -> tuple[float, float] | None:
        """First and last normalised time stamp of *group*, from two records."""
        with self._lock:
            bounds = self._bounds.get(group)
            if bounds is not None or self._source is None:
                return bounds
            cycles = self._source.cycles(group)
            if cycles <= 0:
                return None
            try:
                first = self._source.master(group, 0, 1)
                last = self._source.master(group, cycles - 1, 1)
            except Exception:
                return None
            if not len(first) or not len(last):
                return None
            bounds = (
                float(first[0]) - self._base_ts, float(last[-1]) - self._base_ts
            )
            self._bounds[group] = bounds
            return bounds

    def group_time_axis(self, group: int) -> np.ndarray:
        """*group*'s normalised time stamps; read now, not kept."""
        with self._lock:
            if self._source is None:
                return np.empty(0, dtype=np.float64)
            return self._source.master(group) - self._base_ts

    @property
    def is_closed(self) -> bool:
        return self._source is None

    def close(self) -> None:
        """Release the measurement file. Series not yet read stay empty."""
        with self._lock:
            self._source = None
            self._finalizer()


def _close_source(source) -> None:
    try:
        source.close()
    except Exception:
        pass


def _empty_field(slot):
    if slot is _RAW_VALUES:
        return []
    return np.empty(0, dtype=np.float64)


class SignalStore:
    def __init__(self) -> None:
        self._series_by_key: dict[str, SignalSeries] = {}
        self._signals_by_channel_message: dict[BusChannel | None, dict[str, list[str]]] = (
            defaultdict(lambda: defaultdict(list))
        )
        # Hot-path cache: (channel, msg_name) → ordered list of SignalSeries refs.
        # Built lazily on first decoded frame for each (channel, msg_name); reused
        # for every subsequent frame so the inner loop avoids string-key concat
        # and dict lookup per signal sample.
        self._msg_series_cache: dict[tuple[BusChannel | None, str], list[SignalSeries]] = {}
        # Per-(channel, msg_name, sig_name) choices map provided by the decoder(s).
        # An empty/missing entry means "no labels" — series.has_labels stays False
        # and raw_values is never appended to.
        self._choices_lookup: dict[tuple[BusChannel | None, str, str], dict] = {}
        self._tree_dirty: bool = True
        # Series whose bulk inserts arrived out of time order; see
        # sort_merged_series().
        self._unsorted_keys: set[str] = set()
        # Channels of one MDF group share one time axis. The last bulk insert's
        # source array and its stored copy let the next channel on the same
        # axis reuse that copy instead of storing the timestamps again; the
        # keys in _shared_ts_keys copy their timestamps before appending.
        self._last_bulk_ts: tuple[np.ndarray, _array.array, str] | None = None
        self._shared_ts_keys: set[str] = set()
        self.total_frames    = 0
        self.decoded_frames  = 0
        self.total_samples   = 0
        self.channels: set[BusChannel | None] = set()
        self.channel_frame_counts: dict[BusChannel | None, int] = defaultdict(int)
        self.message_hits: dict[tuple[BusChannel | None, str], int] = defaultdict(int)
        self.unmatched_frames = 0
        self.first_frame_ids: list[str] = []
        self.diagnostics_text: str = ""
        # Non-fatal reader problems. Valid channels remain available and the
        # GUI presents these entries after the measurement finishes loading.
        self.load_warnings: list[str] = []
        # On-disk indexed frame store (Option B: no cap, temp file on disk)
        self.raw_frame_store = None   # set to RawFrameStore by LoadWorker
        self.base_ts: float = 0.0  # set by LoadWorker for streaming
        # Reads lazy series from the measurement file; see add_lazy_series().
        self.sample_cache: SampleCache | None = None
        # Bulk samples past this many bytes go to a temporary file instead of
        # the heap; see _extend() and core.sample_file.
        self.memory_budget = default_cache_budget()
        self._heap_sample_bytes = 0
        self._sample_file = None
        self._sample_file_refused = False

    # ── Decoder integration (called once by LoadWorker) ──────────────────

    def set_choices_lookup(
        self,
        lookup: dict[tuple[BusChannel | int | None, str, str], dict],
    ) -> None:
        """
        Provide the per-signal DBC choices map keyed by
        ``(channel, message_name, signal_name) → {int_key: label}``.
        Used at series-creation time to decide whether to maintain
        ``raw_values`` (label-bearing signals only).

        Channel keys are normalised on the way in. The raw-CAN loops build this
        lookup from integer channels; without normalising, every lookup against
        a bus-tagged key would miss and enum signals would silently lose their
        text labels.
        """
        self._choices_lookup = {
            (as_channel_key(channel), message_name, signal_name): choices
            for (channel, message_name, signal_name), choices in lookup.items()
        }

    # ── Tree-dirty bookkeeping ────────────────────────────────────────────

    def is_tree_dirty(self) -> bool:
        return self._tree_dirty

    def note_frame(
        self,
        frame: RawFrame,
        decoded: bool = False,
        bus: BusType = BusType.CAN,
    ) -> None:
        self.total_frames += 1
        channel = as_channel_key(frame.channel, bus)
        self.channels.add(channel)
        self.channel_frame_counts[channel] += 1
        if not decoded:
            self.unmatched_frames += 1
        if len(self.first_frame_ids) < 20:
            id_hex = (
                f"0x{frame.arbitration_id:08X}"
                if (frame.is_extended_id or frame.arbitration_id > 0x7FF)
                else f"0x{frame.arbitration_id:03X}"
            )
            self.first_frame_ids.append(
                f"{store_key_prefix(channel)} | {id_hex} | "
                f"DLC={frame.dlc} | {frame.direction}"
            )

    def add_samples(self, samples: Iterable[DecodedSignalSample]) -> None:
        sample_list = list(samples)
        if sample_list:
            self.decoded_frames  += 1
            self.unmatched_frames = max(0, self.unmatched_frames - 1)
        for sample in sample_list:
            channel = as_channel_key(sample.channel)
            self.channels.add(channel)
            self.message_hits[(channel, sample.message_name)] += 1
            key = self._make_key(channel, sample.message_name, sample.signal_name)
            if key not in self._series_by_key:
                series = SignalSeries(
                    channel=channel,
                    message_name=sample.message_name,
                    message_id=sample.message_id,
                    signal_name=sample.signal_name,
                    unit=sample.unit,
                    has_labels=bool(self._choices_lookup.get(
                        (channel, sample.message_name, sample.signal_name)
                    )),
                )
                self._series_by_key[key] = series
                sigs = self._signals_by_channel_message[channel][sample.message_name]
                if sample.signal_name not in sigs:
                    sigs.append(sample.signal_name)
                self._tree_dirty = True
            series = self._series_by_key[key]
            if key in self._shared_ts_keys:
                self._own_timestamps(key, series)
            if not isinstance(series.values, _array.array):
                self._make_appendable(series)
            series.timestamps.append(sample.timestamp)
            if series.has_labels:
                series.raw_values.append(sample.value)
            numeric = getattr(sample, 'numeric_value', None)
            if numeric is not None:
                series.values.append(numeric)
            else:
                try:
                    series.values.append(float(sample.value))
                except (TypeError, ValueError):
                    series.values.append(float('nan'))
            self.total_samples += 1

    def add_samples_direct(self, samples: list) -> None:
        """
        Hot path used by the BLF/ASC streaming decoder.

        All samples in *samples* belong to the same decoded message (same
        channel + message_name). The first call for a given (channel,
        message_name) builds and caches the SignalSeries reference list;
        subsequent calls walk the cached list directly — no string-key
        construction, no per-sample dict lookups.
        """
        if not samples:
            return
        self.decoded_frames += 1
        self.unmatched_frames = max(0, self.unmatched_frames - 1)

        first = samples[0]
        channel  = as_channel_key(first.channel)
        msg_name = first.message_name
        msg_key  = (channel, msg_name)

        cached = self._msg_series_cache.get(msg_key)
        # Cache miss OR signal-set changed (rare — e.g. multiplexed messages)
        if cached is None or len(cached) != len(samples):
            cached = self._build_msg_cache(channel, msg_name, samples)

        n = len(samples)
        self.total_samples += n
        self.channels.add(channel)
        self.message_hits[msg_key] += 1

        # Tight inner loop — only the work that scales with sample count.
        for series, sample in zip(cached, samples):
            series.timestamps.append(sample.timestamp)
            series.values.append(sample.numeric_value)
            if series.has_labels:
                series.raw_values.append(sample.value)

    def _build_msg_cache(
        self,
        channel: BusChannel | None,
        msg_name: str,
        samples: list,
    ) -> list[SignalSeries]:
        """Create / look up SignalSeries for each sample's signal and cache them."""
        sigs_for_msg = self._signals_by_channel_message[channel][msg_name]
        choices_lookup = self._choices_lookup
        series_list: list[SignalSeries] = []
        for sample in samples:
            sig_name = sample.signal_name
            str_key = self._make_key(channel, msg_name, sig_name)
            series = self._series_by_key.get(str_key)
            if series is None:
                series = SignalSeries(
                    channel=channel,
                    message_name=msg_name,
                    message_id=sample.message_id,
                    signal_name=sig_name,
                    unit=sample.unit,
                    has_labels=bool(
                        choices_lookup.get((channel, msg_name, sig_name))
                        or choices_lookup.get((None, msg_name, sig_name))
                    ),
                )
                self._series_by_key[str_key] = series
                if sig_name not in sigs_for_msg:
                    sigs_for_msg.append(sig_name)
                self._tree_dirty = True
            else:
                if str_key in self._shared_ts_keys:
                    self._own_timestamps(str_key, series)
                if not isinstance(series.values, _array.array):
                    self._make_appendable(series)
            series_list.append(series)
        self._msg_series_cache[(channel, msg_name)] = series_list
        return series_list

    def add_series_bulk(
        self,
        channel: BusChannel | int | None,
        message_name: str,
        message_id: int,
        signal_name: str,
        unit: str,
        timestamps: "np.ndarray",
        values: "np.ndarray",
        raw_values: Sequence[object],
        has_labels: bool = True,
    ) -> None:
        """
        Insert a complete channel time-series in one bulk operation.

        Uses ``array.array.frombytes(ndarray)`` which is a single C-level
        memcopy — no per-sample Python loop, no temporary list. A new series
        whose *timestamps* is the very array the previous insert received
        (the channels of one MDF group) shares that series' stored copy.
        Called by the LoadWorker MDF/CSV fast path and the BLF/ASC vectorised
        decode path.

        Parameters
        ----------
        timestamps : float64 ndarray, already normalised (t=0 at recording start)
        values     : float64 ndarray of numeric values (integer keys for enum)
        raw_values : list of display values (string labels or floats); may be
                     empty when *has_labels* is False
        has_labels : True for signals whose display value differs from the
                     numeric value (DBC enums, MDF text channels). When False
                     the series is treated as plain numeric and consumers fall
                     back to ``values[idx]`` for display.
        """
        n = len(timestamps)
        if n == 0:
            return

        channel = as_channel_key(channel)
        key = self._make_key(channel, message_name, signal_name)
        if key not in self._series_by_key:
            series = SignalSeries(
                channel=channel,
                message_name=message_name,
                message_id=message_id,
                signal_name=signal_name,
                unit=unit,
                has_labels=has_labels,
            )
            self._series_by_key[key] = series
            sigs = self._signals_by_channel_message[channel][message_name]
            if signal_name not in sigs:
                sigs.append(signal_name)
            self._tree_dirty = True
        else:
            series = self._series_by_key[key]
            if len(series.timestamps) and timestamps[0] < series.timestamps[-1]:
                self._unsorted_keys.add(key)
            if key in self._shared_ts_keys:
                self._own_timestamps(key, series)
            # The recorded copy may be the one about to grow.
            self._last_bulk_ts = None

        # C-level memcopy straight from the array buffer — no Python loop,
        # no intermediate bytes object the size of the series. Past the
        # memory budget the copy goes to the sample file instead.
        last = self._last_bulk_ts
        if not len(series.timestamps) and last is not None and last[0] is timestamps:
            series.timestamps = last[1]
            self._shared_ts_keys.add(key)
            self._shared_ts_keys.add(last[2])
        else:
            series.timestamps = self._extend(series.timestamps, timestamps)
            if len(series.timestamps) == n and isinstance(timestamps, np.ndarray):
                self._last_bulk_ts = (timestamps, series.timestamps, key)
        series.values = self._extend(series.values, values)
        if has_labels and isinstance(raw_values, ChoiceLabels) and (
            len(series.raw_values) + len(raw_values) == len(series.values)
            and (
                not len(series.raw_values)
                or raw_values.same_choices(series.raw_values)
            )
        ):
            # Labels are read from the stored values, wherever they are.
            series.raw_values = raw_values.rebase(series.values)
        elif has_labels and raw_values:
            if not series.raw_values and not isinstance(raw_values, list):
                # MDF native extraction supplies a lazy text sequence so the
                # signal list is not blocked by millions of byte->str decodes.
                series.raw_values = raw_values
            else:
                if not hasattr(series.raw_values, "extend"):
                    series.raw_values = list(series.raw_values)
                series.raw_values.extend(raw_values)

        self.total_samples  += n
        self.decoded_frames += 1        # one "frame" per bulk channel insert
        self.channels.add(channel)
        self.message_hits[(channel, message_name)] += n

    def add_lazy_series(
        self,
        channel: BusChannel | int | None,
        message_name: str,
        message_id: int,
        signal_name: str,
        unit: str,
        group: int,
        sample_count: int,
    ) -> None:
        """
        List a channel whose samples stay in the file until first read.

        The samples are read through ``self.sample_cache``, which must be set
        first. A second channel with the same key (another group of the same
        name) joins the first: the series then reads both groups and puts
        their samples in time order, as add_series_bulk does when it merges.
        """
        if sample_count <= 0:
            return
        cache = self.sample_cache
        if cache is None:
            raise RuntimeError("add_lazy_series needs a sample_cache")
        channel = as_channel_key(channel)
        key = self._make_key(channel, message_name, signal_name)
        series = self._series_by_key.get(key)
        if isinstance(series, LazySignalSeries) and series._cache is cache:
            if group not in series._groups:
                cache.add_group(series, group, sample_count)
        elif series is None:
            series = LazySignalSeries(
                channel, message_name, message_id, signal_name, unit,
                cache, group, sample_count,
            )
            cache.register(series)
            self._series_by_key[key] = series
            sigs = self._signals_by_channel_message[channel][message_name]
            if signal_name not in sigs:
                sigs.append(signal_name)
            self._tree_dirty = True
        else:
            raise ValueError(f"{key} is already an in-memory series")
        self.total_samples  += sample_count
        self.decoded_frames += 1        # one "frame" per channel, as in bulk
        self.channels.add(channel)
        self.message_hits[(channel, message_name)] += sample_count

    def time_bounds(self) -> tuple[float | None, float | None]:
        """
        Earliest and latest time stamp across every signal.

        A lazy series that has not been read answers from its group's first
        and last record, so this never reads a measurement's samples.
        """
        lo = hi = None
        cache = self.sample_cache
        lazy_groups: set[int] = set()
        for series in self._series_by_key.values():
            if (
                isinstance(series, LazySignalSeries)
                and series._cache is cache
                and cache is not None
            ):
                lazy_groups.update(series._groups)
                continue
            ts = series.numpy_timestamps()
            if not len(ts):
                continue
            t0, t1 = float(np.nanmin(ts)), float(np.nanmax(ts))
            lo = t0 if lo is None else min(lo, t0)
            hi = t1 if hi is None else max(hi, t1)
        for group in sorted(lazy_groups):
            bounds = cache.group_bounds(group)
            if bounds is None:
                continue
            lo = bounds[0] if lo is None else min(lo, bounds[0])
            hi = bounds[1] if hi is None else max(hi, bounds[1])
        return lo, hi

    def time_axes(self) -> list[SignalSeries]:
        """
        One series per distinct time axis, for the calculated Time signal.

        Channels sharing a time stamp array count once, and a lazy group gives
        its time channel alone: its other channels are not read.
        """
        axes: list[SignalSeries] = []
        seen: set[int] = set()
        cache = self.sample_cache
        lazy_groups: set[int] = set()
        for series in self._series_by_key.values():
            if (
                isinstance(series, LazySignalSeries)
                and series._cache is cache
                and cache is not None
            ):
                lazy_groups.update(series._groups)
                continue
            ts = series.numpy_timestamps()
            if len(ts) and id(series.timestamps) not in seen:
                seen.add(id(series.timestamps))
                axes.append(series)
        for group in sorted(lazy_groups):
            ts = cache.group_time_axis(group)
            if len(ts):
                axes.append(SignalSeries(None, "", 0, "", "", ts, ts))
        return axes

    @property
    def sample_file_bytes(self) -> int:
        """Bytes of samples kept in the temporary sample file."""
        return 0 if self._sample_file is None else self._sample_file.bytes_stored

    def close(self) -> None:
        """Release the measurement file and the temporary sample file."""
        if self.sample_cache is not None:
            self.sample_cache.close()
        if self._sample_file is not None:
            self._sample_file.close()

    def _own_timestamps(self, key: str, series: SignalSeries) -> None:
        """Give *series* its own timestamps before they are appended to."""
        # Appending in place would change every series sharing the array. An
        # array in the sample file never grows in place: _extend copies it.
        if isinstance(series.timestamps, _array.array):
            series.timestamps = _array.array("d", series.timestamps)
        self._shared_ts_keys.discard(key)

    @staticmethod
    def _make_appendable(series: SignalSeries) -> None:
        """Copy file-backed samples to the heap for one-sample appends."""
        series.timestamps = _heap_copy(series.timestamps)
        series.values = _heap_copy(series.values)
        if isinstance(series.raw_values, ChoiceLabels):
            series.raw_values = list(series.raw_values)

    def _extend(self, stored, new):
        """
        Return *stored* followed by *new*, in the heap or the sample file.

        Up to ``memory_budget`` bytes of samples stay in the heap as
        ``array.array``. Beyond it, arrays go to the sample file; a series
        that grows there is copied whole to a new region.
        """
        if self._spilling():
            from core.sample_file import SampleFileFull
            try:
                return self._sample_file.store(stored, new)
            except SampleFileFull as exc:
                self._sample_file_full(exc)
        if not isinstance(stored, _array.array):
            stored = _heap_copy(stored)
        stored.frombytes(_byte_view(new))
        self._heap_sample_bytes += len(new) * 8
        return stored

    def _replace(self, array: np.ndarray):
        """Store *array* afresh (sorting), in the heap or the sample file."""
        return self._extend(_array.array("d"), array)

    def _spilling(self) -> bool:
        if self._sample_file is not None:
            return not self._sample_file.closed
        if self._sample_file_refused or self._heap_sample_bytes < self.memory_budget:
            return False
        from core.sample_file import SampleFile
        try:
            self._sample_file = SampleFile()
        except OSError as exc:
            self._sample_file_full(exc)
            return False
        return True

    def _sample_file_full(self, exc: OSError) -> None:
        """Keep the rest of the measurement in memory, and say so once."""
        self._sample_file_refused = True
        if self._sample_file is not None:
            self._sample_file.close()
            self._sample_file = None
        self.load_warnings.append(
            "The temporary folder has no room for more decoded signals "
            f"({exc}); the rest of this measurement is kept in memory, "
            "which may run out for a very large file."
        )

    def sort_merged_series(self) -> int:
        """
        Put series assembled from several bulk inserts back in time order.

        A signal fed by more than one frame ID (J1939 senders sharing one
        database message, or priority variants of one ID) is inserted one ID
        at a time, so its samples arrive as back-to-back sweeps of the whole
        recording. Plot clipping and cursor lookups binary-search the
        timestamps, which only works when they increase. Returns the number
        of series reordered.
        """
        # Every bulk loader ends here; stop holding the last source array.
        self._last_bulk_ts = None
        reordered = 0
        for key in self._unsorted_keys:
            series = self._series_by_key.get(key)
            if series is None:
                continue
            ts = series.numpy_timestamps()
            order = np.argsort(ts, kind="stable")
            series.timestamps = self._replace(ts[order])
            series.values = self._replace(series.numpy_values()[order])
            if isinstance(series.raw_values, ChoiceLabels):
                series.raw_values = series.raw_values.rebase(series.values)
            elif series.raw_values and len(series.raw_values) == len(order):
                raw_values = series.raw_values
                series.raw_values = [raw_values[i] for i in order.tolist()]
            reordered += 1
        self._unsorted_keys.clear()
        return reordered

    def normalize_timestamps(self, already_normalized: bool = False) -> None:
        """
        Shift all timestamps so that t=0 is the start of the recording.
        If already_normalized=True (inline normalisation was done by LoadWorker),
        only the raw_frames need correcting (their timestamps were set before subtraction).
        """
        if already_normalized:
            # SignalSeries timestamps AND raw_frame timestamps are already correct:
            # LoadWorker does frame.timestamp -= base_ts BEFORE calling
            # add_raw_frame(), so time_s is already 0-based at storage time.
            # Nothing to do here.
            return

        # Legacy path: full normalisation pass (used if called without LoadWorker)
        min_ts: float | None = None
        for series in self._series_by_key.values():
            if len(series.timestamps):
                local_min = min(series.timestamps)
                if min_ts is None or local_min < min_ts:
                    min_ts = local_min
        if min_ts is None:
            return
        for series in self._series_by_key.values():
            if len(series.timestamps):
                new_ts = _array.array('d', (t - min_ts for t in series.timestamps))
                series.timestamps = new_ts
        # raw_frame_store timestamps are normalised inline by LoadWorker

    def get_series(self, key: str) -> SignalSeries | None:
        return self._series_by_key.get(key)

    def all_keys(self) -> list[str]:
        return sorted(self._series_by_key.keys())

    def build_tree_payload(self) -> dict[BusChannel | None, dict[str, list[str]]]:
        payload: dict[BusChannel | None, dict[str, list[str]]] = {}
        for channel in sorted(self.channels, key=sort_key):
            payload[channel] = {}
        for channel, message_map in self._signals_by_channel_message.items():
            payload.setdefault(channel, {})
            payload[channel].update(
                {msg: sorted(sigs) for msg, sigs in sorted(message_map.items())}
            )
        self._tree_dirty = False
        return payload

    def channel_summary_text(self) -> str:
        if not self.channels:
            return 'Channels: 0'
        ordered = sorted(self.channels, key=sort_key)
        parts = [
            f"{channel_label(ch)} "
            f"({self.channel_frame_counts.get(ch, 0):,} frames)"
            for ch in ordered
        ]
        rfs = self.raw_frame_store
        total_raw = len(rfs) if rfs is not None else 0
        raw_note = f"  | CAN Trace: {total_raw:,} frames" if total_raw else ""
        return f"Channels: {len(ordered)} | " + ', '.join(parts) + raw_note

    @staticmethod
    def _make_key(
        channel: BusChannel | int | None, message_name: str, signal_name: str
    ) -> str:
        prefix = store_key_prefix(as_channel_key(channel))
        return f"{prefix}::{message_name}::{signal_name}"
