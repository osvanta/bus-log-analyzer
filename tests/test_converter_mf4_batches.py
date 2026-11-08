# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Convert files to MF4 adds the data frames a batch at a time.

python-can's MF4 writer extends the file by one frame per call: 183 µs a
frame, about five hours for a 5 GB BLF. The batched writer must give the same
frames, read back, as python-can's own.
"""
from __future__ import annotations

import time

import pytest

can = pytest.importorskip('can')
pytest.importorskip('asammdf')


def _frames():
    t0 = 1_700_000_000.0
    frames = []
    for i in range(40):
        kind = i % 8
        common = dict(timestamp=t0 + i * 0.01, channel=(i % 3) + 1 if i % 5 else None,
                      is_rx=bool(i % 4))
        if kind == 0:
            frames.append(can.Message(arbitration_id=0x18FF0021, is_extended_id=True,
                                      data=bytes([i, 1, 2, 3, 4, 5, 6, 7]), **common))
        elif kind == 1:
            frames.append(can.Message(arbitration_id=0x123, is_fd=True, bitrate_switch=True,
                                      data=bytes(range(i % 64, i % 64 + 64)) [:64], **common))
        elif kind == 2:
            frames.append(can.Message(arbitration_id=0x124, is_fd=True, error_state_indicator=True,
                                      data=bytes([i] * 12), **common))
        elif kind == 3:
            frames.append(can.Message(arbitration_id=0x7FF, data=b'', **common))
        elif kind == 4:
            frames.append(can.Message(arbitration_id=0x100, is_remote_frame=True, dlc=4, **common))
        elif kind == 5:
            frames.append(can.Message(arbitration_id=0x0, is_error_frame=True, **common))
        else:
            frames.append(can.Message(arbitration_id=0x200 + i, data=bytes([i, 255, 0]), **common))
    return frames


def _read(path):
    return sorted(
        (round(m.timestamp, 6), m.channel, m.arbitration_id, m.is_extended_id, m.is_fd,
         m.bitrate_switch, m.error_state_indicator, m.is_rx, m.is_remote_frame,
         m.is_error_frame, m.dlc, bytes(m.data))
        for m in can.MF4Reader(str(path)))


def _rows(path):
    from asammdf import MDF
    mdf = MDF(str(path))
    try:
        return mdf.get('CAN_DataFrame', group=0).samples.copy()
    finally:
        mdf.close()


def _write(writer, frames):
    for frame in frames:
        writer.on_message_received(frame)
    writer.stop()


def test_the_batched_writer_gives_python_cans_frames(tmp_path, monkeypatch):
    from core import converter

    monkeypatch.setattr(converter, 'MF4_BATCH', 3)     # several batches and a rest
    frames = _frames()
    _write(can.MF4Writer(str(tmp_path / 'one_by_one.mf4')), frames)
    _write(converter._writer(tmp_path / 'batched.mf4'), frames)

    expected = _read(tmp_path / 'one_by_one.mf4')
    assert len(expected) == len(frames)
    assert _read(tmp_path / 'batched.mf4') == expected
    # Every stored field too, the DLC code among them, which the reader
    # does not hand back for an FD frame.
    one_by_one, batched = _rows(tmp_path / 'one_by_one.mf4'), _rows(tmp_path / 'batched.mf4')
    assert batched.dtype == one_by_one.dtype
    for name in one_by_one.dtype.names:
        assert (batched[name] == one_by_one[name]).all(), name


def test_a_batch_is_added_when_full_and_the_rest_at_stop(tmp_path, monkeypatch):
    from core import converter

    monkeypatch.setattr(converter, 'MF4_BATCH', 4)
    writer = converter._writer(tmp_path / 'out.mf4')
    added = []
    extend = writer._mdf.extend
    monkeypatch.setattr(writer._mdf, 'extend',
                        lambda index, signals: (added.append((index, len(signals[0][0]))),
                                                extend(index, signals)))
    data = [can.Message(timestamp=1_700_000_000.0 + i, arbitration_id=0x100, channel=1,
                        data=bytes(8)) for i in range(10)]
    _write(writer, data)

    assert added == [(0, 4), (0, 4), (0, 2)]


def test_ten_thousand_frames_convert_in_under_a_second(tmp_path):
    from core import converter

    frames = [can.Message(timestamp=1_700_000_000.0 + i * 0.001, arbitration_id=0x100 + i % 50,
                          channel=1, data=bytes([i % 256] * 8)) for i in range(10_000)]
    writer = converter._writer(tmp_path / 'out.mf4')
    started = time.perf_counter()
    for frame in frames:
        writer.on_message_received(frame)
    writer.stop()
    # One at a time this took about 1.8 s (183 µs a frame).
    assert time.perf_counter() - started < 1.0
    assert len(_read(tmp_path / 'out.mf4')) == 10_000
