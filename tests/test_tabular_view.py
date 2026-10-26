# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The Table view: the plotted signals as a table, one row per sample time of
any of them, each cell the signal's latest value then. Rows and Cursor 1
follow each other.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest

from PySide6.QtCore import Qt


def _series(name, ts, vs, labels=None, unit='u'):
    from core.signal_store import SignalSeries
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100, signal_name=name, unit=unit,
        timestamps=np.asarray(ts, dtype=float), values=np.asarray(vs, dtype=float),
        raw_values=list(labels) if labels else [], has_labels=bool(labels),
    )


@pytest.fixture()
def view(qapp):
    from gui.tabular_view import TabularView
    v = TabularView()
    v.set_series([
        _series('Speed', [0.0, 1.0, 2.0], [10, 20, 30], unit='km/h'),
        _series('Gear', [0.5, 2.0], [1, 2], labels=['Low', 'High'], unit=''),
    ])
    yield v
    v.close()


def _text(view, row, col):
    return view.model.cell_text(row, col)


def test_one_row_per_sample_time_of_any_signal(view):
    assert view.model.times.tolist() == [0.0, 0.5, 1.0, 2.0]
    assert view.model.columnCount() == 3
    headers = [view.model.headerData(c, Qt.Orientation.Horizontal) for c in range(3)]
    assert headers == ['Time s', 'Speed [km/h]', 'Gear']


def test_each_cell_is_the_latest_value_then(view):
    rows = [[_text(view, r, c) for c in range(3)] for r in range(4)]
    assert rows == [['0.000000', '10', ''],
                    ['0.500000', '10', 'Low'],
                    ['1.000000', '20', 'Low'],
                    ['2.000000', '30', 'High']]


def test_held_values_are_grey(view):
    model = view.model
    held = model.data(model.index(1, 1), Qt.ItemDataRole.ForegroundRole)   # Speed at 0.5
    fresh = model.data(model.index(2, 1), Qt.ItemDataRole.ForegroundRole)  # Speed at 1.0
    assert held is not None and fresh is None


def test_a_row_click_asks_for_cursor_1_there(view):
    asked = []
    view.timeClicked.connect(asked.append)
    view._on_clicked(view.model.index(2, 1))
    assert asked == [1.0]


def test_go_to_time_selects_the_row_at_or_before_it(view):
    asked = []
    view.timeClicked.connect(asked.append)
    view.go_to.setText('1.7')
    view._on_go_to()
    assert view.table.selectionModel().selectedRows()[0].row() == 2
    assert asked == [1.0]


def test_copy_gives_the_selected_rows_with_the_header(view, qapp):
    from PySide6.QtGui import QGuiApplication
    view.table.selectRow(3)
    view.copy_selection()
    assert QGuiApplication.clipboard().text().splitlines() == [
        'Time s\tSpeed [km/h]\tGear', '2.000000\t30\tHigh']


def test_the_union_is_capped_as_export_caps_it(qapp, monkeypatch):
    from gui import tabular_view
    from gui.tabular_view import TabularView
    monkeypatch.setattr(tabular_view, '_MAX_GRID_POINTS', 3)
    v = TabularView()
    v.set_series([_series('A', [0, 1, 2, 3, 4], [0, 1, 2, 3, 4])])
    assert v.model.rowCount() == 3 and v.model.truncated
    assert 'first 3' in v.summary.text()


# ── In the window ────────────────────────────────────────────────────────


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    panel = w.plot_panel
    panel.add_series('Speed', _series('Speed', [0.0, 1.0, 2.0], [10, 20, 30]))
    panel.add_series('Temp', _series('Temp', [0.5, 1.5], [5, 6]))
    panel._rebuild_curves(preserve_selection=True)
    yield w
    w.close()
    qapp.processEvents()


def test_the_table_button_puts_the_table_right_of_the_plot(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    qapp.processEvents()
    assert not panel.tabular.isVisible()
    window.btn_table.setChecked(True)
    qapp.processEvents()
    assert panel.table_view_active()
    assert panel.view_stack.isVisible() and panel.tabular.isVisible()      # both shown
    assert panel.area_stack.indexOf(panel.view_stack) == 0                 # plot left
    assert panel.area_stack.indexOf(panel.tabular) == 1                    # table right
    plot, table = panel.area_stack.sizes()
    assert plot > table > 0
    assert panel.tabular.model.columnCount() == 3
    window.btn_table.setChecked(False)
    qapp.processEvents()
    assert not panel.table_view_active() and not panel.tabular.isVisible()
    assert panel.view_stack.isVisible()


def test_the_table_keeps_the_width_it_was_dragged_to(window, qapp):
    panel = window.plot_panel
    window.resize(1400, 800)
    window.show()
    qapp.processEvents()
    window.btn_table.setChecked(True)
    qapp.processEvents()
    total = sum(panel.area_stack.sizes())
    panel.area_stack.setSizes([total - 300, 300])
    window.btn_table.setChecked(False)
    window.btn_table.setChecked(True)
    qapp.processEvents()
    assert panel.area_stack.sizes()[1] == 300


def test_hidden_signals_are_left_out_and_changes_follow(window, qapp):
    panel = window.plot_panel
    window.btn_table.setChecked(True)
    panel._items['Temp'].visible = False
    panel._rebuild_curves(preserve_selection=True)
    qapp.processEvents()
    assert panel.tabular.model.columnCount() == 2


def test_a_row_click_places_cursor_1(window):
    panel = window.plot_panel
    window.btn_table.setChecked(True)
    panel.tabular._on_clicked(panel.tabular.model.index(3, 0))   # t = 1.5
    assert window.btn_cursor1.isChecked()
    assert panel.v_line.value() == pytest.approx(1.5)


def test_cursor_1_moving_selects_its_row(window):
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.btn_table.setChecked(True)
    panel.move_cursor1(0.6)
    assert panel.tabular.table.selectionModel().selectedRows()[0].row() == 1   # t = 0.5
    panel.move_cursor1(1.7)
    assert panel.tabular.table.selectionModel().selectedRows()[0].row() == 3   # t = 1.5
