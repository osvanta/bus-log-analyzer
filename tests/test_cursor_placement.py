# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A cursor that is switched on appears inside the visible window, however short
or long the recording, and plotting a shorter recording after a longer one
does not leave the cursors out past its end.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest

LAYOUTS = pytest.mark.parametrize('layout', ['stacked', 'single-plot', 'multi-axis'])


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
    window.btn_stacked.setChecked(layout == 'stacked')
    window.btn_multi_axis.setChecked(layout == 'multi-axis')
    qapp.processEvents()


def _plot(qapp, window, duration, names=('Speed', 'Torque')):
    """Plot signals recorded for `duration` seconds; each value is its sample number."""
    from core.signal_store import SignalSeries

    for name in names:
        ts = array.array('d', (i * duration / 1000 for i in range(1001)))
        vs = array.array('d', (float(i) for i in range(1001)))
        window.plot_panel.add_series(
            name, SignalSeries(None, 'Msg', 1, name, 'km/h', ts, vs))
    window.plot_panel.fit_to_window()
    qapp.processEvents()


def _cursors(panel):
    """Where each cursor's lines are drawn in the current layout."""
    if panel._stacked_mode:
        return ([line.value() for line in panel._stacked_c1_lines],
                [line.value() for line in panel._stacked_c2_lines])
    return [panel.v_line.value()], [panel.v_line2.value()]


def _assert_inside_view(panel):
    x0, x1 = panel._visible_x_range()
    c1, c2 = _cursors(panel)
    assert c1 and c2
    assert all(x0 < x < x1 for x in c1 + c2), ((x0, x1), c1, c2)
    assert len(set(c1)) == 1 and len(set(c2)) == 1   # every row agrees
    return c1[0], c2[0]


def _cell(panel, key, col):
    return panel.table.item(panel._row_lookup[key], col).text()


@LAYOUTS
@pytest.mark.parametrize('duration', [0.01, 0.2, 1.0, 20.0, 3600.0],
                         ids=['10ms', '200ms', '1s', '20s', '1h'])
def test_switched_on_cursors_appear_inside_the_window(qapp, window, layout, duration):
    _set_layout(qapp, window, layout)
    _plot(qapp, window, duration)

    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)
    qapp.processEvents()

    c1, c2 = _assert_inside_view(window.plot_panel)
    assert c2 > c1   # side by side, not on top of each other


@LAYOUTS
def test_cursor2_appears_inside_a_narrow_zoomed_window(qapp, window, layout):
    _set_layout(qapp, window, layout)
    _plot(qapp, window, 20.0)
    panel = window.plot_panel
    panel.zoom_to_time(10.0, 10.3, margin=0.0)
    qapp.processEvents()
    window.btn_cursor1.setChecked(True)

    window.btn_cursor2.setChecked(True)
    qapp.processEvents()

    _assert_inside_view(panel)


def test_cursor2_switched_back_on_follows_the_window(qapp, window):
    _plot(qapp, window, 20.0)
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)   # placed in the full view first
    qapp.processEvents()
    panel.zoom_to_time(2.0, 2.5, margin=0.0)
    qapp.processEvents()
    window.btn_cursor1.setChecked(False)
    window.btn_cursor1.setChecked(True)

    window.btn_cursor2.setChecked(False)
    window.btn_cursor2.setChecked(True)
    qapp.processEvents()

    _assert_inside_view(panel)


@LAYOUTS
def test_a_shorter_recording_plotted_after_a_longer_one_brings_the_cursors_in(
        qapp, window, layout):
    _set_layout(qapp, window, layout)
    _plot(qapp, window, 20.0)
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)
    panel.move_cursor1(6.0)
    panel.move_cursor2(14.0)
    qapp.processEvents()

    panel.clear_all()
    _plot(qapp, window, 1.0, names=('Short',))

    c1, c2 = _assert_inside_view(panel)
    assert c1 < c2
    # The table shows the values at the new positions (the value is the
    # sample number, one sample per millisecond).
    assert _cell(panel, 'Short', 2) == f'{round(c1 * 1000):.3f}'
    assert _cell(panel, 'Short', 3) == f'{round(c2 * 1000):.3f}'


def test_fit_to_window_leaves_cursors_inside_the_recording_alone(qapp, window):
    _plot(qapp, window, 20.0)
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)
    panel.move_cursor1(3.0)
    panel.move_cursor2(17.5)
    panel.zoom_to_time(8.0, 9.0, margin=0.0)   # both cursors now out of view
    qapp.processEvents()

    window.btn_fit.click()
    qapp.processEvents()

    assert _cursors(panel) == ([3.0] * 2, [17.5] * 2)


def test_fit_to_window_brings_back_a_cursor_dragged_past_the_end(qapp, window):
    _plot(qapp, window, 20.0)
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)
    panel.move_cursor1(5.0)
    panel.move_cursor2(25.0)
    qapp.processEvents()

    window.btn_fit.click()
    qapp.processEvents()

    c1, _c2 = _assert_inside_view(panel)
    assert c1 == 5.0
