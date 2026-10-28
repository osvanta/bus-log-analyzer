# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Read a PEAK-System trace file (.trc), as PCAN-View, PCAN-Explorer and
PCAN-Basic write it, and decode its CAN frames with a :class:`DBCDecoder`.

Written from PEAK's published description of the format ("PEAK CAN TRC File
Format", last updated 2025-08-04). It defines seven versions; each line is one
frame, warning or event, and ``;`` starts a comment:

====  ====================================================================
1.0   ``n) offset ID DLC data``; no version line; offset in whole ms
1.1   adds a type column (Rx, Tx, Warng, Error); offset to 0.1 ms
1.2   adds a bus column; offset to 1 µs
1.3   adds a reserved column (a J1939 destination address)
2.0   the columns ``$COLUMNS`` names; type and direction apart; CAN FD
2.1   optional bus and reserved columns; events (EV)
3.0   CAN XL columns; 11-bit IDs written with 3 digits
====  ====================================================================

python-can's ``TRCReader`` reads 1.1, 1.3, 2.0 and 2.1 frames, fails on 1.2,
3.0 and a 1.0 file holding an error frame, and drops every line that is not a
frame. This reader keeps error frames (FLAG_ERROR), turns hardware status,
error counter and overload lines into :class:`BusStatus` records, keeps events,
and counts what the store cannot hold in ``load_warnings``: CAN XL frames,
protocol exceptions, and J1939 messages longer than 64 bytes.
"""

from __future__ import annotations

import datetime as _dt
import mmap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, NamedTuple

from can.util import dlc2len

from core.blf_reader import controller_state
from core.dbc_decoder import DBCDecoder
from core.models import DecodedSignalSample, RawFrame
from core.raw_frame_store import (
    FLAG_BRS, FLAG_ERROR, FLAG_EXTENDED, FLAG_FD, FLAG_REMOTE, BusStatus,
)

#: The versions PEAK's description defines, oldest first.
TRC_VERSIONS = ('1.0', '1.1', '1.2', '1.3', '2.0', '2.1', '3.0')

# $STARTTIME counts days since this day, on the recording PC's clock.
_DAY_ZERO = _dt.datetime(1899, 12, 30)

# Hardware status (ST, 2.0 on) and error warning (Warng, 1.x) codes: a 32-bit
# set of flags, most significant byte first.
_OVERRUN = 0x0000_0002
_BUSHEAVY = 0x0000_0008     # 1.x BUSHEAVY, 2.x BUSWARNING: 96 error points
_BUSOFF = 0x0000_0010
_BUSPASSIVE = 0x0004_0000   # 2.x only

# 2.x and 3.0 frame types, and the flags each sets.
_DATA_TYPES = {
    b'DT': 0,
    b'FD': FLAG_FD,
    b'FB': FLAG_FD | FLAG_BRS,
    b'FE': FLAG_FD,               # ESI set
    b'BI': FLAG_FD | FLAG_BRS,    # BRS and ESI set
}
_ESI_TYPES = {b'FE', b'BI'}

# Lines about the bus rather than a frame, and their fixed data length.
_BUS_LINE_BYTES = {b'ER': 5, b'ST': 4, b'EC': 2, b'OF': 3, b'PE': 6, b'EN': 5}

# What a record from the line parsers is.
_FRAME, _STATUS, _EVENT, _SKIP = range(4)

# Why a line is skipped, as load_warnings words it.
_SKIP_REASONS = {
    'xl': '{n:,} CAN XL frame(s) are not shown: CAN XL is not supported yet.',
    'xl_error': ('{n:,} CAN XL protocol exception(s) and error notification(s) '
                 'are not shown.'),
    'long': '{n:,} J1939 message(s) longer than 64 bytes are not shown.',
    'unreadable': '{n:,} line(s) could not be read and were skipped.',
}

#: The most data bytes one row of the frame store holds.
_MAX_ROW_BYTES = 64

# Other programs save files as .trc too (text logs starting "@ TEXT @", for
# one). The header read tries this many lines of data and refuses the file
# when none reads as a PEAK trace line; a line is read at most this long,
# because such a file need not break its lines where this reader does.
_PROBE_LINES = 20
_MAX_PROBE_LINE = 64 << 10


class TRCReadError(RuntimeError):
    pass


@dataclass
class TRCHeader:
    """What a trace file says about itself before its first line of data."""
    version: str
    start: _dt.datetime | None = None          # local wall-clock time
    columns: tuple[str, ...] = ()              # 2.0 on: the $COLUMNS letters
    buses: list[dict[str, str]] = field(default_factory=list)
    generated_by: str = ''

    @property
    def start_epoch(self) -> float | None:
        """The start as epoch seconds, read as local time like a BLF's. None
        for a start before 1970 on this PC's clock, which Windows cannot
        convert: python-can's writer stamps a log of relative times there."""
        if self.start is None:
            return None
        try:
            return self.start.timestamp()
        except (OverflowError, OSError, ValueError):
            return None


class TRCEvent(NamedTuple):
    """A user-defined event (EV, 2.1 on)."""
    offset_s: float       # since the start of the trace
    bus: int | None       # None: all buses
    text: str


def _split_columns(text: str) -> list[str]:
    """A header table row: cells are two or more spaces apart, so a cell
    may hold single spaces ("500 kBit/s")."""
    return [cell.strip() for cell in text.split('  ') if cell.strip()]


def _bus_table(comments: list[str]) -> list[dict[str, str]]:
    """The connections table between two dashed comment lines (1.3, 2.x).
    PEAK's converter writes ``N/A`` for what it does not know: left out."""
    for at, text in enumerate(comments[:-1]):
        if not text.startswith('---') or 'Connection' not in comments[at + 1]:
            continue
        headings = _split_columns(comments[at + 1])
        rows = []
        for row in comments[at + 2:]:
            if row.startswith('---'):
                break
            known = {heading: cell for heading, cell in zip(headings, _split_columns(row))
                     if cell != 'N/A'}
            if known:
                rows.append(known)
        return rows
    # 1.x names its one connection in a line of its own.
    for text in comments:
        for label in ('PCAN-Net:', 'Connection:'):
            name = text[len(label):].strip() if text.startswith(label) else ''
            if name and name != 'N/A':
                return [{'Connection': name}]
    return []


def read_trc_header(path: str | Path) -> TRCHeader:
    """The ``$`` keywords and the header comments of a trace file.

    Raises :class:`TRCReadError` for a version this reader does not know, for
    a 2.0 or later file without a usable ``$COLUMNS`` line, and for a file
    none of whose first lines of data is a PEAK trace line.
    """
    version = start = None
    columns: tuple[str, ...] = ()
    comments: list[str] = []
    probe: list[bytes] = []
    with Path(path).open('rb') as stream:
        for raw in iter(lambda: stream.readline(_MAX_PROBE_LINE), b''):
            line = raw.strip()
            if not line:
                continue
            if probe or not line.startswith(b';'):
                # Data from here on; comments between lines are not header.
                if not line.startswith(b';'):
                    probe.append(line)
                if len(probe) == _PROBE_LINES:
                    break
                continue
            text = line[1:].decode('latin-1').strip()
            if text.startswith('$FILEVERSION='):
                version = text.split('=', 1)[1].strip()
            elif text.startswith('$STARTTIME='):
                try:
                    days = float(text.split('=', 1)[1].strip().rstrip(';'))
                    start = _DAY_ZERO + _dt.timedelta(days=days)
                except (ValueError, OverflowError):
                    start = None
            elif text.startswith('$COLUMNS='):
                columns = tuple(c.strip() for c in text.split('=', 1)[1].split(','))
            else:
                comments.append(text)

    version = version or '1.0'
    if version not in TRC_VERSIONS:
        raise TRCReadError(
            f"TRC file version {version} is not supported "
            f"(versions {TRC_VERSIONS[0]} to {TRC_VERSIONS[-1]} are)."
        )
    if version >= '2.0':
        needed = {'O', 'T', 'I', 'd', 'D'}
        if not needed <= set(columns) or not {'l', 'L'} & set(columns):
            raise TRCReadError(
                f"This TRC {version} file has no usable $COLUMNS line, "
                "so its columns cannot be told apart."
            )
    generated_by = next(
        (text.split('Generated by', 1)[1].strip()
         for text in comments if 'Generated by' in text),
        '',
    )
    header = TRCHeader(version, start if version >= '1.1' else None, columns,
                       _bus_table(comments), generated_by)
    parse = _line_parser(header)
    if probe and not any(_reads(parse, line) for line in probe):
        raise TRCReadError(
            f"'{Path(path).name}' is not a PEAK trace: none of its first "
            f"{len(probe)} line(s) of data reads as one. Other programs save "
            ".trc files too."
        )
    return header


def _reads(parse, line: bytes) -> bool:
    """Whether *line* is a trace line this reader knows, shown or not."""
    try:
        return parse(line) is not None
    except (ValueError, IndexError, OverflowError, UnicodeDecodeError):
        return False


def _direction(token: bytes) -> int:
    return 0 if token == b'Rx' else 1 if token == b'Tx' else 2


def _hex_bytes(tokens) -> bytes:
    """Leading two-digit hex tokens as bytes; stops at the first other token
    (a ``--`` filler or a warning's flag names)."""
    out = bytearray()
    for token in tokens:
        if len(token) != 2:
            break
        try:
            out.append(int(token, 16))
        except ValueError:
            break
    return bytes(out)


def _is_extended(id_token: bytes, arb_id: int) -> bool:
    """A 29-bit ID: written with 8 digits, as PEAK's tools do even for a
    small value, or too large for 11 bits (python-can's writer drops a
    leading zero and writes 7)."""
    return len(id_token) == 8 or arb_id > 0x7FF


def _warning(offset: float, channel: int, code: int):
    """A 1.x error warning or a 2.x hardware status line as a status record."""
    state = ('bus off' if code & _BUSOFF else
             'error passive' if code & _BUSPASSIVE else
             'warning' if code & _BUSHEAVY else
             'error active')
    values = {'state': state}
    if code & _OVERRUN:
        values['overrun'] = True
    return (_STATUS, offset, channel, 'controller', values)


def _v1_parser(version: str) -> Callable[[bytes], tuple | None]:
    """The parser for one line of a 1.x file.

    The 1.x layouts differ only in which columns sit between the index and the
    data: ``n) offset [bus] [type] ID [reserved] DLC data``.
    """
    has_type = version >= '1.1'
    has_bus = version >= '1.2'
    has_reserved = version >= '1.3'
    id_at = 2 + has_bus + has_type
    dlc_at = id_at + 1 + has_reserved

    def parse(line: bytes):
        tokens = line.split()
        # A 1.x line starts with its number and a ")".
        if len(tokens) <= dlc_at or not tokens[0].endswith(b')'):
            return None
        offset = float(tokens[1])
        channel = int(tokens[2]) if has_bus else 1
        kind = tokens[id_at - 1] if has_type else b''
        id_token = tokens[id_at]
        dlc = int(tokens[dlc_at])
        rest = tokens[dlc_at + 1:]
        if kind == b'Warng' or (not has_type and id_token == b'FFFFFFFF'):
            return _warning(offset, channel,
                            int.from_bytes(_hex_bytes(rest), 'big'))
        if kind == b'Error' or (not has_type and rest[:1] == [b'ERROR']):
            # direction (0 sending, 1 receiving), bit position, RX and TX
            # error counters.
            data = _hex_bytes(rest[1:] if rest[:1] == [b'ERROR'] else rest)
            if len(data) < 4:
                return None
            direction = 1 if data[0] == 0 else 0
            return (_FRAME, offset, channel, 0, False, 0, direction, FLAG_ERROR,
                    b'', False, {'tec': data[3], 'rec': data[2]})
        if has_type and kind not in (b'Rx', b'Tx'):
            return None
        direction = _direction(kind) if has_type else 2
        arb_id = int(id_token, 16)
        extended = _is_extended(id_token, arb_id)
        if rest[:1] == [b'RTR']:
            return (_FRAME, offset, channel, arb_id, extended, min(dlc, 15),
                    direction, FLAG_REMOTE, b'', False, None)
        if dlc > _MAX_ROW_BYTES:
            return (_SKIP, 'long')
        payload = bytes.fromhex(b' '.join(rest[:dlc]).decode('ascii'))
        if len(payload) != dlc:
            return None
        return (_FRAME, offset, channel, arb_id, extended, dlc, direction, 0,
                payload, False, None)

    return parse


def _v2_parser(columns: tuple[str, ...]) -> Callable[[bytes], tuple | None]:
    """The parser for one line of a 2.x or 3.0 file, whose ``$COLUMNS`` say
    which columns it has, in a fixed order: ``[N],O,T,[B],I,d,[R],…,l/L,D``.
    """
    at = {letter: index for index, letter in enumerate(columns)}
    o_at, t_at, i_at, d_at, data_at = at['O'], at['T'], at['I'], at['d'], at['D']
    b_at = at.get('B')
    length_at = at.get('l')
    dlc_at = at.get('L') if length_at is None else None

    def parse(line: bytes):
        tokens = line.split(None, data_at)
        if len(tokens) <= t_at:
            return None
        kind = tokens[t_at]
        offset = float(tokens[o_at])

        flags = _DATA_TYPES.get(kind)
        if flags is not None or kind == b'RR':
            if len(tokens) < data_at:
                return None
            channel = int(tokens[b_at]) if b_at is not None else 1
            id_token = tokens[i_at]
            arb_id = int(id_token, 16)
            extended = _is_extended(id_token, arb_id)
            direction = _direction(tokens[d_at])
            if length_at is not None:
                length = int(tokens[length_at])
            else:
                dlc = int(tokens[dlc_at])
                length = dlc2len(dlc) if flags else dlc
            if kind == b'RR':
                return (_FRAME, offset, channel, arb_id, extended, min(length, 15),
                        direction, FLAG_REMOTE, b'', False, None)
            if length > _MAX_ROW_BYTES:
                return (_SKIP, 'long')
            payload = (bytes.fromhex(tokens[data_at].decode('ascii'))
                       if length and len(tokens) > data_at else b'')
            if len(payload) != length:
                return None
            return (_FRAME, offset, channel, arb_id, extended, length, direction,
                    flags, payload, kind in _ESI_TYPES, None)

        size = _BUS_LINE_BYTES.get(kind)
        if size is not None:
            # 2.0 leaves the ID and length columns of these lines empty, so
            # only the columns before the ID sit where $COLUMNS says. The data
            # bytes are always the last ones.
            words = line.split()
            data = _hex_bytes(words[-size:])
            if len(data) != size:
                return None
            channel = (int(words[b_at]) if b_at is not None
                       and words[b_at].isdigit() else 1)
            if kind in (b'PE', b'EN'):
                return (_SKIP, 'xl_error')
            if kind == b'ST':
                return _warning(offset, channel, int.from_bytes(data, 'big'))
            if kind == b'OF':
                return (_STATUS, offset, channel, 'overload', {})
            if kind == b'EC':
                rec, tec = data[0], data[1]
                return (_STATUS, offset, channel, 'controller',
                        {'tec': tec, 'rec': rec, 'state': controller_state(tec, rec)})
            # ER: type, direction (0 sending, 1 receiving), bit position, RX
            # and TX error counters.
            direction = next((_direction(word) for word in words[t_at + 1:]
                              if word in (b'Rx', b'Tx')), 1 if data[1] == 0 else 0)
            return (_FRAME, offset, channel, 0, False, 0, direction, FLAG_ERROR,
                    b'', False, {'tec': data[4], 'rec': data[3]})

        if kind == b'EV':
            # The text starts right after the bus, or after the type.
            words = line.split(None, (b_at if b_at is not None else t_at) + 1)
            bus = words[b_at] if b_at is not None else b'-'
            text = words[-1].strip().decode('latin-1') if len(words) > (
                (b_at if b_at is not None else t_at) + 1) else ''
            return (_EVENT, offset, int(bus) if bus.isdigit() else None, text)

        if kind == b'XL':
            return (_SKIP, 'xl')
        return None

    return parse


def _line_parser(header: TRCHeader):
    if header.version >= '2.0':
        return _v2_parser(header.columns)
    return _v1_parser(header.version)


class TRCCANReader:
    """
    Reads a PEAK-System trace file (.trc), any version from 1.0 to 3.0, and
    decodes signals with a :class:`DBCDecoder`.

    Frames come out as :class:`BLFCANReader` and :class:`ASCCANReader` give
    them, so the DBC pipeline downstream is the same. Times are absolute when
    the file records its start (1.1 on), epoch seconds as for a BLF; a 1.0 file
    has none, so its times run from its own zero, as an ASC's do.

    Attributes
    ----------
    source_description : str
    has_raw_frames : bool
        Always ``True``.
    header : TRCHeader
    bus_status, events, load_warnings
        From the last :meth:`iter_raw_batches` pass.
    """

    has_raw_frames: bool = True

    def __init__(self, trc_path: str | Path, decoder: DBCDecoder | None = None) -> None:
        self._path = Path(trc_path)
        self._decoder = decoder
        if not self._path.exists():
            raise TRCReadError(f"TRC file not found: {self._path}")
        self.header = read_trc_header(self._path)
        self.bus_status: list[BusStatus] = []
        self.events: list[TRCEvent] = []
        self.load_warnings: list[str] = []
        database = f" / {decoder.dbc_path.name}" if decoder is not None else ""
        self.source_description = (
            f"TRC {self.header.version} + DBC  ({self._path.name}{database})"
        )
        self.load_messages: list[str] = (
            list(decoder.load_messages) if decoder is not None else []
        ) + ["Fast path: column-wise TRC parsing."]

    # ── Protocol-required iterator ────────────────────────────────────────

    def __iter__(self) -> Iterator[DecodedSignalSample]:
        for _frame, samples in self.iter_with_frames():
            yield from samples

    def iter_with_frames(self) -> Iterator[tuple[RawFrame, list[DecodedSignalSample]]]:
        for frame in self.iter_frames_only():
            yield frame, self._decoder.decode_frame(frame)

    def iter_frames_only(self) -> Iterator[RawFrame]:
        """Yield the data frames, not decoded. Error frames carry nothing to
        decode and are left out."""
        names = ('Rx', 'Tx', 'Unknown')
        for base_ts, timestamps, channels, arb_ids, dlcs, directions, flags, data \
                in self.iter_raw_batches():
            for row, offset in enumerate(timestamps):
                if flags[row] & FLAG_ERROR:
                    continue
                length = 0 if flags[row] & FLAG_REMOTE else dlcs[row]
                yield RawFrame(
                    timestamp=base_ts + offset,
                    channel=channels[row],
                    arbitration_id=arb_ids[row],
                    is_extended_id=bool(flags[row] & FLAG_EXTENDED),
                    is_fd=bool(flags[row] & FLAG_FD),
                    dlc=dlcs[row],
                    data=bytes(data[row * 64:row * 64 + length]),
                    direction=names[directions[row]],
                )

    # ── Records ───────────────────────────────────────────────────────────

    def _records(self) -> Iterator[tuple]:
        """Every readable line as a record, in file order, with
        ``self.load_warnings`` counting the rest once the file is read."""
        parse = _line_parser(self.header)
        skipped: dict[str, int] = {}
        try:
            with self._path.open('rb', buffering=4 << 20) as stream:
                for line in stream:
                    first = line.lstrip()[:1]
                    if not first or first == b';':
                        continue
                    try:
                        record = parse(line)
                    except (ValueError, IndexError, OverflowError, UnicodeDecodeError):
                        record = None
                    if record is None:
                        skipped['unreadable'] = skipped.get('unreadable', 0) + 1
                    elif record[0] == _SKIP:
                        skipped[record[1]] = skipped.get(record[1], 0) + 1
                    else:
                        yield record
        except OSError as exc:
            raise TRCReadError(f"Failed to read TRC file '{self._path}': {exc}") from exc
        self.load_warnings = [
            _SKIP_REASONS[reason].format(n=count)
            for reason, count in skipped.items()
        ]

    def iter_raw_batches(self, batch_size: int = 16_384):
        """Parse the file into column batches for LoadWorker's fast path.

        Yields ``(base_ts, timestamps, channels, arb_ids, dlcs, directions,
        flags, data_block)`` as :meth:`ASCCANReader.iter_raw_batches` does:
        times relative to ``base_ts``, the first frame's time; one 64-byte
        slot of ``data_block`` per frame; ``dlcs`` the number of data bytes
        (a remote frame's: the length it asked for). Error frames are rows
        flagged FLAG_ERROR; status lines go to ``bus_status`` and events to
        ``events``.
        """
        self.bus_status = []
        self.events = []
        start = self.header.start_epoch or 0.0

        timestamps: list[float] = []
        channels = bytearray()
        arb_ids: list[int] = []
        dlcs = bytearray()
        directions = bytearray()
        flags = bytearray()
        data_block = bytearray(batch_size * 64)
        first_offset: float | None = None
        base_ts = 0.0

        for record in self._records():
            kind = record[0]
            if kind == _STATUS:
                _, offset, channel, status_kind, values = record
                self.bus_status.append(
                    BusStatus(start + offset / 1000.0, channel, status_kind, values))
                continue
            if kind == _EVENT:
                _, offset, bus, text = record
                self.events.append(TRCEvent(offset / 1000.0, bus, text))
                continue
            (_, offset, channel, arb_id, extended, length, direction,
             frame_flags, payload, _esi, counters) = record
            if counters is not None:
                values = dict(counters, state=controller_state(counters['tec'],
                                                               counters['rec']))
                self.bus_status.append(
                    BusStatus(start + offset / 1000.0, channel, 'controller', values))
            if first_offset is None:
                first_offset = offset
                base_ts = start + offset / 1000.0
            slot = len(timestamps)
            timestamps.append((offset - first_offset) / 1000.0)
            channels.append(channel if 0 <= channel < 255 else 255)
            arb_ids.append(arb_id & 0xFFFF_FFFF)
            dlcs.append(length)
            directions.append(direction)
            flags.append(frame_flags | (FLAG_EXTENDED if extended else 0))
            if payload:
                data_block[slot * 64:slot * 64 + len(payload)] = payload

            if len(timestamps) == batch_size:
                yield (base_ts, timestamps, channels, arb_ids, dlcs,
                       directions, flags, data_block)
                timestamps = []
                channels = bytearray()
                arb_ids = []
                dlcs = bytearray()
                directions = bytearray()
                flags = bytearray()
                data_block = bytearray(batch_size * 64)

        if timestamps:
            yield (base_ts, timestamps, channels, arb_ids, dlcs,
                   directions, flags, data_block)

    def iter_messages(self):
        """The file as python-can messages, for Convert files: absolute times
        when the file records its start, and 0-based channels as python-can's
        own readers give them."""
        import can

        start = self.header.start_epoch or 0.0
        for record in self._records():
            if record[0] != _FRAME:
                continue
            (_, offset, channel, arb_id, extended, length, direction,
             frame_flags, payload, esi, _counters) = record
            yield can.Message(
                timestamp=start + offset / 1000.0,
                arbitration_id=arb_id,
                is_extended_id=extended,
                is_remote_frame=bool(frame_flags & FLAG_REMOTE),
                is_error_frame=bool(frame_flags & FLAG_ERROR),
                is_fd=bool(frame_flags & FLAG_FD),
                bitrate_switch=bool(frame_flags & FLAG_BRS),
                error_state_indicator=esi,
                channel=channel - 1,
                dlc=length,
                data=payload,
                is_rx=direction != 1,
                check=False,
            )

    @property
    def decoder(self) -> DBCDecoder | None:
        return self._decoder


def read_trc_events(path: str | Path, header: TRCHeader | None = None) -> list[TRCEvent]:
    """The user-defined events (EV) of a trace file, without parsing its
    frames: a search for the type, then a parse of each line found."""
    header = header or read_trc_header(path)
    if header.version < '2.1':
        return []
    parse = _v2_parser(header.columns)
    events: list[TRCEvent] = []
    with Path(path).open('rb') as stream:
        if Path(path).stat().st_size == 0:
            return events
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as view:
            at = view.find(b' EV ')
            while at >= 0:
                start = view.rfind(b'\n', 0, at) + 1
                end = view.find(b'\n', at)
                end = len(view) if end < 0 else end
                line = view[start:end]
                if not line.lstrip().startswith(b';'):
                    try:
                        record = parse(line)
                    except (ValueError, IndexError):
                        record = None
                    if record is not None and record[0] == _EVENT:
                        events.append(TRCEvent(record[1] / 1000.0, record[2], record[3]))
                at = view.find(b' EV ', end)
    return events
