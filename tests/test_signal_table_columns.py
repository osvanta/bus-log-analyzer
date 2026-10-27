# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The signal table's columns, chosen one by one from its header's right-click
menu, and the Δ column: each signal's Cursor 2 value minus its Cursor 1 value.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest


def _series(name, values, labels=False):
    from core.signal_store import SignalSeries
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit='u',
        timestamps=np.arange(len(values), dtype=float), values=np.asarray(values, dtype=float),
        raw_values=[f'L{v}' for v in values] if labels else [], has_labels=labels,
    )


@pytest.fixture()
def shown(panel, qapp):
    """Speed = t², Gear with labels, Temp hidden; both cursors on."""
    panel.add_series('Speed', _series('Speed', np.arange(100.0) ** 2))
    panel.add_series('Gear', _series('Gear', [1, 2, 3] * 33, labels=True))
    panel.add_series('Temp', _series('Temp', range(100)))
    panel._items['Temp'].visible = False
    panel._rebuild_curves(preserve_selection=True)
    panel.set_cursor1_enabled(True)
    panel.set_cursor2_enabled(True)
    panel.move_cursor1(3.0)
    panel.move_cursor2(10.0)
    qapp.processEvents()
    return panel


def _cell(panel, key, col):
    item = panel.table.item(panel._row_lookup[key], col)
    return item.text() if item is not None else None


def _action(panel, name):
    return next(a for a in panel._table_header_menu().actions() if a.text() == name)


# ── Δ Cursor 2 − Cursor 1 ────────────────────────────────────────────────


def test_delta_is_cursor_2_minus_cursor_1(shown):
    from gui.plot_widget import _DELTA_COL
    assert not shown.table.isColumnHidden(_DELTA_COL)
    assert _cell(shown, 'Speed', _DELTA_COL) == '91.000'            # 100 − 9
    assert _cell(shown, 'Speed', 2) == '9.000' and _cell(shown, 'Speed', 3) == '100.000'


def test_delta_follows_either_cursor(shown):
    from gui.plot_widget import _DELTA_COL
    shown.move_cursor1(12.0)
    assert _cell(shown, 'Speed', _DELTA_COL) == '-44.000'           # 100 − 144
    shown.move_cursor2(20.0)
    assert _cell(shown, 'Speed', _DELTA_COL) == '256.000'           # 400 − 144


def test_delta_of_a_label_signal_is_blank(shown):
    from gui.plot_widget import _DELTA_COL
    assert _cell(shown, 'Gear', _DELTA_COL) == ''


def test_delta_needs_both_cursors(shown):
    from gui.plot_widget import _DELTA_COL
    shown.set_cursor2_enabled(False)
    assert shown.table.isColumnHidden(_DELTA_COL)
    shown.set_cursor2_enabled(True)
    assert not shown.table.isColumnHidden(_DELTA_COL)
    shown.set_cursor1_enabled(False)
    assert shown.table.isColumnHidden(_DELTA_COL)


def test_delta_sits_after_cursor_2(panel):
    from gui.plot_widget import _DELTA_COL
    header = panel.table.horizontalHeader()
    assert header.visualIndex(_DELTA_COL) == header.visualIndex(3) + 1
    assert panel.table.horizontalHeaderItem(_DELTA_COL).text() == 'Δ C2−C1'


def test_turning_delta_back_on_fills_it(shown):
    from gui.plot_widget import DELTA_COLUMN, _DELTA_COL
    _action(shown, DELTA_COLUMN).trigger()
    assert shown.table.isColumnHidden(_DELTA_COL)
    shown.move_cursor2(20.0)                    # moved while hidden
    _action(shown, DELTA_COLUMN).trigger()
    assert _cell(shown, 'Speed', _DELTA_COL) == '391.000'           # 400 − 9


# ── Choosing columns ─────────────────────────────────────────────────────


def test_the_menu_lists_every_optional_column_in_order(panel):
    from gui.plot_widget import OPTIONAL_COLUMNS
    names = [a.text() for a in panel._table_header_menu().actions() if not a.isSeparator()]
    assert names == list(OPTIONAL_COLUMNS) == [
        'Cursor 1', 'Cursor 2', 'Δ C2−C1', 'Unit', 'Axis',
        'Min', 'Max', 'Mean', 'Std dev', 'RMS', 'Samples']
    checked = {a.text() for a in panel._table_header_menu().actions() if a.isChecked()}
    assert checked == {'Cursor 1', 'Cursor 2', 'Δ C2−C1', 'Unit', 'Axis'}


def test_unit_can_be_hidden_and_shown(panel):
    changes = []
    panel.columnChoicesChanged.connect(changes.append)
    assert not panel.table.isColumnHidden(4)
    _action(panel, 'Unit').trigger()
    assert panel.table.isColumnHidden(4) and not _action(panel, 'Unit').isChecked()
    _action(panel, 'Unit').trigger()
    assert not panel.table.isColumnHidden(4)
    assert [c['Unit'] for c in changes] == [False, True]


def test_a_cursor_column_turned_off_stays_off_with_its_cursor_on(panel):
    _action(panel, 'Cursor 2').trigger()
    panel.set_cursor2_enabled(True)
    assert panel.table.isColumnHidden(3)
    _action(panel, 'Cursor 2').trigger()
    assert not panel.table.isColumnHidden(3)
    panel.set_cursor1_enabled(True)
    _action(panel, 'Cursor 1').trigger()
    assert panel.table.isColumnHidden(2)


def test_axis_columns_follow_multi_axis_and_the_choice(panel):
    assert panel.table.isColumnHidden(5) and panel.table.isColumnHidden(6)
    panel.set_multi_axis(True)
    assert not panel.table.isColumnHidden(5) and not panel.table.isColumnHidden(6)
    _action(panel, 'Axis').trigger()
    assert panel.table.isColumnHidden(5) and panel.table.isColumnHidden(6)


def test_unknown_names_are_ignored(panel):
    before = panel.column_choices()
    panel.set_column_choices({'Signal': False, 'Nonsense': True})
    assert panel.column_choices() == before and not panel.table.isColumnHidden(1)


def test_the_choice_is_remembered(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    first = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    first.plot_panel.set_column_choices({'Unit': False, 'Min': True})
    first.close()
    second = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        choices = second.plot_panel.column_choices()
        assert (choices['Unit'], choices['Min'], choices['Max']) == (False, True, False)
        assert second.plot_panel.table.isColumnHidden(4)
    finally:
        second.close()
        qapp.processEvents()


def test_std_dev_says_how_it_is_computed(panel):
    tip = _action(panel, 'Std dev').toolTip()
    assert 'Standard deviation' in tip and 'divided by their number' in tip
