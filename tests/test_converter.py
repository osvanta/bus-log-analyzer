# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tools ▸ Convert files: join, cut, keep signals, resample, and change format.

Every test writes its own measurements, and reads the output back with an
independent reader (asammdf or python-can).
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest

from core.converter import Cancelled, ConvertError, ConvertJob, convert, list_signals


# ── Inputs ───────────────────────────────────────────────────────────────


def _raw(path, start, count, step=0.1):
    """A BLF or ASC log of *count* frames from *start* (epoch s), *step* apart."""
    import can
    writer = can.BLFWriter(str(path)) if path.suffix == '.blf' else can.ASCWriter(str(path))
    for i in range(count):
        writer.on_message_received(can.Message(
            timestamp=start + i * step, arbitration_id=0x100 + (i % 3), is_extended_id=False,
            data=bytes([i % 256, 0, 0, 0, 0, 0, 0, 0]), channel=0))
    writer.stop()
    return path


def _frames(path):
    import can
    reader = {'.blf': can.BLFReader, '.asc': can.ASCReader, '.mf4': can.MF4Reader}[path.suffix](str(path))
    try:
        return [(round(m.timestamp, 4), m.arbitration_id, bytes(m.data)) for m in reader]
    finally:
        if hasattr(reader, 'stop'):
            reader.stop()


def _mdf(path, start=0.0, n=101):
    """Two groups: Speed (0..100, every 0.1 s) and Temp (every 0.5 s)."""
    from asammdf import MDF, Signal
    mdf = MDF(version='4.10')
    t = start + np.arange(n) * 0.1
    mdf.append([Signal(np.arange(n, dtype=float), t, name='Speed', unit='km/h')],
               acq_name='Vehicle')
    t2 = start + np.arange(0, n * 0.1, 0.5)
    mdf.append([Signal(np.full(t2.size, 20.0), t2, name='Temp', unit='degC')], acq_name='Engine')
    mdf.save(str(path), overwrite=True)
    mdf.close()
    return path


def _channels(path):
    from asammdf import MDF
    with MDF(str(path)) as mdf:
        return {c.name: mdf.get(c.name, group=g, index=i)
                for g, group in enumerate(mdf.groups)
                for i, c in enumerate(group.channels) if c.name not in ('time', 't')}


# ── Raw frames: BLF and ASC ──────────────────────────────────────────────


@pytest.mark.parametrize('source, target', [
    ('.blf', '.asc'), ('.asc', '.blf'), ('.blf', '.mf4'), ('.asc', '.mf4'), ('.blf', '.blf'),
])
def test_frames_are_copied_into_another_format(tmp_path, source, target):
    src = _raw(tmp_path / f'in{source}', 1_700_000_000.0, 30)
    out = convert(ConvertJob([src], tmp_path / f'out{target}'))
    assert [f[1:] for f in _frames(out)] == [f[1:] for f in _frames(src)]


# ── Raw frames: which bus they are on ────────────────────────────────────
#
# python-can numbers channels from 0 in its readers and adds the 1 back in its
# BLF and ASC writers, but its MF4 writer stores the number as it is: the
# application, like every logger, reads BusChannel n as CHn. The tests open
# the files again through the application, so a shifted bus shows up as a
# different "CH<n>::" key.

#: (offset s, bus from 1, raw EngSpeed): the even frames on CH1, the odd on CH2.
_BUS_FRAMES = [(i * 0.1, 1 + i % 2, (2000 if i % 2 == 0 else 4000) + i) for i in range(12)]


def _bus_log(path):
    """The frames above, as a BLF, ASC or TRC log of EngineControl (0x100)."""
    import can
    if path.suffix == '.trc':
        lines = [';$FILEVERSION=2.1', ';$STARTTIME=45000.0', ';$COLUMNS=N,O,T,B,I,d,R,L,D']
        for n, (offset, bus, speed) in enumerate(_BUS_FRAMES, start=1):
            data = speed.to_bytes(2, 'little') + bytes(6)
            lines.append(f'{n:8d} {offset * 1000:12.3f} DT {bus} {0x100:8X} Rx -  8    '
                         + ' '.join(f'{b:02X}' for b in data))
        path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('latin-1'))
        return path
    writer = can.BLFWriter(str(path)) if path.suffix == '.blf' else can.ASCWriter(str(path))
    for offset, bus, speed in _BUS_FRAMES:
        writer.on_message_received(can.Message(
            timestamp=1_700_000_000.0 + offset, arbitration_id=0x100, is_extended_id=False,
            data=speed.to_bytes(2, 'little') + bytes(6), channel=bus - 1))   # python-can: from 0
    writer.stop()
    return path


def _decoded(path, sample_dbc_path):
    """{signal key: values} of *path*, loaded and decoded as the application does."""
    from core.channel_config import ChannelConfig
    from gui.load_worker import LoadWorker

    worker = LoadWorker(str(path), ChannelConfig.from_single_dbc(str(sample_dbc_path)))
    stores, failures = [], []
    worker.finished.connect(stores.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures
    store = stores[0]
    try:
        return {key: list(store.get_series(key).numpy_values()) for key in store.all_keys()}
    finally:
        if getattr(store, 'raw_frame_store', None) is not None:
            store.raw_frame_store.close()
        cache = getattr(store, 'sample_cache', None)
        if cache is not None and hasattr(cache, 'close'):
            cache.close()


def _expected_speeds():
    return {f'CH{bus}::EngineControl::EngSpeed':
            [speed * 0.5 for _, b, speed in _BUS_FRAMES if b == bus] for bus in (1, 2)}


@pytest.mark.parametrize('source', ['.asc', '.blf', '.trc'])
def test_mf4_made_from_frames_opens_on_the_same_buses(tmp_path, sample_dbc_path, source):
    from core.bus_types import BusType
    from core.readers import prescan_measurement

    src = _bus_log(tmp_path / f'in{source}')
    out = convert(ConvertJob([src], tmp_path / 'out.mf4'))

    assert prescan_measurement(str(out)).channels == [(BusType.CAN, 1), (BusType.CAN, 2)]
    speeds = {key: values for key, values in _decoded(out, sample_dbc_path).items()
              if key.endswith('::EngSpeed')}
    assert speeds == _expected_speeds() == {
        key: values for key, values in _decoded(src, sample_dbc_path).items()
        if key.endswith('::EngSpeed')}


@pytest.mark.parametrize('target', ['.blf', '.asc'])
@pytest.mark.parametrize('source', ['.asc', '.blf', '.trc'])
def test_blf_and_asc_made_from_frames_keep_their_buses(tmp_path, sample_dbc_path, source, target):
    src = _bus_log(tmp_path / f'in{source}')
    out = convert(ConvertJob([src], tmp_path / f'out{target}'))
    assert {key: values for key, values in _decoded(out, sample_dbc_path).items()
            if key.endswith('::EngSpeed')} == _expected_speeds()


@pytest.mark.parametrize('target, expected', [('.mf4', [1, 2, None, 'can4']),
                                              ('.blf', [0, 1, None, 'can4']),
                                              ('.asc', [0, 1, None, 'can4'])])
def test_only_an_integer_channel_is_shifted_and_only_for_mf4(tmp_path, monkeypatch, target, expected):
    """None and a name are handed on as they came: there is no number to shift."""
    import can
    from core import converter

    written = []

    class Recorder:
        def on_message_received(self, message):
            written.append(message.channel)

        def stop(self):
            pass

    channels = [0, 1, None, 'can4']
    frames = [can.Message(timestamp=1_700_000_000.0 + i, arbitration_id=0x100, channel=channel,
                          is_extended_id=False, data=bytes(8)) for i, channel in enumerate(channels)]
    monkeypatch.setattr(converter, '_reader', lambda path: iter(frames))
    monkeypatch.setattr(converter, '_writer', lambda path: Recorder())
    src = tmp_path / 'in.asc'
    src.write_bytes(b'')
    convert(ConvertJob([src], tmp_path / f'out{target}'))
    assert written == expected


def test_files_are_joined_in_order(tmp_path):
    a = _raw(tmp_path / 'a.blf', 1_700_000_000.0, 10)
    b = _raw(tmp_path / 'b.blf', 1_700_000_001.0, 10)
    out = convert(ConvertJob([a, b], tmp_path / 'joined.blf'))
    frames = _frames(out)
    assert len(frames) == 20
    assert [f[0] for f in frames] == sorted(f[0] for f in frames)


def test_a_time_range_cuts_frames(tmp_path):
    src = _raw(tmp_path / 'in.blf', 1_700_000_000.0, 30)        # 0.0 … 2.9 s
    out = convert(ConvertJob([src], tmp_path / 'cut.asc', start=1.0, end=1.95))
    # Each frame's first byte is its number: frames 10 … 19 are 1.0 … 1.9 s.
    assert [f[2][0] for f in _frames(out)] == list(range(10, 20))


# ── MDF / MF4 ────────────────────────────────────────────────────────────


def test_mdf_cut_is_from_the_first_sample(tmp_path):
    src = _mdf(tmp_path / 'in.mf4', start=100.0)
    out = convert(ConvertJob([src], tmp_path / 'cut.mf4', start=2.0, end=4.0))
    speed = _channels(out)['Speed']
    assert speed.samples.min() == pytest.approx(20.0) and speed.samples.max() == pytest.approx(40.0)


def test_mdf_keeps_only_the_chosen_signals(tmp_path):
    src = _mdf(tmp_path / 'in.mf4')
    keep = [(name, g, i) for name, g, i, _group in list_signals(src) if name == 'Temp']
    out = convert(ConvertJob([src], tmp_path / 'temp.mf4', keep=keep))
    assert set(_channels(out)) == {'Temp'}


def test_mdf_resamples_to_a_raster(tmp_path):
    src = _mdf(tmp_path / 'in.mf4')
    out = convert(ConvertJob([src], tmp_path / 'raster.mf4', raster=1.0))
    speed = _channels(out)['Speed']
    assert np.allclose(np.diff(speed.timestamps), 1.0)


def test_mdf_files_join(tmp_path):
    a = _mdf(tmp_path / 'a.mf4', start=0.0)
    b = _mdf(tmp_path / 'b.mf4', start=20.0)
    out = convert(ConvertJob([a, b], tmp_path / 'joined.mf4'))
    assert _channels(out)['Speed'].samples.size == 202


def test_mdf_to_csv_has_one_time_column(tmp_path):
    src = _mdf(tmp_path / 'in.mf4')
    out = convert(ConvertJob([src], tmp_path / 'out.csv'))
    header = out.read_text().splitlines()[0]
    assert header.lower().startswith('timestamps') and 'Speed' in header and 'Temp' in header


def _logger_mf4(path):
    """The frames of ``_BUS_FRAMES`` as a logger writes them: BusChannel 1 and 2."""
    import can
    writer = can.MF4Writer(str(path))
    for offset, bus, speed in _BUS_FRAMES:
        writer.on_message_received(can.Message(
            timestamp=1_700_000_000.0 + offset, arbitration_id=0x100, is_extended_id=False,
            data=speed.to_bytes(2, 'little') + bytes(6), channel=bus))      # MF4: from 1
    writer.stop()
    return path


def test_mf4_converted_to_mf4_keeps_its_buses(tmp_path, sample_dbc_path):
    """The shift is for frames python-can read; an MF4 already counts from 1."""
    src = _logger_mf4(tmp_path / 'logger.mf4')
    out = convert(ConvertJob([src], tmp_path / 'out.mf4'))
    for path in (src, out):
        assert {key: values for key, values in _decoded(path, sample_dbc_path).items()
                if key.endswith('::EngSpeed')} == _expected_speeds()


def test_mf4_converted_to_csv_keeps_its_bus_numbers(tmp_path):
    import csv
    src = _logger_mf4(tmp_path / 'logger.mf4')
    out = convert(ConvertJob([src], tmp_path / 'out.csv'))
    with open(out, newline='') as stream:
        rows = list(csv.DictReader(stream))
    (column,) = [name for name in rows[0] if name.endswith('CAN_DataFrame.BusChannel')]
    assert {float(row[column]) for row in rows if row[column]} == {1.0, 2.0}


def test_signals_are_listed_without_time_channels(tmp_path):
    names = [name for name, *_ in list_signals(_mdf(tmp_path / 'in.mf4'))]
    assert names == ['Speed', 'Temp']


# ── What it refuses ──────────────────────────────────────────────────────


@pytest.mark.parametrize('make_job, message', [
    (lambda d: ConvertJob([d / 'a.blf', d / 'b.mf4'], d / 'o.mf4'), 'one format'),
    (lambda d: ConvertJob([d / 'a.blf'], d / 'o.csv'), 'BLF, ASC, MF4'),
    (lambda d: ConvertJob([d / 'a.mf4'], d / 'o.blf'), 'MF4, CSV'),
    (lambda d: ConvertJob([d / 'a.blf'], d / 'a.blf'), 'overwrite'),
    (lambda d: ConvertJob([d / 'a.mf4'], d / 'o.mf4', start=5, end=2), 'after its start'),
    (lambda d: ConvertJob([d / 'a.blf'], d / 'o.blf', raster=0.01), 'MF4/MDF files only'),
    (lambda d: ConvertJob([], d / 'o.mf4'), 'at least one'),
], ids=['mixed', 'raw-to-csv', 'mdf-to-blf', 'overwrite', 'range', 'raw-resample', 'empty'])
def test_jobs_that_cannot_be_done_say_why(tmp_path, make_job, message):
    with pytest.raises(ConvertError, match=message):
        make_job(tmp_path).check()


def test_a_cancelled_conversion_leaves_no_file(tmp_path, monkeypatch):
    from core import converter
    monkeypatch.setattr(converter, '_REPORT_EVERY', 5)
    src = _raw(tmp_path / 'in.blf', 1_700_000_000.0, 30)
    target = tmp_path / 'out.asc'

    def cancel(done, total):
        raise Cancelled

    with pytest.raises(Cancelled):
        convert(ConvertJob([src], target), cancel)
    assert not target.exists()


# ── The dialog and the Tools menu ────────────────────────────────────────


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    yield w
    w.close()
    qapp.processEvents()


def test_tools_convert_opens_with_the_open_measurement(window, tmp_path):
    src = _mdf(tmp_path / 'open.mf4')
    window.measurement_path = str(src)
    (action,) = window.tools_menu.actions()
    assert action.text() == 'Convert files'
    action.trigger()
    dialog = window._converter_dialog
    assert dialog.isVisible() and dialog.inputs() == [src]
    assert dialog.choose_signals.isEnabled()
    dialog.close()


def test_the_dialog_converts_off_the_gui_thread(qapp, tmp_path):
    from gui.converter_dialog import ConverterDialog

    dialog = ConverterDialog()
    try:
        dialog.set_inputs([_raw(tmp_path / 'in.blf', 1_700_000_000.0, 30)])
        assert not dialog.choose_signals.isEnabled()      # frames: nothing to choose
        dialog.use_range.setChecked(True)
        dialog.start.setValue(1.0)
        dialog.end.setValue(1.95)
        dialog.set_output(tmp_path / 'out.asc')
        dialog.start_convert()
        dialog._job.wait()
        qapp.processEvents()
        assert dialog.status.text().startswith('Written:')
        assert len(_frames(tmp_path / 'out.asc')) == 10
    finally:
        dialog.close()


def test_the_dialog_says_what_is_wrong_before_starting(qapp, tmp_path):
    from gui.converter_dialog import ConverterDialog

    dialog = ConverterDialog()
    try:
        src = _raw(tmp_path / 'in.blf', 1_700_000_000.0, 3)
        dialog.set_inputs([src])
        dialog.set_output(src)
        dialog.start_convert()
        assert dialog._job is None and 'overwrite' in dialog.status.text()
    finally:
        dialog.close()


# ── Signals to keep: search ─────────────────────────────────────────────


@pytest.fixture()
def picker(qapp):
    from gui.converter_dialog import SignalPicker
    signals = [('B_0_0', 1, 1, 'BRaster_0'), ('B_0_1', 1, 2, 'BRaster_0'),
               ('B_1_0', 2, 1, 'BRaster_1'), ('EngSpeed', 3, 1, 'Engine')]
    p = SignalPicker(signals, chosen=[('EngSpeed', 3, 1)])
    yield p
    p.close()


def _shown(picker):
    return [item.text() for item in picker._shown()]


def test_search_narrows_the_list_ignoring_case(picker):
    picker.search.setText('b_0')
    assert _shown(picker) == ['BRaster_0 · B_0_0', 'BRaster_0 · B_0_1']
    picker.search.setText('ENGINE')
    assert _shown(picker) == ['Engine · EngSpeed']
    picker.search.clear()
    assert len(_shown(picker)) == 4


def test_search_takes_wildcards(picker):
    picker.search.setText('b_?_0')
    assert _shown(picker) == ['BRaster_0 · B_0_0', 'BRaster_1 · B_1_0']
    picker.search.setText('*raster_1*')
    assert _shown(picker) == ['BRaster_1 · B_1_0']


def test_tick_shown_ticks_only_what_the_search_shows_and_keeps_other_ticks(picker):
    picker.search.setText('B_0')
    picker.tick_shown.click()
    picker.search.clear()
    assert sorted(picker.chosen()) == [('B_0_0', 1, 1), ('B_0_1', 1, 2), ('EngSpeed', 3, 1)]
    assert picker.count.text() == '3 of 4 ticked'
    picker.untick_all.click()
    assert picker.chosen() == [] and picker.count.text() == '0 of 4 ticked'


def test_the_count_says_how_many_are_shown(picker):
    picker.search.setText('raster')
    assert picker.count.text() == '1 of 4 ticked · 3 shown'
