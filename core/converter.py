# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tools ▸ Convert files: measurement files joined, cut to a time range, reduced
to chosen signals, resampled, and written in another format.

Two kinds of input, each with the steps that make sense for it:

* **MDF / MF4** — through asammdf: join (``MDF.concatenate``), keep signals
  (``filter``), cut (``cut``, seconds from the first sample), resample
  (``resample``); written as MF4 or CSV (one time column for every signal).
* **BLF / ASC / TRC** — raw frames, through python-can's readers and writers:
  join and cut, written as BLF, ASC or MF4 (ASAM bus logging). Frames are
  copied as they are; there are no signals to choose or resample.

Never through the application's own measurement readers, except TRC: python-can
cannot read every version of it, so the trace reader gives its frames. Never
over one of the input files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MDF_INPUTS = ('.mf4', '.mdf', '.dat')
RAW_INPUTS = ('.blf', '.asc', '.trc')
MDF_OUTPUTS = ('.mf4', '.csv')
RAW_OUTPUTS = ('.blf', '.asc', '.mf4')

#: Frames copied between two progress reports (and cancel checks).
_REPORT_EVERY = 20_000


class Cancelled(Exception):
    pass


class ConvertError(ValueError):
    """The job cannot be done as asked; the message says why."""


@dataclass
class ConvertJob:
    inputs: list[Path]
    output: Path
    start: float | None = None      # seconds from the first sample
    end: float | None = None
    keep: list[tuple[str, int, int]] = field(default_factory=list)   # MDF: (name, group, index)
    raster: float | None = None     # seconds; MDF only

    @property
    def kind(self) -> str:
        """'mdf' or 'raw'; ConvertError for anything else or a mix."""
        suffixes = {Path(p).suffix.lower() for p in self.inputs}
        if not self.inputs:
            raise ConvertError('Add at least one file to convert.')
        if suffixes <= set(MDF_INPUTS):
            return 'mdf'
        if len(suffixes) == 1 and suffixes <= set(RAW_INPUTS):
            return 'raw'
        raise ConvertError('Join files of one format only: all MF4/MDF, all BLF, all ASC '
                           'or all TRC.')

    def check(self) -> None:
        kind = self.kind
        outputs = MDF_OUTPUTS if kind == 'mdf' else RAW_OUTPUTS
        suffix = Path(self.output).suffix.lower()
        if suffix not in outputs:
            raise ConvertError(f'From {"MF4/MDF" if kind == "mdf" else "BLF/ASC/TRC"} the output '
                               f'can be {", ".join(s.upper().lstrip(".") for s in outputs)}.')
        target = Path(self.output).resolve()
        if any(Path(p).resolve() == target for p in self.inputs):
            raise ConvertError('The output would overwrite one of the files being converted.')
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ConvertError('The end of the time range must come after its start.')
        if kind == 'raw' and (self.keep or self.raster):
            raise ConvertError('BLF, ASC and TRC hold frames: signals can be chosen and '
                               'resampled in MF4/MDF files only.')
        if self.raster is not None and self.raster <= 0:
            raise ConvertError('The resampling raster must be greater than zero.')


def list_signals(path: str | Path) -> list[tuple[str, int, int, str]]:
    """The signals of an MDF file: ``(name, group, index, group name)``,
    without time channels."""
    from asammdf import MDF

    out = []
    with MDF(str(path)) as mdf:
        for g, group in enumerate(mdf.groups):
            acq = getattr(group.channel_group, 'acq_name', '') or f'Group {g}'
            for c, channel in enumerate(group.channels):
                if getattr(channel, 'channel_type', 0) in (2, 3):   # master channels
                    continue
                if channel.name.lower() in ('time', 't', 'timestamps'):
                    continue
                out.append((channel.name, g, c, str(acq)))
    return out


def convert(job: ConvertJob, progress: Callable[[int, int], None] | None = None) -> Path:
    """Do *job*; returns the file written. *progress(done, total)* may raise
    Cancelled, after which no output is left behind."""
    job.check()
    output = Path(job.output)
    try:
        if job.kind == 'mdf':
            _convert_mdf(job, progress)
        else:
            _convert_raw(job, progress)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output


def _step(progress, done, total):
    if progress is not None:
        progress(done, total)


def _convert_mdf(job: ConvertJob, progress) -> None:
    from asammdf import MDF

    steps = 2 + bool(job.keep) + (job.start is not None or job.end is not None) + bool(job.raster)
    done = 0
    if len(job.inputs) > 1:
        mdf = MDF.concatenate([str(p) for p in job.inputs])
    else:
        mdf = MDF(str(job.inputs[0]))
    opened = [mdf]
    try:
        done += 1
        _step(progress, done, steps)
        if job.keep:
            mdf = mdf.filter(list(job.keep))
            opened.append(mdf)
            done += 1
            _step(progress, done, steps)
        if job.start is not None or job.end is not None:
            mdf = mdf.cut(start=job.start, stop=job.end, whence=1)
            opened.append(mdf)
            done += 1
            _step(progress, done, steps)
        if job.raster:
            mdf = mdf.resample(job.raster)
            opened.append(mdf)
            done += 1
            _step(progress, done, steps)
        if Path(job.output).suffix.lower() == '.csv':
            mdf.export('csv', str(job.output), single_time_base=True)
        else:
            mdf.save(str(job.output), overwrite=True)
        _step(progress, steps, steps)
    finally:
        for item in opened:
            try:
                item.close()
            except Exception:
                pass


def _reader(path: Path):
    import can
    suffix = path.suffix.lower()
    if suffix == '.trc':
        from core.readers.trc_can_reader import TRCCANReader
        return TRCCANReader(path).iter_messages()
    return can.BLFReader(str(path)) if suffix == '.blf' else can.ASCReader(str(path))


def _writer(path: Path):
    import can
    suffix = path.suffix.lower()
    if suffix == '.blf':
        return can.BLFWriter(str(path))
    if suffix == '.asc':
        return can.ASCWriter(str(path))
    return can.MF4Writer(str(path))


def _from_one(message):
    """python-can's readers number buses from 0 and its BLF and ASC writers add
    the 1 back, but its MF4 writer stores the number as it is, and MF4 counts
    from 1: shift an integer channel, leave any other value as it is."""
    if isinstance(message.channel, int):
        message.channel += 1
    return message


def _convert_raw(job: ConvertJob, progress) -> None:
    total_bytes = sum(Path(p).stat().st_size for p in job.inputs) or 1
    done_bytes = 0
    first: float | None = None
    copied = 0
    writer = _writer(Path(job.output))
    to_mf4 = Path(job.output).suffix.lower() == '.mf4'
    try:
        for path in job.inputs:
            path = Path(path)
            reader = _reader(path)
            try:
                for message in reader:
                    if first is None:
                        first = message.timestamp
                    t = message.timestamp - first
                    if job.end is not None and t > job.end:
                        # Files are joined in order: nothing later can be in range.
                        break
                    if job.start is None or t >= job.start:
                        writer.on_message_received(_from_one(message) if to_mf4 else message)
                    copied += 1
                    if copied % _REPORT_EVERY == 0:
                        # Within a file, progress by its share of the bytes.
                        _step(progress, done_bytes, total_bytes)
            finally:
                stop = getattr(reader, 'stop', None)
                if stop is not None:
                    stop()
            done_bytes += path.stat().st_size
            _step(progress, done_bytes, total_bytes)
            if job.end is not None and first is not None and t > job.end:
                break
    finally:
        writer.stop()
