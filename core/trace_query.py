# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Search, filter and colour the CAN Trace, down to signal level.

What the user types in the Trace's Search box is a *query*: terms joined by
``and`` / ``or`` (``and`` binds first). A term is either

* a **condition** on a signal the databases define — ``Gear = Reverse``,
  ``BMS_CellTempMax > 60``, ``Msg.Sig != 0`` (``=`` ``==`` ``!=`` ``>``
  ``>=`` ``<`` ``<=``, a number or one of the signal's value labels), or
* **text**, matched as the Trace always matched it (ID, frame name, data
  bytes, direction, time; see :meth:`RawFrameStore.search`) and, besides,
  against the names of the signals each frame carries.

Nothing here runs per frame in Python. A store holds a few hundred *kinds*
of frame — one ID, on one channel, of one bus and ID type — and millions of
frames; the databases are asked about each kind once (:class:`TraceCatalog`),
and the frames of the kinds a condition concerns are decoded a chunk at a
time with the same vectorised decoder the load uses. Each search, filter and
colouring is a generator of progress steps wrapped in
:class:`core.raw_frame_store.FrameSearch`, so the Trace window answers between
chunks however large the measurement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from core.bus_types import BusType
from core.raw_frame_store import (
    FLAG_ERROR, FLAG_EXTENDED, FLAG_LIN, FLAG_REMOTE, FrameSearch,
)

#: Frames looked at per step.
CHUNK = 1 << 20

#: Bins of the Trace's time bar: where along the measurement something is.
TIME_BINS = 400

_CHANNEL_SHIFT = np.uint64(32)
_EXT_SHIFT = np.uint64(40)
_LIN_SHIFT = np.uint64(41)
#: A kind key without its channel: the same ID on any channel.
ID_MASK = ~(np.uint64(0xFF) << _CHANNEL_SHIFT)
_NOT_A_MESSAGE = np.uint8(FLAG_ERROR | FLAG_REMOTE)


# ── Frame kinds ───────────────────────────────────────────────────────────

def kind_key(arb_id: int, channel: int | None, extended: bool, lin: bool) -> int:
    """One kind of frame as a number: (LIN, extended, channel, ID) packed."""
    ch = 255 if channel is None else int(channel)
    return (int(arb_id) & 0xFFFFFFFF) | (ch << 32) | (int(bool(extended)) << 40) \
        | (int(bool(lin)) << 41)


def id_key(key: int) -> int:
    """*key* with its channel left out."""
    return int(np.uint64(key) & ID_MASK)


def split_key(key: int) -> tuple[int, int | None, bool, BusType]:
    """(ID, channel or None, extended, bus) of a kind key."""
    key = int(key)
    ch = (key >> 32) & 0xFF
    return (key & 0xFFFFFFFF, None if ch == 255 else ch,
            bool(key >> 40 & 1), BusType.LIN if key >> 41 & 1 else BusType.CAN)


def frame_keys(store, start: int, end: int) -> np.ndarray:
    """The kind key of each frame from *start* to *end*."""
    keys = np.frombuffer(store.arb_ids, dtype=np.uint32)[start:end].astype(np.uint64)
    keys |= np.frombuffer(store.channels, dtype=np.uint8)[start:end].astype(np.uint64) \
        << _CHANNEL_SHIFT
    flags = np.frombuffer(store.flags, dtype=np.uint8)[start:end]
    keys |= ((flags & np.uint8(FLAG_EXTENDED)) != 0).astype(np.uint64) << _EXT_SHIFT
    keys |= ((flags & np.uint8(FLAG_LIN)) != 0).astype(np.uint64) << _LIN_SHIFT
    return keys


def _chunks(n: int) -> list[tuple[int, int]]:
    return [(start, min(n, start + CHUNK)) for start in range(0, n, CHUNK)]


def _check_unchanged(store, count: int) -> None:
    # A store closed while the Trace works on it in steps has no frames.
    if len(store.timestamps) != count:
        raise RuntimeError('the measurement was closed')


def _in_chunk(indices: np.ndarray | None, start: int, end: int) -> np.ndarray:
    """The members of sorted *indices* from *start* to *end*, less *start*."""
    if indices is None or not len(indices):
        return np.zeros(0, dtype=np.int64)
    lo, hi = np.searchsorted(indices, [start, end])
    return indices[lo:hi] - start


@dataclass
class SignalRef:
    """A signal one kind of frame carries, and how to decode it."""
    key: int
    message: object
    signal: object
    decoder: object           # MessageVectorDecoder of the message

    @property
    def name(self) -> str:
        return self.signal.name


class TraceCatalog:
    """
    The kinds of frame in a store and the signals each carries.

    Worked out once per store, a chunk per step (:meth:`steps`); a store holds
    a few hundred kinds however many frames, so the databases are asked about
    each kind once.
    """

    def __init__(self, store) -> None:
        self._store = store
        self._count = len(store)
        self.keys: np.ndarray | None = None
        self._by_name: dict[str, list[SignalRef]] = {}
        self._names_by_key: dict[int, list[str]] = {}

    @property
    def ready(self) -> bool:
        return self.keys is not None

    def steps(self):
        """Yield the share done after each chunk."""
        if self.ready:
            return
        store = self._store
        n = self._count
        _check_unchanged(store, n)
        parts = []
        chunks = _chunks(n)
        for done, (start, end) in enumerate(chunks, 1):
            _check_unchanged(store, n)
            parts.append(np.unique(frame_keys(store, start, end)))
            yield done / len(chunks)
        keys = np.unique(np.concatenate(parts)) if parts else np.zeros(0, np.uint64)
        self._build(keys)
        self.keys = keys

    def build(self) -> 'TraceCatalog':
        for _ in self.steps():
            pass
        return self

    def _decoder(self, bus: BusType, channel: int | None):
        config = getattr(self._store, 'channel_config', None)
        if config is not None:
            return config.decoder_for(bus, channel)
        return getattr(self._store, 'decoder', None)

    def _build(self, keys: np.ndarray) -> None:
        from core.vectorized_decoder import VectorizedDBC

        vectorised: dict[int, VectorizedDBC] = {}
        for key in keys.tolist():
            arb_id, channel, extended, bus = split_key(key)
            decoder = self._decoder(bus, channel)
            if decoder is None or not hasattr(decoder, 'candidates_for'):
                continue
            try:
                candidates = decoder.candidates_for(arb_id, extended)
            except Exception:
                continue
            message = next((m for m in candidates if getattr(m, 'signals', None)), None)
            if message is None:
                continue
            wrapper = vectorised.get(id(decoder))
            if wrapper is None:
                wrapper = vectorised[id(decoder)] = VectorizedDBC(decoder)
            msg_decoder = wrapper.get_message_decoder(message)
            names = []
            for signal in message.signals:
                ref = SignalRef(key, message, signal, msg_decoder)
                for name in {signal.name.lower(),
                             f'{message.name}.{signal.name}'.lower()}:
                    self._by_name.setdefault(name, []).append(ref)
                names.append(signal.name.lower())
            self._names_by_key[key] = names

    # ── Questions ─────────────────────────────────────────────────────────

    def has_signal(self, name: str) -> bool:
        return name.lower() in self._by_name

    def signals_named(self, name: str) -> list[SignalRef]:
        """Every kind's copy of the signal *name* (or ``Message.Signal``)."""
        return self._by_name.get(name.lower(), [])

    def keys_with_signal_text(self, text: str) -> np.ndarray:
        """The kinds that carry a signal whose name holds *text*."""
        text = text.lower()
        found = [key for key, names in self._names_by_key.items()
                 if any(text in name for name in names)]
        return np.array(sorted(found), dtype=np.uint64)


# ── Queries ───────────────────────────────────────────────────────────────

_TERM = re.compile(r'^\s*([A-Za-z_][\w.]*)\s*(!=|>=|<=|==|=|>|<)\s*(.+?)\s*$')
_OR = re.compile(r'\s+or\s+', re.IGNORECASE)
_AND = re.compile(r'\s+and\s+', re.IGNORECASE)


@dataclass(frozen=True)
class Condition:
    """``<signal> <op> <value>``; *value* a number or a value label."""
    name: str
    op: str
    value: str

    def number(self) -> float | None:
        try:
            return float(self.value)
        except ValueError:
            return None

    def label(self) -> str:
        return self.value.strip().strip('"\'').lower()

    def targets(self, signal) -> np.ndarray:
        """The physical values of *signal* whose label is :meth:`label`."""
        choices = getattr(signal, 'choices', None) or {}
        scale = float(getattr(signal, 'scale', 1) or 1)
        offset = float(getattr(signal, 'offset', 0) or 0)
        wanted = self.label()
        return np.array([float(raw) * scale + offset for raw, text in choices.items()
                         if str(text).strip().lower() == wanted], dtype=np.float64)

    def test(self, values: np.ndarray, signal) -> np.ndarray:
        """Where *values* (physical, NaN for none) meet the condition."""
        values = np.asarray(values, dtype=np.float64)
        present = ~np.isnan(values)
        number = self.number()
        if number is None:
            targets = self.targets(signal)
            if not len(targets):
                return np.zeros(len(values), dtype=bool)
            if self.op in ('=', '==', '!='):
                hit = np.zeros(len(values), dtype=bool)
                for target in targets:
                    hit |= np.isclose(values, target, rtol=1e-9, atol=1e-6)
                return present & (~hit if self.op == '!=' else hit)
            number = float(targets[0])
        # Scaled values carry rounding (0.1 × 620 = 62.000000000000007):
        # a value that equals the number for = is not greater than it.
        with np.errstate(invalid='ignore'):
            same = np.isclose(values, number, rtol=1e-9, atol=1e-6)
            if self.op in ('=', '=='):
                hit = same
            elif self.op == '!=':
                hit = ~same
            elif self.op == '>':
                hit = (values > number) & ~same
            elif self.op == '>=':
                hit = (values > number) | same
            elif self.op == '<':
                hit = (values < number) & ~same
            else:
                hit = (values < number) | same
        return present & hit

    def concerns(self, message_name: str, signal_name: str) -> bool:
        name = self.name.lower()
        return name in (signal_name.lower(), f'{message_name}.{signal_name}'.lower())


@dataclass(frozen=True)
class Text:
    """Plain text: frame fields, or part of a signal name."""
    text: str


@dataclass
class Query:
    """Terms in groups: the groups joined by *or*, the terms of one by *and*."""
    groups: list[list[Condition | Text]]
    #: Frames a text term found by their own fields (ID, name, data, …),
    #: filled in by the search: a hit outside it was found in a signal.
    field_hits: np.ndarray | None = field(default=None, repr=False)

    def terms(self):
        for group in self.groups:
            yield from group

    @property
    def has_signal_terms(self) -> bool:
        return any(isinstance(term, Condition) for term in self.terms())

    def marks(self, message_name: str, signal_name: str,
              number: float | None, shown: str) -> bool:
        """Whether a signal row is a hit: *number* its physical value (None
        for none), *shown* the text the row shows (a value label, if any)."""
        lowered = signal_name.lower()
        for term in self.terms():
            if isinstance(term, Text):
                if term.text in lowered:
                    return True
                continue
            if not term.concerns(message_name, signal_name) or number is None:
                continue
            if term.number() is None:
                hit = str(shown).strip().lower() == term.label()
                if hit != (term.op == '!='):
                    return True
            elif term.test(np.array([float(number)]), None)[0]:
                return True
        return False

    def found_in_signal(self, index: int) -> bool:
        """Whether frame *index* was found through its signals, not its fields."""
        if self.has_signal_terms:
            return True
        hits = self.field_hits
        if hits is None:
            return True
        at = int(np.searchsorted(hits, index))
        return not (at < len(hits) and int(hits[at]) == index)


def parse(text: str, has_signal) -> Query | None:
    """The query typed, or None for nothing typed. *has_signal(name)* says
    which names are signals: ``a = b`` is a condition only when *a* is one."""
    text = text.strip()
    if not text:
        return None
    groups = []
    for part in _OR.split(text):
        group = []
        for term in _AND.split(part):
            term = term.strip()
            if not term:
                continue
            match = _TERM.match(term)
            if match and has_signal(match.group(1)):
                op = '=' if match.group(2) == '==' else match.group(2)
                group.append(Condition(match.group(1), op, match.group(3)))
            else:
                group.append(Text(term.lower()))
        if group:
            groups.append(group)
    return Query(groups) if groups else None


class QuerySearch(FrameSearch):
    """A query's search: :attr:`result` the sorted indices of matching
    frames, :attr:`query` what was parsed."""

    def __init__(self, store, catalog: TraceCatalog, text: str) -> None:
        self.query: Query | None = None
        super().__init__(_query_steps(self, store, catalog, text, len(store)))


def search(store, catalog: TraceCatalog, text: str) -> QuerySearch:
    """Search *store* for the query *text*, a chunk of frames per step."""
    return QuerySearch(store, catalog, text)


def _query_steps(owner: QuerySearch, store, catalog: TraceCatalog, text: str, n: int):
    _check_unchanged(store, n)
    for share in catalog.steps():
        yield 0.1 * share
    query = parse(text, catalog.has_signal)
    owner.query = query
    if query is None or n == 0:
        return np.zeros(0, dtype=np.int64)

    # Text terms: the frames' own fields, as the Trace always searched.
    texts = sorted({term.text for term in query.terms() if isinstance(term, Text)})
    field_found: dict[str, np.ndarray] = {}
    signal_kinds: dict[str, np.ndarray] = {}
    for number, needle in enumerate(texts):
        running = store.search(needle, None)
        while not running.step():
            yield 0.1 + 0.4 * (number + running.progress) / len(texts)
        found = running.result
        field_found[needle] = (np.zeros(0, dtype=np.int64) if found is None
                               else np.asarray(found, dtype=np.int64))
        signal_kinds[needle] = catalog.keys_with_signal_text(needle)
    if field_found:
        query.field_hits = np.unique(np.concatenate(list(field_found.values())))

    found_parts = []
    chunks = _chunks(n)
    for done, (start, end) in enumerate(chunks, 1):
        _check_unchanged(store, n)
        keys = frame_keys(store, start, end)
        messages = (np.frombuffer(store.flags, dtype=np.uint8)[start:end]
                    & _NOT_A_MESSAGE) == 0
        anywhere = np.zeros(end - start, dtype=bool)
        for group in query.groups:
            within = np.ones(end - start, dtype=bool)
            for term in group:
                if isinstance(term, Text):
                    mask = np.zeros(end - start, dtype=bool)
                    mask[_in_chunk(field_found[term.text], start, end)] = True
                    kinds = signal_kinds[term.text]
                    if len(kinds):
                        mask |= np.isin(keys, kinds) & messages
                else:
                    mask = _condition_mask(store, catalog, term, keys,
                                           messages & within, start)
                within &= mask
                if not within.any():
                    break
            anywhere |= within
        found_parts.append(np.flatnonzero(anywhere) + start)
        yield 0.5 + 0.5 * done / len(chunks)
    return np.concatenate(found_parts).astype(np.int64)


def _condition_mask(store, catalog: TraceCatalog, term: Condition, keys: np.ndarray,
                    candidates: np.ndarray, start: int) -> np.ndarray:
    """Where *term* holds among the *candidates* of one chunk."""
    mask = np.zeros(len(keys), dtype=bool)
    if not candidates.any():
        return mask
    for ref in catalog.signals_named(term.name):
        rows_at = np.flatnonzero((keys == np.uint64(ref.key)) & candidates)
        if not len(rows_at):
            continue
        frames = rows_at + start
        data = store.rows(frames)
        lengths = np.frombuffer(store.dlcs, dtype=np.uint8)[frames]
        try:
            decoded = ref.decoder.decode(data, lengths)
        except Exception:
            continue
        values = decoded.get(ref.name)
        if values is None:
            continue
        mask[rows_at] = term.test(values[1], ref.signal)
    return mask


# ── Filter ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TraceFilter:
    """What the Trace's Filter button hides. Everything shown by default."""
    #: The bus-tagged channels shown; None for every channel.
    channels: frozenset | None = None
    rx: bool = True
    tx: bool = True
    data: bool = True
    errors: bool = True
    remote: bool = True
    #: IDs (kind keys without channel, :func:`id_key`) hidden, or shown alone.
    hidden_ids: frozenset = frozenset()
    only_ids: frozenset = frozenset()
    #: Colour numbers hidden: 0 for frames no rule colours, n for rule n.
    hidden_colours: frozenset = frozenset()

    def restrictions(self) -> int:
        """How many things the filter hides: 0 when it shows everything."""
        return (int(self.channels is not None) + int(not self.rx) + int(not self.tx)
                + int(not self.data) + int(not self.errors) + int(not self.remote)
                + len(self.hidden_ids) + len(self.only_ids) + len(self.hidden_colours))


def filter_search(store, flt: TraceFilter, colours: np.ndarray | None = None,
                  within: np.ndarray | None = None) -> FrameSearch:
    """The frames *flt* shows, among *within* (sorted indices) if given.
    :attr:`result` is None when every frame shows."""
    return FrameSearch(_filter_steps(store, flt, colours, within, len(store)))


def _filter_steps(store, flt: TraceFilter, colours, within, n: int):
    _check_unchanged(store, n)
    hides = flt.restrictions() > 0
    if not hides:
        if within is None:
            return None
        return np.asarray(within, dtype=np.int64)
    hidden_colours = np.array(sorted(flt.hidden_colours), dtype=np.uint8)
    hidden_ids = np.array(sorted(flt.hidden_ids), dtype=np.uint64)
    only_ids = np.array(sorted(flt.only_ids), dtype=np.uint64)
    channel_keys = None
    if flt.channels is not None:
        channel_keys = np.array(sorted(
            (int(bus is BusType.LIN) << 8) | (int(number) & 0xFF)
            for bus, number in flt.channels), dtype=np.uint16)
    found = []
    chunks = _chunks(n)
    for done, (start, end) in enumerate(chunks, 1):
        _check_unchanged(store, n)
        if within is not None:
            rows_at = _in_chunk(within, start, end)
            if not len(rows_at):
                yield done / len(chunks)
                continue
        else:
            rows_at = None
        flags = np.frombuffer(store.flags, dtype=np.uint8)[start:end]
        show = np.ones(end - start, dtype=bool)
        if channel_keys is not None:
            packed = np.frombuffer(store.channels, dtype=np.uint8)[start:end].astype(np.uint16)
            packed |= ((flags & np.uint8(FLAG_LIN)) != 0).astype(np.uint16) << np.uint16(8)
            show &= np.isin(packed, channel_keys)
        if not (flt.rx and flt.tx):
            dirs = np.frombuffer(store.directions, dtype=np.uint8)[start:end]
            if not flt.rx:
                show &= dirs != 0
            if not flt.tx:
                show &= dirs != 1
        if not (flt.data and flt.errors and flt.remote):
            error = (flags & np.uint8(FLAG_ERROR)) != 0
            remote = ((flags & np.uint8(FLAG_REMOTE)) != 0) & ~error
            if not flt.errors:
                show &= ~error
            if not flt.remote:
                show &= ~remote
            if not flt.data:
                show &= error | remote
        if len(hidden_ids) or len(only_ids):
            ids = frame_keys(store, start, end) & ID_MASK
            if len(only_ids):
                show &= np.isin(ids, only_ids)
            if len(hidden_ids):
                show &= ~np.isin(ids, hidden_ids)
        if len(hidden_colours):
            numbers = (colours[start:end] if colours is not None
                       else np.zeros(end - start, dtype=np.uint8))
            show &= ~np.isin(numbers, hidden_colours)
        if rows_at is not None:
            kept = rows_at[show[rows_at]]
        else:
            kept = np.flatnonzero(show)
        found.append(kept + start)
        yield done / len(chunks)
    return np.concatenate(found).astype(np.int64) if found else np.zeros(0, np.int64)


# ── Colours ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ColourRule:
    """Frames of one ID (any channel), or frames meeting a query, in a colour."""
    colour: str
    label: str
    id_key: int | None = None
    query: str | None = None


@dataclass
class Colouring:
    """Each frame's colour number (0: none, n: rule n), how many frames each
    number has, and where along the measurement each rule's frames are."""
    numbers: np.ndarray | None
    counts: list[int]
    bins: list[np.ndarray]


def colour_search(store, catalog: TraceCatalog, rules: list[ColourRule]) -> FrameSearch:
    """Colour the frames by *rules* (the first rule a frame meets wins);
    :attr:`result` a :class:`Colouring`."""
    return FrameSearch(_colour_steps(store, catalog, rules, len(store)))


def time_bins(store, indices: np.ndarray | None, bins: int = TIME_BINS) -> np.ndarray:
    """Which of *bins* equal stretches of the measurement hold *indices*."""
    out = np.zeros(bins, dtype=bool)
    n = len(store)
    if indices is None or not len(indices) or not n:
        return out
    ts = np.frombuffer(store.timestamps, dtype=np.float64)
    t0, t1 = float(ts[0]), float(ts[n - 1])
    span = t1 - t0
    if span <= 0:
        out[0] = True
        return out
    at = ((ts[np.asarray(indices, dtype=np.int64)] - t0) / span * bins).astype(np.int64)
    out[np.clip(at, 0, bins - 1)] = True
    return out


def _colour_steps(store, catalog: TraceCatalog, rules: list[ColourRule], n: int):
    _check_unchanged(store, n)
    if not rules or not n:
        return Colouring(None, [n] + [0] * len(rules), [np.zeros(TIME_BINS, bool)
                                                         for _ in rules])
    members: list[np.ndarray] = []
    for number, rule in enumerate(rules):
        base = number / len(rules)
        if rule.query is not None:
            running = search(store, catalog, rule.query)
            while not running.step():
                yield base + running.progress / len(rules)
            members.append(running.result if running.result is not None
                           else np.zeros(0, np.int64))
        else:
            parts = []
            wanted = np.uint64(rule.id_key if rule.id_key is not None else 0)
            chunks = _chunks(n)
            for done, (start, end) in enumerate(chunks, 1):
                _check_unchanged(store, n)
                parts.append(np.flatnonzero(
                    (frame_keys(store, start, end) & ID_MASK) == wanted) + start)
                yield base + done / len(chunks) / len(rules)
            members.append(np.concatenate(parts).astype(np.int64))
    numbers = np.zeros(n, dtype=np.uint8)
    for number in range(len(rules), 0, -1):       # the first rule wins
        numbers[members[number - 1]] = number
    counts = np.bincount(numbers, minlength=len(rules) + 1).tolist()
    bins = [time_bins(store, np.flatnonzero(numbers == number))
            for number in range(1, len(rules) + 1)]
    return Colouring(numbers, counts, bins)
