# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A frame whose multiplexer value the database does not define.

cantools raises for such a frame, so every signal in it was lost, the
non-multiplexed ones too. asammdf keeps them, so a bus-logging MF4 kept them
while BLF, ASC, TRC and CSV dropped the frame. Whether they did depended on
the first frame of the message: the fast decoder was checked against cantools
on that frame only, and when cantools rejected it, the whole message went
frame by frame through cantools.

Every format must give what the database says: the multiplexer and the
signals outside the undefined branch get their values; that branch's signals
get none. The frames below start on an undefined value on purpose.

Also guarded: a nested multiplexer listed after its own branches' signals
(cantools lists signals by start bit). The fast path matched those branches
against its raw bits on rows where the outer multiplexer left it out.
"""
from __future__ import annotations

import re
import struct

import numpy as np
import pytest

_DBC = """\
VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 609 Status: 8 ECU
 SG_ Index M : 0|2@1+ (1,0) [0|3] "" ECU
 SG_ Counter : 8|4@1+ (1,0) [0|15] "" ECU
 SG_ Other m0 : 32|12@1- (0.1,-204.7) [-409.5|0] "" ECU
 SG_ Mid m1 : 32|12@1+ (1,0) [0|4095] "" ECU
 SG_ Third m2 : 32|10@1+ (0.01,9) [9|19.23] "" ECU
 SG_ Checksum : 56|8@1+ (1,0) [0|255] "" ECU

BO_ 610 Nested: 8 ECU
 SG_ Outer M : 0|2@1+ (1,0) [0|3] "" ECU
 SG_ InnerB m1 : 16|8@1+ (1,0) [0|255] "" ECU
 SG_ InnerA m0 : 16|8@1+ (1,0) [0|255] "" ECU
 SG_ OuterB m1 : 24|8@1+ (1,0) [0|255] "" ECU
 SG_ Inner m0M : 40|2@1+ (1,0) [0|3] "" ECU
 SG_ Plain : 56|8@1+ (1,0) [0|255] "" ECU

BO_ 611 Slow: 8 ECU
 SG_ Sel M : 0|2@1+ (1,0) [0|3] "" ECU
 SG_ Temp m0 : 15|16@0+ (0.1,0) [0|6553.5] "" ECU
 SG_ Volt m1 : 15|16@0+ (0.01,0) [0|655.35] "" ECU
 SG_ Ratio : 39|32@0- (1,0) [0|0] "" ECU

SIG_VALTYPE_ 611 Ratio : 1;

SG_MUL_VAL_ 610 Inner Outer 0-0;
SG_MUL_VAL_ 610 InnerA Inner 0-0;
SG_MUL_VAL_ 610 InnerB Inner 1-1;
SG_MUL_VAL_ 610 OuterB Outer 1-1;
"""

# The multiplexer values each branch signal needs, outermost first. Every
# value not listed is undefined: Index 3; Outer 2 and 3; Inner 2 and 3; Sel
# 2 and 3.
_BRANCHES = {
    "Other": [("Index", 0)],
    "Mid": [("Index", 1)],
    "Third": [("Index", 2)],
    "Inner": [("Outer", 0)],
    "InnerA": [("Outer", 0), ("Inner", 0)],
    "InnerB": [("Outer", 0), ("Inner", 1)],
    "OuterB": [("Outer", 1)],
    "Temp": [("Sel", 0)],
    "Volt": [("Sel", 1)],
}

_ROWS = 12


def _status(i):
    # Fully fast: little-endian integers. Starts on the undefined Index 3.
    index = (3, 0, 1, 2)[i % 4]
    return bytes([index, i & 0x0F, 0, 0, 0x10 + i, 0x01, 0, i])


def _nested(i):
    # Fully fast. Starts on the undefined Outer 3. (1, 1): Inner's bits read
    # 1 where Outer leaves Inner out, so InnerB must stay empty there.
    outer, inner = [(3, 1), (0, 0), (0, 1), (0, 2), (1, 1), (1, 0), (2, 1)][i % 7]
    return bytes([outer, 0, 0x40 + i, 0x80 + i, 0, inner, 0, i])


def _slow(i):
    # Slow path: Motorola signals and a Motorola float. Starts on Sel 3.
    sel = (3, 0, 1)[i % 3]
    return bytes([sel, 0x01, 0x23 + i, 0]) + struct.pack(">f", 1.5 + i)


def _frames():
    """(time, frame ID, data) in time order."""
    frames = []
    for i in range(_ROWS):
        frames += [
            (0.010 * i, 0x261, _status(i)),
            (0.010 * i + 0.001, 0x262, _nested(i)),
            (0.010 * i + 0.002, 0x263, _slow(i)),
        ]
    return frames


@pytest.fixture()
def dbc(tmp_path):
    path = tmp_path / "mux.dbc"
    path.write_text(_DBC, encoding="utf-8")
    return path


_NAMES = {0x261: "Status", 0x262: "Nested", 0x263: "Slow"}


def _single_signal_messages():
    """{message: one cantools message per signal, with no multiplexing}:
    cantools decodes each signal on its own, whatever its branch."""
    import cantools

    signals: dict[str, list[str]] = {}
    for line in _DBC.splitlines():
        if line.startswith("BO_ "):
            current = signals.setdefault(line.split()[2].rstrip(":"), [])
        elif line.startswith(" SG_ "):
            current.append(re.sub(r"^( SG_ \w+) (?:M|m\d+M?) :", r"\1 :", line))
    floats = set(re.findall(r"^SIG_VALTYPE_ \d+ (\w+) : 1;", _DBC, flags=re.MULTILINE))
    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_: ECU", ""]
    value_types = []
    singles = ((m, sg) for m, sgs in signals.items() for sg in sgs)
    for frame_id, (message, signal_line) in enumerate(singles, start=1):
        name = signal_line.split()[1]
        lines += [f"BO_ {frame_id} {message}_{name}: 8 ECU", signal_line, ""]
        if name in floats:
            value_types.append(f"SIG_VALTYPE_ {frame_id} {name} : 1;")
    database = cantools.database.load_string(
        "\n".join(lines + value_types) + "\n", database_format="dbc")
    return {
        message: [m for m in database.messages if m.name.startswith(f"{message}_")]
        for message in signals
    }


def _expected():
    """{message::signal: values} as the database says, worked out apart
    from the decoder under test: each signal of each frame, where every
    multiplexer value its branch needs is there."""
    singles = _single_signal_messages()
    expected: dict[str, list[float]] = {}
    for _t, frame_id, data in _frames():
        message = _NAMES[frame_id]
        values = {}
        for single in singles[message]:
            values.update(single.decode(data, decode_choices=False))
        for name, value in values.items():
            if all(values[mux] == wanted for mux, wanted in _BRANCHES.get(name, ())):
                expected.setdefault(f"{message}::{name}", []).append(float(value))
    return expected


def test_the_expected_values_are_the_hard_cases():
    """The reference itself: what each message is there to catch."""
    expected = _expected()
    # Every frame keeps its non-multiplexed signals and its multiplexer.
    for name in ("Status::Index", "Status::Counter", "Status::Checksum",
                 "Nested::Outer", "Nested::Plain", "Slow::Sel", "Slow::Ratio"):
        assert len(expected[name]) == _ROWS, name
    assert expected["Status::Index"][:4] == [3.0, 0.0, 1.0, 2.0]
    # Only the defined branches: Index 0/1/2 three times each.
    assert [len(expected[f"Status::{n}"]) for n in ("Other", "Mid", "Third")] == [3, 3, 3]
    # Inner keeps its undefined value 2; nothing below it does.
    assert expected["Nested::Inner"] == [0.0, 1.0, 2.0, 0.0, 1.0, 2.0]
    assert expected["Nested::InnerA"] == [0x41, 0x48]
    assert expected["Nested::InnerB"] == [0x42, 0x49]
    assert expected["Nested::OuterB"] == [0x84, 0x85, 0x8B]
    assert len(expected["Slow::Temp"]) == len(expected["Slow::Volt"]) == 4
    assert len(expected) == 16


# ── The same frames in every format ──────────────────────────────────────

def _write_asc(path):
    lines = [
        "date Thu Sep 24 10:00:00.000 am 2026",
        "base hex  timestamps absolute",
        "internal events logged",
        "Begin Triggerblock Thu Sep 24 10:00:00.000 am 2026",
    ]
    for t, frame_id, data in _frames():
        payload = " ".join(f"{b:02X}" for b in data)
        lines.append(f"{t + 0.001:11.6f} 1  {frame_id:X}             Rx   d 8 {payload}")
    lines.append("End TriggerBlock")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _messages():
    import can

    return [
        can.Message(timestamp=1000.0 + t, arbitration_id=frame_id, is_extended_id=False,
                    data=data, channel=0)
        for t, frame_id, data in _frames()
    ]


def _write_blf(path):
    import can

    with can.BLFWriter(str(path)) as writer:
        for message in _messages():
            writer.on_message_received(message)


def _write_mf4(path):
    import can

    pytest.importorskip("asammdf")
    with can.Logger(str(path)) as logger:
        for message in _messages():
            logger.on_message_received(message)


def _write_trc(path):
    lines = [";$FILEVERSION=2.1", ";$COLUMNS=N,O,T,B,I,d,R,L,D"]
    for i, (t, frame_id, data) in enumerate(_frames(), start=1):
        payload = " ".join(f"{b:02X}" for b in data)
        lines.append(f"{i:8d} {1000.0 + t * 1000:13.3f} DT 1      {frame_id:04X} Rx -  8    {payload}")
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("latin-1"))


def _write_csv(path):
    lines = ["TimestampEpoch;BusChannel;ID;IDE;DLC;DataLength;Dir;EDL;BRS;ESI;RTR;DataBytes"]
    lines += [
        f"{1660503551.0 + t:.6f};1;{frame_id:X};0;8;8;0;0;0;0;0;{data.hex().upper()}"
        for t, frame_id, data in _frames()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_WRITERS = {
    "asc": _write_asc, "blf": _write_blf, "trc": _write_trc,
    "csv": _write_csv, "mf4": _write_mf4,
}


def _load(path, dbc_path):
    """{message::signal: values} through LoadWorker."""
    from core.channel_config import ChannelConfig
    from gui.load_worker import LoadWorker

    worker = LoadWorker(str(path), ChannelConfig.from_single_dbc(str(dbc_path)))
    stores, failures = [], []
    worker.finished.connect(stores.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures
    store = stores[0]
    try:
        return {
            key.split("::", 1)[1]: series.numpy_values().tolist()
            for key, series in store._series_by_key.items()
        }
    finally:
        if store.raw_frame_store is not None:
            store.raw_frame_store.close()


@pytest.mark.parametrize("suffix", sorted(_WRITERS))
def test_every_format_keeps_the_signals_outside_an_undefined_branch(
    qapp, tmp_path, dbc, suffix
):
    path = tmp_path / f"mux.{suffix}"
    _WRITERS[suffix](path)

    loaded = _load(path, dbc)

    expected = _expected()
    assert sorted(loaded) == sorted(expected), suffix
    for name, values in expected.items():
        assert loaded[name] == pytest.approx(values, rel=1e-12, abs=1e-12), (suffix, name)


def test_a_message_decoded_in_slices_keeps_them_too(qapp, tmp_path, dbc, monkeypatch):
    """A long measurement is decoded a slice of frames at a time: the
    first slice of each message here is nothing but undefined values."""
    import gui.load_worker as load_worker

    monkeypatch.setattr(load_worker, "_DECODE_SLICE_ROWS", 1)
    path = tmp_path / "mux.asc"
    _write_asc(path)

    loaded = _load(path, dbc)

    for name, values in _expected().items():
        assert loaded[name] == pytest.approx(values, rel=1e-12, abs=1e-12), name


# ── The decoders themselves ──────────────────────────────────────────────

def _message(name):
    import cantools

    return cantools.database.load_string(_DBC, database_format="dbc").get_message_by_name(name)


def _rows(name):
    make = {"Status": _status, "Nested": _nested, "Slow": _slow}[name]
    return np.frombuffer(b"".join(make(i) for i in range(_ROWS)), dtype=np.uint8).reshape(_ROWS, 8)


def _expected_for(name):
    return {
        key.split("::", 1)[1]: values
        for key, values in _expected().items() if key.startswith(f"{name}::")
    }


def _assert_decoded(result, expected, context):
    """*result* from MessageVectorDecoder.decode, NaN rows left out."""
    got = {
        signal: values[~np.isnan(values)].tolist()
        for signal, (_ext, values) in result.items() if (~np.isnan(values)).any()
    }
    assert sorted(got) == sorted(expected), context
    for signal, values in expected.items():
        assert got[signal] == pytest.approx(values), (context, signal)


@pytest.mark.parametrize("name", ["Status", "Nested"])
def test_the_fast_path_is_kept_when_the_first_frame_is_undefined(name):
    from core.vectorized_decoder import MessageVectorDecoder

    decoder = MessageVectorDecoder(_message(name))
    result = decoder.decode(_rows(name))

    assert decoder.fully_fast, name
    _assert_decoded(result, _expected_for(name), name)


def test_the_slow_path_keeps_the_signals_outside_an_undefined_branch():
    from core.vectorized_decoder import MessageVectorDecoder

    decoder = MessageVectorDecoder(_message("Slow"))
    result = decoder.decode(_rows("Slow"))

    assert not decoder.fully_fast
    _assert_decoded(result, _expected_for("Slow"), "Slow")


def test_a_message_with_no_row_cantools_decodes_still_keeps_them():
    """None of the rows the check looks through decodes: the message goes
    the slow way, and still keeps every frame's other signals."""
    from core.vectorized_decoder import _VERIFY_ROWS, MessageVectorDecoder

    rows = _VERIFY_ROWS + 4
    data = np.zeros((rows, 8), dtype=np.uint8)
    data[:, 0] = 3                       # Index 3, undefined, on every row
    data[:, 1] = np.arange(rows) & 0x0F  # Counter
    data[:, 7] = np.arange(rows)         # Checksum
    decoder = MessageVectorDecoder(_message("Status"))

    result = decoder.decode(data)

    assert not decoder.fully_fast
    assert result["Index"][1].tolist() == [3.0] * rows
    assert result["Checksum"][1].tolist() == [float(i) for i in range(rows)]
    for branch in ("Other", "Mid", "Third"):
        assert np.isnan(result[branch][1]).all(), branch


def test_decode_known_branches_is_cantools_wherever_cantools_decodes():
    from core.dbc_decoder import decode_known_branches

    checked = 0
    for name in ("Status", "Nested", "Slow"):
        message = _message(name)
        for row in _rows(name):
            data = row.tobytes()
            try:
                reference = message.decode(data, decode_choices=False)
            except Exception:
                continue
            assert decode_known_branches(message, data) == reference, (name, data)
            checked += 1
    assert checked > 20


def test_decode_known_branches_leaves_out_only_the_undefined_branch():
    from core.dbc_decoder import decode_known_branches

    status = _message("Status")
    assert decode_known_branches(status, _status(0)) == {
        "Index": 3, "Counter": 0, "Checksum": 0}
    # Short as well: Checksum is past the end of two bytes.
    assert decode_known_branches(status, _status(4)[:2]) == {"Index": 3, "Counter": 4}

    nested = _message("Nested")
    # Outer 3: nothing below it.
    assert decode_known_branches(nested, _nested(0)) == {"Outer": 3, "Plain": 0}
    # Outer 0, Inner 2: Inner kept, nothing below it.
    assert decode_known_branches(nested, _nested(3)) == {"Outer": 0, "Inner": 2, "Plain": 3}
    # Outer 2 with Inner's bits at 1: neither Inner nor InnerB.
    assert decode_known_branches(nested, _nested(6)) == {"Outer": 2, "Plain": 6}


def test_decode_known_branches_gives_way_when_cantools_internals_change(monkeypatch):
    import core.dbc_decoder as dbc_decoder

    status = _message("Status")
    monkeypatch.setattr(status, "_codecs", None)
    assert dbc_decoder.decode_known_branches(status, _status(0)) is None

    status = _message("Status")
    monkeypatch.setattr(dbc_decoder, "_cantools_decode_data", None)
    assert dbc_decoder.decode_known_branches(status, _status(0)) is None

    monkeypatch.setattr(
        dbc_decoder, "_cantools_decode_data", lambda *args: {"Index": 3})
    monkeypatch.setattr(status, "_codecs", {"signals": [], "formats": None})
    assert dbc_decoder.decode_known_branches(status, _status(0)) is None


# ── The CAN Trace's decoded signals ──────────────────────────────────────

def test_the_trace_shows_the_signals_outside_an_undefined_branch(dbc):
    from core.dbc_decoder import DBCDecoder
    from core.models import RawFrame

    decoder = DBCDecoder(str(dbc))
    expected = _expected()
    seen = {}
    for t, frame_id, data in _frames():
        samples = decoder.decode_frame(RawFrame(
            timestamp=t, channel=1, arbitration_id=frame_id, is_extended_id=False,
            is_fd=False, dlc=8, data=data, direction="Rx"))
        assert samples, (frame_id, data)
        for sample in samples:
            seen.setdefault(
                f"{sample.message_name}::{sample.signal_name}", []
            ).append(sample.numeric_value)

    assert sorted(seen) == sorted(expected)
    for name, values in expected.items():
        assert seen[name] == pytest.approx(values), name
    assert decoder.stats["decode_fail"] == 0
    assert decoder.stats["decode_success"] == len(_frames())


def test_without_the_cantools_internals_the_trace_gives_what_cantools_gives(
    dbc, monkeypatch
):
    import core.dbc_decoder as dbc_decoder
    from core.models import RawFrame

    monkeypatch.setattr(dbc_decoder, "_cantools_decode_data", None)
    decoder = dbc_decoder.DBCDecoder(str(dbc))
    samples = decoder.decode_frame(RawFrame(
        timestamp=0.0, channel=1, arbitration_id=0x261, is_extended_id=False,
        is_fd=False, dlc=8, data=_status(0), direction="Rx"))

    assert samples == []
    assert decoder.stats["decode_fail"] == 1
