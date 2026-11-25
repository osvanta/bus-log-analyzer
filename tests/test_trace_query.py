# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The Trace searches, filters and colours down to signal level, in every format.

One set of frames is written as ASC, BLF, MF4, CSV and TRC; each loads with
the same database, and the same query must find the same frames in each.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from core import trace_query as tq
from core.bus_types import BusType

can = pytest.importorskip("can")

CYCLES = 400
PERIOD = 0.01
ENGINE_ID = 0x18FEF100

DBC = """VERSION ""

NS_ :

BS_:

BU_: BMS VCU ECU

BO_ 256 Battery: 8 BMS
 SG_ Temp : 0|8@1+ (0.5,-40) [-40|87.5] "degC" Vector__XXX
 SG_ State : 8|2@1+ (1,0) [0|3] "" Vector__XXX

BO_ 512 Drive: 8 VCU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" Vector__XXX

BO_ 2566844672 Engine: 8 ECU
 SG_ Rpm : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX

VAL_ 256 State 0 "Off" 1 "On" 2 "Fault" ;
"""


def frames():
    """(time, channel, id, extended, data) of every data frame, in time order."""
    out = []
    for i in range(CYCLES):
        t = i * PERIOD
        out.append((t, 1, 0x100, False, bytes([i % 200, (i // 50) % 3, 0, 0, 0, 0, 0, 0])))
        if i % 2 == 0:
            out.append((t + 0.001, 2, 0x200, False, (i * 100).to_bytes(2, 'little') + bytes(6)))
        if i % 4 == 0:
            out.append((t + 0.002, 1, ENGINE_ID, True,
                        bytes(3) + (i * 40).to_bytes(2, 'little') + bytes(3)))
    return out


#: What the frames hold, worked out from frames() by hand.
EXPECTED = {
    'Temp > 50': sum(1 for i in range(CYCLES) if i % 200 > 180),
    'State = Fault': sum(1 for i in range(CYCLES) if (i // 50) % 3 == 2),
    'Battery.State != Off': sum(1 for i in range(CYCLES) if (i // 50) % 3 != 0),
    'Speed > 30': sum(1 for i in range(0, CYCLES, 2) if i * 100 > 3000),
    'Rpm >= 1000': sum(1 for i in range(0, CYCLES, 4) if i * 40 * 0.125 >= 1000),
    'Temp > 50 and State = On': sum(1 for i in range(CYCLES)
                                    if i % 200 > 180 and (i // 50) % 3 == 1),
    'Temp > 50 or Speed > 30': (sum(1 for i in range(CYCLES) if i % 200 > 180)
                                + sum(1 for i in range(0, CYCLES, 2) if i * 100 > 3000)),
    'spee': CYCLES // 2,                     # part of a signal name
    'battery': CYCLES,                       # a frame name, as before
}
COUNTS = {'battery': CYCLES, 'drive': CYCLES // 2, 'engine': CYCLES // 4}
#: Error and remote frames, in the formats that record them.
SPECIAL = ('asc', 'blf')


def _write_asc_blf(path, writer_cls, special: bool):
    writer = writer_cls(str(path))
    try:
        for t, channel, arb_id, ext, data in frames():
            writer.on_message_received(can.Message(
                timestamp=1000.0 + t, arbitration_id=arb_id, is_extended_id=ext,
                channel=channel - 1, data=data, dlc=8))
            if special and arb_id == 0x100 and t in (1.0, 2.0):
                writer.on_message_received(can.Message(
                    timestamp=1000.0 + t + 0.0005, is_error_frame=True,
                    channel=0, arbitration_id=0, dlc=0))
                writer.on_message_received(can.Message(
                    timestamp=1000.0 + t + 0.0007, is_remote_frame=True, is_extended_id=False,
                    arbitration_id=0x100, channel=0, dlc=2))   # Battery's ID
    finally:
        writer.stop()


def _write_trc(path):
    start = (dt.datetime(2024, 1, 15, 10) - dt.datetime(1899, 12, 30)).total_seconds() / 86400
    lines = [';$FILEVERSION=2.1', f';$STARTTIME={start:.10f}', ';$COLUMNS=N,O,T,B,I,d,R,L,D',
             ';', ';   made by the test suite', ';']
    for number, (t, channel, arb_id, ext, data) in enumerate(frames(), 1):
        text = f'{arb_id:08X}' if ext else f'{arb_id:04X}'
        lines.append(f'{number:>8} {t * 1000:>13.3f} DT {channel} {text} Rx - 8 '
                     + ' '.join(f'{b:02X}' for b in data))
    path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('latin-1'))


def _write_csv(path):
    lines = ['TimestampEpoch;BusChannel;ID;IDE;DLC;DataLength;Dir;DataBytes']
    for t, channel, arb_id, ext, data in frames():
        lines.append(f'{1_700_000_000 + t:.6f};{channel};{arb_id:X};{int(ext)};8;8;Rx;{data.hex()}')
    path.write_text('\n'.join(lines) + '\n', encoding='ascii')


@pytest.fixture(scope='module')
def logs(tmp_path_factory):
    """Every format's file, and the database."""
    from core.converter import ConvertJob, convert

    folder = tmp_path_factory.mktemp('trace_logs')
    dbc = folder / 'trace.dbc'
    dbc.write_text(DBC, encoding='ascii')
    paths = {'asc': folder / 'log.asc', 'blf': folder / 'log.blf', 'mf4': folder / 'log.mf4',
             'trc': folder / 'log.trc', 'csv': folder / 'log.csv'}
    _write_asc_blf(paths['asc'], can.ASCWriter, True)
    _write_asc_blf(paths['blf'], can.BLFWriter, True)
    plain = folder / 'plain.asc'
    _write_asc_blf(plain, can.ASCWriter, False)
    convert(ConvertJob([plain], paths['mf4']))
    _write_trc(paths['trc'])
    _write_csv(paths['csv'])
    return paths, dbc


_STORES: dict = {}


def load(logs, fmt):
    """The Trace's frames of one format's file, loaded once."""
    from core.channel_config import ChannelConfig
    from gui.load_worker import LoadWorker

    paths, dbc = logs
    if fmt not in _STORES:
        worker = LoadWorker(str(paths[fmt]), ChannelConfig.from_single_dbc(str(dbc)))
        stores, failures = [], []
        worker.finished.connect(stores.append)
        worker.failed.connect(failures.append)
        worker.run()
        assert not failures, failures
        _STORES[fmt] = stores[0]
    return _STORES[fmt].raw_frame_store


FORMATS = ['asc', 'blf', 'mf4', 'csv', 'trc']


@pytest.fixture(params=FORMATS)
def store(request, logs):
    return load(logs, request.param), request.param


def _search(store, text):
    return tq.search(store, tq.TraceCatalog(store), text).run()


def _id_count(store, arb_id):
    """Data frames of *arb_id*: error and remote frames left out."""
    from core.raw_frame_store import FLAG_ERROR, FLAG_REMOTE

    data = (np.frombuffer(store.flags, dtype=np.uint8) & (FLAG_ERROR | FLAG_REMOTE)) == 0
    return int(((np.frombuffer(store.arb_ids, dtype=np.uint32) == arb_id) & data).sum())


# ── Parsing ──────────────────────────────────────────────────────────────

def test_a_condition_needs_a_known_signal():
    known = {'temp', 'state'}.__contains__
    query = tq.parse('Temp > 50 and state = Fault or gear box', lambda n: known(n.lower()))
    assert query.groups == [[tq.Condition('Temp', '>', '50'), tq.Condition('state', '=', 'Fault')],
                            [tq.Text('gear box')]]
    # Not a signal: searched as text, as the Trace always did.
    assert tq.parse('id=5', lambda n: False).groups == [[tq.Text('id=5')]]
    assert tq.parse('   ', known) is None
    assert tq.parse('Temp == 4', lambda n: True).groups == [[tq.Condition('Temp', '=', '4')]]


@pytest.mark.parametrize('op, expected', [
    ('=', [False, True, False, False]), ('!=', [True, False, True, False]),
    ('>', [False, False, True, False]), ('>=', [False, True, True, False]),
    ('<', [True, False, False, False]), ('<=', [True, True, False, False]),
])
def test_conditions_compare_physical_values(op, expected):
    values = np.array([1.0, 2.0000000001, 3.0, np.nan])
    assert tq.Condition('S', op, '2').test(values, None).tolist() == expected


def test_a_label_is_matched_through_its_raw_value():
    class Signal:
        choices = {0: 'Off', 2: 'Fault'}
        scale, offset = 0.5, 10

    values = np.array([10.0, 11.0, 11.0000001, np.nan])
    assert tq.Condition('S', '=', 'fault').test(values, Signal).tolist() == [
        False, True, True, False]
    assert tq.Condition('S', '!=', '"Fault"').test(values, Signal).tolist() == [
        True, False, False, False]
    assert not tq.Condition('S', '=', 'Nope').test(values, Signal).any()


def test_signal_rows_are_marked_by_the_query():
    query = tq.parse('Temp > 50 or spee or State = Fault', lambda n: n.lower() in (
        'temp', 'state'))
    assert query.marks('Battery', 'Temp', 51.0, '51.0')
    assert not query.marks('Battery', 'Temp', 50.0, '50.0')
    assert query.marks('Drive', 'Speed', 1.0, '1.0')
    assert query.marks('Battery', 'State', 2.0, 'Fault')
    assert not query.marks('Battery', 'State', 1.0, 'On')
    assert not query.marks('Other', 'Rpm', 99.0, '99')


# ── Every format ─────────────────────────────────────────────────────────

def test_every_format_has_the_frames(store):
    store, fmt = store
    assert _id_count(store, 0x100) == COUNTS['battery'], fmt
    assert _id_count(store, 0x200) == COUNTS['drive'], fmt
    assert _id_count(store, ENGINE_ID) == COUNTS['engine'], fmt


@pytest.mark.parametrize('text', list(EXPECTED))
def test_a_query_finds_the_same_frames_in_every_format(store, text):
    store, fmt = store
    found = _search(store, text)
    assert len(found) == EXPECTED[text], (fmt, text)
    assert np.all(np.diff(found) > 0)


def test_a_condition_finds_exactly_the_frames_whose_value_meets_it(store):
    store, fmt = store
    found = _search(store, 'Temp > 50')
    ids = np.frombuffer(store.arb_ids, dtype=np.uint32)[found]
    assert set(ids.tolist()) == {0x100}
    rows = store.rows(found)
    assert np.all(rows[:, 0].astype(float) * 0.5 - 40 > 50), fmt


def test_a_signal_on_one_channel_is_not_looked_for_on_another(store):
    store, _fmt = store
    found = _search(store, 'Speed > 30')
    channels = np.frombuffer(store.channels, dtype=np.uint8)[found]
    assert set(channels.tolist()) == {2}


def test_filter_by_channel_direction_and_id(store):
    store, fmt = store
    can2 = tq.filter_search(store, tq.TraceFilter(channels=frozenset({(BusType.CAN, 2)}))).run()
    assert len(can2) == COUNTS['drive'], fmt
    battery = tq.id_key(tq.kind_key(0x100, 1, False, False))
    with_remote = _id_count(store, 0x100) + (2 if fmt in SPECIAL else 0)
    only = tq.filter_search(store, tq.TraceFilter(only_ids=frozenset({battery}))).run()
    assert len(only) == with_remote
    hidden = tq.filter_search(store, tq.TraceFilter(hidden_ids=frozenset({battery}))).run()
    assert len(hidden) == len(store) - with_remote
    no_rx = tq.filter_search(store, tq.TraceFilter(rx=False)).run()
    directions = np.frombuffer(store.directions, dtype=np.uint8)
    assert len(no_rx) == int((directions != 0).sum())
    assert tq.filter_search(store, tq.TraceFilter()).run() is None


def test_the_filter_keeps_only_frames_among_those_found(store):
    store, _fmt = store
    found = _search(store, 'Temp > 50 or Speed > 30')
    can1 = tq.filter_search(store, tq.TraceFilter(channels=frozenset({(BusType.CAN, 1)})),
                            within=found).run()
    assert len(can1) == EXPECTED['Temp > 50']
    assert np.array_equal(tq.filter_search(store, tq.TraceFilter(), within=found).run(), found)


def test_colours_count_and_filter_per_colour(store):
    store, fmt = store
    catalog = tq.TraceCatalog(store)
    battery = tq.id_key(tq.kind_key(0x100, 1, False, False))
    rules = [tq.ColourRule('#f00', 'hot', query='Temp > 50'),       # first rule wins
             tq.ColourRule('#0f0', 'battery', id_key=battery)]
    colouring = tq.colour_search(store, catalog, rules).run()
    hot = EXPECTED['Temp > 50']
    battery_frames = COUNTS['battery'] + (2 if fmt in SPECIAL else 0)    # + remote
    assert colouring.counts == [len(store) - battery_frames, hot,
                                battery_frames - hot], fmt
    assert colouring.bins[0].any() and colouring.bins[1].sum() > colouring.bins[0].sum()
    only_coloured = tq.filter_search(store, tq.TraceFilter(hidden_colours=frozenset({0})),
                                     colouring.numbers).run()
    assert len(only_coloured) == battery_frames
    not_hot = tq.filter_search(store, tq.TraceFilter(hidden_colours=frozenset({1})),
                               colouring.numbers).run()
    assert len(not_hot) == len(store) - hot


@pytest.mark.parametrize('fmt', SPECIAL)
def test_error_and_remote_frames_filter_apart(logs, fmt):
    from core.raw_frame_store import FLAG_ERROR, FLAG_REMOTE

    store = load(logs, fmt)
    flags = np.frombuffer(store.flags, dtype=np.uint8)
    errors = int(((flags & FLAG_ERROR) != 0).sum())
    remote = int((((flags & FLAG_REMOTE) != 0) & ((flags & FLAG_ERROR) == 0)).sum())
    assert errors == 2 and remote == 2
    total = len(store)
    assert len(tq.filter_search(store, tq.TraceFilter(errors=False)).run()) == total - errors
    assert len(tq.filter_search(store, tq.TraceFilter(remote=False)).run()) == total - remote
    assert len(tq.filter_search(store, tq.TraceFilter(data=False)).run()) == errors + remote
    # Neither is a message: no signal search finds them, even an ID-0 one.
    assert not np.isin(_search(store, 'Temp > -100'),
                       np.flatnonzero(flags & (FLAG_ERROR | FLAG_REMOTE))).any()


def test_time_bins_place_frames_along_the_measurement(logs):
    store = load(logs, 'asc')
    bins = tq.time_bins(store, np.array([0, len(store) - 1]), bins=10)
    assert bins.tolist() == [True] + [False] * 8 + [True]
    assert not tq.time_bins(store, None).any()


def test_work_in_steps_stops_when_the_measurement_closes():
    from core.raw_frame_store import RawFrameStore

    store = RawFrameStore()
    count = 5000
    zeros = np.zeros(count, np.uint8)
    store.append_raw_batch(np.arange(count, dtype=np.float64), zeros + 1,
                           np.full(count, 0x100, np.uint32), zeros + 8, zeros, zeros,
                           bytes(count * 64))
    store.seal()
    running = tq.filter_search(store, tq.TraceFilter(rx=False))
    store.close()
    with pytest.raises(RuntimeError):
        running.run()
