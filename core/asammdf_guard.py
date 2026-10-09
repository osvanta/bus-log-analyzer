# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Stop asammdf writing past the end of a channel's sample buffer.

asammdf copies a channel's bytes out of its records in C: one channel at a
time with ``get_channel_raw_bytes``, or a whole group of 100 channels or more
with ``get_channel_raw_bytes_parallel``. A channel whose bytes run past the
end of the record is padded with zeros. A channel that *starts* past the end
gets ``byte_offset - record_size`` more zeros per record than the buffer
allocated for it holds, and they land on whatever memory follows.

Some measurements declare such a channel. Each Load + Decode of one corrupted
the heap: usually nothing showed, and a later load crashed on the load thread
inside the memory allocator. asammdf 8.8.25 and 8.8.27, and its master
branch as of September 2026, still overrun.

``install()`` wraps both functions where asammdf's readers look them up. A
channel that starts past its record is copied as if it started exactly at the
end. asammdf handles that case correctly, and it gives the same all-zero bytes
without the overrun. Every other channel reaches the C code unchanged.
``get_channel_raw_bytes_complete`` is left alone: it copies with a fixed
length, so it can read past a record but never writes past its buffer.
"""

from __future__ import annotations


def install() -> None:
    """Guard asammdf's per-channel copies for the rest of the process.

    Idempotent: the originals always come from ``cutils``, so installing
    twice does not wrap the wrappers.
    """
    try:
        from asammdf.blocks import cutils, mdf_v3, mdf_v4
    except ImportError:
        return  # each reader reports a missing asammdf itself

    copy_channel = cutils.get_channel_raw_bytes
    copy_channels = cutils.get_channel_raw_bytes_parallel

    def get_channel_raw_bytes(data, record_size, byte_offset, byte_count):
        return copy_channel(data, record_size, min(byte_offset, record_size), byte_count)

    def get_channel_raw_bytes_parallel(data, record_size, signals, *thread_count):
        # signals holds one (byte_offset, byte_count) pair per channel.
        if any(signal[0] > record_size for signal in signals):
            signals = [(min(signal[0], record_size), *signal[1:]) for signal in signals]
        return copy_channels(data, record_size, signals, *thread_count)

    mdf_v3.get_channel_raw_bytes = get_channel_raw_bytes
    mdf_v4.get_channel_raw_bytes = get_channel_raw_bytes
    mdf_v4.get_channel_raw_bytes_parallel = get_channel_raw_bytes_parallel
