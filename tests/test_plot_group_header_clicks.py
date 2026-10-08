# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Clicks on a signal group's header row in the signal table.

The first cell of a group's row holds its arrow, at the left, which closes and
opens the group, and its box, at the right, which shows and hides the group's
signals. Qt looked for a click on the box at the cell's left, under the arrow,
so a click on the arrow ticked the box and a click on the box closed the group;
only the cell's outer edge closed it. A signal's box, drawn in its cell's
centre, missed clicks on its right half the same way.

A quick second click on the arrow or a box was a double-click, which opened the
Rename dialog instead of closing the group again or ticking the box again.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import numpy as np
import pytest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QInputDialog

GROUP = 'Powertrain'


def _make_series(name: str):
    from core.signal_store import SignalSeries
    ts = np.linspace(0.0, 10.0, 200)
    return SignalSeries(
        channel=1, message_name='Msg', message_id=0x100,
        signal_name=name, unit='u',
        timestamps=ts, values=np.sin(ts), raw_values=[], has_labels=False,
    )


@pytest.fixture()
def grouped(panel, qapp):
    """Speed and Temp in a group, Pressure outside it."""
    for key in ('Speed', 'Temp', 'Pressure'):
        panel.add_series(key, _make_series(key))
    panel._items['Speed'].group = GROUP
    panel._items['Temp'].group = GROUP
    panel._rebuild_curves(preserve_selection=True)
    qapp.processEvents()
    return panel


@pytest.fixture()
def no_rename(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError('the Rename dialog opened')
    monkeypatch.setattr(QInputDialog, 'getText', fail)


def _cell(panel, row: int, col: int = 0):
    return panel.table.visualRect(panel.table.model().index(row, col))


def _header_row(panel) -> int:
    for row in range(panel.table.rowCount()):
        if panel.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == f'__group__{GROUP}':
            return row
    raise AssertionError('no group header row')


def _arrow(panel) -> QPoint:
    cell = _cell(panel, _header_row(panel))
    return QPoint(cell.left() + 6, cell.center().y())


def _group_box(panel) -> QPoint:
    cell = _cell(panel, _header_row(panel))
    return QPoint(cell.right() - 9, cell.center().y())


def _click(panel, qapp, point: QPoint) -> None:
    QTest.mouseClick(panel.table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, point)
    qapp.processEvents()


def _two_quick_clicks(panel, qapp, point: QPoint) -> None:
    """What the mouse sends: press, release, double-click, release."""
    viewport = panel.table.viewport()
    args = (viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.mouseClick(*args)
    QTest.mouseDClick(*args)  # the double-click event alone
    QTest.mouseRelease(*args)
    qapp.processEvents()


def _group_visible(panel) -> list[bool]:
    return [panel._items[key].visible for key in ('Speed', 'Temp')]


def _closed(panel) -> bool:
    return panel._collapsed_groups.get(GROUP, False)


# ── One click ────────────────────────────────────────────────────────────


def test_click_on_the_group_box_hides_and_shows_its_signals(grouped, qapp):
    _click(grouped, qapp, _group_box(grouped))
    assert _group_visible(grouped) == [False, False]
    assert grouped._items['Pressure'].visible
    assert not _closed(grouped)

    _click(grouped, qapp, _group_box(grouped))
    assert _group_visible(grouped) == [True, True]
    assert not _closed(grouped)


def test_click_on_the_group_arrow_closes_and_opens_the_group(grouped, qapp):
    _click(grouped, qapp, _arrow(grouped))
    assert _closed(grouped)
    assert grouped.table.isRowHidden(grouped._row_lookup['Speed'])
    assert _group_visible(grouped) == [True, True]

    _click(grouped, qapp, _arrow(grouped))
    assert not _closed(grouped)
    assert not grouped.table.isRowHidden(grouped._row_lookup['Speed'])
    assert _group_visible(grouped) == [True, True]


@pytest.mark.parametrize('dx', [-6, 0, 6], ids=['left', 'centre', 'right'])
def test_a_signal_box_takes_a_click_anywhere_on_it(grouped, qapp, dx):
    cell = _cell(grouped, grouped._row_lookup['Pressure'])
    point = QPoint(cell.center().x() + dx, cell.center().y())

    _click(grouped, qapp, point)

    assert not grouped._items['Pressure'].visible


def test_a_click_on_a_selected_signals_box_toggles_every_selected_one(grouped, qapp):
    keys = ['Speed', 'Temp', 'Pressure']
    grouped._restore_selection(keys)
    qapp.processEvents()
    cell = _cell(grouped, grouped._row_lookup['Pressure'])

    _click(grouped, qapp, QPoint(cell.center().x() + 6, cell.center().y()))

    assert [grouped._items[key].visible for key in keys] == [False, False, False]
    assert set(grouped.selected_keys()) == set(keys)


def test_a_box_click_after_a_space_key_toggle_still_counts(grouped, qapp):
    """A box toggled from the keyboard leaves nothing behind that swallows
    the next click on the arrow."""
    row = _header_row(grouped)
    grouped.table.setCurrentCell(row, 0)
    grouped.table.setFocus()
    QTest.keyClick(grouped.table, Qt.Key.Key_Space)
    qapp.processEvents()
    assert _group_visible(grouped) == [False, False]

    _click(grouped, qapp, _arrow(grouped))

    assert _closed(grouped)


# ── Two quick clicks ─────────────────────────────────────────────────────


def test_two_quick_clicks_on_the_arrow_close_and_reopen_the_group(
        grouped, qapp, no_rename):
    _two_quick_clicks(grouped, qapp, _arrow(grouped))

    assert not _closed(grouped)
    assert _group_visible(grouped) == [True, True]


def test_two_quick_clicks_on_the_group_box_hide_and_show_its_signals(
        grouped, qapp, no_rename):
    _two_quick_clicks(grouped, qapp, _group_box(grouped))

    assert _group_visible(grouped) == [True, True]
    assert not _closed(grouped)


def test_two_quick_clicks_on_a_signal_box_untick_and_tick_it(grouped, qapp):
    cell = _cell(grouped, grouped._row_lookup['Pressure'])

    _two_quick_clicks(grouped, qapp, cell.center())

    assert grouped._items['Pressure'].visible


def test_double_click_on_the_group_name_renames_it_in_a_dialog_only(
        grouped, qapp, monkeypatch):
    monkeypatch.setattr(QInputDialog, 'getText',
                        lambda *_a, **_k: ('Drivetrain', True))
    name = _cell(grouped, _header_row(grouped), col=1).center()

    _two_quick_clicks(grouped, qapp, name)

    assert {grouped._items[key].group for key in ('Speed', 'Temp')} == {'Drivetrain'}
    # No editor in the cell after the dialog: it would change nothing.
    assert grouped.table.state() != QAbstractItemView.State.EditingState
