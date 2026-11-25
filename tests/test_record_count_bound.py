# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A channel group cannot claim more records than its data blocks hold.

A bus-logging MF4 of thirty frames whose channel group claimed four thousand
million records made Decode run without end, taking memory all the while:
the readers step through a group up to the count its header states. The
count is now lowered to what the file holds when the file is opened. Every
load of such a file runs in a child process with a time and a memory limit,
so a regression fails here instead of freezing the test run.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

asammdf = pytest.importorskip("asammdf")

import core.readers  # noqa: E402,F401  (installs the guard, as the application does)
from tests.hostile_load import load_in_child  # noqa: E402

RECORDS = 30

# What the reproducing files claimed, and a count past 32 bits.
_CLAIMS = {"4,294,967,295 records": 0xFFFF_FFFF, "a million million records": 10**12}


# ── Building the files ────────────────────────────────────────────────────

def _bus_log(path: Path) -> Path:
    """A raw CAN recording: EngineControl (0x100), RECORDS frames."""
    import can

    writer = can.MF4Writer(str(path))
    for index in range(RECORDS):
        writer.on_message_received(can.Message(
            timestamp=1.0 + index * 0.01, arbitration_id=0x100,
            data=struct.pack("<HB5x", 1000 + index, 40),
            is_extended_id=False, channel=0))
    writer.stop()
    return path


def _decoded(path: Path, version: str = "4.10", compression: int = 0) -> Path:
    """A measurement of two decoded signals, RECORDS samples each."""
    t = np.arange(RECORDS) * 0.01
    mdf = asammdf.MDF(version=version)
    mdf.append([
        asammdf.Signal(np.arange(RECORDS, dtype=np.float64), t, name="Speed"),
        asammdf.Signal(np.arange(RECORDS, dtype=np.uint16), t, name="Gear"),
    ])
    mdf.save(path, overwrite=True, compression=compression)
    mdf.close()
    return path


def _groups_with_records(path: Path) -> list[int]:
    """Addresses of the channel-group blocks that hold the records."""
    with asammdf.MDF(str(path), process_bus_logging=False) as mdf:
        return [
            group.channel_group.address for group in mdf.groups
            if group.channel_group.cycles_nr == RECORDS
        ]


def _claim(path: Path, records: int) -> Path:
    """Rewrite the record count in the file's channel-group headers."""
    addresses = _groups_with_records(path)
    assert addresses, "the file has no group with the records"
    raw = bytearray(path.read_bytes())
    for address in addresses:
        if raw[address:address + 4] == b"##CG":
            # 24-byte header, six links, the record id, then the count.
            assert struct.unpack_from("<Q", raw, address + 80)[0] == RECORDS
            struct.pack_into("<Q", raw, address + 80, records)
        else:
            # MDF3: "CG", length, three links, record id, channel count,
            # record size, then a 32-bit count.
            assert raw[address:address + 2] == b"CG"
            assert struct.unpack_from("<I", raw, address + 22)[0] == RECORDS
            struct.pack_into("<I", raw, address + 22, min(records, 0xFFFF_FFFF))
    path.write_bytes(raw)
    return path


def _record_counts(path: Path) -> tuple[list[int], list[int]]:
    """The counts asammdf works with: per group, and per virtual group."""
    with asammdf.MDF(str(path), process_bus_logging=False) as mdf:
        groups = [group.channel_group.cycles_nr for group in mdf.groups]
        virtual = [group.cycles_nr for group in mdf.virtual_groups.values()]
    return [n for n in groups if n], [n for n in virtual if n]


# ── The count asammdf is left with ────────────────────────────────────────

@pytest.mark.parametrize("claim", _CLAIMS.values(), ids=_CLAIMS.keys())
def test_a_bus_log_claiming_too_many_records_is_bounded(tmp_path, claim):
    path = _claim(_bus_log(tmp_path / "bus.mf4"), claim)

    assert _record_counts(path) == ([RECORDS], [RECORDS])


def test_the_bus_logging_map_is_built_from_the_bounded_count(tmp_path):
    # asammdf builds that map while it opens the file, from whole groups:
    # with the claimed count it took 270 MB for a file of thirty frames.
    import tracemalloc

    path = _claim(_bus_log(tmp_path / "bus.mf4"), 0xFFFF_FFFF)

    tracemalloc.start()
    try:
        asammdf.MDF(str(path)).close()
        _now, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 20 * 1024 * 1024


@pytest.mark.parametrize("compression", [0, 1, 2], ids=["plain", "deflate", "transposed"])
def test_a_decoded_measurement_claiming_too_many_records_is_bounded(
        tmp_path, compression):
    path = _claim(_decoded(tmp_path / "decoded.mf4", compression=compression),
                  0xFFFF_FFFF)

    assert _record_counts(path) == ([RECORDS], [RECORDS])


@pytest.mark.parametrize("version", ["3.30", "2.14"])
def test_an_mdf3_measurement_claiming_too_many_records_is_bounded(tmp_path, version):
    path = _claim(_decoded(tmp_path / "decoded.mdf", version=version), 0xFFFF_FFFF)

    assert _record_counts(path) == ([RECORDS], [RECORDS])


@pytest.mark.parametrize("version", ["4.10", "3.30"])
def test_a_count_that_is_not_too_large_is_left_alone(tmp_path, version):
    suffix = ".mf4" if version.startswith("4") else ".mdf"
    honest = _decoded(tmp_path / f"honest{suffix}", version=version)
    assert _record_counts(honest) == ([RECORDS], [RECORDS])

    # Fewer than are stored is the file's own statement, and is kept.
    fewer = _claim(_decoded(tmp_path / f"fewer{suffix}", version=version), 10)
    assert _record_counts(fewer) == ([10], [10])


# ── Opening such a file ───────────────────────────────────────────────────

def _samples(result: dict, signal: str) -> int:
    """How many samples the loaded store holds for *signal*."""
    return next(
        count for name, count in result["signals"].items()
        if name.split("::")[-1] == signal
    )


@pytest.mark.parametrize("claim", _CLAIMS.values(), ids=_CLAIMS.keys())
def test_a_bus_log_claiming_too_many_records_decodes(tmp_path, sample_dbc_path, claim):
    path = _claim(_bus_log(tmp_path / "bus.mf4"), claim)

    result = load_in_child(path, sample_dbc_path, tmp_path)

    assert result["outcome"] == "loaded", result
    assert result["ids"] == [0x100]
    assert _samples(result, "EngSpeed") == RECORDS
    assert result["frames"] == RECORDS


@pytest.mark.parametrize("version", ["4.10", "3.30"])
def test_a_decoded_measurement_claiming_too_many_records_loads(tmp_path, version):
    suffix = ".mf4" if version.startswith("4") else ".mdf"
    path = _claim(_decoded(tmp_path / f"decoded{suffix}", version=version),
                  0xFFFF_FFFF)

    result = load_in_child(path, None, tmp_path)

    assert result["outcome"] == "loaded", result
    assert _samples(result, "Speed") == RECORDS
    assert _samples(result, "Gear") == RECORDS
