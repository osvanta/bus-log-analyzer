# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""An object that states a size below its own header must not stall a reader.

Opening a log whose last object stated a size of zero froze the window for
good: the channel pre-scan reads through python-can on the GUI thread, and
python-can found that object again and again. Such a log now opens with the
frames before that object and a warning that the rest was skipped. Every read
that could loop runs in a child process with a time limit, so a regression
fails here instead of freezing the test run.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from core.blf_guard import undersized_object_offset
from tests.hostile_load import load_in_child, run_capped

_FILE_HEADER = struct.Struct("<4sLBBBBBBBBQQLL8H8H")
_BASE = struct.Struct("<4sHHLL")
_V1 = struct.Struct("<LHHQ")
_CONTAINER = struct.Struct("<H6xL4x")
_CAN_MSG = struct.Struct("<HBBL8s")

FRAMES = 40


def _can_frame(index: int) -> bytes:
    """A CAN_MESSAGE object: EngineControl (0x100) on channel 1."""
    body = _CAN_MSG.pack(1, 0, 8, 0x100, struct.pack("<HB5x", 1000 + index, 40))
    header = _V1.pack(2, 0, 0, 1_000_000 * (index + 1))
    size = _BASE.size + len(header) + len(body)
    return _BASE.pack(b"LOBJ", _BASE.size + len(header), 1, size, 1) + header + body


def _container(chunk: bytes) -> bytes:
    payload = zlib.compress(chunk)
    size = _BASE.size + _CONTAINER.size + len(payload)
    return (_BASE.pack(b"LOBJ", _BASE.size, 1, size, 10)
            + _CONTAINER.pack(2, len(chunk)) + payload + bytes(size % 4))


def _blf(path: Path, *containers: bytes) -> Path:
    body = b"".join(_container(chunk) for chunk in containers)
    start = (2026, 7, 2, 21, 12, 3, 37, 0)
    header = _FILE_HEADER.pack(b"LOGG", 144, 0, 0, 0, 0, 2, 6, 8, 1,
                               144 + len(body), 144 + len(body), 0, 0,
                               *start, *start)
    path.write_bytes(header + bytes(144 - len(header)) + body)
    return path


_GOOD = b"".join(_can_frame(index) for index in range(FRAMES))

# What the size field of the bad object says, and its header version: the
# first is an object of nothing but zeros after its signature.
_BAD_OBJECTS = {
    "zero size, all zeros": _BASE.pack(b"LOBJ", 0, 0, 0, 0),
    "zero size, version 1": _BASE.pack(b"LOBJ", 32, 1, 0, 1) + bytes(32),
    "zero size, version 2": _BASE.pack(b"LOBJ", 48, 2, 0, 86) + bytes(48),
    "size 8": _BASE.pack(b"LOBJ", 32, 1, 8, 1) + bytes(32),
    "size 15": _BASE.pack(b"LOBJ", 32, 1, 15, 1) + bytes(32),
}


# ── Finding the object ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", _BAD_OBJECTS.values(), ids=_BAD_OBJECTS.keys())
def test_an_undersized_object_on_the_chain_is_found(bad):
    assert undersized_object_offset(_GOOD + bad) == len(_GOOD)
    assert undersized_object_offset(bad + _GOOD) == 0


def test_a_good_log_has_none():
    assert undersized_object_offset(_GOOD) is None
    assert undersized_object_offset(b"") is None


def test_the_same_bytes_inside_a_payload_are_not_an_object():
    # A frame whose eight data bytes spell an object header of size zero
    # is traffic, not structure: the chain steps over it.
    body = _CAN_MSG.pack(1, 0, 8, 0x100, b"LOBJ" + bytes(4)) + bytes(8)
    header = _V1.pack(2, 0, 0, 5)
    size = _BASE.size + len(header) + len(body)
    frame = _BASE.pack(b"LOBJ", _BASE.size + len(header), 1, size, 1) + header + body
    assert undersized_object_offset(_GOOD + frame + _GOOD) is None


def test_an_object_cut_by_the_end_of_the_data_is_left_to_the_reader():
    # Its size is not all there yet: the next container brings the rest.
    assert undersized_object_offset(_GOOD + b"LOBJ" + bytes(6)) is None


# ── Opening such a log ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", _BAD_OBJECTS.values(), ids=_BAD_OBJECTS.keys())
def test_a_log_ending_in_an_undersized_object_opens_with_its_good_frames(
        tmp_path, sample_dbc_path, bad):
    path = _blf(tmp_path / "undersized.blf", _GOOD, bad)

    result = load_in_child(path, sample_dbc_path, tmp_path)

    _assert_the_good_part_loaded(result)


@pytest.mark.parametrize("bad", _BAD_OBJECTS.values(), ids=_BAD_OBJECTS.keys())
def test_frames_after_an_undersized_object_are_skipped_with_a_warning(
        tmp_path, sample_dbc_path, bad):
    # Nothing after the object can be found again: its size is the only
    # way to the next one.
    path = _blf(tmp_path / "middle.blf", _GOOD + bad + _GOOD)

    result = load_in_child(path, sample_dbc_path, tmp_path)

    _assert_the_good_part_loaded(result)


def test_a_log_that_starts_with_an_undersized_object_is_refused(
        tmp_path, sample_dbc_path):
    path = _blf(tmp_path / "first.blf", _BAD_OBJECTS["zero size, all zeros"] + _GOOD)

    result = load_in_child(path, sample_dbc_path, tmp_path)

    assert result["channels"] == []
    assert result["outcome"] == "refused"


def _assert_the_good_part_loaded(result: dict) -> None:
    # The pre-scan offers the channel and frame of the good part.
    assert result["channels"] == [["CAN", 1]]
    assert result["ids"] == [0x100]
    assert result["outcome"] == "loaded", result
    samples = {name.split("::")[-1]: count for name, count in result["signals"].items()}
    assert samples["EngSpeed"] == FRAMES
    assert result["frames"] == FRAMES
    assert len(result["warnings"]) == 1
    assert "smaller than its own header" in result["warnings"][0]


_EVERY_READER = r"""
import sys
import can
import core.readers  # installs the guards, as the application does
from core.blf_reader import BLFReaderService

path = sys.argv[1]
for name, read in (
    ("python-can", lambda: sum(1 for _ in can.BLFReader(path))),
    ("frames", lambda: sum(1 for _ in BLFReaderService(path))),
    ("tuples", lambda: sum(1 for _ in BLFReaderService(path).iter_raw_tuples())),
    ("batches", lambda: sum(len(b[1]) for b in BLFReaderService(path).iter_raw_batches())),
    ("per object", lambda: sum(
        len(b[1]) for b in BLFReaderService(path).iter_raw_batches_per_object())),
):
    try:
        print(name, "read", read(), flush=True)
    except Exception as exc:
        print(name, "refused", type(exc).__name__, flush=True)
"""


def test_no_blf_reader_loops_on_a_zero_size_object(tmp_path):
    path = _blf(tmp_path / "zero.blf", _GOOD, _BAD_OBJECTS["zero size, all zeros"])

    stdout, stderr, _code = run_capped(
        ["-c", _EVERY_READER, str(path)], what="reading the log")

    lines = stdout.splitlines()
    assert len(lines) == 5, stderr[-3000:]
    for line in lines:
        assert " refused " in line or line.endswith(f" read {FRAMES}"), line
    # The reader the application loads with keeps the good frames.
    assert f"batches read {FRAMES}" in lines
