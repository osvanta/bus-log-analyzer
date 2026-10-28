# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The TRC reader against trace files written here, one layout per version of
PEAK's format description: frames, times, the lines about the bus, and what it
cannot show. The files are self-made, not recorded by PEAK's tools.
"""
from __future__ import annotations

import datetime as dt
from typing import NamedTuple

import pytest

from core.raw_frame_store import FLAG_BRS, FLAG_ERROR, FLAG_EXTENDED, FLAG_FD, FLAG_REMOTE
from core.readers.trc_can_reader import (
    TRCCANReader, TRCReadError, read_trc_events, read_trc_header,
)

# 2024-01-15 10:00:00 on the recording PC's clock.
START = dt.datetime(2024, 1, 15, 10, 0, 0)
START_DAYS = (START - dt.datetime(1899, 12, 30)).total_seconds() / 86400


class Row(NamedTuple):
    time: float
    channel: int
    arb_id: int
    dlc: int
    direction: int
    flags: int
    data: bytes


def _write(path, lines, newline='\r\n'):
    path.write_bytes((newline.join(lines) + newline).encode('latin-1'))
    return path


def _rows(path, batch_size=16_384):
    reader = TRCCANReader(path)
    rows = []
    for base, times, channels, ids, dlcs, dirs, flags, data in reader.iter_raw_batches(batch_size):
        for i, offset in enumerate(times):
            length = 0 if flags[i] & (FLAG_REMOTE | FLAG_ERROR) else dlcs[i]
            rows.append(Row(base + offset, channels[i], ids[i], dlcs[i], dirs[i], flags[i],
                            bytes(data[i * 64:i * 64 + length])))
    return rows, reader


def _header(version, columns=None):
    lines = [f';$FILEVERSION={version}', f';$STARTTIME={START_DAYS:.10f}']
    if columns:
        lines.append(f';$COLUMNS={columns}')
    return lines + [';', ';   made by the test suite', ';']


# ── One file per version: the same three frames ──────────────────────────
#
# 0x123 on bus 1, received, 8 bytes; 0x18FEF100 (29-bit) on bus 2, sent,
# 3 bytes; 0x00000123 — a 29-bit ID whose value fits 11 bits — on bus 1,
# received, 1 byte. Offsets 1000.0, 1010.5 and 1021.25 ms where the version
# has the resolution.

FRAMES = {
    '1.0': [
        '     1)      1000  0123  8  11 22 33 44 55 66 77 88',
        '     2)      1010  18FEF100  3  AA BB CC',
        '     3)      1021  00000123  1  5A',
    ],
    '1.1': _header('1.1') + [
        '     1)      1000.0  Rx         0123  8  11 22 33 44 55 66 77 88',
        '     2)      1010.5  Tx     18FEF100  3  AA BB CC',
        '     3)      1021.2  Rx     00000123  1  5A',
    ],
    '1.2': _header('1.2') + [
        '     1)      1000.000  1  Rx         0123  8  11 22 33 44 55 66 77 88',
        '     2)      1010.500  2  Tx     18FEF100  3  AA BB CC',
        '     3)      1021.250  1  Rx     00000123  1  5A',
    ],
    '1.3': _header('1.3') + [
        '     1)      1000.000 1  Rx        0123 -  8    11 22 33 44 55 66 77 88',
        '     2)      1010.500 2  Tx    18FEF100 -  3    AA BB CC',
        '     3)      1021.250 1  Rx    00000123 -  1    5A',
    ],
    '2.0': _header('2.0', 'N,O,T,I,d,l,D') + [
        '       1      1000.000 DT     0123 Rx 8  11 22 33 44 55 66 77 88',
        '       2      1010.500 DT 18FEF100 Tx 3  AA BB CC',
        '       3      1021.250 DT 00000123 Rx 1  5A',
    ],
    '2.1': _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        '       1      1000.000 DT 1      0123 Rx -  8    11 22 33 44 55 66 77 88',
        '       2      1010.500 DT 2  18FEF100 Tx -  3    AA BB CC',
        '       3      1021.250 DT 1  00000123 Rx -  1    5A',
    ],
    '3.0': _header('3.0', 'N,O,T,B,I,d,R,V,S,A,r,s,L,D') + [
        '        1      1000.000 DT 1       123 Rx -  -  -  -        - - 8    11 22 33 44 55 66 77 88',
        '        2      1010.500 DT 2  18FEF100 Tx -  -  -  -        - - 3    AA BB CC',
        '        3      1021.250 DT 1  00000123 Rx -  -  -  -        - - 1    5A',
    ],
}


@pytest.mark.parametrize('version', list(FRAMES))
def test_each_version_reads_its_frames(tmp_path, version):
    rows, reader = _rows(_write(tmp_path / f'v{version}.trc', FRAMES[version]))

    assert reader.header.version == version
    assert [row.arb_id for row in rows] == [0x123, 0x18FEF100, 0x123]
    # Extended by its 8 digits, not by its value.
    assert [bool(row.flags & FLAG_EXTENDED) for row in rows] == [False, True, True]
    assert [row.data for row in rows] == [bytes.fromhex('1122334455667788'),
                                          bytes.fromhex('AABBCC'), b'\x5a']
    assert [row.dlc for row in rows] == [8, 3, 1]
    has_bus = version not in ('1.0', '1.1', '2.0')
    assert [row.channel for row in rows] == ([1, 2, 1] if has_bus else [1, 1, 1])
    assert [row.direction for row in rows] == ([2, 2, 2] if version == '1.0' else [0, 1, 0])
    assert reader.load_warnings == []


def test_a_29_bit_id_written_with_7_digits_is_still_extended(tmp_path):
    """python-can's TRCWriter writes 0x0CF00400 as CF00400; no 11-bit ID is
    that large, so it is a 29-bit one all the same."""
    import can

    path = tmp_path / 'pycan.trc'
    with can.TRCWriter(str(path)) as writer:
        writer.on_message_received(can.Message(timestamp=1.0, arbitration_id=0x0CF00400,
                                               is_extended_id=True, data=b'\x01', channel=0))
        writer.on_message_received(can.Message(timestamp=1.1, arbitration_id=0x123,
                                               is_extended_id=False, data=b'\x02', channel=0))
    assert ' CF00400 ' in path.read_text()
    rows, _ = _rows(path)
    assert [(row.arb_id, bool(row.flags & FLAG_EXTENDED)) for row in rows] == [
        (0x0CF00400, True), (0x123, False)]


@pytest.mark.parametrize('version, offsets', [
    ('1.0', (1.000, 1.010, 1.021)),             # whole milliseconds
    ('1.1', (1.000, 1.0105, 1.0212)),           # 0.1 ms
    ('1.2', (1.000, 1.0105, 1.02125)),          # 1 µs from here on
    ('1.3', (1.000, 1.0105, 1.02125)),
    ('2.0', (1.000, 1.0105, 1.02125)),
    ('2.1', (1.000, 1.0105, 1.02125)),
    ('3.0', (1.000, 1.0105, 1.02125)),
])
def test_times_keep_each_versions_resolution(tmp_path, version, offsets):
    rows, _ = _rows(_write(tmp_path / 'times.trc', FRAMES[version]))
    first = rows[0].time
    assert [row.time - first for row in rows] == pytest.approx(
        [offset - offsets[0] for offset in offsets], abs=1e-7)


def test_the_start_time_makes_times_absolute(tmp_path):
    rows, reader = _rows(_write(tmp_path / 'start.trc', FRAMES['2.1']))
    # Read as the recording PC's clock, as a BLF's start is.
    assert reader.header.start == pytest.approx(START, abs=dt.timedelta(microseconds=10))
    assert rows[0].time == pytest.approx(START.timestamp() + 1.000, abs=1e-5)


def test_a_1_0_file_has_no_start_and_keeps_its_own_zero(tmp_path):
    rows, reader = _rows(_write(tmp_path / 'v10.trc', FRAMES['1.0']))
    assert reader.header.start is None
    assert rows[0].time == pytest.approx(1.000)


def test_a_start_before_1970_leaves_times_relative(tmp_path):
    """Day 0 is 30 December 1899: no epoch time on Windows, so the frames keep
    the file's own zero rather than the file failing to open."""
    path = _write(tmp_path / 'day0.trc', [';$FILEVERSION=2.1', ';$STARTTIME=0',
                                           ';$COLUMNS=N,O,T,B,I,d,R,L,D'] + FRAMES['2.1'][-3:])
    rows, reader = _rows(path)
    assert reader.header.start == dt.datetime(1899, 12, 30)
    assert rows[0].time == pytest.approx(1.000)


def test_batches_split_anywhere_give_the_same_rows(tmp_path):
    lines = _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        f'{i + 1:>8} {1000 + i * 1.25:>13.3f} DT 1      0{0x100 + i:03X} Rx -  2    {i:02X} 00'
        for i in range(7)
    ]
    path = _write(tmp_path / 'batches.trc', lines)
    whole, _ = _rows(path)
    split, _ = _rows(path, batch_size=3)
    assert len(whole) == 7 and split == whole


def test_memory_stays_flat_however_long_the_file(tmp_path):
    """Batches are handed on as they fill: a file eight times longer needs
    no more memory to read."""
    import tracemalloc

    def peak(frames):
        path = tmp_path / f'long{frames}.trc'
        with path.open('w', newline='\r\n') as stream:
            stream.write(f';$FILEVERSION=2.1\n;$STARTTIME={START_DAYS:.10f}\n'
                         ';$COLUMNS=N,O,T,B,I,d,R,L,D\n')
            for i in range(frames):
                stream.write(f'{i + 1:>8} {i * 0.5:>13.3f} DT 1      0{i % 0x7FF:03X} '
                             'Rx -  8    01 02 03 04 05 06 07 08\n')
        tracemalloc.start()
        try:
            for _batch in TRCCANReader(path).iter_raw_batches():
                pass
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    assert peak(160_000) < 1.5 * peak(20_000)


# ── CAN FD, remote frames ────────────────────────────────────────────────

def test_fd_lengths_from_the_length_or_the_dlc_column(tmp_path):
    twelve = ' '.join(f'{i:02X}' for i in range(12))
    by_length = _write(tmp_path / 'l.trc', _header('2.0', 'N,O,T,I,d,l,D') + [
        f'       1      1000.000 FD     0300 Tx 12 {twelve}',
    ])
    by_dlc = _write(tmp_path / 'L.trc', _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        f'       1      1000.000 FD 1      0300 Tx -  9    {twelve}',
    ])
    for path in (by_length, by_dlc):
        (row,), _ = _rows(path)
        assert row.dlc == 12 and row.data == bytes(range(12))
        assert row.flags & FLAG_FD and not row.flags & FLAG_BRS


@pytest.mark.parametrize('kind, brs, esi', [
    ('FD', False, False), ('FB', True, False), ('FE', False, True), ('BI', True, True),
])
def test_fd_types_set_bit_rate_switch_and_esi(tmp_path, kind, brs, esi):
    path = _write(tmp_path / 'fd.trc', _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        f'       1      1000.000 {kind} 1      0300 Rx -  2    01 02',
    ])
    (row,), reader = _rows(path)
    assert row.flags & FLAG_FD
    assert bool(row.flags & FLAG_BRS) is brs
    (message,) = list(reader.iter_messages())
    assert message.error_state_indicator is esi and message.bitrate_switch is brs


@pytest.mark.parametrize('version, line', [
    ('1.0', '     1)      1000  0055  6  RTR'),
    ('1.1', '     1)      1000.0  Rx         0055  6  RTR'),
    ('1.3', '     1)      1000.000 1  Rx        0055 -  6    RTR'),
    ('2.1', '       1      1000.000 RR 1      0055 Rx -  6'),
])
def test_remote_frames_keep_the_length_they_asked_for(tmp_path, version, line):
    columns = 'N,O,T,B,I,d,R,L,D' if version >= '2.0' else None
    lines = ([line] if version == '1.0' else _header(version, columns) + [line])
    (row,), _ = _rows(_write(tmp_path / 'rtr.trc', lines))
    assert row.flags & FLAG_REMOTE and row.arb_id == 0x55
    assert row.dlc == 6 and row.data == b''


# ── Lines about the bus ──────────────────────────────────────────────────

@pytest.mark.parametrize('version, line, direction', [
    # 1.x: direction (0 sending, 1 receiving), position, RX and TX counters.
    ('1.0', '     1)      1000  0008  4  ERROR 01 19 0C 21', 0),
    ('1.1', '     1)      1000.0  Error      0004  4  00 19 0C 21', 1),
    # 2.0 leaves the ID and length columns of an error frame empty.
    ('2.0', '       1      1000.000 ER          Rx    04 01 02 0C 21', 0),
    ('2.1', '       1      1000.000 ER 2         - Tx -  5    04 00 02 0C 21', 1),
])
def test_error_frames_are_rows_with_their_counters(tmp_path, version, line, direction):
    columns = {'2.0': 'N,O,T,I,d,l,D', '2.1': 'N,O,T,B,I,d,R,L,D'}.get(version)
    lines = [line] if version == '1.0' else _header(version, columns) + [line]
    (row,), reader = _rows(_write(tmp_path / 'error.trc', lines))
    assert row.flags & FLAG_ERROR and row.direction == direction
    assert row.channel == (2 if version == '2.1' else 1)
    (status,) = reader.bus_status
    assert status.kind == 'controller' and status.channel == row.channel
    assert status.values == {'rec': 0x0C, 'tec': 0x21, 'state': 'error active'}
    assert status.time_s == pytest.approx(row.time)


@pytest.mark.parametrize('code, state, overrun', [
    ('00 00 00 00', 'error active', False),
    ('00 00 00 02', 'error active', True),
    ('00 00 00 08', 'warning', False),
    ('00 04 00 00', 'error passive', False),
    ('00 00 00 10', 'bus off', False),
])
def test_hardware_status_gives_the_controller_state(tmp_path, code, state, overrun):
    path = _write(tmp_path / 'st.trc', _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        f'       1      1000.000 ST 1         - Rx -  4    {code}',
    ])
    rows, reader = _rows(path)
    assert rows == []
    (status,) = reader.bus_status
    assert (status.kind, status.channel, status.values.get('state')) == ('controller', 1, state)
    assert status.values.get('overrun', False) is overrun


def test_1_x_error_warnings_give_the_controller_state(tmp_path):
    v10 = _write(tmp_path / 'w10.trc', [
        '     1)      1000  FFFFFFFF  4  00 00 00 10 -- -- -- --  BUSOFF',
    ])
    v11 = _write(tmp_path / 'w11.trc', _header('1.1') + [
        '     1)      1000.0  Warng  FFFFFFFF  4  00 00 00 08  BUSHEAVY',
    ])
    assert [s.values['state'] for s in _rows(v10)[1].bus_status] == ['bus off']
    assert [s.values['state'] for s in _rows(v11)[1].bus_status] == ['warning']


def test_error_counters_and_overload_frames(tmp_path):
    path = _write(tmp_path / 'ec.trc', _header('3.0', 'N,O,T,B,I,d,R,V,S,A,r,s,L,D') + [
        '        1      1000.000 EC 1         - Rx -  -  -  -        - - 2    05 82',
        '        2      1001.000 OF 2         - Rx -  -  -  -        - - 3    01 02 00',
    ])
    rows, reader = _rows(path)
    assert rows == []
    counters, overload = reader.bus_status
    assert counters.values == {'rec': 5, 'tec': 0x82, 'state': 'error passive'}
    assert (overload.kind, overload.channel) == ('overload', 2)


def test_events_are_kept_and_found_without_reading_frames(tmp_path):
    path = _write(tmp_path / 'ev.trc', _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        '       1      1000.000 DT 1      0123 Rx -  1    01',
        '       2      1500.250 EV 1  Gear change on bus 1',
        # Commented out with no space after the ';', so its columns still
        # line up: only the comment check keeps it out.
        ';3      1600.000 EV 1  a commented-out event',
        '       4      2000.000 EV -  For every bus',
        '       5      2500.000 DT 1      0123 Rx -  1    02',
    ])
    rows, reader = _rows(path)
    assert len(rows) == 2
    for events in (reader.events, read_trc_events(path)):
        assert [(e.bus, e.text) for e in events] == [
            (1, 'Gear change on bus 1'), (None, 'For every bus')]
        assert [e.offset_s for e in events] == pytest.approx([1.50025, 2.0])


# ── What cannot be shown is counted, never silently dropped ──────────────

def test_can_xl_and_its_errors_are_counted(tmp_path):
    path = _write(tmp_path / 'xl.trc', _header('3.0', 'N,O,T,B,I,d,R,V,S,A,r,s,L,D') + [
        '        1      1000.000 DT 1       300 Rx -  -  -  -        - - 1    01',
        '        2      1001.000 XL 1       100 Tx -  00 03 00000456 0 0 3    03 11 22 33',
        '        3      1002.000 XL 1       101 Tx -  00 03 00000456 0 0 1    03 11',
        '        4      1003.000 PE 1         - Rx -  -  -  -        - - 6    01 01 02 00 00 00',
        '        5      1004.000 EN 1         - Rx -  -  -  -        - - 5    01 01 02 00 00',
        '        6      1005.000 DT 1       301 Rx -  -  -  -        - - 1    02',
    ])
    rows, reader = _rows(path)
    assert [row.arb_id for row in rows] == [0x300, 0x301]
    assert reader.load_warnings == [
        '2 CAN XL frame(s) are not shown: CAN XL is not supported yet.',
        '2 CAN XL protocol exception(s) and error notification(s) are not shown.',
    ]


def test_j1939_messages_too_long_for_a_row_are_counted(tmp_path):
    long_data = ' '.join('AB' for _ in range(100))
    path = _write(tmp_path / 'j1939.trc', _header('1.3') + [
        f'     1)      1000.000 3  Rx    18EBFF00 21 100  {long_data}',
        '     2)      1001.000 3  Rx    18FEF100 -  8    01 02 03 04 05 06 07 08',
    ])
    rows, reader = _rows(path)
    assert [row.arb_id for row in rows] == [0x18FEF100]
    assert reader.load_warnings == ['1 J1939 message(s) longer than 64 bytes are not shown.']


def test_unreadable_lines_are_counted_and_the_rest_read(tmp_path):
    path = _write(tmp_path / 'bad.trc', _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        '       1      1000.000 DT 1      0123 Rx -  8    11 22 33 44 55 66 77 88',
        'this is not a trace line',
        '       2      1001.000 DT 1      0124 Rx -  8    11 22 33',      # too short
        '       3      1002.000 DT 1      0125 Rx -  1    01',
        '       4      1003.0',                                            # cut off
    ])
    rows, reader = _rows(path)
    assert [row.arb_id for row in rows] == [0x123, 0x125]
    assert reader.load_warnings == ['3 line(s) could not be read and were skipped.']


def test_line_endings_blank_lines_and_comments_between_frames(tmp_path):
    lines = FRAMES['2.1'][:-1] + ['', '; a comment between frames', '   '] + FRAMES['2.1'][-1:]
    crlf, crlf_reader = _rows(_write(tmp_path / 'crlf.trc', lines))
    lf, lf_reader = _rows(_write(tmp_path / 'lf.trc', lines, newline='\n'))
    assert len(crlf) == 3 and lf == crlf
    assert crlf_reader.load_warnings == lf_reader.load_warnings == []


def test_an_empty_file_has_no_frames(tmp_path):
    rows, reader = _rows(_write(tmp_path / 'empty.trc', _header('2.1', 'N,O,T,B,I,d,R,L,D')))
    assert rows == [] and reader.bus_status == [] and reader.load_warnings == []


# ── The header ───────────────────────────────────────────────────────────

def test_columns_are_found_by_letter_when_optional_ones_are_missing(tmp_path):
    no_bus = _write(tmp_path / 'nob.trc', _header('2.1', 'N,O,T,I,d,L,D') + [
        '       1      1000.000 DT     0123 Rx 2  01 02',
    ])
    no_number = _write(tmp_path / 'non.trc', _header('2.0', 'O,T,I,d,l,D') + [
        '      1000.000 DT     0123 Tx 2  01 02',
    ])
    assert [(r.arb_id, r.channel, r.direction, r.data) for r in _rows(no_bus)[0]] == [
        (0x123, 1, 0, b'\x01\x02')]
    assert [(r.arb_id, r.channel, r.direction, r.data) for r in _rows(no_number)[0]] == [
        (0x123, 1, 1, b'\x01\x02')]


def test_a_newer_version_is_refused_by_number(tmp_path):
    # Columns it could be read by, so only its version can refuse it.
    path = _write(tmp_path / 'v4.trc', [';$FILEVERSION=4.0', ';$COLUMNS=N,O,T,B,I,d,R,L,D'])
    with pytest.raises(TRCReadError, match='version 4.0 is not supported'):
        TRCCANReader(path)


def test_a_2_x_file_without_columns_is_refused(tmp_path):
    path = _write(tmp_path / 'nocols.trc', _header('2.1'))
    with pytest.raises(TRCReadError, match='COLUMNS'):
        read_trc_header(path)


def test_a_missing_file_is_named(tmp_path):
    with pytest.raises(TRCReadError, match='not found'):
        TRCCANReader(tmp_path / 'nowhere.trc')


@pytest.mark.parametrize('newline', ['\r\n', '\r'])
def test_a_trc_file_of_another_program_is_refused(tmp_path, newline):
    # Laid out like another CAN tool's text log, which is saved as .trc as
    # well: an @-header, then tab-separated lines that no PEAK version reads.
    path = _write(tmp_path / 'other.trc', [
        '@ TEXT @ 3 @ 64 @ 0 @ 30 @ 30 @ 00:00:00.030 @', '# $LocalTime=12:00:00.000',
    ] + [f'0,{n:03d}000\t1\t00000004\t123\t2\t01 02\t00000000\t..' for n in range(30)],
        newline)
    with pytest.raises(TRCReadError, match="'other.trc' is not a PEAK trace"):
        TRCCANReader(path)


def test_numbered_notes_saved_as_trc_are_refused(tmp_path):
    # Lines that start like a 1.x line, "n)", and then fail to parse.
    path = _write(tmp_path / 'notes.trc', ['1) Switch the ignition on', '2) Start the engine'])
    with pytest.raises(TRCReadError, match='not a PEAK trace'):
        read_trc_header(path)


def test_a_file_without_line_breaks_is_not_read_whole_to_refuse_it(tmp_path):
    import tracemalloc

    path = tmp_path / 'oneline.trc'
    path.write_bytes(b'@ TEXT @ ' + b'x' * (8 << 20))
    tracemalloc.start()
    try:
        with pytest.raises(TRCReadError, match='not a PEAK trace'):
            read_trc_header(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 4 << 20


def test_a_trace_whose_first_lines_are_unreadable_still_reads(tmp_path):
    path = _write(tmp_path / 'late.trc', FRAMES['2.1'][:-3] + ['not a trace line'] * 19
                  + FRAMES['2.1'][-3:])
    rows, reader = _rows(path)
    assert len(rows) == 3
    assert reader.load_warnings == ['19 line(s) could not be read and were skipped.']


def test_a_comment_between_frames_is_not_read_as_header(tmp_path):
    lines = FRAMES['2.1'][:-1] + [';$FILEVERSION=9.9', ';$COLUMNS=O,T'] + FRAMES['2.1'][-1:]
    rows, reader = _rows(_write(tmp_path / 'mid.trc', lines))
    assert reader.header.version == '2.1' and len(rows) == 3


def test_the_connections_table_and_the_generator(tmp_path):
    path = _write(tmp_path / 'table.trc', [
        ';$FILEVERSION=2.1', f';$STARTTIME={START_DAYS:.10f}', ';$COLUMNS=N,O,T,B,I,d,R,L,D',
        ';   Generated by a test tool v1.2.3',
        ';-------------------------------------------------------------------------------',
        ';   Bus  Name          Connection             Protocol  Bit rate',
        ';   1    Powertrain    PT@pcan_usb            CAN       500 kBit/s',
        ';   2    Body          BODY@pcan_usb          CAN FD    Nominal 500 kBit/s, Data 2 MBit/s',
        ';-------------------------------------------------------------------------------',
    ])
    header = read_trc_header(path)
    assert header.generated_by == 'a test tool v1.2.3'
    assert header.buses == [
        {'Bus': '1', 'Name': 'Powertrain', 'Connection': 'PT@pcan_usb', 'Protocol': 'CAN',
         'Bit rate': '500 kBit/s'},
        {'Bus': '2', 'Name': 'Body', 'Connection': 'BODY@pcan_usb', 'Protocol': 'CAN FD',
         'Bit rate': 'Nominal 500 kBit/s, Data 2 MBit/s'},
    ]


def test_a_1_x_connection_line(tmp_path):
    path = _write(tmp_path / 'conn.trc', _header('1.1') + [';   Connection: TestNet'])
    assert read_trc_header(path).buses == [{'Connection': 'TestNet'}]


def test_what_the_converter_did_not_know_is_left_out(tmp_path):
    """PEAK's converter fills unknown connections with N/A."""
    table = _write(tmp_path / 'na.trc', _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        ';-------------------------------------------------------------------------------',
        ';   Bus   Name            Connection               Protocol',
        ';   N/A   N/A             N/A                      N/A',
        ';   1     N/A             PT@pcan_usb              CAN',
        ';-------------------------------------------------------------------------------',
    ])
    line = _write(tmp_path / 'na10.trc', [';    PCAN-Net: N/A', '     1)  1000  0100  1  01'])
    assert read_trc_header(table).buses == [
        {'Bus': '1', 'Connection': 'PT@pcan_usb', 'Protocol': 'CAN'}]
    assert read_trc_header(line).buses == []


# ── The other ways frames come out ───────────────────────────────────────

def test_messages_for_convert_files(tmp_path):
    reader = TRCCANReader(_write(tmp_path / 'm.trc', FRAMES['2.1']))
    messages = list(reader.iter_messages())
    # 0-based channels and absolute times, as python-can's own readers give.
    assert [m.channel for m in messages] == [0, 1, 0]
    assert [m.is_rx for m in messages] == [True, False, True]
    assert messages[0].timestamp == pytest.approx(START.timestamp() + 1.0, abs=1e-5)
    assert bytes(messages[1].data) == bytes.fromhex('AABBCC') and messages[1].is_extended_id


def test_frames_only_leave_error_frames_out_and_decode(tmp_path, sample_dbc_path):
    from core.dbc_decoder import DBCDecoder

    path = _write(tmp_path / 'decode.trc', _header('2.1', 'N,O,T,B,I,d,R,L,D') + [
        '       1      1000.000 DT 1      0100 Rx -  8    60 09 64 00 00 00 00 00',
        '       2      1000.500 ER 1         - Rx -  5    04 00 02 00 00',
    ])
    reader = TRCCANReader(path, DBCDecoder(str(sample_dbc_path)))
    frames = list(reader.iter_frames_only())
    assert [f.arbitration_id for f in frames] == [0x100]
    samples = {s.signal_name: s.value for s in reader}
    assert samples['EngSpeed'] == 1200.0


# ── python-can as a second opinion ───────────────────────────────────────

@pytest.mark.parametrize('version', ['1.1', '1.3', '2.0', '2.1'])
def test_python_can_reads_the_same_data_frames(tmp_path, version):
    """python-can's TRCReader reads these versions' data frames; where it
    reads, it must agree. Times are compared from the first frame: python-can
    takes $STARTTIME as UTC, this reader as the local clock, like BLF."""
    import can

    path = _write(tmp_path / f'v{version}.trc', FRAMES[version])
    ours, _ = _rows(path)
    with can.TRCReader(str(path)) as reader:
        theirs = list(reader)
    assert len(theirs) == len(ours)
    for row, message in zip(ours, theirs):
        assert row.arb_id == message.arbitration_id
        assert bool(row.flags & FLAG_EXTENDED) == message.is_extended_id
        assert row.data == bytes(message.data)
        assert row.channel == int(message.channel)
        assert row.direction == (0 if message.is_rx else 1)
        assert row.time - ours[0].time == pytest.approx(
            message.timestamp - theirs[0].timestamp, abs=1e-6)
