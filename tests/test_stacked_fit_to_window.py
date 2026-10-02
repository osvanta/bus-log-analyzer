# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Fit to Window in Stacked and MultiStack shows the whole recording, start
included, when it runs straight after rows are added and before Qt has laid
them out.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest

LAYOUTS = pytest.mark.parametrize('layout', ['stacked', 'multistack'])
DURATIONS = pytest.mark.parametrize('duration', [1.0, 20.0], ids=['1s', '20s'])


@pytest.fixture()
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical", Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))

    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def _set_layout(qapp, window, layout):
    window.btn_stacked.setChecked(True)
    window.btn_multistack.setChecked(layout == 'multistack')
    qapp.processEvents()


def _series(name, duration, samples=1001):
    from core.signal_store import SignalSeries

    ts = array.array('d', (i * duration / (samples - 1) for i in range(samples)))
    vs = array.array('d', (float(i) for i in range(samples)))
    return SignalSeries(None, 'Msg', 1, name, 'km/h', ts, vs)


def _assert_whole_recording_shown(panel, start, end):
    """Every row shows [start, end] with Fit to Window's 2 % margin."""
    margin = (end - start) * 0.02
    ranges = [p.vb.viewRange()[0] for p in panel._stacked_plots]
    assert len(ranges) > 1
    for x0, x1 in ranges:
        assert x0 == pytest.approx(start - margin, abs=margin * 1e-3), ranges
        assert x1 == pytest.approx(end + margin, abs=margin * 1e-3), ranges


@LAYOUTS
@DURATIONS
@pytest.mark.parametrize('count', [2, 5])
def test_plotting_several_signals_at_once_shows_the_whole_recording(
        qapp, window, layout, duration, count):
    # The signal tree plots a multi-selection as one batch, then fits.
    _set_layout(qapp, window, layout)
    panel = window.plot_panel

    panel.begin_batch_add()
    for k in range(count):
        panel.add_series(f'Sig{k}', _series(f'Sig{k}', duration))
    panel.end_batch_add()
    qapp.processEvents()

    _assert_whole_recording_shown(panel, 0.0, duration)


@LAYOUTS
@DURATIONS
def test_fit_straight_after_each_added_signal_shows_the_whole_recording(
        qapp, window, layout, duration):
    _set_layout(qapp, window, layout)
    panel = window.plot_panel

    for k in range(3):
        panel.add_series(f'Sig{k}', _series(f'Sig{k}', duration))
    panel.fit_to_window()
    qapp.processEvents()

    _assert_whole_recording_shown(panel, 0.0, duration)


@LAYOUTS
def test_fit_to_window_after_adding_a_row_shows_the_whole_recording(qapp, window, layout):
    _set_layout(qapp, window, layout)
    panel = window.plot_panel
    for k in range(2):
        panel.add_series(f'Sig{k}', _series(f'Sig{k}', 20.0))
    qapp.processEvents()
    panel.zoom_to_time(5.0, 6.0, margin=0.0)
    qapp.processEvents()

    panel.add_series('Late', _series('Late', 20.0))
    window.btn_fit.click()
    qapp.processEvents()

    _assert_whole_recording_shown(panel, 0.0, 20.0)


@LAYOUTS
@pytest.mark.parametrize('count', [3, 5])
def test_undo_shows_the_whole_recording(qapp, window, layout, count):
    # Ctrl+Z rebuilds every row and fits straight away.
    _set_layout(qapp, window, layout)
    panel = window.plot_panel
    for k in range(count):
        panel.add_series(f'Sig{k}', _series(f'Sig{k}', 20.0))
    qapp.processEvents()
    panel.remove_series('Sig0')
    qapp.processEvents()

    panel.undo()
    qapp.processEvents()

    assert len(panel._stacked_plots) == count
    _assert_whole_recording_shown(panel, 0.0, 20.0)


@LAYOUTS
def test_fit_to_window_still_fits_each_row_vertically(qapp, window, layout):
    _set_layout(qapp, window, layout)
    panel = window.plot_panel
    panel.begin_batch_add()
    panel.add_series('Small', _series('Small', 1.0, samples=11))      # values 0..10
    panel.add_series('Large', _series('Large', 1.0, samples=1001))    # values 0..1000
    panel.end_batch_add()
    qapp.processEvents()

    y_ranges = [p.vb.viewRange()[1] for p in panel._stacked_plots]
    assert y_ranges[0] == pytest.approx([-0.5, 10.5])
    assert y_ranges[1] == pytest.approx([-50.0, 1050.0])
