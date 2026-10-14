# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The bulk BLF reader yields exactly the per-object reader's rows.

``BLFReaderService.iter_raw_batches`` parses whole blocks with NumPy;
``iter_raw_batches_per_object`` walks one object at a time the way python-can
does, and is the reference. The files here are built to break the bulk
reader: ``LOBJ`` inside payloads, objects split across containers and across
the reader's blocks, both header versions, every frame type, bus events,
objects it must skip, and payloads shorter than they claim.
"""
from __future__ import annotations

import random
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

import core.blf_bulk_reader as bulk
from core.blf_reader import BLFReadError, BLFReaderService

_FILE_HEADER = struct.Struct("<4sLBBBBBBBBQQLL8H8H")
_BASE = struct.Struct("<4sHHLL")
_V1 = struct.Struct("<LHHQ")
_V2 = struct.Struct("<LBxHQ8x")
_CONTAINER = struct.Struct("<H6xL4x")
_CAN_MSG = struct.Struct("<HBBL8s")
_CAN_ERROR = struct.Struct("<HHLBBBxLLH2x8s")
_CAN_FD = struct.Struct("<HBBLLBBB5x64s")
_CAN_FD64 = struct.Struct("<BBBBLLLLLLLHBBL")

_EXT = 0x8000_0000


# ── Building BLF files ───────────────────────────────────────────────────

def _object(obj_type: int, body: bytes, timestamp: int, version: int = 1,
            time_flags: int = 2, padding: bytes = b"\0") -> bytes:
    if version == 1:
        header = _V1.pack(time_flags, 0, 0, timestamp)
    elif version == 2:
        header = _V2.pack(time_flags, 0, 0, timestamp)
    else:
        header = bytes(16)          # a version python-can does not read
    header_size = _BASE.size + len(header)
    size = header_size + len(body)
    return (
        _BASE.pack(b"LOBJ", header_size, version, size, obj_type)
        + header + body + padding * (size % 4)
    )


def _payload(rng: random.Random, length: int) -> bytes:
    """Random bytes that now and then contain a false ``LOBJ`` signature."""
    data = bytearray(rng.getrandbits(8) for _ in range(length))
    if length >= 4 and rng.random() < 0.3:
        at = rng.randrange(length - 3)
        data[at:at + 4] = b"LOBJ"
    return bytes(data)


def _random_object(rng: random.Random, timestamp: int) -> bytes:
    version = rng.choice([1, 1, 1, 2, 2, 3])
    time_flags = rng.choice([1, 2])
    kind = rng.randrange(11)
    can_id = rng.getrandbits(29) | (_EXT if rng.random() < 0.4 else 0)
    if kind == 0:
        body = _CAN_MSG.pack(rng.randrange(1, 9), rng.getrandbits(8),
                             rng.randrange(16), can_id, _payload(rng, 8))
        obj_type = rng.choice([1, 86])
    elif kind == 1:
        body = _CAN_ERROR.pack(rng.randrange(1, 9), 0, 0, 0, 0, rng.randrange(16),
                               0, can_id, 0, _payload(rng, 8))
        obj_type = 73
    elif kind == 2:
        body = _CAN_FD.pack(rng.randrange(1, 9), rng.getrandbits(8), rng.randrange(16),
                            can_id, 0, 0, rng.getrandbits(8), rng.randrange(80),
                            _payload(rng, 64)) + bytes(rng.choice([0, 4]))
        obj_type = 100
    elif kind in (3, 4, 5):
        valid = rng.choice([0, 1, 5, 8, 12, 20, 48, 64, 70])
        # Usually exactly the valid bytes; sometimes fewer (zero-padded back
        # up by python-can), or an extended data offset after the payload or
        # inside it. Always within the object, as CANoe writes it.
        stored = valid if rng.random() < 0.7 else rng.randrange(valid + 1)
        header_size = 40 if version == 2 else 32
        ext_offset = 0
        if rng.random() < 0.2:
            ext_offset = header_size + _CAN_FD64.size + rng.randrange(stored + 1)
        body = _CAN_FD64.pack(rng.randrange(1, 256), rng.randrange(16), valid, 0,
                              can_id, 0, rng.choice([0, 0x1000, 0x3000]), 0, 0, 0,
                              0, 0, rng.randrange(3), ext_offset, 0)
        body += _payload(rng, stored) + (bytes(12) if ext_offset else b"")
        obj_type = 101
    elif kind == 6:
        body = bytearray(_payload(rng, rng.choice([136, 136, 100])))
        body[38] = rng.randrange(12)
        body[122 % len(body)] = rng.randrange(4)
        obj_type = 57
    elif kind == 7:
        body = bytearray(_payload(rng, rng.choice([12, 16, 8])))
        body[3] = rng.randrange(12)
        obj_type = 11
    elif kind == 8:
        body = bytes(24)
        obj_type = rng.choice([20, 21, 17, 58])
    else:
        # Objects nobody decodes: markers, FlexRay, app text.
        body = _payload(rng, rng.randrange(0, 300))
        obj_type = rng.choice([96, 29, 65, 120])
    return _object(obj_type, bytes(body), timestamp, version, time_flags)


def _container(chunk: bytes, method: int = 2) -> bytes:
    payload = zlib.compress(chunk) if method == 2 else chunk
    obj_size = _BASE.size + _CONTAINER.size + len(payload)
    return (_BASE.pack(b"LOBJ", _BASE.size, 1, obj_size, 10)
            + _CONTAINER.pack(method, len(chunk)) + payload + bytes(obj_size % 4))


def _write_blf(path: Path, stream: bytes, rng: random.Random,
               container_sizes=(40, 3000), extra_top_level: bool = True,
               cuts: list[int] | None = None, tail: bytes = b"") -> None:
    """Split *stream* into containers at arbitrary byte offsets, as CANoe does.

    *cuts* gives the container sizes instead, the last one repeating; *tail*
    is appended to the file after the last container.
    """
    parts = []
    pos = 0
    while pos < len(stream):
        if cuts:
            size = cuts[min(len(parts), len(cuts) - 1)]
        else:
            size = rng.randrange(*container_sizes)
        chunk = stream[pos:pos + size]
        pos += size
        parts.append(_container(chunk, 2 if cuts or rng.random() < 0.8 else 0))
        if extra_top_level and rng.random() < 0.05:
            # An object outside any container, which python-can skips.
            parts.append(_object(96, bytes(20), 0))
    body = b"".join(parts) + tail
    start = (2026, 7, 2, 21, 12, 3, 37, 0)
    header = _FILE_HEADER.pack(b"LOGG", 144, 0, 0, 0, 0, 2, 6, 8, 1,
                               144 + len(body), 144 + len(stream), 0, 0,
                               *start, *start)
    path.write_bytes(header + bytes(144 - len(header)) + body)


def _random_blf(path: Path, seed: int, objects: int = 1500, **kwargs) -> None:
    rng = random.Random(seed)
    stream = b"".join(
        _random_object(rng, 1_000_000 + index * rng.randrange(1, 50_000))
        for index in range(objects)
    )
    _write_blf(path, stream, rng, **kwargs)


# ── Reading them back ────────────────────────────────────────────────────

def _rows(batches):
    """Every batch's rows as arrays, payload as (n, 64)."""
    bases, columns, payloads = set(), [[] for _ in range(6)], []
    for base_ts, *values, data in batches:
        count = len(values[0])
        bases.add(base_ts)
        for column, value in zip(columns, values):
            column.extend(list(value))
        payloads.append(
            np.frombuffer(bytes(memoryview(data)[:count * 64]), dtype=np.uint8)
            .reshape(count, 64)
        )
    names = ("timestamps", "channels", "arb_ids", "dlcs", "directions", "flags")
    rows = {name: np.array(column) for name, column in zip(names, columns)}
    rows["payload"] = (np.concatenate(payloads) if payloads
                       else np.empty((0, 64), dtype=np.uint8))
    rows["bases"] = bases
    return rows


def _assert_same_rows(path: Path, batch_size: int = 16_384) -> dict:
    bulk_service = BLFReaderService(path)
    fast = _rows(bulk_service.iter_raw_batches(batch_size))
    reference_service = BLFReaderService(path)
    reference = _rows(reference_service.iter_raw_batches_per_object(batch_size))

    assert len(fast["timestamps"]) == len(reference["timestamps"])
    for name in ("timestamps", "channels", "arb_ids", "dlcs", "directions", "flags"):
        assert np.array_equal(fast[name], reference[name]), name
    assert np.array_equal(fast["payload"], reference["payload"])
    assert fast["bases"] == reference["bases"]
    assert bulk_service.lin_event_objects == reference_service.lin_event_objects
    return fast


@pytest.fixture()
def small_blocks(monkeypatch):
    """Blocks of a few hundred bytes, so objects cross block boundaries."""
    monkeypatch.setattr(bulk, "_BLOCK_BYTES", 300)


@pytest.mark.parametrize("seed", range(6))
def test_random_files_match_the_per_object_reader(tmp_path, seed):
    path = tmp_path / f"random_{seed}.blf"
    _random_blf(path, seed)
    rows = _assert_same_rows(path)
    assert len(rows["timestamps"]) > 500


@pytest.mark.parametrize("seed", range(4))
def test_objects_split_across_blocks_match(tmp_path, small_blocks, seed):
    path = tmp_path / f"blocks_{seed}.blf"
    _random_blf(path, 100 + seed, objects=600)
    _assert_same_rows(path, batch_size=7)


def test_an_object_longer_than_a_block_is_carried_whole(tmp_path, monkeypatch):
    # Blocks of one 60-byte container each, objects up to 400 bytes: one
    # object spans several blocks before it can be parsed.
    monkeypatch.setattr(bulk, "_BLOCK_BYTES", 1)
    rng = random.Random(7)
    stream = b"".join(
        _object(101, _CAN_FD64.pack(1, 15, 64, 0, 0x123, 0, 0x1000, 0, 0, 0, 0, 0,
                                    0, 0, 0) + _payload(rng, 64), index * 1000)
        + _object(96, _payload(rng, 300), index * 1000 + 1)
        for index in range(40)
    )
    path = tmp_path / "long_objects.blf"
    _write_blf(path, stream, rng, container_sizes=(50, 70), extra_top_level=False)
    rows = _assert_same_rows(path, batch_size=3)
    assert len(rows["timestamps"]) == 40


def test_a_header_cut_by_a_block_end_is_read_on(tmp_path, monkeypatch):
    """Eight bytes of a 6 KB object end the first block.

    Its size is not readable yet, so the first look into the next block is a
    guess; the guess falls short and has to grow until the object is whole.
    """
    monkeypatch.setattr(bulk, "_BLOCK_BYTES", 4808)
    small = _object(86, _CAN_MSG.pack(1, 0, 8, 0x100, bytes(range(8))), 5)
    assert len(small) == 48
    big = _object(96, bytes(6000), 6)
    stream = small * 100 + big + small * 50
    path = tmp_path / "cut_header.blf"
    _write_blf(path, stream, random.Random(1), extra_top_level=False,
               cuts=[4808, 9000, 3000])
    rows = _assert_same_rows(path)
    assert len(rows["timestamps"]) == 150


def test_unreadable_containers_are_skipped_like_the_per_object_reader(tmp_path):
    """An unknown compression method drops that container's bytes, whole objects here."""
    rng = random.Random(8)
    frame = _object(86, _CAN_MSG.pack(2, 1, 4, 0x321, b"abcd" + bytes(4)), 9)
    unknown = bytearray(_container(frame * 3, 0))
    unknown[_BASE.size:_BASE.size + 2] = (5).to_bytes(2, "little")
    stream_file = tmp_path / "unknown_method.blf"
    _write_blf(stream_file, frame * 10, rng, extra_top_level=False,
               cuts=[480], tail=bytes(unknown) + _container(frame * 4))
    rows = _assert_same_rows(stream_file)
    assert len(rows["timestamps"]) == 14


def test_a_bad_top_level_signature_fails_like_the_per_object_reader(tmp_path):
    frame = _object(86, _CAN_MSG.pack(1, 0, 8, 0x100, bytes(8)), 1)
    path = tmp_path / "bad_top_level.blf"
    _write_blf(path, frame * 10, random.Random(2), extra_top_level=False,
               tail=b"XOBJ" + bytes(28))
    with pytest.raises(BLFReadError):
        list(BLFReaderService(path).iter_raw_batches_per_object())
    with pytest.raises(BLFReadError):
        list(BLFReaderService(path).iter_raw_batches())


def test_a_top_level_size_of_15_reads_to_the_end(tmp_path):
    """python-can reads ``size - 16`` bytes: ``read(-1)``, everything left."""
    frame = _object(86, _CAN_MSG.pack(1, 0, 8, 0x100, bytes(8)), 1)
    path = tmp_path / "short_top_level.blf"
    _write_blf(path, frame * 10, random.Random(2), extra_top_level=False,
               tail=_BASE.pack(b"LOBJ", 16, 1, 15, 96) + _container(frame * 2))
    rows = _assert_same_rows(path)
    assert len(rows["timestamps"]) == 10


def test_a_smaller_top_level_size_fails_like_the_per_object_reader(tmp_path):
    frame = _object(86, _CAN_MSG.pack(1, 0, 8, 0x100, bytes(8)), 1)
    path = tmp_path / "shorter_top_level.blf"
    _write_blf(path, frame * 10, random.Random(2), extra_top_level=False,
               tail=_BASE.pack(b"LOBJ", 16, 1, 8, 96) + _container(frame * 2))
    with pytest.raises(BLFReadError):
        list(BLFReaderService(path).iter_raw_batches_per_object())
    with pytest.raises(BLFReadError):
        list(BLFReaderService(path).iter_raw_batches())


def test_signature_inside_every_payload(tmp_path, small_blocks):
    """A false ``LOBJ`` in each frame sends every block down the slow walk."""
    rng = random.Random(11)
    stream = b"".join(
        _object(86, _CAN_MSG.pack(1, 0, 8, 0x100 + index, b"xLOBJyyy"), index)
        for index in range(300)
    )
    path = tmp_path / "false_signatures.blf"
    _write_blf(path, stream, rng)
    rows = _assert_same_rows(path)
    assert len(rows["timestamps"]) == 300
    assert bytes(rows["payload"][0, :8]) == b"xLOBJyyy"


def test_lin_events_are_counted_like_the_per_object_reader(tmp_path):
    rng = random.Random(3)
    stream = b"".join(
        _object(rng.choice([20, 21, 57]), bytes(136), index) for index in range(200)
    )
    path = tmp_path / "lin_events.blf"
    _write_blf(path, stream, rng)
    service = BLFReaderService(path)
    rows = _rows(service.iter_raw_batches())
    reference = BLFReaderService(path)
    list(reference.iter_raw_batches_per_object())
    assert service.lin_event_objects == reference.lin_event_objects > 0
    assert len(rows["timestamps"]) + service.lin_event_objects == 200


def test_batches_have_the_requested_size(tmp_path, small_blocks):
    path = tmp_path / "batches.blf"
    _random_blf(path, 21, objects=400)
    sizes = [len(batch[1]) for batch in BLFReaderService(path).iter_raw_batches(9)]
    assert all(size == 9 for size in sizes[:-1])
    assert 0 < sizes[-1] <= 9


@pytest.mark.parametrize("fixture", ["blf_path", "lin_blf_path", "mixed_bus_blf_path"])
def test_fixture_files_match(request, fixture):
    _assert_same_rows(request.getfixturevalue(fixture))


def test_empty_log_yields_nothing(tmp_path):
    path = tmp_path / "empty.blf"
    _write_blf(path, b"", random.Random(0))
    assert list(BLFReaderService(path).iter_raw_batches()) == []


@pytest.mark.parametrize("junk", [5, 8, 12, 40])
def test_garbage_between_objects_fails_like_the_per_object_reader(tmp_path, junk):
    """python-can looks for the next object in the eight bytes after the last.

    Four bytes of junk still leave the whole signature in that window; five
    do not.
    """
    rng = random.Random(5)
    good = _object(86, _CAN_MSG.pack(1, 0, 8, 0x100, bytes(8)), 1)
    stream = good * 20 + b"\x01" * junk + good * 20
    path = tmp_path / "garbage.blf"
    _write_blf(path, stream, rng)
    with pytest.raises(BLFReadError):
        list(BLFReaderService(path).iter_raw_batches_per_object())
    with pytest.raises(BLFReadError):
        list(BLFReaderService(path).iter_raw_batches())


@pytest.mark.parametrize("junk", [1, 4])
def test_up_to_four_junk_bytes_between_objects_are_skipped(tmp_path, junk):
    rng = random.Random(5)
    good = _object(86, _CAN_MSG.pack(1, 0, 8, 0x100, bytes(8)), 1)
    stream = good * 20 + b"\x01" * junk + good * 20
    path = tmp_path / "junk.blf"
    _write_blf(path, stream, rng)
    assert len(_assert_same_rows(path)["timestamps"]) == 40


@pytest.mark.parametrize("junk, fails", [(7, False), (8, True), (30, True)])
def test_junk_at_the_end_of_the_log(tmp_path, junk, fails):
    """Fewer than eight bytes after the last object end the log; more are an error."""
    rng = random.Random(4)
    good = _object(86, _CAN_MSG.pack(1, 0, 8, 0x100, bytes(8)), 1)
    path = tmp_path / "trailing.blf"
    _write_blf(path, good * 30 + b"\x02" * junk, rng)
    if fails:
        with pytest.raises(BLFReadError):
            list(BLFReaderService(path).iter_raw_batches_per_object())
        with pytest.raises(BLFReadError):
            list(BLFReaderService(path).iter_raw_batches())
    else:
        assert len(_assert_same_rows(path)["timestamps"]) == 30


def test_padding_that_starts_like_a_signature(tmp_path):
    """Padding bytes of "L": only a whole ``LOBJ`` starts an object."""
    rng = random.Random(9)
    stream = b"".join(
        _object(101, _CAN_FD64.pack(1, 5, 5, 0, 0x200 + index, 0, 0x1000, 0, 0, 0,
                                    0, 0, 0, 0, 0) + bytes(range(index % 3 + 5)),
                index, padding=b"L")
        for index in range(200)
    )
    path = tmp_path / "padding.blf"
    _write_blf(path, stream, rng)
    assert len(_assert_same_rows(path)["timestamps"]) == 200


def test_object_of_size_zero_is_an_error_not_a_hang(tmp_path):
    rng = random.Random(6)
    good = _object(86, _CAN_MSG.pack(1, 0, 8, 0x100, bytes(8)), 1)
    zero = _BASE.pack(b"LOBJ", 32, 1, 0, 86) + bytes(32)
    path = tmp_path / "zero_size.blf"
    _write_blf(path, good * 5 + zero + good * 5, rng)
    with pytest.raises(BLFReadError):
        list(BLFReaderService(path).iter_raw_batches())


def test_a_partial_read_stops_the_workers(tmp_path, monkeypatch):
    """The channel pre-scan reads the first batch and closes the generator."""
    monkeypatch.setattr(bulk, "_BLOCK_BYTES", 2000)
    path = tmp_path / "prescan.blf"
    _random_blf(path, 42, objects=3000)
    batches = BLFReaderService(path).iter_raw_batches(50)
    first = next(batches)
    batches.close()
    assert len(first[1]) == 50


def test_a_buffer_too_short_for_a_signature_has_no_objects():
    # Below four bytes NumPy would read count=-1 as "everything", padding too.
    for data in (b"", b"L", b"LO", b"LOB"):
        scan = bulk._scan(data + b"LOBJ" + bulk._PAD_BYTES[4:], 0.0)
        assert len(scan.starts) == 0
