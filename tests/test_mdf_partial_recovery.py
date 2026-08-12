# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import struct
import sys

import numpy as np

from core.readers.mdf_can_reader import MDFCANReader
from core.readers.mdf_reader import MDFReader
from core.readers.mdf_recovery import (
    make_bounded_mdf4_copy,
    remove_recovery_copy,
)
from core.raw_frame_store import RawFrameStore
from core.signal_store import SignalStore


def test_bad_channel_is_reported_while_readable_channel_is_yielded():
    group = SimpleNamespace(
        channels=[
            SimpleNamespace(name="Time", channel_type=1),
            SimpleNamespace(name="Good", channel_type=0),
            SimpleNamespace(name="BadDataBytes", channel_type=0),
        ],
        channel_group=SimpleNamespace(acq_name="CAN signals"),
    )
    good = SimpleNamespace(
        timestamps=np.array([0.0, 1.0]),
        samples=np.array([10.0, 20.0]),
        unit="V",
    )

    class BrokenMDF:
        groups = [group]

        def select(self, _specs, raw=False):
            raise ValueError("wrong signal data block reference")

        def get(self, _name, group, index, raw=False):
            if index == 2:
                raise ValueError("seek out of range")
            return good

    failures = []
    rows = list(MDFReader._iter_arrays(
        BrokenMDF(),
        batch_all_groups=True,
        channel_error=lambda group_name, channel_name, exc: failures.append(
            (group_name, channel_name, str(exc))
        ),
    ))

    assert [row[0][1] for row in rows] == ["Good"]
    assert failures == [("CAN signals", "BadDataBytes", "seek out of range")]


def test_bad_raw_can_group_does_not_discard_other_groups():
    raw_channel = SimpleNamespace(name="CAN_DataFrame")
    groups = [
        SimpleNamespace(
            channels=[raw_channel],
            channel_group=SimpleNamespace(acq_name="Broken CAN"),
        ),
        SimpleNamespace(
            channels=[raw_channel],
            channel_group=SimpleNamespace(acq_name="Good CAN"),
        ),
    ]
    dtype = np.dtype([
        ("CAN_DataFrame.BusChannel", "u1"),
        ("CAN_DataFrame.ID", "u4"),
        ("CAN_DataFrame.DataBytes", "u1", (8,)),
        ("CAN_DataFrame.DLC", "u1"),
    ])
    samples = np.zeros(1, dtype=dtype)
    samples["CAN_DataFrame.BusChannel"] = 1
    samples["CAN_DataFrame.ID"] = 0x123
    samples["CAN_DataFrame.DLC"] = 8

    class PartialRawMDF:
        def __init__(self):
            self.groups = groups

        def get(self, group, index, raw=False):
            if group == 0:
                raise ValueError("wrong signal data block reference")
            return SimpleNamespace(
                samples=samples,
                timestamps=np.array([1.5]),
            )

    batches = []
    failures = []
    count = MDFCANReader._emit_raw_frame_arrays(
        PartialRawMDF(),
        lambda *arrays: batches.append(arrays),
        lambda group_name, channel_name, exc: failures.append(
            (group_name, channel_name, str(exc))
        ),
    )

    assert count == 1
    assert len(batches) == 1
    assert failures == [
        ("Broken CAN", "CAN_DataFrame", "wrong signal data block reference")
    ]


def test_dbc_extraction_failure_still_yields_native_decoded_channels(
    tmp_path, monkeypatch
):
    native_group = SimpleNamespace(
        channels=[
            SimpleNamespace(name="Time", channel_type=1),
            SimpleNamespace(name="VehicleSpeed", channel_type=0, unit="km/h"),
        ],
        channel_group=SimpleNamespace(acq_name="Recorder decoded"),
    )
    native_signal = SimpleNamespace(
        timestamps=np.array([0.0, 1.0]),
        samples=np.array([10.0, 11.0]),
        unit="km/h",
    )

    class PartialMDF:
        groups = [native_group]
        _mdf = SimpleNamespace(bus_logging_map={"CAN": {}})

        def extract_bus_logging(self, **_kwargs):
            raise ValueError("wrong signal data block reference")

        def select(self, _specs, raw=False):
            return [native_signal]

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "asammdf",
        SimpleNamespace(MDF=lambda *_args, **_kwargs: PartialMDF()),
    )
    reader = MDFCANReader.__new__(MDFCANReader)
    reader._path = tmp_path / "mixed.mf4"
    reader._dbc_path = tmp_path / "network.dbc"
    reader.load_warnings = []

    rows = list(reader.iter_decoded_channel_arrays(None))

    assert len(rows) == 1
    assert rows[0][0] == (
        None, "Recorder decoded", 0, "VehicleSpeed", "km/h"
    )
    assert any(
        "DBC extraction / raw CAN groups" in warning
        and "wrong signal data block reference" in warning
        for warning in reader.load_warnings
    )


def test_recovery_fallback_decodes_readable_raw_groups_and_keeps_global_time(
    sample_dbc_path,
):
    from core.channel_config import ChannelConfig
    from core.load_worker import LoadWorker

    raw = RawFrameStore()
    raw.append_numpy_batch(
        timestamps=np.array([10.0]),
        channels=np.array([1], dtype=np.uint8),
        arb_ids=np.array([0x100], dtype=np.uint32),
        dlcs=np.array([8], dtype=np.uint8),
        directions=np.array([0], dtype=np.uint8),
        flags=np.array([0], dtype=np.uint8),
        data_rows=np.array([[0x60, 0x09, 0x64, 0, 0, 0, 0, 0]], dtype=np.uint8),
    )
    worker = LoadWorker(
        "unused.mf4",
        ChannelConfig.from_single_dbc(str(sample_dbc_path)),
    )
    store = SignalStore()

    global_base = worker._decode_recovered_mdf_trace(raw, store, 5.0)
    try:
        assert global_base == 5.0
        assert store.base_ts == 5.0
        assert store.total_frames == 1
        assert len(store.all_keys()) > 0
        for series in store._series_by_key.values():
            assert series.timestamps[0] == 5.0
    finally:
        if store.raw_frame_store is not None:
            store.raw_frame_store.close()


def _write_block(data: bytearray, address: int, block_id: bytes, links: list[int]):
    block_len = 24 + len(links) * 8
    data[address:address + 24] = struct.pack(
        "<4s4sQQ", block_id, b"\0" * 4, block_len, len(links)
    )
    if links:
        data[address + 24:address + block_len] = struct.pack(
            f"<{len(links)}Q", *links
        )


def test_out_of_file_group_tail_is_truncated_only_in_temporary_copy(tmp_path):
    path = tmp_path / "truncated.mf4"
    data = bytearray(512)
    _write_block(data, 64, b"##HD", [128])
    _write_block(data, 128, b"##DG", [0x5000, 192])
    _write_block(data, 192, b"##CG", [0])
    path.write_bytes(data)
    original = path.read_bytes()

    recovered, warnings = make_bounded_mdf4_copy(path)
    try:
        assert recovered is not None
        assert recovered != path
        assert path.read_bytes() == original
        repaired = recovered.read_bytes()
        next_dg = struct.unpack_from("<Q", repaired, 128 + 24)[0]
        assert next_dg == 0
        assert any("outside the file" in warning for warning in warnings)
    finally:
        remove_recovery_copy(recovered)

    assert not Path(recovered).exists()
