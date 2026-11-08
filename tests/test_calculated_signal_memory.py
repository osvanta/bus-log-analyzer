# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A calculated signal that needs more memory than the PC has free.

A formula on `Time` alone over a 5 GB log estimated 443 million samples; one
Yes to "may require substantial RAM" took the PC's commit to its limit within
seconds. The calculation is now refused when its estimate is above the free
memory, and the question says how much it needs.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest

from core.calculated_signals import (BYTES_PER_POINT, TIME_ONLY_BYTES_PER_POINT,
                                     CalculatedSignalDefinition, available_memory,
                                     estimate_peak_bytes)
from core.signal_store import SignalSeries


class _Store:
    def __init__(self, *series: SignalSeries) -> None:
        self._series = {item.key: item for item in series}
        self.raw_frame_store = None

    def all_keys(self):
        return sorted(self._series)

    def get_series(self, key):
        return self._series.get(key)

    def time_axes(self):
        return list(self._series.values())


SPEED = SignalSeries(channel=1, message_name='Message', message_id=1, signal_name='Speed',
                     unit='V', timestamps=array.array('d', [0.0, 1.0, 2.0]),
                     values=array.array('d', [1.0, 2.0, 3.0]))


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    warning = Mock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w.store = _Store(SPEED)
    w.warning = warning
    yield w
    import time
    deadline = time.monotonic() + 5.0
    while (w._calc_thread is not None or w._calc_queue) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _free(monkeypatch, nbytes):
    monkeypatch.setattr('gui.main_window.available_memory', lambda: nbytes)


def test_the_estimate_is_per_sample_and_smaller_for_time_alone():
    assert estimate_peak_bytes(10) == 10 * BYTES_PER_POINT
    assert estimate_peak_bytes(10, time_only=True) == 10 * TIME_ONLY_BYTES_PER_POINT
    assert TIME_ONLY_BYTES_PER_POINT < BYTES_PER_POINT


def test_the_free_memory_is_known_here():
    free = available_memory()
    assert free is not None and free > 0


def test_a_formula_needing_more_than_is_free_is_refused(window, monkeypatch):
    _free(monkeypatch, 3 * BYTES_PER_POINT - 1)          # three samples need more
    definition = CalculatedSignalDefinition('Twice', f'`{SPEED.key}` * 2')

    window._queue_calculation(definition, 'create', plot_after=False)

    window.warning.assert_called_once()
    title, text = window.warning.call_args[0][1:3]
    assert title == 'Calculated signal too large'
    assert 'GB of memory' in text and 'is free' in text
    assert window._calc_thread is None and window._calc_queue == []
    assert not window.calculated_signals.contains_key(definition.key)


def test_a_formula_that_fits_is_calculated(window, monkeypatch, qapp):
    _free(monkeypatch, 3 * BYTES_PER_POINT)
    window._queue_calculation(CalculatedSignalDefinition('Twice', f'`{SPEED.key}` * 2'),
                              'create', plot_after=False)

    window.warning.assert_not_called()
    assert window._calc_thread is not None or window._calc_queue


def test_time_alone_is_judged_by_its_own_estimate(window, monkeypatch):
    # Between the two estimates: on `Time` it fits, on a signal it would not.
    _free(monkeypatch, 3 * TIME_ONLY_BYTES_PER_POINT)
    window._queue_calculation(CalculatedSignalDefinition('Clock', '`Time` * 1'),
                              'create', plot_after=False)
    window.warning.assert_not_called()
    assert window._calc_thread is not None or window._calc_queue


def test_a_formula_a_tab_asks_for_is_told_in_the_summary(window, monkeypatch, qapp):
    _free(monkeypatch, 1)
    definition = CalculatedSignalDefinition('Twice', f'`{SPEED.key}` * 2')
    window.calculated_signals.commit(definition)

    window.add_signal_to_plot(definition.key)
    assert window.warning.call_count == 0
    qapp.processEvents()

    title, text = window.warning.call_args[0][1:3]
    assert title == 'Calculated signals not calculated'
    assert 'Twice: it would need about' in text


def test_the_large_signal_question_says_how_much_memory(window, monkeypatch):
    monkeypatch.setattr('gui.main_window.LARGE_OUTPUT_WARNING_POINTS', 1)
    _free(monkeypatch, 8_000_000_000)
    window._queue_calculation(CalculatedSignalDefinition('Twice', f'`{SPEED.key}` * 2'),
                              'create', plot_after=False)

    title, text = window.warning.call_args[0][1:3]
    assert title == 'Large calculated signal'
    assert 'needs about 0.0 GB of memory (8.0 GB is free)' in text
