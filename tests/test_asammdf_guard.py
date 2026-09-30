# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A channel that starts past the end of its record must not overrun memory.

asammdf's C copy wrote past its output buffer for such a channel. The heap
corruption did nothing visible at first, and a later Load + Decode crashed on
the load thread. ``python -X dev`` checks every buffer when it is freed, so
each end-to-end read runs in a fresh interpreter with those checks on.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

asammdf = pytest.importorskip("asammdf")
from asammdf.blocks import cutils, mdf_v4  # noqa: E402

from core.asammdf_guard import install  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = 5

_READ = r"""
import sys
from core.readers.mdf_reader import MDFReader
if sys.argv[2] == "unguarded":
    from asammdf.blocks import cutils, mdf_v4
    mdf_v4.get_channel_raw_bytes = cutils.get_channel_raw_bytes
    mdf_v4.get_channel_raw_bytes_parallel = cutils.get_channel_raw_bytes_parallel
for (_group, name, _unit), _ts, values, _labels in MDFReader(sys.argv[1]).iter_channel_arrays(
    batch_all_groups=True
):
    print(name, values.tolist(), flush=True)
"""


def _measurement_with_a_channel_past_its_record(path: Path, channels: int) -> Path:
    """An MF4 whose last channel, Flag, starts one byte past its record."""
    t = np.arange(SAMPLES) * 0.1
    signals = [
        asammdf.Signal(np.arange(SAMPLES, dtype=np.float64) + i, t, name=f"Speed{i}")
        for i in range(channels - 1)
    ]
    signals.append(asammdf.Signal(np.full(SAMPLES, 7, dtype=np.uint8), t, name="Flag"))
    mdf = asammdf.MDF(version="4.10")
    mdf.append(signals)
    mdf.save(path, overwrite=True)
    mdf.close()

    with asammdf.MDF(path) as mdf:
        group = mdf.groups[0]
        record_size = group.channel_group.samples_byte_nr + group.channel_group.invalidation_bytes_nr
        address = next(ch for ch in group.channels if ch.name == "Flag").address

    raw = bytearray(path.read_bytes())
    # CN block: 24-byte header, its links, then four one-byte fields before
    # the 32-bit byte offset.
    link_count = struct.unpack_from("<Q", raw, address + 16)[0]
    struct.pack_into("<I", raw, address + 24 + 8 * link_count + 4, record_size + 1)
    path.write_bytes(raw)
    return path


def _read_with_memory_checks(path: Path, mode: str):
    return subprocess.run(
        [sys.executable, "-X", "dev", "-c", _READ, str(path), mode],
        cwd=REPO_ROOT,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )


@pytest.mark.parametrize(
    "channels", [3, 120], ids=["one channel at a time", "group copied in parallel"],
)
def test_a_channel_past_its_record_reads_as_zeros_without_an_overrun(tmp_path, channels):
    path = _measurement_with_a_channel_past_its_record(tmp_path / "past.mf4", channels)

    completed = _read_with_memory_checks(path, "guarded")

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert "bad trailing pad byte" not in completed.stderr
    assert f"Flag {[0.0] * SAMPLES}" in completed.stdout
    assert "Speed1 [1.0, 2.0, 3.0, 4.0, 5.0]" in completed.stdout


def test_the_measurement_overruns_without_the_guard(tmp_path):
    # Proves the check above would catch the overrun, not pass by chance.
    path = _measurement_with_a_channel_past_its_record(tmp_path / "past.mf4", 3)

    completed = _read_with_memory_checks(path, "unguarded")

    if completed.returncode == 0:
        pytest.skip("this asammdf no longer overruns; core/asammdf_guard.py can go")
    assert "Fatal Python error: _PyMem_DebugRawFree: bad trailing pad byte" in completed.stderr


def test_channels_inside_their_record_are_copied_unchanged():
    install()
    record_size = 17
    data = bytes(range(record_size * SAMPLES))
    # Whole, ending at the record's end, and running past it (zero-padded).
    layouts = [(0, 8), (8, 8), (16, 1), (9, 8), (16, 2), (17, 1)]

    for byte_offset, byte_count in layouts:
        assert mdf_v4.get_channel_raw_bytes(data, record_size, byte_offset, byte_count) == \
            cutils.get_channel_raw_bytes(data, record_size, byte_offset, byte_count)
    signals = [list(layout) for layout in layouts]
    assert mdf_v4.get_channel_raw_bytes_parallel(data, record_size, signals, 2) == \
        cutils.get_channel_raw_bytes_parallel(data, record_size, signals, 2)


def test_a_channel_past_its_record_copies_as_zeros():
    install()
    record_size = 17
    data = bytes(range(record_size * SAMPLES))

    assert mdf_v4.get_channel_raw_bytes(data, record_size, 25, 8) == bytearray(8 * SAMPLES)
    inside, past = mdf_v4.get_channel_raw_bytes_parallel(data, record_size, [[0, 8], [25, 8]], 2)
    assert inside == cutils.get_channel_raw_bytes(data, record_size, 0, 8)
    assert past == bytearray(8 * SAMPLES)
