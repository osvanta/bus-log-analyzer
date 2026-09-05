# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Generate the binary measurement fixtures for the Osvanta Bus Log Analyzer
test suite: sample.blf, sample.asc, sample_lin.mf4, sample_lin.blf,
sample_lin.asc and sample_mixed_bus.blf.

Run once after cloning, or whenever sample.dbc changes:

    python tests/fixtures/_generate.py

Output files are intentionally excluded from git (.gitignore: *.blf, *.asc,
*.mf4).  The .ldf inputs are text and are committed.
Each file contains three repeated bursts of CAN frames that match sample.dbc:

  0x100  EngineControl  EngSpeed=1200.0 rpm, Throttle=50.0 %
  0x200  GearStatus     Gear=4 (Drive)
  0x300  DiagRequest    8 bytes, no signals (exercises decoded_no_signals path)
"""
from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent


def _eng_control_payload() -> bytes:
    # EngSpeed raw = 1200.0 / 0.5 = 2400 = 0x0960, little-endian at bits 0-15
    # Throttle raw = 50.0 / 0.5 = 100 = 0x64, at bits 16-23
    return bytes([0x60, 0x09, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00])


def _gear_status_payload() -> bytes:
    # Gear raw = 4 (Drive), little-endian at bits 0-7
    return bytes([0x04, 0x00, 0x00, 0x00])


def _diag_request_payload() -> bytes:
    return bytes(8)


def _frames():
    """Yield (timestamp_s, arb_id, data) tuples for one burst."""
    burst = [
        (0.001, 0x100, _eng_control_payload()),
        (0.002, 0x200, _gear_status_payload()),
        (0.003, 0x300, _diag_request_payload()),
    ]
    for offset in (0.0, 0.010, 0.020):
        for ts, arb_id, data in burst:
            yield ts + offset, arb_id, data


def generate_blf(out_path: Path) -> None:
    import can

    start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    messages = [
        can.Message(
            timestamp=ts,
            arbitration_id=arb_id,
            data=data,
            is_extended_id=False,
            channel=1,
        )
        for ts, arb_id, data in _frames()
    ]

    with can.BLFWriter(str(out_path), channel=1) as writer:
        writer.start_timestamp = start_time.timestamp()
        for msg in messages:
            writer(msg)

    print(f"Written: {out_path} ({len(messages)} frames)")


def generate_asc(out_path: Path) -> None:
    import can

    start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    messages = [
        can.Message(
            timestamp=ts,
            arbitration_id=arb_id,
            data=data,
            is_extended_id=False,
            channel=1,
        )
        for ts, arb_id, data in _frames()
    ]

    with can.ASCWriter(str(out_path)) as writer:
        writer.start_timestamp = start_time.timestamp()
        for msg in messages:
            writer(msg)

    print(f"Written: {out_path} ({len(messages)} frames)")




# ── LIN bus-logging MF4 ───────────────────────────────────────────────────
#
# Frame IDs and payloads line up with sample.ldf so decoded values can be
# asserted directly:
#
#   0x20 DoorCmd     byte0 -> WindowPos, byte1 low 2 bits -> LockState
#   0x21 DoorStatus  byte0 -> MirrorAngle
#
# Written as an ASAM MDF bus-logging group: a structured LIN_Frame channel,
# an acquisition source marked BUS_TYPE_LIN, and FLAG_CG_BUS_EVENT on the
# channel group. asammdf needs all three before extract_bus_logging() will
# treat the group as a LIN bus at all.
_LIN_FRAMES = [
    # (bus_channel, frame_id, data_length, payload bytes)
    (1, 0x20, 2, (10, 1)),
    (1, 0x21, 1, (20, 0)),
    (1, 0x20, 2, (30, 2)),
    (1, 0x21, 1, (40, 0)),
]


def generate_lin_mf4(out_path: Path) -> None:
    """Write a small LIN bus-logging MF4 that pairs with sample.ldf."""
    import numpy as np
    from asammdf import MDF, Signal
    from asammdf.blocks import v4_constants as v4c
    from asammdf.blocks.v4_blocks import SourceInformation

    count = len(_LIN_FRAMES)
    record = np.zeros(count, dtype=np.dtype([
        ("LIN_Frame.BusChannel", "<u1"),
        ("LIN_Frame.ID", "<u1"),
        ("LIN_Frame.DataLength", "<u1"),
        ("LIN_Frame.DataBytes", "(8,)u1"),
    ]))
    for index, (bus, frame_id, length, payload) in enumerate(_LIN_FRAMES):
        record["LIN_Frame.BusChannel"][index] = bus
        record["LIN_Frame.ID"][index] = frame_id
        record["LIN_Frame.DataLength"][index] = length
        for byte_index, value in enumerate(payload):
            record["LIN_Frame.DataBytes"][index, byte_index] = value

    signal = Signal(
        samples=record,
        timestamps=np.arange(count, dtype=np.float64) * 0.01,
        name="LIN_Frame",
    )
    signal.source = SourceInformation(
        source_type=v4c.SOURCE_BUS, bus_type=v4c.BUS_TYPE_LIN
    )

    mdf = MDF(version="4.10")
    try:
        group_index = mdf.append(
            [signal], acq_name="LIN", comment="LIN bus logging"
        )
        mdf.groups[group_index].channel_group.flags = v4c.FLAG_CG_BUS_EVENT
        mdf.save(out_path, overwrite=True)
    finally:
        mdf.close()
    print(f"Written: {out_path} ({count} LIN frames)")


# ── LIN-only BLF ──────────────────────────────────────────────────────────
#
# A Vector log holding LIN traffic and no CAN.  python-can's BLFWriter only
# emits CAN objects, so the container is assembled here from the documented
# LinMessage2 layout instead — the same layout the reader parses, verified
# against a real 7,275-object CANoe log.

_BLF_FILE_HEADER = struct.Struct("<4sLBBBBBBBBQQLL8H8H")
_BLF_FILE_HEADER_SIZE = 144
_BLF_OBJ_HEADER = struct.Struct("<4sHHLL")
_BLF_OBJ_HEADER_V1 = struct.Struct("<LHHQ")
_BLF_LOG_CONTAINER = struct.Struct("<H6xL4x")

_LIN_MESSAGE2 = 57
_LOG_CONTAINER = 10
_ZLIB_DEFLATE = 2

# 40 bytes of LinMessageDescriptor, 72 of databyte timestamps, 8 of payload,
# then the trailing status fields, which are left zeroed.
_LIN_BODY_SIZE = 136


def _lin_message2_object(timestamp_ns: int, channel: int, frame_id: int,
                         payload: bytes) -> bytes:
    body = bytearray(_LIN_BODY_SIZE)
    struct.pack_into("<QLH2x", body, 0, timestamp_ns, 19200, channel)
    # supplierId, messageId, nad, id, dlc, checksumModel
    struct.pack_into("<HHBBBB", body, 32, 0, 0, 1, frame_id, len(payload), 1)
    body[112:112 + len(payload)] = payload

    header_size = _BLF_OBJ_HEADER.size + _BLF_OBJ_HEADER_V1.size
    obj_size = header_size + len(body)
    return b"".join((
        _BLF_OBJ_HEADER.pack(b"LOBJ", header_size, 1, obj_size, _LIN_MESSAGE2),
        _BLF_OBJ_HEADER_V1.pack(2, 0, 0, timestamp_ns),
        bytes(body),
    ))


# Two LIN channels carrying the *same* frame IDs with different lengths.
# This is the shape of the real CANoe log: LIN 1 and LIN 2 both use 0x00-0x06,
# so frame IDs alone cannot say which database belongs to which channel, and
# only the lengths can. Channel 1 matches sample.ldf, channel 2 sample_alt.ldf.
_LIN_BUS_FRAMES = {
    1: [
        (0x20, bytes((10, 1))),
        (0x21, bytes((20,))),
        (0x20, bytes((30, 2))),
        (0x21, bytes((40,))),
    ],
    2: [
        (0x20, bytes((7,))),
        (0x21, bytes((50, 3))),
        (0x20, bytes((9,))),
        (0x21, bytes((60, 4))),
    ],
}

# Bus events, not frames: they must be counted and skipped, never stored.
# 20 = LIN_SLEEP, 21 = LIN_WAKEUP, 17 = LIN_SCHED_MODCH.
_LIN_EVENT_TYPES = (20, 21, 17)


_CAN_MESSAGE = 1
# CanMessage body: channel(H) flags(B) dlc(B) id(L) data[8]
_CAN_MSG_BODY = struct.Struct("<HBBL8s")


def _can_message_object(timestamp_ns: int, channel: int, arb_id: int,
                        payload: bytes) -> bytes:
    """A classic CAN object, written the way python-can reads one back."""
    body = _CAN_MSG_BODY.pack(
        channel, 0, len(payload), arb_id, payload.ljust(8, b"\0")
    )
    header_size = _BLF_OBJ_HEADER.size + _BLF_OBJ_HEADER_V1.size
    obj_size = header_size + len(body)
    return b"".join((
        _BLF_OBJ_HEADER.pack(b"LOBJ", header_size, 1, obj_size, _CAN_MESSAGE),
        _BLF_OBJ_HEADER_V1.pack(2, 0, 0, timestamp_ns),
        body,
    ))


def _lin_event_object(timestamp_ns: int, object_type: int) -> bytes:
    """A LIN bus event (sleep, wakeup, schedule change) with no frame payload."""
    body = bytes(24)
    header_size = _BLF_OBJ_HEADER.size + _BLF_OBJ_HEADER_V1.size
    obj_size = header_size + len(body)
    return b"".join((
        _BLF_OBJ_HEADER.pack(b"LOBJ", header_size, 1, obj_size, object_type),
        _BLF_OBJ_HEADER_V1.pack(2, 0, 0, timestamp_ns),
        body,
    ))


def generate_lin_blf(out_path: Path) -> None:
    import zlib

    parts = []
    tick = 0
    for repeat in range(8):
        for channel, frames in _LIN_BUS_FRAMES.items():
            for frame_id, payload in frames:
                tick += 1
                parts.append(
                    _lin_message2_object(
                        tick * 10_000_000, channel, frame_id, payload
                    )
                )
        if repeat == 0:
            for event_type in _LIN_EVENT_TYPES:
                tick += 1
                parts.append(_lin_event_object(tick * 10_000_000, event_type))
    objects = b"".join(parts)

    compressed = zlib.compress(objects)
    container_size = _BLF_OBJ_HEADER.size + _BLF_LOG_CONTAINER.size + len(compressed)
    container = b"".join((
        _BLF_OBJ_HEADER.pack(b"LOBJ", _BLF_OBJ_HEADER.size, 1,
                             container_size, _LOG_CONTAINER),
        _BLF_LOG_CONTAINER.pack(_ZLIB_DEFLATE, len(objects)),
        compressed,
    ))
    container += b"\0" * (container_size % 4)

    count = len(parts)
    file_size = _BLF_FILE_HEADER_SIZE + len(container)
    start = (2024, 1, 15, 1, 10, 0, 0, 0)
    header = _BLF_FILE_HEADER.pack(
        b"LOGG", _BLF_FILE_HEADER_SIZE, 0, 0, 0, 0, 2, 6, 8, 1,
        file_size, _BLF_FILE_HEADER_SIZE + len(objects), count, count,
        *start, *start,
    )
    header += b"\0" * (_BLF_FILE_HEADER_SIZE - len(header))

    out_path.write_bytes(header + container)
    print(f"Written: {out_path} ({count} LIN frames, no CAN)")


def generate_lin_asc(out_path: Path) -> None:
    """Write a LIN ASC log mirroring sample_lin.blf frame for frame.

    Deliberately varies the optional columns line by line — symbolic name
    present or absent, checksum model present or absent, trailing measurement
    fields present or absent — because those are what differ between CANoe
    versions and are the only thing the anchored parser has to absorb.

    This is a self-made reference, not a CANoe one: no LIN ASC export exists on
    this machine. It proves the parser is internally consistent; it does not
    prove agreement with what CANoe writes.
    """
    lines = [
        "date Mon Jan 15 10:00:00 am 2024",
        "base hex  timestamps absolute",
        "internal events logged",
        "// version 13.0.0",
    ]
    tick = 0
    for repeat in range(8):
        for channel, frames in _LIN_BUS_FRAMES.items():
            for frame_id, payload in frames:
                tick += 1
                data = " ".join(f"{byte:02x}" for byte in payload)
                variant = tick % 3
                if variant == 0:
                    body = (f"Frame_{frame_id:02X} Tx   d {len(payload)} {data}  "
                            f"checksum = 0x{sum(payload) & 0xFF:02x}  "
                            f"SOF = {tick * 0.01:.6f} BR = 19230")
                elif variant == 1:
                    body = f"Tx   d {len(payload)} {data}"
                else:
                    body = f"Rx {len(payload)} {data}"
                lines.append(
                    f"{tick * 0.01:12.6f} Li {channel}  {frame_id:x} {body}"
                )
        if repeat == 0:
            # Bus events, which carry no frame and must be skipped.
            lines.append(f"{tick * 0.01:12.6f} Li 1 LinSyncError")
            lines.append(
                f"{tick * 0.01:12.6f} Li 1 LinSleepModeEvent  Tx  reason = StartState"
            )
            lines.append(f"{tick * 0.01:12.6f} Li 2 WakeupFrame  Rx  signal = 0x80")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Written: {out_path} ({tick} LIN frames, no CAN)")


def generate_mixed_bus_blf(out_path: Path) -> None:
    """CAN 1 and LIN 1 both carrying frame 0x20.

    The collision is the point. LIN IDs are 6-bit and overlap the low CAN
    range, so a channel number and a frame ID together are *not* unique across
    buses — and the bulk decoder groups frames by exactly that pair before
    choosing a database. Without the bus in the grouping key the two merge
    into one group and whichever sorts first decides which database decodes
    both. A fixture with LIN on channel 1 and CAN on channel 2 would not catch
    it, because the channel numbers alone would keep them apart.

    CAN 1  0x20  CanLow.CanLowValue = 12345   (sample_lowid.dbc)
    LIN 1  0x20  DoorCmd            = (10, 1) (sample.ldf)
    """
    import zlib

    parts = []
    for index in range(4):
        parts.append(
            _can_message_object((index * 2 + 1) * 10_000_000, 1, 0x20,
                                bytes((0x39, 0x30, 0, 0, 0, 0, 0, 0)))
        )
        parts.append(
            _lin_message2_object((index * 2 + 2) * 10_000_000, 1, 0x20,
                                 bytes((10, 1)))
        )
    objects = b"".join(parts)

    compressed = zlib.compress(objects)
    container_size = _BLF_OBJ_HEADER.size + _BLF_LOG_CONTAINER.size + len(compressed)
    container = b"".join((
        _BLF_OBJ_HEADER.pack(b"LOBJ", _BLF_OBJ_HEADER.size, 1,
                             container_size, _LOG_CONTAINER),
        _BLF_LOG_CONTAINER.pack(_ZLIB_DEFLATE, len(objects)),
        compressed,
    ))
    container += b"\0" * (container_size % 4)

    count = len(parts)
    file_size = _BLF_FILE_HEADER_SIZE + len(container)
    start = (2024, 1, 15, 1, 10, 0, 0, 0)
    header = _BLF_FILE_HEADER.pack(
        b"LOGG", _BLF_FILE_HEADER_SIZE, 0, 0, 0, 0, 2, 6, 8, 1,
        file_size, _BLF_FILE_HEADER_SIZE + len(objects), count, count,
        *start, *start,
    )
    header += b"\0" * (_BLF_FILE_HEADER_SIZE - len(header))
    out_path.write_bytes(header + container)
    print(f"Written: {out_path} ({count // 2} CAN + {count // 2} LIN frames, "
          "both on channel 1, both frame 0x20)")


def generate_other_bus_blf(out_path: Path) -> None:
    """A BLF carrying only objects this application does not decode.

    Object type 29 is FLEXRAY_DATA. python-can skips it, and so does the LIN
    reader, so without an explicit check the file would load as an empty
    measurement — the failure this fixture exists to pin down.
    """
    import zlib

    objects = b"".join(
        _lin_event_object((index + 1) * 10_000_000, 29) for index in range(4)
    )
    compressed = zlib.compress(objects)
    container_size = _BLF_OBJ_HEADER.size + _BLF_LOG_CONTAINER.size + len(compressed)
    container = b"".join((
        _BLF_OBJ_HEADER.pack(b"LOBJ", _BLF_OBJ_HEADER.size, 1,
                             container_size, _LOG_CONTAINER),
        _BLF_LOG_CONTAINER.pack(_ZLIB_DEFLATE, len(objects)),
        compressed,
    ))
    container += b"\0" * (container_size % 4)

    file_size = _BLF_FILE_HEADER_SIZE + len(container)
    start = (2024, 1, 15, 1, 10, 0, 0, 0)
    header = _BLF_FILE_HEADER.pack(
        b"LOGG", _BLF_FILE_HEADER_SIZE, 0, 0, 0, 0, 2, 6, 8, 1,
        file_size, _BLF_FILE_HEADER_SIZE + len(objects), 4, 4,
        *start, *start,
    )
    header += b"\0" * (_BLF_FILE_HEADER_SIZE - len(header))
    out_path.write_bytes(header + container)


if __name__ == "__main__":
    generate_blf(HERE / "sample.blf")
    generate_asc(HERE / "sample.asc")
    generate_lin_mf4(HERE / "sample_lin.mf4")
    generate_lin_blf(HERE / "sample_lin.blf")
    generate_lin_asc(HERE / "sample_lin.asc")
    generate_mixed_bus_blf(HERE / "sample_mixed_bus.blf")
