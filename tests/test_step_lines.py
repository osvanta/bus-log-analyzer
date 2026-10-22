# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The line mode: Step Mode, the default, holds each value until the next
sample; Interpolation Mode joins the samples with straight lines. One button
switches between them and shows the mode the plot is in.

Steps are made from what pyqtgraph is about to draw (clipped to the view and
down-sampled), so a long signal costs no more than with straight lines.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import numpy as np
import pytest

from core.signal_store import SignalSeries
from gui.plot_widget import _step_points


def _plain(values):
    return [None if value != value else value for value in np.asarray(values).tolist()]


# ── The vertices ─────────────────────────────────────────────────────────

def test_each_value_is_held_until_the_next_sample():
    x, y, connect = _step_points(np.array([0.0, 1.0, 3.0]), np.array([5.0, 7.0, 6.0]), None)
    assert x.tolist() == [0.0, 1.0, 1.0, 3.0, 3.0]
    assert y.tolist() == [5.0, 5.0, 7.0, 7.0, 6.0]
    assert connect is None


def test_the_steps_break_where_the_samples_do():
    # Sample 1 is not joined to sample 2: neither its hold nor the jump is drawn.
    x, y, connect = _step_points(np.arange(4.0), np.array([1.0, 2.0, 3.0, 4.0]),
                                 np.array([True, False, True, False]))
    assert connect.tolist() == [True, True, False, False, True, True, False]
    assert x.tolist() == [0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0]


def test_one_sample_or_none_is_left_as_it_is():
    for n in (0, 1):
        x, y = np.arange(float(n)), np.arange(float(n))
        assert _step_points(x, y, None)[0] is x


# ── On the plot ──────────────────────────────────────────────────────────

def _add(panel, qapp, ts, vs, key="S"):
    panel.add_series(key, SignalSeries(None, "Msg", 1, key, "V",
                                       np.asarray(ts, dtype=np.float64),
                                       np.asarray(vs, dtype=np.float64)))
    qapp.processEvents()
    return panel._items[key].curve


def _drawn(curve):
    """What the curve item actually draws."""
    return curve.curve.xData.tolist(), _plain(curve.curve.yData)


def test_a_signal_is_drawn_in_steps_by_default(panel, qapp):
    assert panel.step_lines()
    curve = _add(panel, qapp, [0, 1, 2, 3], [5, 7, 6, 9])
    assert _drawn(curve) == ([0, 1, 1, 2, 2, 3, 3], [5, 5, 7, 7, 6, 6, 9])
    # The signal itself is untouched.
    assert curve.yData.tolist() == [5, 7, 6, 9]


@pytest.mark.parametrize("stacked", [False, True])
def test_interpolation_draws_straight_lines_and_back_steps(panel, qapp, stacked):
    panel.set_stacked(stacked)
    curve = _add(panel, qapp, [0, 1, 2, 3], [5, 7, 6, 9])
    panel.set_step_lines(False)
    qapp.processEvents()
    assert _drawn(curve) == ([0, 1, 2, 3], [5, 7, 6, 9])
    panel.set_step_lines(True)
    qapp.processEvents()
    assert _drawn(curve) == ([0, 1, 1, 2, 2, 3, 3], [5, 5, 7, 7, 6, 6, 9])


def test_a_signal_added_in_interpolation_mode_is_drawn_straight(panel, qapp):
    panel.set_step_lines(False)
    curve = _add(panel, qapp, [0, 1, 2], [1, 3, 2])
    assert _drawn(curve) == ([0, 1, 2], [1, 3, 2])
    # And a rebuild (another plot mode) keeps the choice.
    panel.set_stacked(True)
    qapp.processEvents()
    assert _drawn(panel._items["S"].curve) == ([0, 1, 2], [1, 3, 2])


def test_a_gap_breaks_the_steps(panel, qapp):
    curve = _add(panel, qapp, [0, 1, 2, 3, 4], [1, 2, np.nan, 4, 5])
    connect = curve.curve.opts["connect"]
    # Samples 1 and 2 border the gap: nothing leaves them; 3 joins 4.
    assert np.asarray(connect).tolist() == [True, True, False, False, False, False,
                                            True, True, False]


def test_time_going_back_is_not_joined(panel, qapp):
    curve = _add(panel, qapp, [0, 1, 2, 0.5], [1, 2, 3, 4])
    assert np.asarray(curve.curve.opts["connect"]).tolist() == [
        True, True, True, True, False, False, False]


def test_a_long_signal_steps_only_what_is_drawn(panel, qapp):
    n = 2_000_000
    ts = np.arange(n, dtype=np.float64) * 0.001
    curve = _add(panel, qapp, ts, np.sin(ts))
    panel.grab()
    # The whole recording: down-sampled to a few points a pixel, then stepped.
    assert len(curve.curve.xData) < 50_000
    panel.plot.setXRange(1000.0, 1000.2, padding=0)
    qapp.processEvents()
    panel.grab()
    x = curve.curve.xData
    assert len(x) < 1_000
    assert x[0] <= 1000.0 and x[-1] >= 1000.2       # to both edges of the view
    assert (x[1::2] == x[2::2]).all()                # in steps


def test_samples_too_many_to_step_are_drawn_straight(panel, qapp, monkeypatch):
    import gui.plot_widget as plot_widget

    monkeypatch.setattr(plot_widget, "_STEP_MAX_POINTS", 4)
    curve = _add(panel, qapp, [0, 1, 2, 3, 4], [5, 7, 6, 9, 8])
    assert _drawn(curve) == ([0, 1, 2, 3, 4], [5, 7, 6, 9, 8])
    monkeypatch.setattr(plot_widget, "_STEP_MAX_POINTS", 5)
    panel.set_step_lines(True)
    assert _drawn(curve)[0] == [0, 1, 1, 2, 2, 3, 3, 4, 4]


# ── The button ───────────────────────────────────────────────────────────

@pytest.fixture()
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    yield w
    w.close()
    qapp.processEvents()


def _image(icon):
    from gui.plot_icons import ICON_SIZE
    return icon.pixmap(ICON_SIZE).toImage()


def _shows(button, mode):
    """The button shows *mode*: its drawing, name and tooltip."""
    from gui.plot_icons import plot_icon

    drawing = {'Step Mode': 'step_lines', 'Interpolation Mode': 'interpolated_lines'}[mode]
    return (_image(button.icon()) == _image(plot_icon(drawing))
            and button.accessibleName() == mode
            and button.toolTip().startswith(f'{mode} (S)'))


def test_the_two_drawings_differ(qapp):
    from gui.plot_icons import plot_icon
    assert _image(plot_icon('step_lines')) != _image(plot_icon('interpolated_lines'))


def test_one_button_starts_in_step_mode_and_switches_the_mode(window):
    button = window.btn_line_mode
    assert not button.isCheckable()
    assert window.plot_panel.step_lines() and _shows(button, 'Step Mode')
    button.click()
    assert not window.plot_panel.step_lines() and _shows(button, 'Interpolation Mode')
    button.click()
    assert window.plot_panel.step_lines() and _shows(button, 'Step Mode')


def test_s_switches_the_mode(window):
    from PySide6.QtGui import QKeySequence, QShortcut

    shortcut, = [s for s in window.findChildren(QShortcut) if s.key() == QKeySequence('S')]
    shortcut.activated.emit()
    assert not window.plot_panel.step_lines()
    assert _shows(window.btn_line_mode, 'Interpolation Mode')
    shortcut.activated.emit()
    assert window.plot_panel.step_lines() and _shows(window.btn_line_mode, 'Step Mode')
    assert 'S' in dict(window._SHORTCUTS)


def _config_dialogs(monkeypatch, path):
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(path), ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))


@pytest.mark.parametrize("saved", [True, False])
def test_the_mode_is_saved_in_the_configuration(window, monkeypatch, tmp_path, saved):
    measurement = tmp_path / "m.mf4"
    measurement.write_bytes(b"")
    window.measurement_path = str(measurement)
    config = tmp_path / "config.json"
    _config_dialogs(monkeypatch, config)
    window._set_step_lines(saved)
    window.save_configuration()
    assert json.loads(config.read_text())['step_lines'] is saved

    window._set_step_lines(not saved)
    window.load_configuration()
    assert window.plot_panel.step_lines() is saved
    assert _shows(window.btn_line_mode, 'Step Mode' if saved else 'Interpolation Mode')


def test_a_configuration_from_before_draws_steps(window, monkeypatch, tmp_path):
    measurement = tmp_path / "m.mf4"
    measurement.write_bytes(b"")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({'measurement_path': str(measurement), 'signals': []}))
    _config_dialogs(monkeypatch, config)
    window._set_step_lines(False)
    window.load_configuration()
    assert window.plot_panel.step_lines() and _shows(window.btn_line_mode, 'Step Mode')
