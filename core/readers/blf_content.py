# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Which buses a BLF actually contains.

python-can's ``BLFReader`` yields CAN messages and silently skips every other
object type, so a Vector log holding a bus it does not model reads as an empty
file rather than an unsupported one.  That is the worst possible failure mode:
no error, no frames, and no indication of why.

This module answers "what is in here?" cheaply, without decoding anything, so
the load path can say what the file holds instead of producing an empty
measurement.  CAN and LIN both decode (see ``core.blf_reader``), so what
survives that check is a log carrying neither.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

# Header layout, matching python-can's reader so CAN detection here agrees
# with what BLFCANReader will actually yield.
_BASE = struct.Struct("<4sHHLL")
_LOG_CONTAINER = struct.Struct("<H6xL4x")

_LOG_CONTAINER_TYPE = 10
_NO_COMPRESSION = 0
_ZLIB_DEFLATE = 2

# The CAN objects python-can turns into messages.  Anything outside this set
# does not become a frame, however CAN-ish it looks.
_CAN_TYPES = frozenset({1, 73, 86, 100, 101})

# LIN frame objects: LIN_MESSAGE (v1) and LIN_MESSAGE2 (v2, what modern CANoe
# writes).  Bus-management events — sleep, wakeup, schedule change, the various
# error objects — are excluded on purpose: they appear in CAN logs that carry a
# LIN side channel, and treating them as traffic would misreport those files.
_LIN_TYPES = frozenset({11, 57})

# How much of the file to read before giving up.  A LIN-only log has to be
# scanned to the end to prove the absence of CAN, so the budget bounds the
# pathological case; the common case exits at the first CAN object.
_SCAN_BUDGET_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class BLFBusContent:
    """What a BLF holds, as far as the scan got."""

    has_can: bool
    has_lin: bool
    can_objects: int
    lin_objects: int
    truncated: bool


def _iter_object_types(data: bytes, tail: bytes) -> tuple[list[int], bytes]:
    """Yield object types in one decompressed container, plus the leftover.

    Objects straddle container boundaries, so the unconsumed remainder is
    carried into the next container exactly as python-can does.
    """
    data = tail + data
    types: list[int] = []
    pos = 0
    consumed = 0
    while True:
        consumed = pos
        try:
            # Objects are padded to a 4-byte boundary; the signature search
            # skips that padding without having to model it.
            pos = data.index(b"LOBJ", pos, pos + 8)
        except ValueError:
            break
        if pos + _BASE.size > len(data):
            break
        _signature, _header_size, _version, obj_size, obj_type = _BASE.unpack_from(
            data, pos
        )
        if obj_size < _BASE.size or pos + obj_size > len(data):
            break
        types.append(obj_type)
        pos += obj_size
        consumed = pos
    return types, data[consumed:]


def blf_bus_content(path: str | Path) -> BLFBusContent:
    """Report which buses *path* carries, scanning only as far as needed.

    Returns an empty result rather than raising for a file that is not a
    readable BLF: callers use this to improve an error message, and a probe
    that itself fails must not replace the real one.
    """
    can_objects = 0
    lin_objects = 0
    truncated = False

    try:
        with open(path, "rb") as handle:
            header = handle.read(8)
            if len(header) < 8 or header[:4] != b"LOGG":
                return BLFBusContent(False, False, 0, 0, False)
            handle.seek(struct.unpack_from("<I", header, 4)[0])

            tail = b""
            scanned = 0
            while True:
                raw_header = handle.read(_BASE.size)
                if len(raw_header) < _BASE.size:
                    break
                signature, _hs, _hv, obj_size, obj_type = _BASE.unpack(raw_header)
                if signature != b"LOBJ" or obj_size < _BASE.size:
                    break
                body = handle.read(obj_size - _BASE.size)
                handle.read(obj_size % 4)

                scanned += obj_size
                if scanned > _SCAN_BUDGET_BYTES:
                    truncated = True
                    break

                if obj_type != _LOG_CONTAINER_TYPE:
                    continue
                method, _size = _LOG_CONTAINER.unpack_from(body)
                payload = body[_LOG_CONTAINER.size:]
                if method == _ZLIB_DEFLATE:
                    try:
                        payload = zlib.decompress(payload)
                    except zlib.error:
                        truncated = True
                        break
                elif method != _NO_COMPRESSION:
                    truncated = True
                    break

                types, tail = _iter_object_types(payload, tail)
                for obj in types:
                    if obj in _CAN_TYPES:
                        can_objects += 1
                    elif obj in _LIN_TYPES:
                        lin_objects += 1
                if can_objects:
                    # The file has CAN traffic, so the normal reader applies
                    # and nothing further needs proving.
                    truncated = True
                    break
    except OSError:
        return BLFBusContent(False, False, 0, 0, False)

    return BLFBusContent(
        has_can=can_objects > 0,
        has_lin=lin_objects > 0,
        can_objects=can_objects,
        lin_objects=lin_objects,
        truncated=truncated,
    )
