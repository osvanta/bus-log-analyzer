# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Statistics of plotted signals: Min, Max, Mean, RMS and Samples, over the
visible range or between the two cursors, as columns of the signal table that
the table header's menu shows and hides one by one.

Requires a Qt platform plugin for the panel tests; skipped (not failed) if
none is available.
"""
from __future__ import annotations

import numpy as np
import pytest

from core import signal_statistics
from core.signal_statistics import range_statistics


# ── The computation ──────────────────────────────────────────────────────


def test_statistics_of_a_range_match_numpy():
    ts = np.linspace(0.0, 10.0, 1001)
    vs = np.sin(ts) * 5 + 1
    inside = (ts >= 2.0) & (ts <= 7.5)

    stats = range_statistics(ts, vs, 2.0, 7.5)

    expected = vs[inside]
    assert stats.samples == expected.size
    assert stats.minimum == pytest.approx(expected.min())
    assert stats.maximum == pytest.approx(expected.max())
    assert stats.mean == pytest.approx(expected.mean())
    assert stats.rms == pytest.approx(np.sqrt(np.mean(expected ** 2)))


def test_nan_is_skipped_and_an_empty_range_is_none():
    ts = np.array([0.0, 1.0, 2.0, 3.0])
    vs = np.array([1.0, np.nan, 3.0, 5.0])
    stats = range_statistics(ts, vs, 0.0, 2.0)
    assert (stats.samples, stats.mean) == (2, 2.0)
    assert range_statistics(ts, vs, 5.0, 6.0) is None
    assert range_statistics(ts, np.full(4, np.nan), 0.0, 3.0) is None


def test_a_range_without_samples_holds_the_last_value_before_it():
    ts = np.array([0.0, 1.0, 2.0])
    vs = np.array([5.0, -7.0, 9.0])
    held = range_statistics(ts, vs, 1.2, 1.8, hold=True)
    assert (held.minimum, held.maximum, held.mean, held.rms, held.samples) == (-7.0, -7.0, -7.0, 7.0, 0)
    assert range_statistics(ts, vs, 1.2, 1.8) is None            # sample-based only
    assert range_statistics(ts, vs, -2.0, -1.0, hold=True) is None   # nothing before it


def test_holding_ignores_order_and_chunks(monkeypatch):
    monkeypatch.setattr(signal_statistics, 'CHUNK', 2)
    # The latest sample before 2.5 s is in the second chunk, out of order.
    ts = np.array([1.0, 0.0, 5.0, 2.0])
    vs = np.array([-7.0, 5.0, 1.0, 9.0])
    assert range_statistics(ts, vs, 2.5, 3.0, hold=True).mean == 9.0


def test_chunks_and_time_order_do_not_matter(monkeypatch):
    rng = np.random.default_rng(1)
    ts = rng.uniform(0, 100, 5000)            # not in time order
    vs = rng.normal(0, 3, 5000)
    whole = range_statistics(ts, vs, 20.0, 80.0)
    monkeypatch.setattr(signal_statistics, 'CHUNK', 7)
    chunked = range_statistics(ts, vs, 20.0, 80.0)
    assert chunked.samples == whole.samples
    assert chunked.mean == pytest.approx(whole.mean)
    assert chunked.rms == pytest.approx(whole.rms)
    assert (chunked.minimum, chunked.maximum) == (whole.minimum, whole.maximum)


# ── The signal table ─────────────────────────────────────────────────────


def _series(name, values, labels=False):
    from core.signal_store import SignalSeries
    ts = np.arange(len(values), dtype=float)
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit='u',
        timestamps=ts, values=np.asarray(values, dtype=float),
        raw_values=[f'L{v}' for v in values] if labels else [], has_labels=labels,
    )


@pytest.fixture()
def shown(panel, qapp, monkeypatch):
    """Speed 0..99, Gear with labels, Temp hidden; visible range 10..19."""
    panel.add_series('Speed', _series('Speed', range(100)))
    panel.add_series('Gear', _series('Gear', [1, 2, 3] * 33, labels=True))
    panel.add_series('Temp', _series('Temp', range(100)))
    panel._items['Temp'].visible = False
    panel._rebuild_curves(preserve_selection=True)
    monkeypatch.setattr(panel, '_visible_x_range', lambda: [10.0, 19.0])
    _show_statistics(panel)
    qapp.processEvents()
    return panel


def _show_statistics(panel):
    from gui.plot_widget import STAT_COLUMNS
    panel.set_column_choices({name: True for name in STAT_COLUMNS})


def _stats_row(panel, key):
    from gui.plot_widget import STAT_COLUMNS, _STAT_FIRST
    row = panel._row_lookup[key]
    return [panel.table.item(row, _STAT_FIRST + i).text() for i in range(len(STAT_COLUMNS))]


def test_columns_are_hidden_until_asked_for(panel):
    from gui.plot_widget import _STAT_FIRST
    assert not panel.statistics_columns_visible()
    assert panel.table.isColumnHidden(_STAT_FIRST)


def test_statistics_over_the_visible_range(shown):
    assert _stats_row(shown, 'Speed') == ['10.000', '19.000', '14.500',
                                          f'{np.sqrt(np.mean(np.arange(10, 20) ** 2)):.3f}', '10']


def test_a_range_between_two_samples_shows_the_held_value(shown, monkeypatch):
    monkeypatch.setattr(shown, '_visible_x_range', lambda: [10.2, 10.7])
    shown._update_statistics()
    assert _stats_row(shown, 'Speed') == ['10.000', '10.000', '10.000', '10.000', '0']


def test_they_are_in_the_signals_colour(shown):
    from PySide6.QtGui import QColor
    from gui.plot_widget import STAT_COLUMNS, _STAT_FIRST
    row = shown._row_lookup['Speed']
    colour = QColor(shown._items['Speed'].color)
    assert shown.table.item(row, 1).foreground().color() == colour     # its name
    for i in range(len(STAT_COLUMNS)):
        assert shown.table.item(row, _STAT_FIRST + i).foreground().color() == colour
    hidden_row = shown._row_lookup['Temp']
    assert shown.table.item(hidden_row, _STAT_FIRST).foreground().color() == QColor('#606060')


def test_label_and_hidden_signals_stay_blank(shown):
    assert _stats_row(shown, 'Gear') == [''] * 5
    assert _stats_row(shown, 'Temp') == [''] * 5


def test_between_the_cursors_when_both_are_on(shown, qapp):
    from gui.plot_widget import _STAT_FIRST
    shown.set_cursor1_enabled(True)
    shown.set_cursor2_enabled(True)
    shown.move_cursor1(40.0)
    shown.move_cursor2(30.0)
    shown._update_statistics()

    assert _stats_row(shown, 'Speed')[:3] == ['30.000', '40.000', '35.000']
    tip = shown.table.horizontalHeaderItem(_STAT_FIRST).toolTip()
    assert 'between the cursors' in tip


def test_moving_the_view_refreshes_them(shown, qapp, monkeypatch):
    monkeypatch.setattr(shown, '_visible_x_range', lambda: [50.0, 59.0])
    shown._on_xrange_changed()
    assert shown._stats_timer.isActive()
    shown._stats_timer.timeout.emit()
    assert _stats_row(shown, 'Speed')[0] == '50.000'


def test_the_header_menu_shows_each_one_on_its_own(panel):
    from gui.plot_widget import _STAT_FIRST

    def action(name):
        return next(a for a in panel._table_header_menu().actions() if a.text() == name)

    assert not action('Max').isChecked()
    action('Max').trigger()
    assert not panel.table.isColumnHidden(_STAT_FIRST + 1)          # Max
    assert panel.table.isColumnHidden(_STAT_FIRST)                  # Min still off
    assert panel.statistics_columns_visible() and action('Max').isChecked()
    action('Max').trigger()
    assert not panel.statistics_columns_visible()


def test_a_group_row_paints_the_statistics_columns_too(panel, qapp):
    from gui.plot_widget import _STAT_FIRST
    panel.add_series('Speed', _series('Speed', range(10)))
    panel._items['Speed'].group = 'Powertrain'
    panel._rebuild_curves(preserve_selection=True)
    _show_statistics(panel)
    row = next(r for r in range(panel.table.rowCount())
               if str(panel.table.item(r, 0).data(256)).startswith('__group__'))
    assert panel.table.item(row, _STAT_FIRST + 4) is not None

