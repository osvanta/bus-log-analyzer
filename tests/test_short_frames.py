# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A frame shorter than the message it matched yields only the signals that fit
in the bytes it holds: one past the end gets no value from that frame, never a
0 read from the zero padding of the fixed-width record. That is what cantools
does with allow_truncated, and what the CAN Trace shows, so the plot and the
Trace agree. Every format reaches the same decode, so each gives the same
signals.
"""
from __future__ import annotations

from pathlib import Path

import cantools
import numpy as np
import pytest

from core.vectorized_decoder import MessageVectorDecoder

_KWARGS = {'decode_choices': False, 'scaling': True,
           'allow_truncated': True, 'allow_excess': True}

# Little-endian (fast path): byte and odd bit offsets, signed and scaled,
# a float, a 16-byte CAN FD message and a multiplexed one. Big-endian
# (slow path): Motorola signals across byte boundaries.
_UNIT_DBC = '''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 256 LittleEndian: 8 ECU
 SG_ A : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ D : 9|3@1+ (1,0) [0|7] "" ECU
 SG_ B : 12|10@1- (0.5,-3) [-300|300] "" ECU
 SG_ F : 32|32@1- (1,0) [0|0] "" ECU
BO_ 257 Muxed: 8 ECU
 SG_ Mux M : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ Low m1 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ High m2 : 40|16@1+ (1,0) [0|65535] "" ECU
BO_ 258 BigEndian: 8 ECU
 SG_ Big1 : 7|16@0+ (1,0) [0|65535] "" ECU
 SG_ Big2 : 39|12@0- (1,0) [0|0] "" ECU
BO_ 259 Long: 16 ECU
 SG_ Lo : 0|16@1+ (1,0) [0|65535] "" ECU
 SG_ Hi : 96|16@1+ (1,0) [0|65535] "" ECU
SIG_VALTYPE_ 256 F : 1;
'''


@pytest.fixture(scope='module')
def unit_db():
    return cantools.database.load_string(_UNIT_DBC, database_format='dbc')


def _rows(message, count: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, size=(count, 64), dtype=np.uint8)
    if message.name == 'Muxed':
        # Only the multiplexer values the message defines: cantools refuses others.
        data[:, 0] = rng.choice([1, 2], size=count)
    lengths = rng.integers(0, message.length + 1, size=count).astype(np.uint8)
    lengths[:count // 4] = message.length   # plenty of whole frames too
    return data, lengths


def _expected(message, data: np.ndarray, lengths: np.ndarray) -> dict[str, list]:
    """What cantools gives each row from the bytes it holds."""
    out = {signal.name: [] for signal in message.signals}
    for row, length in zip(data, lengths, strict=True):
        payload = bytes(row[:min(int(length), message.length)])
        decoded = message.decode(payload, **_KWARGS)
        for name in out:
            out[name].append(float(decoded[name]) if name in decoded else np.nan)
    return out


@pytest.mark.parametrize('name, fast', [
    ('LittleEndian', True), ('Muxed', True), ('Long', True), ('BigEndian', False),
])
def test_each_row_decodes_what_cantools_decodes_from_the_bytes_it_holds(
        unit_db, name, fast):
    message = unit_db.get_message_by_name(name)
    data, lengths = _rows(message, 400, seed=sum(map(ord, name)))
    decoder = MessageVectorDecoder(message, _KWARGS)

    result = decoder.decode(data, lengths=lengths)

    assert decoder.fully_fast is fast
    expected = _expected(message, data, lengths)
    for signal, values in expected.items():
        got = result[signal][1]
        np.testing.assert_allclose(got, values, rtol=1e-6, equal_nan=True,
                                   err_msg=f'{name}.{signal}')
    # Not vacuous: some rows were cut, some signals kept.
    assert any(np.isnan(v).any() for v in map(np.asarray, expected.values()))
    assert all(not np.isnan(np.asarray(v)).all() for v in expected.values())


@pytest.mark.parametrize('name', ['LittleEndian', 'Muxed', 'Long', 'BigEndian'])
def test_without_lengths_or_with_whole_frames_nothing_changes(unit_db, name):
    message = unit_db.get_message_by_name(name)
    data, _lengths = _rows(message, 50, seed=7)
    whole = np.full(50, message.length, dtype=np.uint8)
    longer = np.full(50, 64, dtype=np.uint8)

    plain = MessageVectorDecoder(message, _KWARGS).decode(data)
    for lengths in (whole, longer):
        given = MessageVectorDecoder(message, _KWARGS).decode(data, lengths=lengths)
        for signal, (_ext, values) in plain.items():
            np.testing.assert_array_equal(given[signal][1], values)


def test_a_cut_off_multiplexer_leaves_every_branch_out(unit_db):
    message = unit_db.get_message_by_name('Muxed')
    data = np.zeros((2, 64), dtype=np.uint8)
    data[:, 0] = 1
    data[:, 1] = 77

    result = MessageVectorDecoder(message, _KWARGS).decode(
        data, lengths=np.array([0, 8], dtype=np.uint8))

    assert np.isnan(result['Mux'][1][0]) and np.isnan(result['Low'][1][0])
    assert result['Low'][1][1] == 77


# ── Loaded, in every format ──────────────────────────────────────────────

_LOAD_DBC = '''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 128 Sync: 1 ECU
 SG_ Sync_Counter : 0|8@1+ (1,0) [0|255] "" ECU
BO_ 291 Short: 8 ECU
 SG_ Fits : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ Past : 32|16@1+ (1,0) [0|65535] "" ECU
BO_ 292 Motor: 8 ECU
 SG_ Speed : 7|16@0+ (1,0) [0|65535] "" ECU
 SG_ Temp : 39|8@0+ (1,0) [0|255] "" ECU
'''


def _load_frames() -> list[tuple[float, int, bytes]]:
    """Whole and short frames of three messages; every SYNC is empty, as
    CANopen sends it without a counter."""
    frames = []
    for i in range(10):
        t = 0.010 * (i + 1)
        frames.append((t, 0x080, b''))
        short = bytes([i + 1, 0, 0, 0, 0x34, 0x12, 0, 0])
        frames.append((t + 0.001, 0x123, short if i % 2 == 0 else short[:2]))
        motor = bytes([0x01, 0x02, 0, 0, 50, 0, 0, 0])
        frames.append((t + 0.002, 0x124, motor if i % 3 == 0 else motor[:2]))
    return frames


def _write_asc(path: Path) -> Path:
    import can
    with can.ASCWriter(str(path)) as writer:
        for t, arb_id, data in _load_frames():
            writer.on_message_received(can.Message(
                timestamp=t, arbitration_id=arb_id, data=data, dlc=len(data),
                is_extended_id=False, channel=0))
    return path


def _write_blf(path: Path) -> Path:
    import can
    with can.BLFWriter(str(path)) as writer:
        for t, arb_id, data in _load_frames():
            writer.on_message_received(can.Message(
                timestamp=1_700_000_000 + t, arbitration_id=arb_id, data=data,
                dlc=len(data), is_extended_id=False, channel=0))
    return path


def _write_trc(path: Path) -> Path:
    lines = [';$FILEVERSION=2.1', ';$STARTTIME=45306.4166666667',
             ';$COLUMNS=N,O,T,B,I,d,R,L,D']
    for index, (t, arb_id, data) in enumerate(_load_frames(), 1):
        lines.append(f'{index:>8} {t * 1000:>13.3f} DT 1  {arb_id:04X} Rx -  '
                     f'{len(data):<4} ' + ' '.join(f'{b:02X}' for b in data))
    path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('latin-1'))
    return path


def _write_mf4(path: Path) -> Path:
    from core.converter import ConvertJob, convert
    asc = _write_asc(path.with_suffix('.src.asc'))
    convert(ConvertJob(inputs=[asc], output=path))
    return path


_WRITERS = {'.asc': _write_asc, '.blf': _write_blf, '.trc': _write_trc, '.mf4': _write_mf4}

_FORMATS = list(_WRITERS)


def _load(measurement: Path, dbc: Path | None = None, config=None):
    from core.channel_config import ChannelConfig
    from gui.load_worker import LoadWorker

    worker = LoadWorker(
        str(measurement), config or ChannelConfig.from_single_dbc(str(dbc)))
    messages: list[str] = []
    result: dict = {}
    worker.progress.connect(messages.append)
    worker.finished.connect(lambda store: result.setdefault('store', store))
    worker.failed.connect(lambda err: result.setdefault('error', err))
    worker.run()
    assert 'error' not in result, result.get('error')
    store = result['store']
    # Keyed without the channel: formats number the first bus differently.
    decoded = {
        key.split('::', 1)[1]: list(series.values)
        for key, series in store._series_by_key.items()
    }
    return decoded, store, messages


@pytest.fixture()
def load_dbc(tmp_path) -> Path:
    path = tmp_path / 'short.dbc'
    path.write_text(_LOAD_DBC, encoding='utf-8')
    return path


@pytest.mark.parametrize('suffix', _FORMATS)
def test_every_format_decodes_only_what_each_frame_holds(qapp, tmp_path, load_dbc, suffix):
    measurement = _WRITERS[suffix](tmp_path / f'short{suffix}')

    decoded, store, messages = _load(measurement, load_dbc)

    assert decoded['Short::Fits'] == [float(i + 1) for i in range(10)]
    assert decoded['Short::Past'] == [4660.0] * 5          # whole frames only
    assert decoded['Motor::Speed'] == [258.0] * 10        # big-endian, fits
    assert decoded['Motor::Temp'] == [50.0] * 4           # whole frames only
    assert 'Sync::Sync_Counter' not in decoded            # never sent
    # In the Log for the frame formats; a bus-logging MF4 reports it among
    # its load warnings, the only report its reader makes after decoding.
    warning = next(m for m in [*messages, *store.load_warnings]
                   if 'shorter than the message' in m)
    assert '21 frame(s)' in warning and 'got no value' in warning


@pytest.mark.parametrize('suffix', [s for s in _FORMATS if s != '.asc'])
def test_the_formats_agree(qapp, tmp_path, load_dbc, suffix):
    asc = _load(_write_asc(tmp_path / 'agree.asc'), load_dbc)[0]

    decoded = _load(_WRITERS[suffix](tmp_path / f'agree{suffix}'), load_dbc)[0]

    assert decoded == asc


def test_the_plot_holds_what_the_can_trace_shows(qapp, tmp_path, load_dbc):
    from core.models import RawFrame

    decoded, store, _messages = _load(_write_asc(tmp_path / 'trace.asc'), load_dbc)
    rfs = store.raw_frame_store
    trace: dict[str, list] = {}
    for record in rfs.get_window(range(len(rfs))):
        frame = RawFrame(
            timestamp=record.time_s, channel=record.channel,
            arbitration_id=record.arbitration_id, is_extended_id=record.is_extended,
            is_fd=record.is_fd, dlc=record.dlc, data=record.data[:record.dlc],
            direction=record.direction,
        )
        for sample in rfs.decoder.decode_frame(frame):
            trace.setdefault(f'{sample.message_name}::{sample.signal_name}', []).append(
                sample.numeric_value)

    assert decoded == trace


# ── Bus-logging MF4, decoded by asammdf ──────────────────────────────────
#
# asammdf reads each frame at its message's full length; the reader leaves
# out what a short frame does not hold, matched to asammdf's groups by time
# stamp. The same frames as ASC go through the vectorized decoder, proven
# against cantools above, so an MF4 must decode exactly as its ASC does.

_HARD_DBC = f'''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ {0x98FECAEF} DM1_239: 22 ECU
 SG_ DTC1 : 16|32@1+ (1,0) [0|4294967295] "" ECU
 SG_ DTC2 : 48|32@1+ (1,0) [0|4294967295] "" ECU
BO_ {0x98FECAF3} DM1_243: 22 ECU
 SG_ DTC1 : 16|32@1+ (1,0) [0|4294967295] "" ECU
 SG_ DTC2 : 48|32@1+ (1,0) [0|4294967295] "" ECU
BO_ {0x8CFEF100} CCVS: 8 ECU
 SG_ Speed : 8|16@1+ (1,0) [0|65535] "" ECU
 SG_ Brake : 32|8@1+ (1,0) [0|255] "" ECU
BO_ 801 MuxMsg: 8 ECU
 SG_ Mux M : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ Near m1 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ Far m2 : 40|16@1+ (1,0) [0|65535] "" ECU
BO_ 802 Motor: 8 ECU
 SG_ Speed : 7|16@0+ (1,0) [0|65535] "" ECU
 SG_ Temp : 39|8@0+ (1,0) [0|255] "" ECU
BA_DEF_ BO_ "VFrameFormat" ENUM "StandardCAN","ExtendedCAN","reserved","J1939PG";
BA_DEF_DEF_ "VFrameFormat" "StandardCAN";
BA_ "VFrameFormat" BO_ {0x98FECAEF} 3;
BA_ "VFrameFormat" BO_ {0x98FECAF3} 3;
BA_ "VFrameFormat" BO_ {0x8CFEF100} 3;
'''


def _hard_frames(two_priorities: bool = False) -> list[tuple[float, int, bytes, bool]]:
    frames = []
    for i in range(6):
        t = 0.010 * (i + 1)
        dm1 = bytes([0x00, 0xFF, 0x45, 0xF1, 0, 0, 0xFF, 0xFF])
        frames.append((t, 0x18FECAEF, dm1, True))                 # 8 of 22 bytes
        frames.append((t + 0.0001, 0x18FECAF3, bytes(8), True))
        ccvs = bytes([0, 0x10 + i, 0x02, 0, 7, 0, 0, 0])
        frames.append((t + 0.0002, 0x0CFEF117, ccvs, True))       # sender 0x17: whole
        frames.append((t + 0.0003, 0x0CFEF121, ccvs[:4], True))   # sender 0x21: no Brake
        if two_priorities and i % 2:
            # Sender 0x17 again, at priority 6 and short.
            frames.append((t + 0.0006, 0x18FEF117, ccvs[:4], True))
        mux = bytes([1 + i % 2, 30 + i, 0, 0, 0, 0x34, 0x12, 0])
        frames.append((t + 0.0004, 0x321, mux if i < 2 else mux[:2], False))
        motor = bytes([0x01, 0x02, 0, 0, 50, 0, 0, 0])
        frames.append((t + 0.0005, 0x322, motor if i % 3 == 0 else motor[:2], False))
    return frames


def _write_hard_asc(path: Path, frames=None) -> Path:
    import can
    with can.ASCWriter(str(path)) as writer:
        for t, arb_id, data, extended in frames or _hard_frames():
            writer.on_message_received(can.Message(
                timestamp=t, arbitration_id=arb_id, data=data, dlc=len(data),
                is_extended_id=extended, channel=0))
    return path


def _write_hard_mf4(path: Path, frames=None) -> Path:
    from core.converter import ConvertJob, convert
    asc = _write_hard_asc(path.with_suffix('.src.asc'), frames)
    convert(ConvertJob(inputs=[asc], output=path))
    return path


@pytest.fixture()
def hard_dbc(tmp_path) -> Path:
    path = tmp_path / 'hard.dbc'
    path.write_text(_HARD_DBC, encoding='utf-8')
    return path


def test_an_mf4_decodes_exactly_as_its_asc(qapp, tmp_path, hard_dbc):
    asc = _load(_write_hard_asc(tmp_path / 'hard.asc'), hard_dbc)[0]

    mf4 = _load(_write_hard_mf4(tmp_path / 'hard.mf4'), hard_dbc)[0]

    assert mf4 == asc
    # Not vacuous: each case left something out, and kept the rest.
    assert 'DM1_239::DTC2' not in mf4 and mf4['DM1_239::DTC1'] == [61765.0] * 6
    assert 'CCVS [SA 0x21]::Brake' not in mf4
    assert len(mf4['CCVS [SA 0x21]::Speed']) == 6 and len(mf4['CCVS [SA 0x17]::Brake']) == 6
    assert len(mf4['MuxMsg::Near']) == 3 and mf4['MuxMsg::Far'] == [4660.0]
    assert mf4['Motor::Temp'] == [50.0, 50.0] and len(mf4['Motor::Speed']) == 6


def test_a_j1939_senders_short_frames_are_found_at_any_priority(qapp, tmp_path, hard_dbc):
    """asammdf gathers a J1939 sender's frames by PGN and source address,
    whatever their priority, so its short frames are looked for the same way;
    and of its group per frame ID, each holding all those frames, one is read."""
    frames = _hard_frames(two_priorities=True)
    asc = _load(_write_hard_asc(tmp_path / 'priorities.asc', frames), hard_dbc)[0]

    decoded = _load(_write_hard_mf4(tmp_path / 'priorities.mf4', frames), hard_dbc)[0]

    # Brake is past the end of the priority-6 frames: only the whole frames'
    # 7, never the padding. Speed fits in them, so their values are kept.
    assert set(decoded['CCVS [SA 0x17]::Brake']) == {7.0}
    assert {529.0, 531.0, 533.0} <= set(decoded['CCVS [SA 0x17]::Speed'])
    # Each sample once, as the ASC of the same frames has it.
    assert decoded == asc
    assert len(decoded['CCVS [SA 0x17]::Speed']) == 9


def test_an_mf4_without_short_frames_is_unchanged(qapp, tmp_path, hard_dbc, monkeypatch):
    from core.readers import mdf_can_reader

    whole = [frame for frame in _hard_frames()
             if len(frame[2]) == 8 and frame[1] not in (0x18FECAEF, 0x18FECAF3)]
    path = _write_hard_mf4(tmp_path / 'whole.mf4', whole)

    decoded = _load(path, hard_dbc)[0]
    monkeypatch.setattr(mdf_can_reader.MDFCANReader, '_find_short_frames',
                        lambda *args: mdf_can_reader._ShortFrames())
    before = _load(path, hard_dbc)[0]

    assert decoded == before


def _lazy_and_eager(path: Path, database: Path, config):
    """By (message, signal): the values of the eager and of the lazy read of
    a bus-logging MF4; and the signal tree's rows, announced before any
    sample is read."""
    from core.readers.mdf_can_reader import MDFCANReader

    eager = {
        (meta[1], meta[3]): list(np.asarray(values, dtype=float))
        for meta, _ts, values, _display in
        MDFCANReader(path, str(database)).iter_decoded_channel_arrays(config)
    }
    tree = []
    sources = MDFCANReader(path, str(database)).open_decoded_sources(
        config, metadata_ready=tree.extend)
    try:
        groups = sorted({signal[0] for signal in sources.signals})
        read = sources.source.read_groups(groups)
        lazy = {}
        for group, _channel, message, _id, name, _unit, count in sources.signals:
            parts = read[(group, name)]
            values = [float(v) for part in parts for v in np.asarray(part[1], dtype=float)]
            assert len(values) == count, (message, name)
            lazy[(message, name)] = values
    finally:
        sources.source.close()
    return eager, lazy, tree


def test_the_lazy_and_the_eager_mf4_read_agree(qapp, tmp_path, hard_dbc):
    from core.channel_config import ChannelConfig

    path = _write_hard_mf4(tmp_path / 'both.mf4', None)
    config = ChannelConfig.from_single_dbc(str(hard_dbc))

    eager, lazy, tree = _lazy_and_eager(path, hard_dbc, config)

    assert lazy == eager
    assert ('MuxMsg', 'Far') in lazy and ('Motor', 'Temp') in lazy
    # A signal no frame was long enough for is not offered at all.
    assert {(row[1], row[3]) for row in tree} == set(lazy)
    assert ('DM1_239', 'DTC2') not in lazy


@pytest.mark.parametrize('name', ['LittleEndian', 'BigEndian', 'Long'])
def test_the_bytes_a_signal_needs_are_what_cantools_decides(unit_db, name):
    from core.readers.mdf_can_reader import _bytes_needed

    message = unit_db.get_message_by_name(name)
    for length in range(message.length + 1):
        decoded = message.decode(bytes(range(1, length + 1)), **_KWARGS)
        for signal in message.signals:
            assert (signal.name in decoded) == (length >= _bytes_needed(signal)), \
                (name, signal.name, length)


# ── LIN bus-logging MF4, decoded by asammdf ──────────────────────────────
#
# asammdf reads a LIN frame at its message's full length too, and fills each
# bus's group with the frames of its ID from every bus channel. Both are left
# out the same way. The MF4 records are filled up with 0xFF, so a value read
# past a frame's end would show.

def _lin_writers():
    from tests.fixtures._generate import write_lin_asc, write_lin_blf, write_lin_mf4
    return {'.asc': write_lin_asc, '.blf': write_lin_blf,
            '.mf4': lambda path, frames, **kw: write_lin_mf4(path, frames, padding=0xFF, **kw)}


def _lin_frames(longest: int = 2) -> list[tuple[float, int, int, bytes]]:
    """sample.ldf's DoorCmd (2 bytes) whole, cut to 1 byte and empty, at most
    *longest* bytes; DoorStatus (1 byte) always whole. All on LIN 1."""
    frames = []
    for i in range(9):
        t = 0.010 * (i + 1)
        cmd = bytes([10 + i, i // 3])
        frames.append((t, 1, 0x20, cmd[:min(longest, 2 - i % 3)]))
        frames.append((t + 0.001, 1, 0x21, bytes([20 + i])))
    return frames


def _two_bus_frames() -> list[tuple[float, int, int, bytes]]:
    """The traffic of sample_lin.blf: LIN 1 and LIN 2 carry the same frame
    IDs at opposite lengths."""
    from tests.fixtures._generate import _LIN_BUS_FRAMES
    frames = []
    for _repeat in range(2):
        for channel, payloads in _LIN_BUS_FRAMES.items():
            for frame_id, payload in payloads:
                frames.append((0.010 * (len(frames) + 1), channel, frame_id, payload))
    return frames


@pytest.fixture()
def lin_ldf(sample_ldf_path) -> Path:
    from core.readers.db_format import ldf_support_available
    available, message = ldf_support_available()
    if not available:
        pytest.skip(f'LDF support unavailable: {message}')
    return sample_ldf_path


def _lin_config(*databases: Path):
    from core.bus_types import BusType
    from core.channel_config import ChannelConfig
    return ChannelConfig(name='LIN', channels={
        (BusType.LIN, number): str(path) for number, path in enumerate(databases, 1)
    })


def _every_lin_channel(database: Path):
    """*database* on all LIN channels, as the Database Manager assigns it."""
    from core.bus_types import BusType
    from core.channel_config import ChannelConfig, all_channels_key
    return ChannelConfig(name='LIN', channels={all_channels_key(BusType.LIN): str(database)})


def _short_frame_warning(lines) -> str:
    return next(line for line in lines if 'shorter than the message' in line)


def _with_channels(store) -> dict[str, list]:
    return {key: list(series.values) for key, series in store._series_by_key.items()}


@pytest.mark.parametrize('reference', ['.asc', '.blf'])
def test_a_lin_mf4_decodes_exactly_as_the_same_frames(qapp, tmp_path, lin_ldf, reference):
    frames = _lin_frames()
    writers = _lin_writers()
    expected, _store, report = _load(
        writers[reference](tmp_path / f'lin{reference}', frames), config=_lin_config(lin_ldf))

    decoded, store, _report = _load(
        writers['.mf4'](tmp_path / 'lin.mf4', frames), config=_lin_config(lin_ldf))

    assert decoded == expected
    # Not vacuous: each cut left something out, and kept the rest.
    assert decoded['DoorCmd::LockState'] == [0.0, 1.0, 2.0]
    assert decoded['DoorCmd::WindowPos'] == [10.0, 11.0, 13.0, 14.0, 16.0, 17.0]
    assert len(decoded['DoorStatus::MirrorAngle']) == 9
    warning = _short_frame_warning(store.load_warnings)
    assert '6 frame(s)' in warning and '(DoorCmd)' in warning
    assert '6 frame(s)' in _short_frame_warning(report)


def test_the_lazy_and_the_eager_lin_mf4_read_agree(qapp, tmp_path, lin_ldf, alt_ldf_path):
    """LIN 1 sends DoorCmd, never whole, and DoorStatus; LIN 2 only Seat
    (0x21, as DoorStatus). asammdf gives LIN 2 a Latch group of LIN 1's 0x20
    frames, and each 0x21 group both buses' frames."""
    frames = _lin_frames(longest=1) + [
        (0.010 * (i + 1) + 0.005, 2, 0x21, bytes([50 + i, i])) for i in range(4)]
    path = _lin_writers()['.mf4'](tmp_path / 'both.mf4', sorted(frames))

    eager, lazy, tree = _lazy_and_eager(path, lin_ldf, _lin_config(lin_ldf, alt_ldf_path))

    assert lazy == eager
    assert lazy[('DoorCmd', 'WindowPos')] == [10.0, 11.0, 13.0, 14.0, 16.0, 17.0]
    assert lazy[('DoorStatus', 'MirrorAngle')] == [20.0 + i for i in range(9)]
    assert lazy[('Seat', 'SeatHeat')] == [0.0, 1.0, 2.0, 3.0]
    # In no frame of their own bus long enough: not offered at all.
    assert ('DoorCmd', 'LockState') not in lazy
    assert not any(message == 'Latch' for message, _signal in lazy)
    assert {(row[1], row[3]) for row in tree} == set(lazy)


def test_a_lin_mf4_without_short_frames_is_unchanged(qapp, tmp_path, lin_ldf, monkeypatch):
    from core.readers import mdf_can_reader

    frames = [frame for frame in _lin_frames() if len(frame[3]) == (2 if frame[2] == 0x20 else 1)]
    path = _lin_writers()['.mf4'](tmp_path / 'whole.mf4', frames)

    decoded, store, _report = _load(path, config=_lin_config(lin_ldf))
    monkeypatch.setattr(mdf_can_reader.MDFCANReader, '_find_short_frames',
                        lambda *args: mdf_can_reader._ShortFrames())
    before = _load(path, config=_lin_config(lin_ldf))[0]

    assert decoded == before
    assert decoded['DoorCmd::LockState'] == [0.0, 1.0, 2.0]
    assert store.load_warnings == []


@pytest.mark.parametrize('swapped', [False, True])
def test_a_two_bus_lin_mf4_decodes_exactly_as_the_same_frames(
        qapp, tmp_path, lin_ldf, alt_ldf_path, swapped):
    """LIN 1 and LIN 2 both send 0x20 and 0x21, at opposite lengths, each
    decoded with its own LDF. asammdf gives each bus's group every frame of
    its ID: the other bus's frames are left out. Swapped, the databases make
    each bus's frames short for one message, and the same frames are reported."""
    frames = _two_bus_frames()
    writers = _lin_writers()
    config = _lin_config(*((alt_ldf_path, lin_ldf) if swapped else (lin_ldf, alt_ldf_path)))
    blf_store, report = _load(writers['.blf'](tmp_path / 'buses.blf', frames), config=config)[1:]

    store = _load(writers['.mf4'](tmp_path / 'buses.mf4', frames), config=config)[1]

    decoded = _with_channels(store)
    assert decoded == _with_channels(blf_store)
    if swapped:
        assert decoded['LIN1::Seat::SeatPos'] == [20.0, 40.0] * 2   # LIN 1's only
        assert 'LIN1::Seat::SeatHeat' not in decoded                # never whole
        warning = _short_frame_warning(store.load_warnings)
        assert '8 frame(s)' in warning and '(DoorCmd, Seat)' in warning
        assert '8 frame(s)' in _short_frame_warning(report)
    else:
        assert decoded['LIN1::DoorCmd::WindowPos'] == [10.0, 30.0] * 2
        assert decoded['LIN2::Latch::LatchState'] == [7.0, 9.0] * 2
        assert not any('shorter than the message' in line for line in store.load_warnings)


def test_a_lin_frame_id_on_one_bus_is_not_decoded_on_another(qapp, tmp_path, lin_ldf):
    """One LDF on all LIN channels; LIN 1 sends only DoorCmd and LIN 2 only
    DoorStatus. asammdf still gives each bus a group for each ID."""
    frames = sorted(
        [(0.010 * (i + 1), 1, 0x20, bytes([10 + i, i % 3])) for i in range(4)]
        + [(0.010 * (i + 1) + 0.005, 2, 0x21, bytes([20 + i])) for i in range(4)])
    writers = _lin_writers()
    config = _every_lin_channel(lin_ldf)
    expected = _with_channels(
        _load(writers['.blf'](tmp_path / 'apart.blf', frames), config=config)[1])

    decoded = _with_channels(
        _load(writers['.mf4'](tmp_path / 'apart.mf4', frames), config=config)[1])

    assert decoded == expected
    assert set(decoded) == {'LIN1::DoorCmd::WindowPos', 'LIN1::DoorCmd::LockState',
                            'LIN2::DoorStatus::MirrorAngle'}


def test_a_lin_mf4_without_bus_channels_is_on_lin_1(qapp, tmp_path, lin_ldf, alt_ldf_path):
    """A one-bus recorder may leave out LIN_Frame.BusChannel: asammdf then
    takes every frame as LIN 1, and so does the reader. The LDF on LIN 1
    decodes it, not the one on all LIN channels."""
    from core.bus_types import BusType
    from core.channel_config import ChannelConfig, all_channels_key

    frames = _lin_frames()
    writers = _lin_writers()
    config = ChannelConfig(name='LIN', channels={
        (BusType.LIN, 1): str(lin_ldf), all_channels_key(BusType.LIN): str(alt_ldf_path)})
    expected = _with_channels(
        _load(writers['.asc'](tmp_path / 'one.asc', frames), config=config)[1])

    store = _load(writers['.mf4'](tmp_path / 'one.mf4', frames, bus_channel=False),
                  config=config)[1]

    assert _with_channels(store) == expected
    assert expected['LIN1::DoorCmd::LockState'] == [0.0, 1.0, 2.0]
    assert '6 frame(s)' in _short_frame_warning(store.load_warnings)
