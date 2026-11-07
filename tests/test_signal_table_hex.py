# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The signal table's Hex column: Cursor 1's value as the raw value on the bus,
(value - offset) / factor from the database, padded to the signal's length;
the IEEE 754 bits of a float signal; the value itself, when whole, for a
signal no database defines.

The every-format test writes one set of frames as ASC, BLF, TRC, CSV and MF4,
loads each through the real LoadWorker and reads the column cell by cell.

GUI tests require a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import math
import struct
from unittest.mock import Mock

import numpy as np
import pytest

from core.signal_properties import RawFormat, hex_text


def _fmt(length, signed=False, scale=1.0, offset=0.0, is_float=False):
    return RawFormat(scale, offset, length, signed, is_float)


# ── hex_text ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("length, raw, text", [
    (1, 1, "0x1"),
    (4, 9, "0x9"),
    (11, 0x7FF, "0x7FF"),
    (11, 5, "0x005"),
    (12, 0, "0x000"),
    (16, 0xBEEF, "0xBEEF"),
    (29, 0x1ABCDEF, "0x01ABCDEF"),
])
def test_padded_to_the_signals_length(length, raw, text):
    assert hex_text(float(raw), _fmt(length)) == text


@pytest.mark.parametrize("length, raw, text", [
    (8, -1, "0xFF"),
    (8, -128, "0x80"),
    (8, 127, "0x7F"),
    (12, -2048, "0x800"),
    (12, -100, "0xF9C"),
    (16, -32768, "0x8000"),
])
def test_a_signed_signal_is_twos_complement_over_its_length(length, raw, text):
    assert hex_text(float(raw), _fmt(length, signed=True)) == text


def test_the_raw_value_undoes_factor_and_offset():
    temp = _fmt(8, signed=True, scale=0.5, offset=-10.0)
    assert hex_text(-74.0, temp) == "0x80"          # raw -128
    assert hex_text(-10.0, temp) == "0x00"          # raw 0
    assert hex_text(0.0, temp) == "0x14"            # raw 20
    amps = _fmt(16, signed=True, scale=-0.1)        # a negative factor
    assert hex_text(3276.8, amps) == "0x8000"       # raw -32768
    assert hex_text(-0.2, amps) == "0x0002"         # raw 2
    assert hex_text(0.1, amps) == "0xFFFF"          # raw -1


def test_a_float_signal_shows_its_ieee_bits():
    assert hex_text(1.0, _fmt(32, is_float=True)) == "0x3F800000"
    assert hex_text(-2.5, _fmt(32, is_float=True)) == "0xC0200000"
    assert hex_text(1.0, _fmt(64, is_float=True)) == "0x3FF0000000000000"
    assert hex_text(1.0, _fmt(16, is_float=True)) == "0x3C00"
    assert hex_text(1e300, _fmt(32, is_float=True)) == ""     # does not fit
    assert hex_text(1.0, _fmt(24, is_float=True)) == ""       # no such float


def test_no_hex_rather_than_a_wrong_one():
    assert hex_text(0.25, _fmt(8, scale=0.5)) == ""           # not a raw step
    assert hex_text(256.0, _fmt(8)) == ""                     # past 8 bits
    assert hex_text(-1.0, _fmt(8)) == ""                      # unsigned
    assert hex_text(128.0, _fmt(8, signed=True)) == ""
    assert hex_text(math.nan, _fmt(8)) == ""
    assert hex_text("Fault", _fmt(8)) == ""
    assert hex_text(1.0, _fmt(8, scale=0.0)) == ""


def test_without_a_definition_a_whole_value_is_shown_as_it_is():
    assert hex_text(200.0, None) == "0xC8"
    assert hex_text(-5.0, None) == "-0x5"
    assert hex_text(0.0, None) == "0x0"
    assert hex_text(2.5, None) == ""
    assert hex_text(math.inf, None) == ""


# ── The column ───────────────────────────────────────────────────────────


def _series(name, values, labels=False, message_id=0x123):
    from core.signal_store import SignalSeries
    return SignalSeries(
        channel=1, message_name='Msg', message_id=message_id, signal_name=name, unit='u',
        timestamps=np.arange(len(values), dtype=float), values=np.asarray(values, dtype=float),
        raw_values=[f'L{v}' for v in values] if labels else [], has_labels=labels,
    )


_FORMATS = {
    'Count': _fmt(11),
    'Temp': _fmt(8, signed=True, scale=0.5, offset=-10.0),
    'Gear': _fmt(4),
}


@pytest.fixture()
def lookups():
    calls = []

    def lookup(key, series):
        calls.append(key)
        return _FORMATS.get(key)
    return lookup, calls


@pytest.fixture()
def hexed(panel, qapp, lookups):
    """Count, Temp (scaled, signed), Gear (labels), Free (no definition);
    Cursor 1 at t=2, Hex on."""
    panel.set_raw_format_lookup(lookups[0])
    panel.add_series('Count', _series('Count', [0, 0x7FF, 5, 0x400]))
    panel.add_series('Temp', _series('Temp', [-74.0, -10.5, 0.0, 53.5]))
    panel.add_series('Gear', _series('Gear', [0, 3, 15, 7], labels=True))
    panel.add_series('Free', _series('Free', [200, 2.5, -5, 0], message_id=0))
    panel.set_cursor1_enabled(True)
    panel.move_cursor1(2.0)
    panel.set_column_shown('Hex', True)
    qapp.processEvents()
    return panel


def _cell(panel, key, col):
    item = panel.table.item(panel._row_lookup[key], col)
    return item.text() if item is not None else None


def _hex(panel, key):
    from gui.plot_widget import _HEX_COL
    return _cell(panel, key, _HEX_COL)


def _action(panel, name):
    return next(a for a in panel._table_header_menu().actions() if a.text() == name)


def test_hex_is_in_the_menu_after_cursor_1_and_off_by_default(panel):
    from gui.plot_widget import _HEX_COL
    names = [a.text() for a in panel._table_header_menu().actions() if not a.isSeparator()]
    assert names.index('Hex') == names.index('Cursor 1') + 1
    assert not _action(panel, 'Hex').isChecked()
    assert panel.column_choices()['Hex'] is False
    panel.set_cursor1_enabled(True)
    assert panel.table.isColumnHidden(_HEX_COL)


def test_hex_sits_after_cursor_1(panel):
    from gui.plot_widget import _HEX_COL, _DELTA_COL
    header = panel.table.horizontalHeader()
    assert header.visualIndex(_HEX_COL) == header.visualIndex(2) + 1
    assert header.visualIndex(3) == header.visualIndex(_HEX_COL) + 1
    assert header.visualIndex(_DELTA_COL) == header.visualIndex(3) + 1
    assert panel.table.horizontalHeaderItem(_HEX_COL).text() == 'Hex'
    assert 'raw value on the bus' in panel.table.horizontalHeaderItem(_HEX_COL).toolTip()


def test_turning_hex_on_fills_it_with_cursor_1s_raw_values(hexed):
    assert _hex(hexed, 'Count') == '0x005'
    assert _hex(hexed, 'Temp') == '0x14'          # 0.0 = 20 × 0.5 − 10
    assert _hex(hexed, 'Gear') == '0xF'            # its raw value, not its label
    assert _cell(hexed, 'Gear', 2) == 'L15'
    assert _hex(hexed, 'Free') == '-0x5'           # no definition: the value


def test_hex_follows_cursor_1(hexed):
    hexed.move_cursor1(1.0)
    assert (_hex(hexed, 'Count'), _hex(hexed, 'Temp'), _hex(hexed, 'Free')) \
        == ('0x7FF', '0xFF', '')                   # -10.5 → raw -1; 2.5 not whole
    hexed.move_cursor1(3.0)
    assert (_hex(hexed, 'Count'), _hex(hexed, 'Temp')) == ('0x400', '0x7F')


def test_hex_is_hidden_while_cursor_1_is_off(hexed):
    from gui.plot_widget import _HEX_COL
    assert not hexed.table.isColumnHidden(_HEX_COL)
    hexed.set_cursor1_enabled(False)
    assert hexed.table.isColumnHidden(_HEX_COL)
    hexed.set_cursor1_enabled(True)
    assert not hexed.table.isColumnHidden(_HEX_COL)
    assert _hex(hexed, 'Count') != ''              # refilled where it now stands


def test_turning_hex_back_on_fills_it(hexed):
    _action(hexed, 'Hex').trigger()
    from gui.plot_widget import _HEX_COL
    assert hexed.table.isColumnHidden(_HEX_COL)
    hexed.move_cursor1(0.0)                        # moved while hidden
    _action(hexed, 'Hex').trigger()
    assert _hex(hexed, 'Count') == '0x000'


def test_a_rebuilt_table_gets_its_hex_back(hexed):
    hexed._refresh_table_and_cursors()
    assert _hex(hexed, 'Count') == '0x005'


def test_each_signal_is_looked_up_once(hexed, lookups):
    _lookup, calls = lookups
    for x in (0.0, 1.0, 3.0, 2.0):
        hexed.move_cursor1(x)
    assert sorted(calls) == ['Count', 'Free', 'Gear', 'Temp']


def test_a_replaced_series_is_looked_up_again(hexed, lookups):
    _lookup, calls = lookups
    calls.clear()
    hexed.replace_series('Count', _series('Count', [1, 2, 3, 4]))
    hexed.move_cursor1(2.0)
    assert calls == ['Count'] and _hex(hexed, 'Count') == '0x003'


def test_a_new_lookup_replaces_what_the_old_one_said(hexed):
    hexed.set_raw_format_lookup(lambda key, series: _fmt(16) if key == 'Count' else None)
    assert _hex(hexed, 'Count') == '0x0005'
    assert _hex(hexed, 'Temp') == '0x0'            # no definition now: the value itself
    assert _hex(hexed, 'Free') == '-0x5'


def test_without_a_lookup_every_signal_has_no_definition(panel, qapp):
    panel.add_series('Count', _series('Count', [0, 0x7FF, 5, 0x400]))
    panel.set_cursor1_enabled(True)
    panel.move_cursor1(1.0)
    panel.set_column_shown('Hex', True)
    assert _hex(panel, 'Count') == '0x7FF'         # the value itself, unpadded


# ── The main window's lookup ─────────────────────────────────────────────


_DBC = """VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 291 Status: 8 ECU
 SG_ Counter : 0|11@1+ (1,0) [0|2047] "" ECU
 SG_ Flag : 11|1@1+ (1,0) [0|1] "" ECU
 SG_ Mode : 12|4@1+ (1,0) [0|15] "" ECU
 SG_ Temp : 16|8@1- (0.5,-10) [-74|53.5] "degC" ECU
 SG_ Amps : 24|16@1- (-0.1,0) [-3276.7|3276.8] "A" ECU

BO_ 292 Drive: 8 ECU
 SG_ Ratio : 0|32@1- (1,0) [0|0] "" ECU
 SG_ Torque : 39|12@0- (2,0) [-4096|4094] "Nm" ECU

VAL_ 291 Mode 0 "Off" 1 "Idle" 2 "Run" 3 "Fault" ;
SIG_VALTYPE_ 292 Ratio : 1;
"""

# Raw values per frame, both messages at the same times.
_RAW = {
    'Counter': [0, 1, 0x7FF, 0x400, 0x2A, 0x123],
    'Flag': [0, 1, 0, 1, 1, 0],
    'Mode': [0, 3, 15, 7, 1, 2],
    'Temp': [-128, -1, 0, 127, 20, -20],
    'Amps': [-32768, -1, 0, 32767, 2, -2],
    'Ratio': [1.0, -2.5, 0.0, 1024.5, 0.15625, -1.0e10],
    'Torque': [-2048, -1, 0, 2047, 100, -100],
}
_MESSAGE_OF = {'Counter': 'Status', 'Flag': 'Status', 'Mode': 'Status', 'Temp': 'Status',
               'Amps': 'Status', 'Ratio': 'Drive', 'Torque': 'Drive'}


def _expected_hex(name, raw):
    if name == 'Ratio':
        return '0x' + struct.pack('>f', raw).hex().upper()
    length = {'Counter': 11, 'Flag': 1, 'Mode': 4, 'Temp': 8, 'Amps': 16, 'Torque': 12}[name]
    return f'0x{raw & ((1 << length) - 1):0{-(-length // 4)}X}'


@pytest.fixture()
def dbc(tmp_path):
    path = tmp_path / 'hex.dbc'
    path.write_text(_DBC, encoding='utf-8')
    return path


def _frames(dbc_path):
    """[(t, frame_id, data)] encoded by cantools from the raw values."""
    import cantools
    db = cantools.database.load_file(str(dbc_path))
    frames = []
    for i in range(len(_RAW['Counter'])):
        t = 0.010 * i
        for message_name in ('Status', 'Drive'):
            message = db.get_message_by_name(message_name)
            raw = {s.name: _RAW[s.name][i] for s in message.signals}
            frames.append((t, message.frame_id,
                           message.encode(raw, scaling=False, strict=False)))
    return frames


def _write_asc(path, frames):
    lines = ['date Thu Sep 24 10:00:00.000 am 2026', 'base hex  timestamps absolute',
             'internal events logged', 'Begin Triggerblock Thu Sep 24 10:00:00.000 am 2026']
    for t, frame_id, data in frames:
        payload = ' '.join(f'{b:02X}' for b in data)
        lines.append(f'{t + 0.001:11.6f} 1  {frame_id:X}             Rx   d 8 {payload}')
    lines.append('End TriggerBlock')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _messages(frames):
    import can
    return [can.Message(timestamp=1000.0 + t, arbitration_id=frame_id, is_extended_id=False,
                        data=data, channel=0) for t, frame_id, data in frames]


def _write_blf(path, frames):
    import can
    with can.BLFWriter(str(path)) as writer:
        for message in _messages(frames):
            writer.on_message_received(message)


def _write_mf4(path, frames):
    import can
    pytest.importorskip('asammdf')
    with can.Logger(str(path)) as logger:
        for message in _messages(frames):
            logger.on_message_received(message)


def _write_trc(path, frames):
    lines = [';$FILEVERSION=2.1', ';$COLUMNS=N,O,T,B,I,d,R,L,D']
    for i, (t, frame_id, data) in enumerate(frames, start=1):
        payload = ' '.join(f'{b:02X}' for b in data)
        lines.append(f'{i:8d} {1000.0 + t * 1000:13.3f} DT 1      {frame_id:04X} Rx -  8    '
                     f'{payload}')
    path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('latin-1'))


def _write_csv(path, frames):
    lines = ['TimestampEpoch;BusChannel;ID;IDE;DLC;DataLength;Dir;EDL;BRS;ESI;RTR;DataBytes']
    lines += [f'{1660503551.0 + t:.6f};1;{frame_id:X};0;8;8;0;0;0;0;0;{data.hex().upper()}'
              for t, frame_id, data in frames]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


_WRITERS = {'asc': _write_asc, 'blf': _write_blf, 'trc': _write_trc,
            'csv': _write_csv, 'mf4': _write_mf4}


def _load(path, config):
    from gui.load_worker import LoadWorker
    worker = LoadWorker(str(path), config)
    stores, failures = [], []
    worker.finished.connect(stores.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures
    return stores[0]


@pytest.mark.parametrize('suffix', sorted(_WRITERS))
def test_every_format_shows_the_raw_value_on_the_bus(panel, qapp, tmp_path, dbc, suffix):
    from core.channel_config import ChannelConfig
    from core.signal_properties import raw_format

    path = tmp_path / f'hex.{suffix}'
    _WRITERS[suffix](path, _frames(dbc))
    config = ChannelConfig.from_single_dbc(str(dbc))
    store = _load(path, config)
    try:
        series = {key.rsplit('::', 1)[1]: s for key, s in store._series_by_key.items()}
        assert sorted(series) == sorted(_RAW)
        panel.set_raw_format_lookup(lambda key, s: raw_format(s, config))
        for name, s in series.items():
            panel.add_series(name, s)
        panel.set_cursor1_enabled(True)
        panel.set_column_shown('Hex', True)
        times = series['Counter'].numpy_timestamps()
        assert len(times) == len(_RAW['Counter'])
        shown = {name: [] for name in series}
        for t in times:
            panel.move_cursor1(float(t))
            for name in series:
                shown[name].append(_hex(panel, name))
        expected = {name: [_expected_hex(name, raw) for raw in raws]
                    for name, raws in _RAW.items()}
        assert shown == expected
    finally:
        if store.raw_frame_store is not None:
            store.raw_frame_store.close()


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    main = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    yield main
    main.close()
    qapp.processEvents()


def _status(name, values, message_id=291):
    from core.bus_types import BusType
    from core.signal_store import SignalSeries
    return SignalSeries(
        channel=(BusType.CAN, 1), message_name='Status', message_id=message_id,
        signal_name=name, unit='', timestamps=np.arange(len(values), dtype=float),
        values=np.asarray(values, dtype=float))


def test_the_main_window_reads_the_definition_from_its_database(window, dbc):
    from core.channel_config import ChannelConfig
    window.channel_config = ChannelConfig.from_single_dbc(str(dbc))
    panel = window.plot_panel
    panel.add_series('CAN1::Status::Temp', _status('Temp', [-74.0, 0.0]))
    panel.add_series('CAN1::Status::Counter', _status('Counter', [3, 5]))
    panel.set_cursor1_enabled(True)
    panel.move_cursor1(1.0)
    panel.set_column_shown('Hex', True)
    assert _hex(panel, 'CAN1::Status::Temp') == '0x14'
    assert _hex(panel, 'CAN1::Status::Counter') == '0x005'


def test_a_file_decoded_or_calculated_signal_has_no_definition(window, dbc):
    from core.channel_config import ChannelConfig
    window.channel_config = ChannelConfig.from_single_dbc(str(dbc))
    file_decoded = _status('Temp', [0.0], message_id=0)
    assert window._raw_format_of('CAN1::Status::Temp', file_decoded) is None
    window.calculated_signals.definition = Mock(return_value=object())
    assert window._raw_format_of('CAN1::Status::Temp', _status('Temp', [0.0])) is None


def test_the_hex_choice_is_remembered(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    first = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    first.plot_panel.set_column_shown('Hex', True)
    first.close()
    second = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        assert second.plot_panel.column_choices()['Hex'] is True
    finally:
        second.close()
        qapp.processEvents()
