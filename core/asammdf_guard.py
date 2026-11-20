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

Some measurements declare such a channel. Each Decode of one corrupted
the heap: usually nothing showed, and a later load crashed on the load thread
inside the memory allocator. asammdf 8.8.25 and 8.8.27, and its master
branch as of September 2026, still overrun.

``install()`` wraps both functions where asammdf's readers look them up. A
channel that starts past its record is copied as if it started exactly at the
end. asammdf handles that case correctly, and it gives the same all-zero bytes
without the overrun. Every other channel reaches the C code unchanged.
``get_channel_raw_bytes_complete`` is left alone: it copies with a fixed
length, so it can read past a record but never writes past its buffer.

It also stops a channel group claiming more records than the file holds.
asammdf takes the record count from the group's header. Readers that work
through a group a chunk at a time, this application's included, step up to
that count, and buffers are sized by it. A damaged or hostile file that claims
four thousand million records for thirty made Decode run without end while
memory grew. ``install()`` wraps the ``_read`` of asammdf's MDF3 and MDF4
readers, which every open of a file passes through, and the step inside it
that builds asammdf's bus-logging map: once the groups are known, each count
is lowered to the records its data blocks can hold. A count that is not too
large is never changed.
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

    read_v3 = getattr(mdf_v3.MDF3._read, "unguarded", mdf_v3.MDF3._read)
    read_v4 = getattr(mdf_v4.MDF4._read, "unguarded", mdf_v4.MDF4._read)

    def _read_v3(self, stream, *args, **kwargs):
        read_v3(self, stream, *args, **kwargs)
        bound_record_counts_v3(self, stream)

    def _read_v4(self, stream, *args, **kwargs):
        read_v4(self, stream, *args, **kwargs)
        bound_record_counts_v4(self)

    bus_logging = getattr(
        mdf_v4.MDF4._process_bus_logging, "unguarded", mdf_v4.MDF4._process_bus_logging
    )

    def _process_bus_logging(self):
        # asammdf builds its bus-logging map inside _read, from whole
        # groups: the counts have to be right before it, too.
        bound_record_counts_v4(self)
        bus_logging(self)

    _read_v3.unguarded = read_v3
    _read_v4.unguarded = read_v4
    _process_bus_logging.unguarded = bus_logging
    mdf_v3.MDF3._read = _read_v3
    mdf_v4.MDF4._read = _read_v4
    mdf_v4.MDF4._process_bus_logging = _process_bus_logging


def bound_record_counts_v4(mdf) -> None:
    """Lower each MDF4 group's record count to what its data blocks hold.

    asammdf has by now listed the group's data blocks with their real sizes,
    checked against the end of the file. Groups that share one data block
    (an unsorted data group) each get the bound of the whole block, which is
    all that is known before the records are told apart.
    """
    from asammdf.blocks import v4_constants as v4c

    for index, group in enumerate(mdf.groups):
        channel_group = group.channel_group
        if channel_group.flags & v4c.FLAG_CG_VLSD:
            # Variable-length records: at least their four-byte length each.
            record_size = 4
        else:
            record_size = channel_group.samples_byte_nr
            if not group.uses_ld:
                record_size += channel_group.invalidation_bytes_nr
        if record_size <= 0:
            continue  # no bytes per record: nothing in the file bounds it
        stored = sum(int(block.original_size or 0) for block in group.get_data_blocks())
        held = stored // record_size
        if channel_group.cycles_nr <= held:
            continue
        channel_group.cycles_nr = held
        virtual = mdf.virtual_groups.get(mdf.virtual_groups_map.get(index))
        if virtual is not None and virtual.cycles_nr > held:
            virtual.cycles_nr = held


def bound_record_counts_v3(mdf, stream) -> None:
    """Lower each MDF3 group's record count to what the file can hold.

    An MDF3 data block has no length of its own: asammdf sizes it from the
    record counts. A block that would run past the end of the file is cut
    at the next block the file's links point to, or at the end of the file
    when the data comes last, as it does in a recording that was cut short.
    """
    stream.seek(0, 2)
    file_size = stream.tell()
    linked: set[int] | None = None
    for group in mdf.groups:
        for block in group.data_blocks:
            start = int(block.address)
            if start + int(block.original_size or 0) <= file_size:
                continue
            if linked is None:
                linked = _linked_addresses_v3(mdf)
            end = min((address for address in linked if address > start), default=file_size)
            block.original_size = block.compressed_size = max(min(end, file_size) - start, 0)
        channel_group = group.channel_group
        record_size = channel_group.samples_byte_nr
        if record_size <= 0:
            continue
        held = sum(int(block.original_size or 0) for block in group.data_blocks) // record_size
        if channel_group.cycles_nr > held:
            channel_group.cycles_nr = held


def _linked_addresses_v3(mdf) -> set[int]:
    """Every address the blocks of an MDF3 file state: their own and their links."""
    found: set[int] = set()

    def add(block) -> None:
        if block is None:
            return
        names = [name for name in getattr(block, "__slots__", ()) if name.endswith("_addr")]
        for name in ("address", *names):
            value = getattr(block, name, None)
            if isinstance(value, int) and value > 0:
                found.add(value)

    for group in mdf.groups:
        add(group.data_group)
        add(group.channel_group)
        add(group.trigger)
        for channel in group.channels:
            add(channel)
            add(getattr(channel, "conversion", None))
            add(getattr(channel, "source", None))
    return found
