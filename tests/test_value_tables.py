# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Signals with a value table, in every format.

A DBC value table names raw values. A signal's values are its physical
values, and each sample's label is the table's text for its raw value, or the
physical value where the table has none: what cantools gives with
``decode_choices=True``. Every format must give exactly that.

Two bugs this guards against:

- the decoder looked a label up with the physical value cut to an integer, so
  a scaled or offset signal lost its labels, or got another value's label
  (scale 0.5: raw 2 and raw 3 both showed raw 1's label);
- asammdf reads a bus-logging MF4's value table as a range-to-text conversion
  whose default is the scaling. Read as physical values, every sample in the
  table came back NaN with no text, or, when every sample was in it, as text
  over the raw number.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

_DBC = """\
VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 Body: 8 ECU
 SG_ Gear : 0|4@1+ (1,0) [0|15] "" Vector__XXX
 SG_ Mode : 8|8@1+ (0.5,10) [10|137.5] "deg" Vector__XXX
 SG_ Temp : 16|8@1- (1,-40) [-168|87] "C" Vector__XXX
 SG_ State : 24|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ Level : 32|8@1+ (0.5,0) [0|127.5] "" Vector__XXX
 SG_ Speed : 40|16@1+ (0.1,0) [0|6553.5] "km/h" Vector__XXX

BA_DEF_ BO_  "VFrameFormat" ENUM  "StandardCAN","ExtendedCAN","reserved","J1939PG";
BA_DEF_DEF_  "VFrameFormat" "StandardCAN";

VAL_ 256 Gear 0 "P" 1 "R" 2 "N" 3 "D" ;
VAL_ 256 Mode 0 "Off" 255 "Invalid" ;
VAL_ 256 Temp -1 "Error" 0 "Zero" ;
VAL_ 256 State 1 "On" 2 "Off" ;
VAL_ 256 Level 1 "Low" 2 "High" 3 "Max" ;
"""

_SIGNALS = ("Gear", "Mode", "Temp", "State", "Level", "Speed")

# Raw values per frame: Gear 4 and 5 are past its table; Mode is scaled and
# offset; Temp is signed with a negative key; State has a partial table;
# Level 2 and 3 are physical 1.0 and 1.5, which the old lookup labelled
# "Low", and Level 4 is physical 2.0, which it labelled "High".
_ROWS = [
    # Gear Mode Temp State Level Speed
    (0, 0, -1, 1, 1, 100),
    (1, 20, 0, 2, 2, 200),
    (2, 255, 50, 7, 3, 300),
    (3, 0, -1, 1, 4, 400),
    (4, 20, 0, 2, 2, 500),
    (5, 255, 50, 7, 3, 600),
]


def _payload(gear, mode, temp, state, level, speed) -> bytes:
    return bytes([gear, mode, temp & 0xFF, state, level]) + speed.to_bytes(2, "little") + b"\x00"


def _frames():
    return [(0.010 * i, _payload(*row)) for i, row in enumerate(_ROWS)]


@pytest.fixture()
def dbc(tmp_path):
    path = tmp_path / "tables.dbc"
    path.write_text(_DBC, encoding="utf-8")
    return path


def _expected(dbc_path):
    """{signal: (values, labels)} as cantools decodes the frames."""
    import cantools

    message = cantools.database.load_file(str(dbc_path)).get_message_by_name("Body")
    values = {name: [] for name in _SIGNALS}
    labels = {name: [] for name in _SIGNALS}
    for _t, data in _frames():
        physical = message.decode(data, decode_choices=False)
        named = message.decode(data, decode_choices=True)
        for name in _SIGNALS:
            values[name].append(float(physical[name]))
            label = named[name]
            labels[name].append(
                str(float(label)) if isinstance(label, (int, float)) else str(label)
            )
    labels["Speed"] = []        # no table: no labels
    return {name: (values[name], labels[name]) for name in _SIGNALS}


# ── The same frames in every format ──────────────────────────────────────

def _write_asc(path):
    lines = [
        "date Thu Sep 24 10:00:00.000 am 2026",
        "base hex  timestamps absolute",
        "internal events logged",
        "Begin Triggerblock Thu Sep 24 10:00:00.000 am 2026",
    ]
    for t, data in _frames():
        payload = " ".join(f"{b:02X}" for b in data)
        lines.append(f"{t + 0.001:11.6f} 1  100             Rx   d 8 {payload}")
    lines.append("End TriggerBlock")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _messages():
    import can

    return [
        can.Message(timestamp=1000.0 + t, arbitration_id=0x100, is_extended_id=False,
                    data=data, channel=0)
        for t, data in _frames()
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
    for i, (t, data) in enumerate(_frames(), start=1):
        payload = " ".join(f"{b:02X}" for b in data)
        lines.append(f"{i:8d} {1000.0 + t * 1000:13.3f} DT 1      0100 Rx -  8    {payload}")
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("latin-1"))


def _write_csv(path):
    lines = ["TimestampEpoch;BusChannel;ID;IDE;DLC;DataLength;Dir;EDL;BRS;ESI;RTR;DataBytes"]
    lines += [
        f"{1660503551.0 + t:.6f};1;100;0;8;8;0;0;0;0;0;{data.hex().upper()}"
        for t, data in _frames()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_WRITERS = {
    "asc": _write_asc, "blf": _write_blf, "trc": _write_trc,
    "csv": _write_csv, "mf4": _write_mf4,
}


def _load(path, dbc_path=None):
    """{message::signal: (values, labels, has_labels)} through LoadWorker."""
    from core.channel_config import ChannelConfig
    from gui.load_worker import LoadWorker

    config = ChannelConfig.from_single_dbc(str(dbc_path)) if dbc_path else None
    worker = LoadWorker(str(path), config)
    stores, failures = [], []
    worker.finished.connect(stores.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures
    store = stores[0]
    try:
        return {
            key.split("::", 1)[1]: (
                series.numpy_values().tolist(),
                [str(label) for label in series.raw_values] if series.has_labels else [],
                series.has_labels,
            )
            for key, series in store._series_by_key.items()
        }
    finally:
        if store.raw_frame_store is not None:
            store.raw_frame_store.close()


@pytest.mark.parametrize("suffix", sorted(_WRITERS))
def test_every_format_gives_the_physical_values_and_the_raw_values_labels(
    qapp, tmp_path, dbc, suffix
):
    path = tmp_path / f"tables.{suffix}"
    _WRITERS[suffix](path)

    loaded = _load(path, dbc)

    for name, (values, labels) in _expected(dbc).items():
        got_values, got_labels, has_labels = loaded[f"Body::{name}"]
        assert got_values == values, (suffix, name)
        assert got_labels == labels, (suffix, name)
        assert has_labels == (name != "Speed"), (suffix, name)


def test_a_signal_decoded_in_slices_keeps_its_labels(qapp, tmp_path, dbc, monkeypatch):
    """A long measurement is decoded a slice of frames at a time and each
    signal handed to the store in parts; its labels are made for the parts."""
    import gui.load_worker as load_worker

    monkeypatch.setattr(load_worker, "_DECODE_SLICE_ROWS", 2)
    path = tmp_path / "tables.asc"
    _write_asc(path)

    loaded = _load(path, dbc)

    for name, (values, labels) in _expected(dbc).items():
        assert loaded[f"Body::{name}"][:2] == (values, labels), name


def test_the_expected_labels_are_the_hard_cases(dbc):
    """The reference itself: what each signal is there to catch."""
    expected = _expected(dbc)
    assert expected["Gear"][1] == ["P", "R", "N", "D", "4.0", "5.0"]
    assert expected["Mode"] == ([10.0, 20.0, 137.5] * 2, ["Off", "20.0", "Invalid"] * 2)
    assert expected["Temp"] == ([-41.0, -40.0, 10.0] * 2, ["Error", "Zero", "10.0"] * 2)
    assert expected["Level"] == (
        [0.5, 1.0, 1.5, 2.0, 1.0, 1.5], ["Low", "High", "Max", "2.0", "High", "Max"])


# ── MF4: the other ways a value table is read ────────────────────────────

def test_the_lazy_and_the_eager_mf4_read_agree(qapp, tmp_path, dbc):
    from core.channel_config import ChannelConfig
    from core.readers.mdf_can_reader import MDFCANReader

    path = tmp_path / "tables.mf4"
    _write_mf4(path)
    config = ChannelConfig.from_single_dbc(str(dbc))

    eager = {
        meta[3]: (np.asarray(values, dtype=float).tolist(), [str(x) for x in display])
        for meta, _ts, values, display in
        MDFCANReader(str(path), str(dbc)).iter_decoded_channel_arrays(config)
    }
    sources = MDFCANReader(str(path), str(dbc)).open_decoded_sources(config)
    try:
        read = sources.source.read_groups(sorted({s[0] for s in sources.signals}))
        lazy = {}
        for group, _channel, _message, _id, name, _unit, _count in sources.signals:
            (_ts, values, display, _unit2), = read[(group, name)]
            lazy[name] = (np.asarray(values, dtype=float).tolist(), [str(x) for x in display])
    finally:
        sources.source.close()

    expected = _expected(dbc)
    assert lazy == eager
    for name in _SIGNALS:
        assert lazy[name] == expected[name], name


def test_a_large_group_copy_reads_a_value_table_as_a_direct_read_does(tmp_path, dbc):
    """A group too large to read whole is copied raw and converted on the way
    out (_GroupCopy -> _physical_arrays)."""
    asammdf = pytest.importorskip("asammdf")
    from core.readers.mdf_reader import _physical_arrays

    path = tmp_path / "tables.mf4"
    _write_mf4(path)
    source = asammdf.MDF(str(path))
    extracted = source.extract_bus_logging(
        {"CAN": [(str(dbc), 0)]}, ignore_value2text_conversion=False)
    try:
        found = {}
        for g, group in enumerate(extracted.groups):
            for i, channel in enumerate(group.channels):
                if channel.name in _SIGNALS:
                    raw = extracted.get(channel.name, group=g, index=i, raw=True)
                    values, labels = _physical_arrays(raw.samples, raw.conversion)
                    found[channel.name] = (
                        values.tolist(), [str(x) for x in labels] if len(labels) else [])
    finally:
        extracted.close()
        source.close()

    assert found == _expected(dbc)


@pytest.fixture()
def recorded_mf4(tmp_path):
    """An MF4 a recorder decoded: a value-to-text conversion whose default is
    the scaling, beside a plain channel."""
    asammdf = pytest.importorskip("asammdf")

    conversion = {
        "val_0": 0, "text_0": b"Off", "val_1": 255, "text_1": b"Invalid",
        "default_addr": {"a": 0.5, "b": 10.0},
    }
    timestamps = np.arange(4) * 0.1
    mdf = asammdf.MDF(version="4.10")
    mdf.append([
        asammdf.Signal(np.array([0, 20, 255, 0], dtype=np.uint8), timestamps,
                       name="Mode", unit="deg", conversion=conversion),
        asammdf.Signal(np.array([1.0, 2.0, 3.0, 4.0]), timestamps, name="Plain"),
    ])
    path = tmp_path / "recorded.mf4"
    mdf.save(str(path), overwrite=True)
    mdf.close()
    return path


_RECORDED_MODE = ([10.0, 20.0, 137.5, 10.0], ["Off", "20.0", "Invalid", "Off"])


def test_a_recorder_mf4s_value_table_with_a_scaling_default(qapp, recorded_mf4):
    loaded = {key.rsplit("::", 1)[-1]: value for key, value in _load(recorded_mf4).items()}

    assert loaded["Mode"] == (*_RECORDED_MODE, True)
    assert loaded["Plain"] == ([1.0, 2.0, 3.0, 4.0], [], False)


@pytest.mark.parametrize("batch_all_groups", [True, False])
def test_the_batched_and_the_per_group_mdf_read_agree(recorded_mf4, batch_all_groups):
    from core.readers.mdf_reader import MDFReader

    found = {
        meta[1]: (np.asarray(values, dtype=float).tolist(), [str(x) for x in display])
        for meta, _ts, values, display in
        MDFReader(str(recorded_mf4)).iter_channel_arrays(batch_all_groups=batch_all_groups)
    }

    assert found["Mode"] == _RECORDED_MODE
    assert found["Plain"] == ([1.0, 2.0, 3.0, 4.0], [])


def test_a_value_table_with_a_text_default_keeps_the_raw_value(qapp, tmp_path):
    """The MF4s this application writes (Save decoded, Convert files) give
    their tables a text default: those still plot the stored value."""
    asammdf = pytest.importorskip("asammdf")

    conversion = {"val_0": 1, "text_0": b"On", "val_1": 2, "text_1": b"Off", "default": b""}
    mdf = asammdf.MDF(version="4.10")
    mdf.append([asammdf.Signal(np.array([1, 2, 1], dtype=np.uint8), np.arange(3) * 0.1,
                               name="State", conversion=conversion)])
    path = tmp_path / "written.mf4"
    mdf.save(str(path), overwrite=True)
    mdf.close()

    loaded = {key.rsplit("::", 1)[-1]: value for key, value in _load(path).items()}

    assert loaded["State"] == ([1.0, 2.0, 1.0], ["On", "Off", "On"], True)


# ── The CAN Trace's decoded signals ──────────────────────────────────────

def test_the_trace_labels_a_frames_signals_by_raw_value(dbc):
    from core.dbc_decoder import DBCDecoder
    from core.models import RawFrame

    decoder = DBCDecoder(str(dbc))
    expected = _expected(dbc)
    for row, (t, data) in enumerate(_frames()):
        samples = decoder.decode_frame(RawFrame(
            timestamp=t, channel=1, arbitration_id=0x100, is_extended_id=False,
            is_fd=False, dlc=8, data=data, direction="Rx"))
        got = {s.signal_name: s for s in samples}
        for name in _SIGNALS[:-1]:
            value = got[name].value
            label = str(float(value)) if isinstance(value, (int, float)) else str(value)
            assert label == expected[name][1][row], (row, name)
            assert got[name].numeric_value == expected[name][0][row], (row, name)


# ── The label lookup itself ──────────────────────────────────────────────

def _labels(choices, values, scale=1.0, offset=0.0):
    from gui.load_worker import _choice_labels

    return [str(x) for x in _choice_labels(choices, np.asarray(values), scale=scale,
                                           offset=offset)]


def test_a_physical_value_a_hair_off_its_raw_value_still_finds_its_label():
    # 0.3 * 0.1 + ... : float arithmetic rarely lands on the exact value.
    assert _labels({3: "Max"}, [2.9999999999 * 0.1, 0.30000000001], scale=0.1) == [
        "Max", "Max"]


def test_a_float_signal_between_keys_has_no_label():
    # cantools labels a float signal only on an exact key.
    assert _labels({2: "Two"}, [2.0, 2.5, 1.5]) == ["Two", "2.5", "1.5"]


def test_negative_keys_and_wide_tables_label_too():
    assert _labels({-1: "Error", 0: "Zero"}, [-1.0, 0.0, 1.0]) == ["Error", "Zero", "1.0"]
    # Keys too far apart for a lookup table take the per-sample path.
    assert _labels({-1: "Error", 70000: "Far"}, [-41.0, 69960.0, -39.0], offset=-40.0) == [
        "Error", "Far", "-39.0"]
    assert _labels({1: "One"}, [math.nan]) == [""]


def test_the_labels_of_a_scaled_signal_follow_its_stored_values():
    from core.signal_store import SignalStore
    from gui.load_worker import _choice_labels

    store = SignalStore()
    values = np.array([1.0, 0.5, 1.5])
    store.add_series_bulk(
        channel=1, message_name="Body", message_id=0x100, signal_name="Level",
        unit="", timestamps=np.array([0.0, 0.1, 0.2]), values=values,
        raw_values=_choice_labels({1: "Low", 2: "High", 3: "Max"}, values, scale=0.5),
        has_labels=True,
    )

    series = next(iter(store._series_by_key.values()))
    assert [str(x) for x in series.raw_values] == ["High", "Low", "Max"]
    assert series.raw_values[2] == "Max" and series.raw_values[0:2] == ["High", "Low"]


def _add_level(store, timestamps, values, choices, parts=False):
    from gui.load_worker import _choice_labels

    values = np.array(values)
    if parts:
        values = [values[:1], values[1:]]
        labels = _choice_labels(choices, values, len(timestamps), scale=0.5)
    else:
        labels = _choice_labels(choices, values, scale=0.5)
    store.add_series_bulk(
        channel=1, message_name="Body", message_id=0x100, signal_name="Level",
        unit="", timestamps=np.array(timestamps), values=values, raw_values=labels,
        has_labels=True,
    )
    return next(iter(store._series_by_key.values()))


@pytest.mark.parametrize("parts", [False, True], ids=["whole", "parts"])
@pytest.mark.parametrize("budget", [None, 0], ids=["heap", "file"])
def test_a_signal_added_twice_keeps_one_label_per_sample(budget, parts):
    # A J1939 sender on two priorities is decoded one frame ID at a time, so
    # its series is added twice, out of time order. In the heap the stored
    # values grow in place, and the first part's labels, read from them, grew
    # too: the second part was labelled twice, 15 labels for 10 values.
    from core.signal_store import ChoiceLabels, SignalStore

    store = SignalStore()
    if budget is not None:
        store.memory_budget = budget
    choices = {1: "Low", 2: "High", 3: "Max"}
    _add_level(store, [0.0, 0.2, 0.4], [0.5, 1.0, 1.5], choices, parts)
    series = _add_level(store, [0.1, 0.3], [1.0, 0.25], choices, parts)

    assert len(series.raw_values) == len(series.values) == 5
    # Still read from the stored values: no list of a label per sample.
    assert isinstance(series.raw_values, ChoiceLabels)
    assert store.sort_merged_series() == 1
    assert series.numpy_values().tolist() == [0.5, 1.0, 1.0, 0.25, 1.5]
    assert [str(x) for x in series.raw_values] == ["Low", "High", "High", "0.25", "Max"]
    store.close()


@pytest.mark.parametrize("parts", [False, True], ids=["whole", "parts"])
@pytest.mark.parametrize("budget", [None, 0], ids=["heap", "file"])
def test_a_second_part_with_another_table_keeps_each_parts_labels(budget, parts):
    # Handed over in parts (a long signal decoded in slices), the second
    # part's labels are bound to no values yet: reading them failed.
    from core.signal_store import SignalStore

    store = SignalStore()
    if budget is not None:
        store.memory_budget = budget
    _add_level(store, [0.0, 0.1], [0.5, 1.0], {1: "Low", 2: "High"}, parts)
    series = _add_level(store, [0.2, 0.3], [0.5, 1.0], {1: "Lo"}, parts)

    assert len(series.raw_values) == len(series.values) == 4
    assert [str(x) for x in series.raw_values] == ["Low", "High", "Lo", "1.0"]
    store.close()


def test_labels_of_another_scaling_are_not_the_same_choices():
    from gui.load_worker import _choice_labels

    values = np.array([1.0])
    plain = _choice_labels({1: "One"}, values)
    scaled = _choice_labels({1: "One"}, values, scale=0.5)
    assert plain.same_choices(_choice_labels({1: "One"}, values))
    assert not plain.same_choices(scaled)
