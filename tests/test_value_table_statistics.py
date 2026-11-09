# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Statistics and Δ of a numeric signal whose value table names special values.

v00.02.03: a DBC value table made a signal count as text, so Δ, Min, Max,
Mean, Std dev and RMS stayed blank for every such signal. Many vehicle
databases give numeric signals a table for one special raw value only, e.g.
a torque signal with 4096 = "SNA", so on a real recording most of the
signal table showed no statistics. Now only the samples shown as text are
left out; a signal shown all as text (an enum) still has none.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.signal_statistics import range_statistics
from core.signal_store import ChoiceLabels, SignalSeries

SNA = 8192.0        # raw 4096 at scale 2: the special value


def _sna_labels(values):
    """A value table naming raw 4096 only, on a signal with scale 2."""
    return ChoiceLabels(values, np.array(['SNA'], dtype=object), np.array([True]),
                        scaling=(2.0, 0.0, 4096))


def _enum_labels(values):
    """OFF, ON and INVALID: every value named."""
    return ChoiceLabels(values, np.array(['OFF', 'ON', 'INVALID'], dtype=object),
                        np.array([True, True, True]), scaling=(1.0, 0.0, 0))


TORQUE = np.array([10.0, SNA, -20.0, 30.0, SNA, 4.0])


# ── range_statistics ─────────────────────────────────────────────────────


def test_a_special_value_is_left_out_of_the_statistics():
    ts = np.arange(len(TORQUE), dtype=float)
    stats = range_statistics(ts, TORQUE, 0.0, 10.0, labels=_sna_labels(TORQUE))
    assert (stats.minimum, stats.maximum, stats.samples) == (-20.0, 30.0, 4)
    assert stats.mean == pytest.approx(6.0)


def test_labels_kept_per_sample_are_read_by_position():
    labels = [10.0, 'SNA', -20.0, 30.0, 'SNA', 4.0]
    stats = range_statistics(np.arange(6.0), TORQUE, 0.0, 10.0, labels=labels)
    assert (stats.minimum, stats.maximum, stats.samples) == (-20.0, 30.0, 4)


def test_a_signal_shown_all_as_text_has_no_statistics():
    values = np.array([0.0, 1.0, 2.0, 1.0])
    assert range_statistics(np.arange(4.0), values, 0.0, 10.0,
                            labels=_enum_labels(values)) is None


def test_a_held_special_value_is_not_the_range_value():
    """With no sample in the range, the value held is the last number."""
    values = np.array([5.0, SNA])
    stats = range_statistics(np.array([0.0, 1.0]), values, 1.5, 2.0, hold=True,
                             labels=_sna_labels(values))
    assert (stats.minimum, stats.maximum, stats.samples) == (5.0, 5.0, 0)


@pytest.mark.parametrize('per_sample', [False, True])
def test_a_long_signal_is_looked_up_a_chunk_at_a_time(monkeypatch, per_sample):
    import core.signal_statistics as signal_statistics

    monkeypatch.setattr(signal_statistics, 'CHUNK', 4)
    values = np.tile(TORQUE, 5)
    labels = ([v if v != SNA else 'SNA' for v in values.tolist()] if per_sample
              else _sna_labels(values))
    stats = range_statistics(np.arange(len(values), dtype=float), values, 0.0, 1e9,
                             labels=labels)
    assert (stats.minimum, stats.maximum, stats.samples) == (-20.0, 30.0, 20)


# ── The signal table ─────────────────────────────────────────────────────


def _series(name, values, labels):
    values = np.asarray(values, dtype=float)
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit='Nm',
        timestamps=np.arange(len(values), dtype=float), values=values,
        raw_values=labels(values), has_labels=True,
    )


@pytest.fixture()
def table(panel, qapp, monkeypatch):
    """Torque with an SNA value, and an enum; cursors at 0 and 3."""
    from gui.plot_widget import STAT_COLUMNS

    panel.add_series('Torque', _series('Torque', TORQUE, _sna_labels))
    panel.add_series('Brake', _series('Brake', [0, 1, 2, 1, 0, 1], _enum_labels))
    panel._rebuild_curves(preserve_selection=True)
    panel.set_column_choices({name: True for name in (*STAT_COLUMNS, 'Δ C2−C1')})
    panel.set_cursor1_enabled(True)
    panel.set_cursor2_enabled(True)
    panel.move_cursor1(0.0)
    panel.move_cursor2(3.0)
    panel._update_statistics()
    qapp.processEvents()
    return panel


def _cell(panel, key, col):
    item = panel.table.item(panel._row_lookup[key], col)
    return item.text() if item is not None else ''


def test_the_table_shows_statistics_of_a_signal_with_a_special_value(table):
    from gui.plot_widget import _STAT_FIRST
    # Between the cursors, 0..3: 10, SNA, -20, 30.
    assert _cell(table, 'Torque', _STAT_FIRST) == '-20.000'
    assert _cell(table, 'Torque', _STAT_FIRST + 1) == '30.000'
    assert _cell(table, 'Torque', _STAT_FIRST + 5) == '3'
    assert _cell(table, 'Brake', _STAT_FIRST) == ''


def test_delta_of_a_signal_with_a_special_value(table, qapp):
    from gui.plot_widget import _DELTA_COL
    assert _cell(table, 'Torque', _DELTA_COL) == '20.000'      # 30 - 10
    assert _cell(table, 'Brake', _DELTA_COL) == ''
    table.move_cursor2(1.0)                                     # on the SNA sample
    qapp.processEvents()
    assert _cell(table, 'Torque', _DELTA_COL) == ''
