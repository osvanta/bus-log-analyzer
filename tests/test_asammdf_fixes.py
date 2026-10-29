# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""asammdf decodes a bus-logging MF4 as the application's decoder decodes the
same frames from an ASC.

Three differences of asammdf 8.8.27 this guards against: a signed 8-bit signal
off a byte boundary read unsigned (0xFC as 252), a J1939 signal that is "not
available" throughout left out of the file, and -0.0 from a linear conversion
with a negative factor and no offset.
"""
from __future__ import annotations

import numpy as np
import pytest

asammdf = pytest.importorskip("asammdf")
can = pytest.importorskip("can")
cantools = pytest.importorskip("cantools")

import core.readers  # noqa: E402,F401  (installs the fixes, as every load does)

_DBC = """\
VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 Body: 8 ECU
 SG_ Aligned : 0|8@1- (1,0) [-128|127] "" ECU
 SG_ Off4 : 12|8@1- (1,0) [-128|127] "" ECU
 SG_ Off7 : 23|8@1- (1,0) [-128|127] "" ECU
 SG_ Scaled : 31|8@1- (0.5,1) [-63|64.5] "" ECU
 SG_ Motorola : 43|8@0- (1,0) [-128|127] "" ECU

BO_ 512 Current: 8 ECU
 SG_ Amps : 0|16@1- (-0.1,0) [-3276.7|3276.7] "A" ECU
 SG_ Word : 20|16@1- (1,0) [-32768|32767] "" ECU
 SG_ Wide12 : 36|12@1- (1,0) [-2048|2047] "" ECU

"""

_SIGNED = [-128, -4, -1, 0, 5, 127]


def _frames():
    db = cantools.database.load_string(_DBC, "dbc")
    body, current = db.get_message_by_name("Body"), db.get_message_by_name("Current")
    frames = []
    for i, value in enumerate(_SIGNED):
        frames.append((i * 0.01, 0x100, body.encode({
            "Aligned": value, "Off4": value, "Off7": value, "Scaled": value * 0.5 + 1,
            "Motorola": value,
        })))
        frames.append((i * 0.01 + 0.005, 0x200, current.encode({
            "Amps": (value % 3) * -0.1, "Word": value * 256, "Wide12": value * 16,
        })))
    return frames


@pytest.fixture()
def dbc(tmp_path):
    path = tmp_path / "body.dbc"
    path.write_text(_DBC, encoding="utf-8")
    return path


def _write(path, frames, extended=False):
    if path.suffix == ".asc":
        with can.ASCWriter(str(path)) as writer:
            for t, frame_id, data in frames:
                writer.on_message_received(can.Message(
                    timestamp=t, arbitration_id=frame_id, data=data,
                    is_extended_id=extended, channel=0))
    else:
        with can.Logger(str(path)) as logger:
            for t, frame_id, data in frames:
                logger.on_message_received(can.Message(
                    timestamp=1000 + t, arbitration_id=frame_id, data=data,
                    is_extended_id=extended, channel=1))
    return path


def _load(path, dbc_path) -> dict[str, np.ndarray]:
    from core.channel_config import ChannelConfig
    from gui.load_worker import LoadWorker

    worker = LoadWorker(str(path), ChannelConfig.from_single_dbc(str(dbc_path)))
    result: dict = {}
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda error: result.setdefault("error", error))
    worker.run()
    assert "error" not in result, result.get("error")
    store = result["store"]
    try:
        return {
            key.split("::", 1)[1]: series.numpy_values().copy()
            for key, series in store._series_by_key.items()
        }
    finally:
        store.raw_frame_store.close()


def _both(tmp_path, dbc_path, frames, extended=False):
    asc = _load(_write(tmp_path / "frames.asc", frames, extended), dbc_path)
    mf4 = _load(_write(tmp_path / "frames.mf4", frames, extended), dbc_path)
    return asc, mf4


def test_signed_signals_off_a_byte_boundary_read_as_the_asc_reads_them(tmp_path, dbc):
    asc, mf4 = _both(tmp_path, dbc, _frames())

    assert set(mf4) == set(asc)
    for name in ("Aligned", "Off4", "Off7", "Motorola"):
        assert asc[f"Body::{name}"].tolist() == _SIGNED, name
        assert mf4[f"Body::{name}"].tolist() == _SIGNED, name
    assert mf4["Body::Scaled"].tolist() == [v * 0.5 + 1 for v in _SIGNED]
    assert mf4["Current::Wide12"].tolist() == [v * 16 for v in _SIGNED]
    assert mf4["Current::Word"].tolist() == [v * 256 for v in _SIGNED]
    for key in asc:
        assert mf4[key].tolist() == asc[key].tolist(), key


def test_a_negative_factor_gives_zero_not_negative_zero(tmp_path, dbc):
    asc, mf4 = _both(tmp_path, dbc, _frames())

    amps = mf4["Current::Amps"]
    assert amps.tolist() == asc["Current::Amps"].tolist()
    zeros = amps == 0
    assert zeros.any()
    assert not np.signbit(amps[zeros]).any()
    assert not np.signbit(asc["Current::Amps"][zeros]).any()


@pytest.mark.parametrize("version", ["3.30", "4.10"])
def test_a_recorded_channel_with_a_negative_factor_gives_zero(tmp_path, version):
    from core.readers.mdf_reader import MDFReader

    path = tmp_path / f"recorded.{'mdf' if version.startswith('3') else 'mf4'}"
    mdf = asammdf.MDF(version=version)
    mdf.append([asammdf.Signal(
        np.array([0, 10, 0], dtype=np.int16), np.array([0.0, 0.1, 0.2]), name="Amps",
        conversion={"a": -0.1, "b": 0.0},
    )])
    mdf.save(path, overwrite=True)
    mdf.close()

    (values,) = [
        values for (_group, name, _unit), _ts, values, _labels
        in MDFReader(str(path)).iter_channel_arrays(batch_all_groups=True) if name == "Amps"
    ]
    assert values.tolist() == [0.0, -1.0, 0.0]
    assert not np.signbit(values[[0, 2]]).any()



def test_a_single_value_with_a_negative_factor_gives_zero():
    from asammdf.blocks import v4_constants as v4c
    from asammdf.blocks.v4_blocks import ChannelConversion

    conversion = ChannelConversion(conversion_type=v4c.CONVERSION_TYPE_LIN, a=-0.1, b=0.0)

    assert conversion.convert(10) == -1.0
    assert not np.signbit(conversion.convert(0))

_J1939_DBC = """\
VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 2566834927 DM1: 8 ECU
 SG_ Lamp : 0|16@1+ (1,0) [0|65535] "" ECU
 SG_ Status : 16|8@1+ (1,0) [0|255] "" ECU
 SG_ DTC : 24|32@1+ (1,0) [0|4294967295] "" ECU

BA_DEF_ BO_  "VFrameFormat" ENUM  "StandardCAN","ExtendedCAN","reserved","J1939PG";
BA_DEF_ "ProtocolType" STRING ;
BA_DEF_DEF_  "VFrameFormat" "J1939PG";
BA_DEF_DEF_  "ProtocolType" "J1939";
BA_ "ProtocolType" "J1939";
"""


def test_a_j1939_signal_not_available_throughout_is_kept(tmp_path):
    # Lamp reads 0xFF00 ("not available") in every frame; Status only in some.
    dbc = tmp_path / "dm1.dbc"
    dbc.write_text(_J1939_DBC, encoding="utf-8")
    frames = [
        (i * 0.01, 0x18FECAEF,
         bytes([0x00, 0xFF, 0xFF if i % 2 else 7]) + (61765).to_bytes(4, "little") + b"\xFF")
        for i in range(10)
    ]

    asc, mf4 = _both(tmp_path, dbc, frames, extended=True)

    assert asc["DM1::Lamp"].tolist() == [65280.0] * 10
    assert set(mf4) == set(asc)
    for key in asc:
        assert mf4[key].tolist() == asc[key].tolist(), key


def test_extract_signal_still_converts_when_asked_for_physical_values():
    from asammdf.blocks import bus_logging_utils
    from canmatrix import Signal

    signal = Signal(name="Off4", start_bit=28, size=8, is_little_endian=True,
                    is_signed=True, factor=0.5, offset=1)
    payload = np.zeros((2, 8), dtype=np.uint8)
    payload[:, 3] = 0xC0   # bits 28-31 of 0xFC
    payload[:, 4] = 0x0F   # bits 32-35
    payload[1, 3:5] = (0x50, 0x00)

    raw = bus_logging_utils.extract_signal(signal, payload, raw=True)
    physical = bus_logging_utils.extract_signal(signal, payload, raw=False)

    assert raw.tolist() == [-4, 5]
    assert physical.tolist() == [-1.0, 3.5]


def test_installing_twice_does_not_wrap_the_wrappers():
    from asammdf.blocks import bus_logging_utils, v2_v3_blocks, v4_blocks

    from core.asammdf_fixes import install

    install()
    install()

    for function in (
        bus_logging_utils.extract_signal,
        bus_logging_utils.extract_mux,
        v4_blocks.ChannelConversion.convert,
        v2_v3_blocks.ChannelConversion.convert,
    ):
        original = function.__wrapped__
        assert not hasattr(original, "__wrapped__"), function
        assert original.__module__.startswith("asammdf."), function
