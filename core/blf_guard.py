# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Stop python-can's BLF reader looping for ever on an object of size zero.

``BLFReader._parse_data`` steps from one object to the next by the size the
object states for itself. An object that states a size of zero is found again
at the same place, so the walk never ends: opening such a log froze the
window for good, because the channel pre-scan reads through this reader on
the GUI thread. python-can 4.6.1 still does it.

No object is smaller than its 16-byte base header. ``install()`` wraps
``BLFReader._parse_container``, which every python-can BLF read passes
through, this application's subclass of it included. A container is searched
for an object that states less than that; the objects before it are read as
before and the read then fails with ``BLFParseError``, the error the
application's own bulk reader gives for the same object.
"""

from __future__ import annotations

import re

_BASE_HEADER_SIZE = 16

# "LOBJ", header size and version, then an object size below 16. A match is
# only a candidate: the same bytes may sit inside a frame's payload, so the
# object chain is walked before anything is refused.
_UNDERSIZED = re.compile(rb"LOBJ.{4}[\x00-\x0f]\x00\x00\x00", re.DOTALL)


def undersized_object_offset(data: bytes) -> int | None:
    """Where the object chain of *data* reaches an object below 16 bytes.

    None when it reaches none. The chain is followed as python-can follows
    it: each object is looked for within eight bytes of the last one's end.
    """
    if _UNDERSIZED.search(data) is None:
        return None
    end = len(data)
    pos = 0
    while pos <= end:
        at = data.find(b"LOBJ", pos, pos + 8)
        if at < 0 or at + _BASE_HEADER_SIZE > end:
            return None
        size = int.from_bytes(data[at + 8:at + 12], "little")
        if size < _BASE_HEADER_SIZE:
            return at
        pos = at + size
    return None


def install() -> None:
    """Guard python-can's BLF reader for the rest of the process.

    Idempotent: the wrapper carries the original, so installing twice does
    not wrap the wrapper.
    """
    try:
        from can.io import blf
    except ImportError:
        return  # each reader reports a missing python-can itself

    current = blf.BLFReader._parse_container
    parse_container = getattr(current, "unguarded", current)

    def _parse_container(self, data):
        if self._tail:
            # An object header may be split between two containers.
            data = b"".join((self._tail, data))
            self._tail = b""
        cut = undersized_object_offset(data)
        if cut is None:
            yield from parse_container(self, data)
            return
        yield from parse_container(self, data[:cut])
        raise blf.BLFParseError("Object smaller than its own header")

    _parse_container.unguarded = parse_container
    blf.BLFReader._parse_container = _parse_container
