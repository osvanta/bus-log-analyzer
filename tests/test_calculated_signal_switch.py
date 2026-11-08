# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Calculated signals that cannot be recalculated for a new measurement.

The tabs ask for their calculated signals again after every Decode. Opening a
measurement without the inputs of ten formulas gave ten boxes to click, one
after the other; it now gives one that names them all.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest

from core.calculated_signals import CalculatedSignalDefinition
from core.signal_store import SignalSeries


class _Store:
    def __init__(self, *series: SignalSeries) -> None:
        self._series = {item.key: item for item in series}
        self.raw_frame_store = None

    def all_keys(self):
        return sorted(self._series)

    def get_series(self, key):
        return self._series.get(key)


def _series(name: str) -> SignalSeries:
    return SignalSeries(channel=1, message_name='Message', message_id=1, signal_name=name,
                        unit='V', timestamps=array.array('d', [0.0, 1.0, 2.0]),
                        values=array.array('d', [1.0, 2.0, 3.0]))


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    warning = Mock(return_value=QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w.warning = warning
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _formulas(window, count: int) -> list[str]:
    speed = _series('Speed')
    window.store = _Store(speed)
    keys = []
    for n in range(count):
        definition = CalculatedSignalDefinition(f'F{n}', f'`{speed.key}` * {n + 1}')
        window.calculated_signals.commit(definition)
        keys.append(definition.key)
    return keys


def test_formulas_without_their_inputs_are_told_in_one_message(window, qapp):
    keys = _formulas(window, 10)
    window.store = _Store(_series('Other'))     # the next measurement

    for key in keys:                            # as the tabs ask for them
        assert window.add_signal_to_plot(key) is False
    assert window.warning.call_count == 0       # not one box per formula
    qapp.processEvents()

    assert window.warning.call_count == 1
    title, text = window.warning.call_args[0][1:3]
    assert title == 'Calculated signals not calculated'
    assert text.startswith('10 calculated signal(s)')
    assert all(f'F{n}:' in text for n in range(10))
    assert window._calc_queue == [] and window._calc_thread is None


def test_a_long_list_is_cut_short(window, qapp):
    keys = _formulas(window, 15)
    window.store = _Store(_series('Other'))
    for key in keys:
        window.add_signal_to_plot(key)
    qapp.processEvents()

    text = window.warning.call_args[0][2]
    assert 'F11:' in text and 'F12:' not in text
    assert '… and 3 more (see the Log)' in text


def test_a_formula_the_user_makes_is_still_refused_at_once(window):
    _formulas(window, 0)
    window._queue_calculation(CalculatedSignalDefinition('Bad', '`CH1::Message::Missing` + 1'),
                              'create', plot_after=False)

    window.warning.assert_called_once()
    assert window.warning.call_args[0][1] == 'Invalid calculated signal'
