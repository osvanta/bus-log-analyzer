# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
J1939 messages sent by several ECUs, through the real load paths.

A database often defines a J1939 message once, at a placeholder source
address, while several ECUs send it: in a truck log CCVS came from the engine
and the body controller with real switch states and from two other nodes with
"not available". Those senders must end up in separate series, each in time
order, for both the ASC/BLF loader (analyzer decoder) and the MF4 loader
(asammdf extraction).
"""
from __future__ import annotations

import numpy as np
import pytest

from core.channel_config import ChannelConfig
from gui.load_worker import LoadWorker
from core.signal_store import SignalStore


_DM1_SIGNAL = ' SG_ {prefix}DTC1 : 16|32@1+ (1,0) [0|4294967295] "" Vector__XXX'

_DBC_HEADER = """\
VERSION ""

NS_ :

BS_:

BU_: ECU

"""

_DBC_J1939 = """
BA_DEF_ BO_  "VFrameFormat" ENUM  "StandardCAN","ExtendedCAN","reserved","J1939PG";
BA_DEF_ "ProtocolType" STRING ;
BA_DEF_DEF_  "VFrameFormat" "J1939PG";
BA_DEF_DEF_  "ProtocolType" "J1939";
BA_ "ProtocolType" "J1939";
"""


def _dbc(tmp_path, messages: list[tuple[str, int]]):
    """Write a J1939 DBC defining DM1 (PGN 0xFECA) as *messages* [(name, id)]."""
    body = "".join(
        f"BO_ {frame_id | 0x80000000} {name}: 8 ECU\n"
        f"{_DM1_SIGNAL.format(prefix=name + '_')}\n\n"
        for name, frame_id in messages
    )
    path = tmp_path / "dm1.dbc"
    path.write_text(_DBC_HEADER + body + _DBC_J1939, encoding="utf-8")
    return path


def _dm1(dtc: int) -> bytes:
    return bytes([0x00, 0xFF]) + dtc.to_bytes(4, "little") + bytes([0xFF, 0xFF])


# SA 0x13 reports DTC 34243 in cycles 3-4, SA 0xEF reports 61765 in cycles
# 1-6, SA 0xE6 never reports one. All three send every 10 ms, SA 0xEF first.
_SENDERS = (0xEF, 0x13, 0xE6)
_EVENTS = {0xEF: (1, 7, 61765), 0x13: (3, 5, 34243)}
_CYCLES = 10


def _frames():
    for cycle in range(_CYCLES):
        for order, sa in enumerate(_SENDERS):
            start, end, value = _EVENTS.get(sa, (0, 0, 0))
            dtc = value if start <= cycle < end else 0
            yield cycle * 0.010 + order * 0.0002, 0x18FECA00 | sa, _dm1(dtc)


def _write_asc(path):
    lines = [
        "date Thu Sep 24 10:00:00.000 am 2026",
        "base hex  timestamps absolute",
        "internal events logged",
        "Begin Triggerblock Thu Sep 24 10:00:00.000 am 2026",
    ]
    for timestamp, frame_id, data in _frames():
        payload = " ".join(f"{byte:02X}" for byte in data)
        lines.append(
            f"{timestamp + 0.001:11.6f} 1  {frame_id:08X}x       Rx   d 8 {payload}"
        )
    lines.append("End TriggerBlock")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_mf4(path):
    can = pytest.importorskip("can")
    pytest.importorskip("asammdf")
    with can.Logger(str(path)) as logger:
        for timestamp, frame_id, data in _frames():
            logger.on_message_received(can.Message(
                timestamp=1000.0 + timestamp, arbitration_id=frame_id,
                is_extended_id=True, data=data, channel=0,
            ))
    return path


@pytest.fixture(params=["asc", "mf4"])
def measurement(request, tmp_path):
    """The same frames as ASC (analyzer decoder) and MF4 (asammdf)."""
    writer = _write_asc if request.param == "asc" else _write_mf4
    return writer(tmp_path / f"dm1.{request.param}")


def _load(measurement, dbc_path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Run the real load path; return {message::signal: (timestamps, values)}."""
    worker = LoadWorker(str(measurement), ChannelConfig.from_single_dbc(str(dbc_path)))
    result: dict = {}
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda error: result.setdefault("error", error))
    worker.run()
    if "error" in result:
        raise AssertionError(result["error"])
    store = result["store"]
    try:
        return {
            key.split("::", 1)[1]: (
                series.numpy_timestamps().copy(), series.numpy_values().copy()
            )
            for key, series in store._series_by_key.items()
        }
    finally:
        store.raw_frame_store.close()


def _nonzero(series) -> list[float]:
    return sorted(set(series[1].tolist()) - {0.0})


def test_placeholder_message_gets_one_series_per_sender(measurement, tmp_path):
    dbc = _dbc(tmp_path, [("DM1", 0x18FECAFE)])

    series = _load(measurement, dbc)

    assert set(series) == {
        "DM1 [SA 0xEF]::DM1_DTC1",
        "DM1 [SA 0x13]::DM1_DTC1",
        "DM1 [SA 0xE6]::DM1_DTC1",
    }
    assert _nonzero(series["DM1 [SA 0xEF]::DM1_DTC1"]) == [61765.0]
    assert _nonzero(series["DM1 [SA 0x13]::DM1_DTC1"]) == [34243.0]
    assert _nonzero(series["DM1 [SA 0xE6]::DM1_DTC1"]) == []
    for timestamps, values in series.values():
        assert len(values) == _CYCLES
        assert np.all(np.diff(timestamps) > 0)


def test_single_sender_keeps_the_database_name(tmp_path):
    # Only SA 0xEF sends; there is nothing to tell apart.
    asc = tmp_path / "one.asc"
    _write_asc(asc)
    asc.write_text(
        "\n".join(
            line for line in asc.read_text(encoding="utf-8").splitlines()
            if "18FECA13x" not in line and "18FECAE6x" not in line
        ) + "\n",
        encoding="utf-8",
    )
    dbc = _dbc(tmp_path, [("DM1", 0x18FECAFE)])

    assert set(_load(asc, dbc)) == {"DM1::DM1_DTC1"}


def test_undefined_sender_of_sa_specific_pgn_stays_undecoded(measurement, tmp_path):
    # Issue #13: DM1 defined for SA 0xEF and 0xF3; 0x13 and 0xE6 must not
    # borrow either message, in the asammdf path as well as the decoder.
    dbc = _dbc(tmp_path, [("DM1_239", 0x18FECAEF), ("DM1_243", 0x18FECAF3)])

    series = _load(measurement, dbc)

    assert set(series) == {"DM1_239::DM1_239_DTC1"}
    assert len(series["DM1_239::DM1_239_DTC1"][1]) == _CYCLES
    assert _nonzero(series["DM1_239::DM1_239_DTC1"]) == [61765.0]


def test_one_sender_on_two_priorities_loads_in_time_order(tmp_path):
    # SA 0xEF alternates priority 6 and 7: one sender, one series, but the
    # loader decodes it one frame ID at a time.
    asc = tmp_path / "priorities.asc"
    _write_asc(asc)
    lines = [
        line for line in asc.read_text(encoding="utf-8").splitlines()
        if "18FECA13x" not in line and "18FECAE6x" not in line
    ]
    seen = 0
    for index, line in enumerate(lines):
        if "18FECAEFx" in line:
            if seen % 2:
                lines[index] = line.replace("18FECAEFx", "1CFECAEFx")
            seen += 1
    asc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dbc = _dbc(tmp_path, [("DM1", 0x18FECAFE)])

    series = _load(asc, dbc)

    assert set(series) == {"DM1::DM1_DTC1"}
    timestamps, values = series["DM1::DM1_DTC1"]
    assert len(values) == _CYCLES
    assert np.all(np.diff(timestamps) > 0)


def test_store_reorders_series_fed_out_of_time_order():
    store = SignalStore()
    for timestamps, values, labels in (
        ([0.0, 0.2, 0.4], [1.0, 1.0, 1.0], ["On", "On", "On"]),
        ([0.1, 0.3], [3.0, 3.0], ["N/A", "N/A"]),
    ):
        store.add_series_bulk(
            channel=1, message_name="CCVS", message_id=0x18FEF100,
            signal_name="BrakeSwitch", unit="",
            timestamps=np.array(timestamps), values=np.array(values),
            raw_values=labels, has_labels=True,
        )

    assert store.sort_merged_series() == 1
    series = next(iter(store._series_by_key.values()))
    assert series.numpy_timestamps().tolist() == [0.0, 0.1, 0.2, 0.3, 0.4]
    assert series.numpy_values().tolist() == [1.0, 3.0, 1.0, 3.0, 1.0]
    assert list(series.raw_values) == ["On", "N/A", "On", "N/A", "On"]


def test_store_leaves_in_order_series_alone():
    store = SignalStore()
    for timestamps in ([0.0, 0.1], [0.2, 0.3]):
        store.add_series_bulk(
            channel=1, message_name="EEC1", message_id=0x0CF00400,
            signal_name="EngSpeed", unit="rpm",
            timestamps=np.array(timestamps), values=np.array([1.0, 2.0]),
            raw_values=[], has_labels=False,
        )

    assert store.sort_merged_series() == 0
