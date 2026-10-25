# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A signal group's row in the signal table: its arrow, dragging the whole group,
and its colour.

- The arrow kept pointing down when a click closed the group; it points right
  while the group is closed.
- A press on the group's row and a drag did nothing: its cells could not be
  dragged. The row now drags the whole group, which keeps its name and lands
  above or below another group, never inside it.
- The row takes the theme's colours, and a colour of the group's own can be
  chosen from its menu. It stays through a switch of theme and is saved with
  the configuration.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QDrag, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog

GROUP = 'Powertrain'
OTHER = 'Chassis'
ORANGE = '#f4c08a'


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
    """Speed and Temp in one group, Pressure alone, Brake and Steer in another."""
    for key in ('Speed', 'Temp', 'Pressure', 'Brake', 'Steer'):
        panel.add_series(key, _make_series(key))
    for key in ('Speed', 'Temp'):
        panel._items[key].group = GROUP
    for key in ('Brake', 'Steer'):
        panel._items[key].group = OTHER
    panel._rebuild_curves(preserve_selection=True)
    qapp.processEvents()
    return panel


def _header_row(panel, group: str = GROUP) -> int:
    for row in range(panel.table.rowCount()):
        if panel.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == f'__group__{group}':
            return row
    raise AssertionError(f'no header row for {group}')


def _cell(panel, row: int, col: int = 0):
    return panel.table.visualRect(panel.table.model().index(row, col))


def _click(panel, qapp, point: QPoint) -> None:
    QTest.mouseClick(panel.table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, point)
    qapp.processEvents()


def _arrow_text(panel, group: str = GROUP) -> str:
    return panel.table.item(_header_row(panel, group), 0).text()


def _order(panel) -> list[str]:
    return list(panel._items)


def _groups(panel) -> dict[str, str]:
    return {key: plotted.group for key, plotted in panel._items.items()}


# ── The arrow ────────────────────────────────────────────────────────────


@pytest.mark.parametrize('col', [0, 1, 3], ids=['arrow', 'name', 'empty-cell'])
def test_the_arrow_points_right_while_the_group_is_closed(grouped, qapp, col):
    assert _arrow_text(grouped) == '▼'
    cell = _cell(grouped, _header_row(grouped), col)
    point = QPoint(cell.left() + 6, cell.center().y())

    _click(grouped, qapp, point)
    assert grouped._collapsed_groups[GROUP]
    assert _arrow_text(grouped) == '▶'

    _click(grouped, qapp, point)
    assert not grouped._collapsed_groups[GROUP]
    assert _arrow_text(grouped) == '▼'


def test_turning_the_arrow_leaves_the_groups_signals_shown(grouped, qapp):
    cell = _cell(grouped, _header_row(grouped))
    _click(grouped, qapp, QPoint(cell.left() + 6, cell.center().y()))

    assert grouped._items['Speed'].visible and grouped._items['Temp'].visible


def _arrow_size(panel, qapp) -> tuple[int, int]:
    """Width and height of what is drawn left of the group's box."""
    panel.table.clearSelection()
    qapp.processEvents()
    row = _header_row(panel)
    cell = _cell(panel, row)
    background = panel.table.item(row, 0).background().color().name()
    image = panel.table.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    xs, ys = [], []
    for x in range(round(cell.left() * ratio), round((cell.left() + 12) * ratio)):
        for y in range(round((cell.top() + 2) * ratio), round((cell.bottom() - 2) * ratio)):
            if image.pixelColor(x, y).name() != background:
                xs.append(x)
                ys.append(y)
    assert xs, 'no arrow drawn'
    return max(xs) - min(xs), max(ys) - min(ys)


def test_the_closed_arrow_is_as_big_as_the_open_one(grouped, qapp):
    """The two glyphs differ in size in Segoe UI; the arrow is drawn instead."""
    grouped.table_panel.resize(500, 300)
    grouped.table_panel.show()
    opened = _arrow_size(grouped, qapp)
    grouped._collapsed_groups[GROUP] = True
    grouped._apply_collapse_state()

    closed = _arrow_size(grouped, qapp)

    assert closed != opened  # it turned
    assert abs(closed[0] - opened[1]) <= 2 and abs(closed[1] - opened[0]) <= 2


def test_a_closed_group_keeps_its_arrow_after_the_table_is_rebuilt(grouped, qapp):
    grouped._collapsed_groups[GROUP] = True
    grouped._rebuild_curves(preserve_selection=True)
    qapp.processEvents()

    assert _arrow_text(grouped) == '▶'
    assert grouped.table.isRowHidden(grouped._row_lookup['Speed'])


# ── Dragging the whole group ─────────────────────────────────────────────


@pytest.fixture()
def drags(monkeypatch):
    """What a drag would carry, without running the drag's own event loop."""
    started: list = []

    def fake_exec(self, *_args, **_kwargs):
        mime = self.mimeData()
        started.append({fmt: bytes(mime.data(fmt)).decode('utf-8')
                        for fmt in mime.formats()})
        return Qt.DropAction.MoveAction

    monkeypatch.setattr(QDrag, 'exec', fake_exec)
    return started


def _press_and_drag(panel, qapp, start: QPoint) -> None:
    viewport = panel.table.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, start)
    end = QPointF(start.x(), start.y() + 4 * QApplication.startDragDistance())
    move = QMouseEvent(QEvent.Type.MouseMove, end, viewport.mapToGlobal(end),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, move)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, end.toPoint())
    qapp.processEvents()


@pytest.mark.parametrize('col', [1, 3], ids=['name', 'empty-cell'])
def test_a_drag_from_the_group_row_carries_the_whole_group(grouped, qapp, drags, col):
    from gui.plot_widget import _ReorderTable

    cell = _cell(grouped, _header_row(grouped), col)
    _press_and_drag(grouped, qapp, QPoint(cell.left() + 8, cell.center().y()))

    assert len(drags) == 1
    assert drags[0][_ReorderTable._ROW_REORDER_MIME].splitlines() == ['Speed', 'Temp']
    assert drags[0][_ReorderTable._GROUP_REORDER_MIME].splitlines() == [GROUP]


def test_a_closed_group_drags_whole(grouped, qapp, drags):
    from gui.plot_widget import _ReorderTable

    grouped._collapsed_groups[GROUP] = True
    grouped._apply_collapse_state()
    cell = _cell(grouped, _header_row(grouped), 1)
    _press_and_drag(grouped, qapp, QPoint(cell.left() + 8, cell.center().y()))

    assert drags[0][_ReorderTable._ROW_REORDER_MIME].splitlines() == ['Speed', 'Temp']


def test_a_drag_from_a_signal_row_still_carries_that_signal_alone(grouped, qapp, drags):
    from gui.plot_widget import _ReorderTable

    cell = _cell(grouped, grouped._row_lookup['Pressure'], 1)
    _press_and_drag(grouped, qapp, QPoint(cell.left() + 8, cell.center().y()))

    assert drags[0][_ReorderTable._ROW_REORDER_MIME].splitlines() == ['Pressure']
    assert _ReorderTable._GROUP_REORDER_MIME not in drags[0]


class _Drop:
    """A drop at *y* in the table's viewport carrying *formats*."""

    def __init__(self, formats: dict[str, str], y: float) -> None:
        from PySide6.QtCore import QByteArray, QMimeData
        self._mime = QMimeData()
        for fmt, text in formats.items():
            self._mime.setData(fmt, QByteArray(text.encode('utf-8')))
        self._y = y
        self.accepted = False

    def mimeData(self):
        return self._mime

    def position(self):
        return QPointF(10, self._y)

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def _group_drop(group: str, keys: list[str], y: float) -> _Drop:
    from gui.plot_widget import _ReorderTable
    return _Drop({_ReorderTable._ROW_REORDER_MIME: '\n'.join(keys),
                  _ReorderTable._GROUP_REORDER_MIME: group}, y)


def _below(panel, row: int) -> float:
    return _cell(panel, row).bottom() - 2


def _above(panel, row: int) -> float:
    return _cell(panel, row).top() + 2


def test_a_group_dropped_below_a_signal_moves_there_whole(grouped, qapp):
    drop = _group_drop(GROUP, ['Speed', 'Temp'], _below(grouped, grouped._row_lookup['Pressure']))

    grouped._table_drop(drop)
    qapp.processEvents()

    assert drop.accepted
    assert _order(grouped) == ['Pressure', 'Speed', 'Temp', 'Brake', 'Steer']
    assert _groups(grouped)['Speed'] == GROUP and _groups(grouped)['Temp'] == GROUP
    assert _groups(grouped)['Pressure'] == ''


def test_a_group_dropped_at_the_end_moves_there_whole(grouped, qapp):
    drop = _group_drop(GROUP, ['Speed', 'Temp'], _below(grouped, grouped._row_lookup['Steer']))

    grouped._table_drop(drop)
    qapp.processEvents()

    assert _order(grouped) == ['Pressure', 'Brake', 'Steer', 'Speed', 'Temp']
    assert _groups(grouped)['Speed'] == GROUP
    assert _groups(grouped)['Brake'] == OTHER


@pytest.mark.parametrize('where, expected', [
    ('upper', ['Pressure', 'Speed', 'Temp', 'Brake', 'Steer']),
    ('lower', ['Pressure', 'Brake', 'Steer', 'Speed', 'Temp']),
], ids=['upper-half-goes-above', 'lower-half-goes-below'])
def test_a_group_dropped_inside_another_lands_beside_it(grouped, qapp, where, expected):
    # Pressure first, so the group moves down past it.
    grouped._items = {k: grouped._items[k]
                      for k in ('Pressure', 'Speed', 'Temp', 'Brake', 'Steer')}
    grouped._rebuild_curves(preserve_selection=True)
    qapp.processEvents()
    # Upper: just below the other group's header, nearer its top.
    # Lower: just above its last signal, nearer its bottom.
    y = (_below(grouped, _header_row(grouped, OTHER)) if where == 'upper'
         else _above(grouped, grouped._row_lookup['Steer']))

    grouped._table_drop(_group_drop(GROUP, ['Speed', 'Temp'], y))
    qapp.processEvents()

    assert _order(grouped) == expected
    assert _groups(grouped)['Brake'] == OTHER and _groups(grouped)['Steer'] == OTHER
    assert _groups(grouped)['Speed'] == GROUP and _groups(grouped)['Temp'] == GROUP


def test_a_group_dropped_on_a_closed_groups_lower_half_lands_below_it(grouped, qapp):
    grouped._items = {k: grouped._items[k]
                      for k in ('Pressure', 'Speed', 'Temp', 'Brake', 'Steer')}
    grouped._collapsed_groups[OTHER] = True
    grouped._rebuild_curves(preserve_selection=True)
    qapp.processEvents()

    grouped._table_drop(_group_drop(
        GROUP, ['Speed', 'Temp'], _below(grouped, _header_row(grouped, OTHER))))
    qapp.processEvents()

    assert _order(grouped) == ['Pressure', 'Brake', 'Steer', 'Speed', 'Temp']


def test_the_drop_line_shows_where_the_group_lands(grouped, qapp):
    """Over the middle of another group the line is drawn at that group's
    edge, where the dragged group will land."""
    from gui.plot_widget import _ReorderTable

    drop = _group_drop(GROUP, ['Speed', 'Temp'], _below(grouped, _header_row(grouped, OTHER)))
    grouped.table_panel.show()
    grouped._table_drag_move(drop)
    qapp.processEvents()

    header_top = _cell(grouped, _header_row(grouped, OTHER)).top()
    assert drop.accepted
    assert abs(grouped._drop_indicator.geometry().top() - header_top) <= 2
    assert _ReorderTable._GROUP_REORDER_MIME  # same constant the drag uses


def test_a_group_drag_can_be_undone(grouped, qapp):
    before = _order(grouped)
    grouped._table_drop(_group_drop(
        GROUP, ['Speed', 'Temp'], _below(grouped, grouped._row_lookup['Steer'])))
    qapp.processEvents()

    grouped.undo()
    qapp.processEvents()

    assert _order(grouped) == before


def test_a_signal_dragged_into_a_group_still_joins_it(grouped, qapp):
    """Dragging one signal is unchanged: it takes the group it lands in."""
    from gui.plot_widget import _ReorderTable

    drop = _Drop({_ReorderTable._ROW_REORDER_MIME: 'Pressure'},
                 _below(grouped, grouped._row_lookup['Brake']))
    grouped._table_drop(drop)
    qapp.processEvents()

    assert _groups(grouped)['Pressure'] == OTHER


# ── Colour ───────────────────────────────────────────────────────────────


@pytest.fixture()
def theme(qapp):
    from PySide6.QtGui import QPalette

    saved = QPalette(qapp.palette())

    def apply(window: str, text: str) -> None:
        palette = QPalette(saved)
        palette.setColor(QPalette.ColorRole.Window, QColor(window))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(text))
        qapp.setPalette(palette)
        qapp.processEvents()

    yield apply
    qapp.setPalette(saved)
    qapp.processEvents()


def _row_colours(panel, group: str = GROUP) -> tuple[set[str], set[str]]:
    row = _header_row(panel, group)
    backgrounds = {panel.table.item(row, col).background().color().name()
                   for col in range(panel.table.columnCount())}
    texts = {panel.table.item(row, col).foreground().color().name() for col in (0, 1)}
    return backgrounds, texts


def _menu_action(panel, group: str, text: str):
    menu = panel._group_header_menu(group)
    for action in menu.actions():
        if action.text() == text:
            return action
    raise AssertionError(f'no "{text}" in {[a.text() for a in menu.actions()]}')


def test_the_group_menu_sets_a_colour_of_its_own(grouped, qapp, monkeypatch):
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *a, **k: QColor(ORANGE))

    _menu_action(grouped, GROUP, 'Change group color...').trigger()
    qapp.processEvents()

    assert _row_colours(grouped) == ({ORANGE}, {'#000000'})
    # The other group keeps the theme's colour.
    assert ORANGE not in _row_colours(grouped, OTHER)[0]


def test_a_cancelled_colour_dialog_changes_nothing(grouped, qapp, monkeypatch):
    before = _row_colours(grouped)
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *a, **k: QColor())

    _menu_action(grouped, GROUP, 'Change group color...').trigger()
    qapp.processEvents()

    assert _row_colours(grouped) == before
    assert grouped.group_colors() == {}


@pytest.mark.parametrize('colour, text', [('#1f3a5f', '#ffffff'), ('#ffe08a', '#000000')],
                         ids=['dark-colour', 'light-colour'])
def test_the_name_stays_readable_on_its_colour(grouped, qapp, colour, text):
    grouped.set_group_color(GROUP, colour)
    qapp.processEvents()

    assert _row_colours(grouped) == ({colour}, {text})


def test_the_default_colour_follows_the_theme_again(grouped, qapp, theme):
    from gui.edge_tab import _mix
    from gui.plot_widget import _GROUP_ROW_SHADE

    theme('#1e1e1e', '#ffffff')
    grouped.set_group_color(GROUP, ORANGE)
    assert _menu_action(grouped, GROUP, 'Default group color').isEnabled()

    _menu_action(grouped, GROUP, 'Default group color').trigger()
    qapp.processEvents()

    shade = _mix(QColor('#1e1e1e'), QColor('#ffffff'), _GROUP_ROW_SHADE).name()
    assert _row_colours(grouped) == ({shade}, {'#ffffff'})
    assert not _menu_action(grouped, GROUP, 'Default group color').isEnabled()


def test_a_chosen_colour_stays_through_a_switch_of_theme(grouped, qapp, theme):
    theme('#f3f3f3', '#000000')
    grouped.set_group_color(GROUP, ORANGE)

    theme('#1e1e1e', '#ffffff')

    assert _row_colours(grouped) == ({ORANGE}, {'#000000'})
    assert ORANGE not in _row_colours(grouped, OTHER)[0]


def test_the_colour_is_painted(grouped, qapp):
    grouped.set_group_color(GROUP, ORANGE)
    grouped.table_panel.resize(500, 300)
    grouped.table_panel.show()
    grouped.table.clearSelection()
    qapp.processEvents()
    row = _header_row(grouped)
    cell = _cell(grouped, row, 4)
    image = grouped.table.viewport().grab().toImage()
    ratio = image.devicePixelRatio()

    pixel = image.pixelColor(round((cell.left() + 4) * ratio), round(cell.center().y() * ratio))

    assert pixel.name() == ORANGE


def test_a_renamed_group_keeps_its_colour(grouped, qapp, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    grouped.set_group_color(GROUP, ORANGE)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Drivetrain', True))

    grouped._rename_group_dialog(GROUP)
    qapp.processEvents()

    assert grouped.group_colors() == {'Drivetrain': ORANGE}
    assert _row_colours(grouped, 'Drivetrain')[0] == {ORANGE}


def test_an_ungrouped_groups_colour_is_forgotten(grouped, qapp):
    grouped.set_group_color(GROUP, ORANGE)

    grouped.ungroup_selected(GROUP)

    assert grouped.group_colors() == {}


@pytest.fixture()
def window(qapp, monkeypatch):
    from unittest.mock import Mock

    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    yield w
    w.close()
    qapp.processEvents()


def test_the_colour_is_saved_in_the_configuration(window, qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    measurement = tmp_path / 'm.mf4'
    measurement.write_bytes(b'')
    window.measurement_path = str(measurement)
    config = tmp_path / 'config.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(config), ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(config), ''))
    window.plot_panel.set_group_color(GROUP, ORANGE)

    window.save_configuration()
    assert json.loads(config.read_text())['group_colors'] == {GROUP: ORANGE}

    window.plot_panel.set_group_color(GROUP, None)
    window.load_configuration()
    assert window.plot_panel.group_colors() == {GROUP: ORANGE}


def test_a_configuration_without_colours_leaves_the_theme_colour(window, qapp, monkeypatch,
                                                                 tmp_path):
    from PySide6.QtWidgets import QFileDialog

    measurement = tmp_path / 'm.mf4'
    measurement.write_bytes(b'')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'measurement_path': str(measurement), 'signals': []}))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(config), ''))
    window.plot_panel.set_group_color(GROUP, ORANGE)

    window.load_configuration()

    assert window.plot_panel.group_colors() == {}
