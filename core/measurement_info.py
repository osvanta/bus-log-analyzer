# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
What a measurement file says about itself: its format, size and dates, and
for MF4/MDF the header (author, project, comment…), events and attachments;
for BLF the logging start and stop; for ASC its date line; for TRC its version,
start, connections and events.

Read on demand, apart from loading: with asammdf for MF4/MDF and python-can
for BLF, never through the measurement readers. TRC uses the trace reader's
header and event functions, which read no frames.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path

FORMATS = {
    '.mf4': 'MF4 (ASAM MDF 4)', '.mdf': 'MDF (ASAM MDF 3)', '.dat': 'MDF',
    '.blf': 'BLF (Vector binary log)', '.asc': 'ASC (Vector ASCII log)',
    '.trc': 'TRC (PEAK trace)', '.csv': 'CSV',
}

#: MF4 header fields shown, in this order, when the file fills them in.
_HEADER_FIELDS = (('author', 'Author'), ('department', 'Department'),
                  ('project', 'Project'), ('subject', 'Subject'),
                  ('description', 'Comment'))


@dataclass(frozen=True)
class MeasurementEvent:
    time_s: float          # on the file's own time base
    name: str
    comment: str = ''


@dataclass(frozen=True)
class Attachment:
    index: int
    file_name: str
    comment: str
    embedded: bool


@dataclass
class FileInfo:
    path: Path
    format: str
    size_bytes: int
    modified: _dt.datetime
    start: _dt.datetime | None = None        # when the recording started
    stop: _dt.datetime | None = None
    header: list[tuple[str, str]] = field(default_factory=list)
    events: list[MeasurementEvent] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # what could not be read


def read_file_info(path: str | Path) -> FileInfo:
    """The file's own information. Never raises for a readable file: what
    cannot be read is said in ``notes``."""
    path = Path(path)
    stat = path.stat()
    info = FileInfo(path, FORMATS.get(path.suffix.lower(), path.suffix.upper().lstrip('.')),
                    stat.st_size, _dt.datetime.fromtimestamp(stat.st_mtime))
    suffix = path.suffix.lower()
    try:
        if suffix in ('.mf4', '.mdf', '.dat'):
            _read_mdf(path, info)
        elif suffix == '.blf':
            _read_blf(path, info)
        elif suffix == '.asc':
            _read_asc(path, info)
        elif suffix == '.trc':
            _read_trc(path, info)
    except Exception as exc:  # noqa: BLE001 - shown in the dialog, never fatal
        info.notes.append(f'Could not read the file header: {exc}')
    return info


def _read_mdf(path: Path, info: FileInfo) -> None:
    from asammdf import MDF

    with MDF(str(path)) as mdf:
        header = mdf.header
        start = getattr(header, 'start_time', None)
        info.start = start if isinstance(start, _dt.datetime) else None
        for attr, label in _HEADER_FIELDS:
            value = str(getattr(header, attr, '') or '').strip()
            if value:
                info.header.append((label, value))
        info.header.append(('MDF version', str(mdf.version)))
        for event in getattr(mdf, 'events', None) or ():
            info.events.append(MeasurementEvent(
                float(getattr(event, 'value', 0.0) or 0.0),
                str(getattr(event, 'name', '') or ''),
                str(getattr(event, 'comment', '') or '')))
        for index, attachment in enumerate(getattr(mdf, 'attachments', None) or ()):
            flags = int(getattr(attachment, 'flags', 0) or 0)
            info.attachments.append(Attachment(
                index, str(getattr(attachment, 'file_name', '') or f'attachment {index + 1}'),
                str(getattr(attachment, 'comment', '') or ''), bool(flags & 1)))


def _read_blf(path: Path, info: FileInfo) -> None:
    import can

    reader = can.BLFReader(str(path))
    try:
        for attr, target in (('start_timestamp', 'start'), ('stop_timestamp', 'stop')):
            stamp = getattr(reader, attr, None)
            if stamp:
                setattr(info, target, _dt.datetime.fromtimestamp(float(stamp)))
        count = getattr(reader, 'object_count', None)
        if count:
            info.header.append(('Objects', f'{int(count):,}'))
    finally:
        reader.stop()


def _read_asc(path: Path, info: FileInfo) -> None:
    """The header lines before the first frame: ``date …``, ``base …``."""
    with path.open('r', encoding='latin-1', errors='replace') as stream:
        for _, line in zip(range(20), stream):
            text = line.strip()
            lowered = text.lower()
            if lowered.startswith('date '):
                info.header.append(('Date', text[5:].strip()))
            elif lowered.startswith('base '):
                info.header.append(('Number base, timestamps', text[5:].strip()))
            elif lowered.startswith('begin triggerblock'):
                info.header.append(('Trigger block', text[len('begin triggerblock'):].strip()))
                break


def _read_trc(path: Path, info: FileInfo) -> None:
    """The ``$`` keywords and header comments, and the events (EV, 2.1 on)."""
    from core.readers.trc_can_reader import read_trc_events, read_trc_header

    header = read_trc_header(path)
    info.start = header.start
    info.header.append(('TRC version', header.version))
    if header.generated_by:
        info.header.append(('Generated by', header.generated_by))
    for bus in header.buses:
        label = f"Bus {bus['Bus']}" if 'Bus' in bus else 'Connection'
        info.header.append((label, ', '.join(
            value for key, value in bus.items() if key != 'Bus')))
    for event in read_trc_events(path, header):
        info.events.append(MeasurementEvent(
            event.offset_s, event.text,
            f'Bus {event.bus}' if event.bus is not None else 'All buses'))


def save_attachment(path: str | Path, index: int, target: str | Path) -> Path:
    """Write MF4 attachment *index* of *path* to *target*; returns *target*.

    Raises ValueError for an attachment that is only a link to another file.
    """
    from asammdf import MDF

    target = Path(target)
    with MDF(str(path)) as mdf:
        data, file_path, *_ = mdf.extract_attachment(index)
    if not isinstance(data, (bytes, bytearray)):
        # A link to another file, not embedded in this one.
        raise ValueError(f'This attachment is a link to {file_path}, not stored in the file.')
    target.write_bytes(bytes(data))
    return target
