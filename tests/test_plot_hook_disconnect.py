# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The plot connects its Multi-axis resize hook and its Stacked click handler
only in those modes, and disconnects them only when they are connected.

Every rebuild disconnected both, connected or not, and PySide6 answers a
disconnect of a slot that is not connected with a "Failed to disconnect"
RuntimeWarning: two of them on every start of the application.
"""
from __future__ import annotations

import array
import warnings
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt


def _series(name, unit):
    from core.signal_store import SignalSeries

    ts = array.array('d', (i * 0.1 for i in range(100)))
    vs = array.array('d', (float(i) for i in range(100)))
    return SignalSeries(None, 'Msg', 1, name, unit, ts, vs)


def _plot_two_units(panel):
    panel.add_series('Speed', _series('Speed', 'km/h'))
    panel.add_series('Voltage', _series('Voltage', 'V'))


def _disconnect_warnings(record):
    return [str(w.message) for w in record if 'Failed to disconnect' in str(w.message)]


def test_rebuilding_in_every_mode_disconnects_without_warnings(qapp, panel):
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter('always')
        _plot_two_units(panel)
        for mode in ('multi_axis', 'stacked', 'multistack', 'normal'):
            panel.set_multi_axis(mode == 'multi_axis')
            panel.set_stacked(mode in ('stacked', 'multistack'))
            panel.set_multistack(mode == 'multistack')
            qapp.processEvents()
            panel.add_series(f'Extra_{mode}', _series(f'Extra_{mode}', 'A'))
            qapp.processEvents()
        panel.clear_all()
        qapp.processEvents()

    assert _disconnect_warnings(record) == []


@pytest.mark.parametrize('rebuilds', [1, 3])
def test_multi_axis_views_still_follow_the_plot_size(qapp, panel, rebuilds):
    from PySide6.QtTest import QTest

    panel.set_multi_axis(True)
    for k in range(rebuilds):
        panel.add_series(f'Sig{k}', _series(f'Sig{k}', f'unit{k}'))
        qapp.processEvents()
    panel.add_series('Speed', _series('Speed', 'km/h'))
    assert panel._extra_axes

    panel.resize(700, 400)
    QTest.qWait(100)
    main = panel.plot.plotItem.vb.sceneBoundingRect()
    assert main.width() > 10
    for _axis, view in panel._extra_axes:
        assert view.geometry() == main


@pytest.mark.parametrize('rebuilds', [1, 3])
def test_a_click_in_stacked_reaches_its_handler_once(qapp, panel, rebuilds):
    panel.set_stacked(True)
    for k in range(rebuilds):
        panel.add_series(f'Sig{k}', _series(f'Sig{k}', 'V'))
        qapp.processEvents()
    panel._emit_plot_area_click = Mock()

    event = Mock()
    event.button.return_value = Qt.MouseButton.LeftButton
    panel.glw.scene().sigMouseClicked.emit(event)

    assert panel._emit_plot_area_click.call_count == 1


def test_leaving_stacked_disconnects_its_click_handler(qapp, panel):
    panel.set_stacked(True)
    panel.add_series('Sig', _series('Sig', 'V'))
    qapp.processEvents()

    panel.set_stacked(False)
    qapp.processEvents()
    panel._emit_plot_area_click = Mock()
    event = Mock()
    event.button.return_value = Qt.MouseButton.LeftButton
    panel.glw.scene().sigMouseClicked.emit(event)

    panel._emit_plot_area_click.assert_not_called()
