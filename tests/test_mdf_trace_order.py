# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
CAN Trace row order after an MF4 bus-logging load.

The MF4 reader hands the raw ``CAN_DataFrame`` and ``LIN_Frame`` groups over
one group at a time, so a file with one group per bus channel reached the
trace channel by channel -- every CAN 1 frame, then every CAN 2 frame --
while the same traffic from a BLF interleaves. The trace dialog's
jump-to-time is a binary search that assumes time order.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.bus_types import BusType
from core.channel_config import ChannelConfig
from core.raw_frame_store import RawFrameStore

asammdf = pytest.importorskip("asammdf")
can = pytest.importorskip("can")

CAN1, CAN2 = (BusType.CAN, 1), (BusType.CAN, 2)
START = 1_700_000_000.0
PERIOD = 0.01
LIN_ID = 0x21

LIN_DTYPE = np.dtype([
    ("LIN_Frame.BusChannel", "u1"),
    ("LIN_Frame.ID", "u1"),
    ("LIN_Frame.DataLength", "u1"),
    ("LIN_Frame.DataBytes", "u1", (8,)),
])


def _can_rows(per_channel: int, channels=(1, 2)) -> list[tuple]:
    """(epoch time, channel, payload): each channel half a period after the last."""
    rows = []
    for i in range(per_channel):
        for position, channel in enumerate(channels):
            value = (i * channel) & 0xFFFF
            rows.append((START + i * PERIOD + position * PERIOD / 2, channel,
                         value.to_bytes(2, "little") + bytes(6)))
    return rows


def _bus_group(mdf, samples, timestamps, name, bus_type, acq_name) -> None:
    from asammdf.blocks import v4_constants as v4c
    from asammdf.blocks.v4_blocks import SourceInformation

    signal = asammdf.Signal(
        samples=samples, timestamps=timestamps, name=name,
        source=SourceInformation(source_type=v4c.SOURCE_BUS, bus_type=bus_type),
    )
    index = mdf.append([signal], acq_name=acq_name)
    mdf.groups[index].channel_group.flags = (           # 6, as bus loggers write
        v4c.FLAG_CG_BUS_EVENT | v4c.FLAG_CG_PLAIN_BUS_EVENT
    )


def _write_mf4(path, can_rows, lin_rows=()):
    """One ``CAN_DataFrame`` group per CAN channel, then one ``LIN_Frame`` group."""
    from asammdf.blocks import v4_constants as v4c
    from can.io.mf4 import STD_DTYPE

    mdf = asammdf.MDF(version="4.10")
    for channel in sorted({row[1] for row in can_rows}):
        mine = [row for row in can_rows if row[1] == channel]
        records = np.zeros(len(mine), dtype=STD_DTYPE)
        records["CAN_DataFrame.BusChannel"] = channel
        records["CAN_DataFrame.ID"] = 0x100
        records["CAN_DataFrame.DLC"] = 8
        records["CAN_DataFrame.DataLength"] = 8
        records["CAN_DataFrame.DataBytes"][:, :8] = [
            np.frombuffer(row[2], np.uint8) for row in mine
        ]
        _bus_group(mdf, records, np.array([row[0] - START for row in mine]),
                   "CAN_DataFrame", v4c.BUS_TYPE_CAN, f"CAN{channel}")
    if lin_rows:
        records = np.zeros(len(lin_rows), dtype=LIN_DTYPE)
        records["LIN_Frame.BusChannel"] = 1
        records["LIN_Frame.ID"] = LIN_ID
        records["LIN_Frame.DataLength"] = 8
        records["LIN_Frame.DataBytes"] = [np.frombuffer(row[2], np.uint8) for row in lin_rows]
        _bus_group(mdf, records, np.array([row[0] - START for row in lin_rows]),
                   "LIN_Frame", v4c.BUS_TYPE_LIN, "LIN1")
    mdf.save(str(path), overwrite=True)
    mdf.close()
    return path


def _write_blf(path, can_rows):
    with can.BLFWriter(str(path)) as writer:
        for timestamp, channel, payload in can_rows:
            writer(can.Message(timestamp=timestamp, arbitration_id=0x100,
                               is_extended_id=False, data=payload,
                               channel=channel - 1))
    return path


@pytest.fixture()
def load(sample_dbc_path):
    """Run the real Load + Decode worker synchronously; close every trace after."""
    from gui.load_worker import LoadWorker

    stores = []

    def run(path, messages=None):
        config = ChannelConfig(channels={CAN1: str(sample_dbc_path),
                                         CAN2: str(sample_dbc_path)})
        worker = LoadWorker(str(path), config)
        result = {}
        worker.finished.connect(lambda store: result.setdefault("store", store))
        worker.failed.connect(lambda error: result.setdefault("error", error))
        if messages is not None:
            worker.progress.connect(messages.append)
        worker.run()
        assert "error" not in result, result.get("error")
        stores.append(result["store"])
        return result["store"]

    yield run
    for store in stores:
        if store.raw_frame_store is not None:
            store.raw_frame_store.close()


def _times(store) -> np.ndarray:
    return np.frombuffer(store.raw_frame_store.timestamps, dtype=np.float64)


def _trace(store) -> list[tuple]:
    rfs = store.raw_frame_store
    return [(record.bus, record.channel, record.arbitration_id, record.data,
             round(record.time_s, 6))
            for record in rfs.get_window(range(len(rfs)))]


def _assert_same_signals(actual, expected) -> None:
    want = {str(key): series for key, series in expected._series_by_key.items()}
    got = {str(key): series for key, series in actual._series_by_key.items()}
    assert want, "nothing decoded; the comparison would prove nothing"
    assert sorted(got) == sorted(want)
    for key, series in want.items():
        assert list(got[key].values) == list(series.values), key
        np.testing.assert_allclose(np.asarray(got[key].timestamps, dtype=np.float64),
                                   np.asarray(series.timestamps, dtype=np.float64),
                                   atol=1e-9, err_msg=key)


# ── the order itself ─────────────────────────────────────────────────────

def test_a_two_channel_mf4_trace_is_in_time_order_like_the_blf(tmp_path, load):
    rows = _can_rows(400)
    mf4 = load(_write_mf4(tmp_path / "two.mf4", rows))
    blf = load(_write_blf(tmp_path / "two.blf", rows))

    times = _times(mf4)
    assert len(times) == 800
    assert np.all(np.diff(times) > 0), "the trace is not in time order"
    assert _trace(mf4) == _trace(blf)
    _assert_same_signals(mf4, blf)


def test_can_and_lin_groups_interleave_in_the_trace(tmp_path, load):
    can_rows = _can_rows(200, channels=(1,))
    lin_rows = [(START + i * PERIOD + PERIOD / 2, 1, bytes([i & 0xFF] * 8))
                for i in range(200)]

    trace = _trace(load(_write_mf4(tmp_path / "can_lin.mf4", can_rows, lin_rows)))

    assert [row[4] for row in trace] == sorted(row[4] for row in trace)
    assert [row[0] for row in trace[:4]] == [BusType.CAN, BusType.LIN] * 2
    lin = [row for row in trace if row[0] is BusType.LIN]
    assert [row[2] for row in lin] == [LIN_ID] * 200
    assert [row[3] for row in lin] == [bytes([i] * 8) for i in range(200)]


# ── nothing else changes ─────────────────────────────────────────────────

def test_sorting_the_trace_changes_no_decoded_signal(tmp_path, load, monkeypatch):
    path = _write_mf4(tmp_path / "two.mf4", _can_rows(400))
    ordered = load(path)
    monkeypatch.setattr(RawFrameStore, "sort_by_time", lambda self: False)
    file_order = load(path)

    assert not np.all(np.diff(_times(file_order)) > 0), \
        "the file must arrive out of order, or this proves nothing"
    _assert_same_signals(ordered, file_order)
    assert sorted(_trace(ordered), key=repr) == sorted(_trace(file_order), key=repr)
    assert (ordered.decoded_frames, ordered.unmatched_frames) == \
        (file_order.decoded_frames, file_order.unmatched_frames)
    assert ordered.channel_frame_counts == file_order.channel_frame_counts


def test_the_recovery_path_also_gets_a_time_ordered_trace(tmp_path, load, monkeypatch):
    path = _write_mf4(tmp_path / "two.mf4", _can_rows(400))
    native = load(path)

    def broken(self, *args, **kwargs):
        raise ValueError("wrong signal data block reference")

    monkeypatch.setattr(asammdf.MDF, "extract_bus_logging", broken)
    messages = []
    recovered = load(path, messages)

    assert any("native DBC extraction failed" in text for text in messages), \
        "the recovery path did not run"
    assert np.all(np.diff(_times(recovered)) > 0)
    assert _trace(recovered) == _trace(native)
    _assert_same_signals(recovered, native)


def test_a_failed_sort_keeps_the_load_and_every_frame(tmp_path, load, monkeypatch):
    path = _write_mf4(tmp_path / "two.mf4", _can_rows(400))

    def full_disk(self):
        raise OSError("No space left on device")

    monkeypatch.setattr(RawFrameStore, "sort_by_time", full_disk)
    messages = []
    store = load(path, messages)

    assert any("CAN Trace left in file order" in text and "No space" in text
               for text in messages)
    assert len(store.raw_frame_store) == 800
    assert len(store.all_keys()) == 4


# ── what the order is for ────────────────────────────────────────────────

def test_jump_to_time_shows_every_channel_near_the_target(qapp, tmp_path, load):
    from gui.raw_frame_dialog import _WINDOW, RawFrameDialog

    # Long enough that, grouped by channel, one window around the target
    # could only ever hold one channel's frames.
    per_channel = _WINDOW + 1_000
    store = load(_write_mf4(tmp_path / "long.mf4", _can_rows(per_channel)))
    target = round(per_channel * PERIOD / 2, 3)

    dialog = RawFrameDialog(store.raw_frame_store)
    try:
        dialog._jump_edit.setText(f"{target}")
        dialog._go_jump()
        shown = range(dialog._win_start, dialog._win_start + _WINDOW)
    finally:
        dialog.deleteLater()

    near = np.flatnonzero(np.abs(_times(store) - target) < 0.5)
    channels = np.frombuffer(store.raw_frame_store.channels, dtype=np.uint8)
    assert set(channels[near].tolist()) == {1, 2}
    assert near[0] in shown and near[-1] in shown, \
        "frames within half a second of the target are off screen"


# ── other formats are not reordered (rule 9) ─────────────────────────────

_OUT_OF_ORDER = (0.0, 0.2, 0.1, 0.3)


def _write_out_of_order(fmt, path):
    messages = [can.Message(timestamp=START + offset, arbitration_id=0x100,
                            is_extended_id=False, data=bytes([index] * 8),
                            channel=0)
                for index, offset in enumerate(_OUT_OF_ORDER)]
    if fmt == "blf":
        with can.BLFWriter(str(path)) as writer:
            for msg in messages:
                writer(msg)
    elif fmt == "asc":
        with can.ASCWriter(str(path)) as writer:
            for msg in messages:
                writer(msg)
    else:
        lines = ["TimestampEpoch;BusChannel;ID;IDE;DLC;DataLength;Dir;EDL;BRS;ESI;RTR;DataBytes"]
        lines += [f"{msg.timestamp:.6f};1;100;0;8;8;0;0;0;0;0;{msg.data.hex().upper()}"
                  for msg in messages]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("fmt", ["blf", "asc", "csv"])
def test_file_order_formats_keep_their_trace_order(fmt, tmp_path, load):
    """
    The sort is for MF4 group order only. A trace read in file order keeps
    it, out-of-order frames included -- the change must not reach these.
    """
    store = load(_write_out_of_order(fmt, tmp_path / f"out_of_order.{fmt}"))

    assert [row[3][0] for row in _trace(store)] == [0, 1, 2, 3]
    np.testing.assert_allclose(_times(store), _OUT_OF_ORDER, atol=1e-6)
