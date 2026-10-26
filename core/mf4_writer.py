# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Decoded signals written to an MF4 file (ASAM MDF 4.10) with asammdf.

One channel group per message; signals of a message that share their
timestamps share the group's time channel, others get a group each. Units are
kept, and a signal with text labels gets a value-to-text conversion, so the
labels survive. Each group's acquisition source names this application and
the bus channel ("CAN1", "LIN2"), from which core.readers.mdf_reader gives the
signals back their keys ("CH1::Message::Signal") when the file is opened
again: a plot configuration saved on the original measurement then works on
the MF4 too. Timestamps are kept relative to the recording's start, which goes
into the file header.

Signals that stay in the measurement file until read (LazySignalSeries) are
read a message at a time and let go again, so memory stays bounded.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

from core.bus_types import BusType, coerce_channel_key
from core.signal_store import SignalSeries

#: The acquisition source name that marks a channel group as written here.
SOURCE_NAME = 'Osvanta Bus Log Analyzer'

#: An epoch time later than this is taken for a wall-clock recording start.
_EPOCH_MIN = 1e8


class Cancelled(Exception):
    pass


def bus_path(channel) -> str:
    """The acquisition source path for a bus channel: "CAN1", "LIN2", or ""."""
    key = coerce_channel_key(channel)
    if key is None:
        return ''
    bus, number = key
    return f'{"LIN" if bus is BusType.LIN else "CAN"}{number}'


def _conversion(series: SignalSeries, values: np.ndarray):
    """A value-to-text conversion from the series' (value, label) pairs."""
    labels = list(series.raw_values)
    if not series.has_labels or not labels:
        return None
    pairs: dict[float, str] = {}
    for value, label in zip(values.tolist(), labels):
        if value == value and value not in pairs:
            pairs[value] = str(label)
    if not pairs:
        return None
    conversion = {}
    for i, (value, label) in enumerate(sorted(pairs.items())):
        conversion[f'val_{i}'] = value
        conversion[f'text_{i}'] = label.encode('utf-8')
    conversion['default'] = b''
    return conversion


def _buckets(items: Sequence[SignalSeries]) -> list[tuple[np.ndarray, list[SignalSeries]]]:
    """The message's signals, grouped by identical timestamps."""
    out: list[tuple[np.ndarray, list[SignalSeries]]] = []
    for series in items:
        ts = np.asarray(series.numpy_timestamps(), dtype=np.float64)
        if not ts.size:
            continue
        for stamps, members in out:
            if stamps.size == ts.size and (stamps is ts or np.array_equal(stamps, ts)):
                members.append(series)
                break
        else:
            out.append((ts, [series]))
    return out


def write_mf4(series_items: Iterable[SignalSeries], path: str | Path, *,
              base_ts: float = 0.0,
              progress: Callable[[int, int], None] | None = None) -> int:
    """Write *series_items* to *path*; returns how many signals were written.

    *base_ts* is the recording's start as the store has it (an epoch time
    for BLF and ASC); it becomes the header's start time when it is one.
    *progress(done, total)* is called per message and may raise Cancelled.
    """
    from asammdf import MDF, Signal
    from asammdf.blocks import v4_constants as v4c
    from asammdf.blocks.source_utils import Source

    groups: dict[tuple, list[SignalSeries]] = {}
    for series in series_items:
        groups.setdefault((coerce_channel_key(series.channel), series.message_name),
                          []).append(series)

    mdf = MDF(version='4.10')
    written = 0
    try:
        if base_ts and base_ts > _EPOCH_MIN:
            mdf.header.start_time = _dt.datetime.fromtimestamp(base_ts).astimezone()
        mdf.header.description = f'Decoded signals, written by {SOURCE_NAME}.'
        total = len(groups)
        for done, ((channel, message), items) in enumerate(groups.items(), start=1):
            bus = BusType.LIN if channel is not None and channel[0] is BusType.LIN else BusType.CAN
            source = Source(SOURCE_NAME, bus_path(channel), '', v4c.SOURCE_TOOL,
                            v4c.BUS_TYPE_LIN if bus is BusType.LIN else v4c.BUS_TYPE_CAN)
            for stamps, members in _buckets(items):
                signals = []
                for series in members:
                    values = np.asarray(series.numpy_values(), dtype=np.float64)
                    signals.append(Signal(
                        samples=values, timestamps=stamps, name=series.signal_name,
                        unit=series.unit or '', conversion=_conversion(series, values)))
                mdf.append(signals, acq_name=message, acq_source=source, comment='')
                written += len(signals)
            if progress is not None:
                progress(done, total)
        mdf.save(str(path), overwrite=True)
    finally:
        mdf.close()
    return written
