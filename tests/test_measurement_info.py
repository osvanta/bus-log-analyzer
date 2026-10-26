# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
File ▸ Measurement info, and MF4 events as markers on the plot.

Requires a Qt platform plugin for the dialog and window tests; skipped (not
failed) if none is available.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest


@pytest.fixture()
def mf4_with_extras(tmp_path):
    """An MF4 with an author, a comment, two events and an attachment."""
    from asammdf import MDF, Signal
    from asammdf.blocks.v4_blocks import EventBlock

    path = tmp_path / 'extras.mf4'
    mdf = MDF(version='4.10')
    mdf.append([Signal(np.arange(10.0), np.arange(10.0) + 5.0, name='Speed', unit='km/h')],
               acq_name='Vehicle')
    mdf.header.author = 'Test bench'
    mdf.header.description = 'Brake test, second run'
    for when, name in ((7.5, 'Brake on'), (9.0, 'Stop')):
        event = EventBlock(cause=1, event_type=1, sync_type=1, range_type=0, flags=0,
                           sync_base=1, sync_factor=1.0)
        event.value, event.name, event.comment = when, name, ''
        mdf.events.append(event)
    mdf.attach(b'setup notes', file_name='notes.txt', comment='bench setup', embedded=True)
    mdf.save(str(path), overwrite=True)
    mdf.close()
    return path


# ── Reading the file ─────────────────────────────────────────────────────


def test_mf4_header_events_and_attachments(mf4_with_extras):
    from core.measurement_info import read_file_info

    info = read_file_info(mf4_with_extras)

    assert info.format.startswith('MF4')
    assert info.size_bytes == mf4_with_extras.stat().st_size
    assert ('Author', 'Test bench') in info.header
    assert ('Comment', 'Brake test, second run') in info.header
    assert [(e.time_s, e.name) for e in info.events] == [(7.5, 'Brake on'), (9.0, 'Stop')]
    (attachment,) = info.attachments
    assert (attachment.file_name, attachment.comment, attachment.embedded) == \
        ('notes.txt', 'bench setup', True)
    assert info.start is not None and not info.notes


def test_an_attachment_is_saved(mf4_with_extras, tmp_path):
    from core.measurement_info import save_attachment

    target = save_attachment(mf4_with_extras, 0, tmp_path / 'out.txt')
    assert target.read_bytes() == b'setup notes'


def test_blf_start_and_stop(tmp_path):
    import can
    from core.measurement_info import read_file_info

    path = tmp_path / 'log.blf'
    writer = can.BLFWriter(str(path))
    for i in range(3):
        writer.on_message_received(can.Message(timestamp=1_700_000_000.0 + i, arbitration_id=1,
                                               data=b'\x00', is_extended_id=False))
    writer.stop()
    info = read_file_info(path)
    assert info.format.startswith('BLF')
    assert info.start is not None and info.stop is not None
    assert (info.stop - info.start).total_seconds() == pytest.approx(2.0, abs=0.01)


def test_asc_date_line(tmp_path):
    from core.measurement_info import read_file_info

    path = tmp_path / 'log.asc'
    path.write_text('date Mon Oct 26 09:15:00.000 am 2026\nbase hex  timestamps absolute\n'
                    'Begin Triggerblock Mon Oct 26 09:15:00.000 am 2026\n'
                    '   0.010000 1  100             Rx   d 1 00\nEnd TriggerBlock\n')
    info = read_file_info(path)
    assert ('Date', 'Mon Oct 26 09:15:00.000 am 2026') in info.header
    assert ('Number base, timestamps', 'hex  timestamps absolute') in info.header


def test_csv_and_an_unreadable_header_never_raise(tmp_path):
    from core.measurement_info import read_file_info

    csv = tmp_path / 'data.csv'
    csv.write_text('time,a\n0,1\n')
    assert read_file_info(csv).format == 'CSV'
    broken = tmp_path / 'broken.mf4'
    broken.write_bytes(b'not an mdf')
    info = read_file_info(broken)
    assert info.notes and 'header' in info.notes[0]


# ── The dialog ───────────────────────────────────────────────────────────


def test_the_dialog_shows_every_part(qapp, mf4_with_extras):
    from core.bus_types import BusType
    from core.channel_config import ChannelConfig
    from core.measurement_info import read_file_info
    from gui.measurement_info import MeasurementInfoDialog

    store = SimpleNamespace(
        total_frames=1200, channels={(BusType.CAN, 1), (BusType.CAN, 2)},
        channel_frame_counts={(BusType.CAN, 1): 1000, (BusType.CAN, 2): 200},
        all_keys=lambda: ['CH1::M::S'],
        get_series=lambda key: SimpleNamespace(timestamps=[0.0, 12.5]))
    config = ChannelConfig('Bench', {(BusType.CAN, 1): 'C:/db/powertrain.dbc'})
    dialog = MeasurementInfoDialog(read_file_info(mf4_with_extras), store, config)
    try:
        tabs = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        assert tabs == ['General', 'Channels', 'Header', 'Events (2)', 'Attachments (1)']
        text = dialog.as_text()
        for expected in ('Duration\t12.500 s', 'Frames\t1,200', 'Decoded signals\t1',
                         'Channel configuration\tBench', 'CAN 1\t1,000\tpowertrain.dbc',
                         'CAN 2\t200\t', '7.500000\tBrake on'):
            assert expected in text
    finally:
        dialog.close()


# ── In the window ────────────────────────────────────────────────────────


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


def test_the_file_menu_has_the_new_entries(window):
    items = [a.text() for a in window.file_menu.actions() if not a.isSeparator()]
    assert items == ['Open configuration', 'Save configuration', 'Export plotted signals',
                     'Save decoded measurement as MF4', 'Measurement info']
    assert window.file_menu.info_action.shortcut().toString() == 'Ctrl+I'


def test_measurement_info_opens_for_the_open_file(window, mf4_with_extras):
    window.measurement_path = str(mf4_with_extras)
    window.show_measurement_info()
    assert window._info_dialog.isVisible()
    assert window._info_dialog.tabs.count() == 4      # no store: no Channels tab
    window._info_dialog.close()


def test_events_are_drawn_on_the_plots_time_base(window, mf4_with_extras):
    from core.signal_store import SignalSeries

    window.measurement_path = str(mf4_with_extras)
    window.store = SimpleNamespace(base_ts=2.0, close=lambda: None)
    panel = window.plot_panel
    panel.add_series('Speed', SignalSeries(None, 'Vehicle', 0, 'Speed', 'km/h',
                                           np.arange(10.0), np.arange(10.0)))
    panel.events_action.setChecked(True)

    assert panel.events() == [(5.5, 'Brake on'), (7.0, 'Stop')]
    drawn = sorted(line.value() for _plot, line in panel._event_lines)
    plots = len(panel._stacked_plots) if panel._stacked_mode else 1
    assert drawn == sorted([5.5, 7.0] * plots)

    panel.events_action.setChecked(False)
    assert panel._event_lines == []


def test_a_file_without_events_says_so_and_unticks(window, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    path = tmp_path / 'log.asc'
    path.write_text('date x\n')
    window.measurement_path = str(path)
    window.plot_panel.events_action.setChecked(True)
    assert not window.plot_panel.events_action.isChecked()
    assert QMessageBox.information.call_args[0][1] == 'No measurement events'


def test_clearing_the_plot_drops_the_events(window, mf4_with_extras):
    window.measurement_path = str(mf4_with_extras)
    window.plot_panel.events_action.setChecked(True)
    window.plot_panel.clear_all()
    assert window.plot_panel.events() == [] and not window.plot_panel.events_action.isChecked()
