# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tabs of plots and what came after them: what every tab shares (markers,
measurement events, the signal table's columns, the line mode, Rectangle
Zoom), what each tab keeps for itself (the Table view), each tab's menus
reaching the window, and the last session keeping every tab.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui import session_restore
from tests.test_plot_tabs import _new_tab, _plot, _show, _Store


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    w._user_settings_path = tmp_path / 'settings.json'
    w.store = _Store(20.0, ('Speed', 'Torque', 'Current', 'Voltage'))
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def _marker_names(panel):
    return [(round(m.time, 6), m.name) for m in panel.markers()]


# ── Markers: one set, in every tab ────────────────────────────────────────


def test_a_marker_added_in_one_tab_is_in_every_tab(qapp, window):
    first = window.plot_panel
    _plot(qapp, window, 'Speed')
    second = _new_tab(qapp, window)

    first.add_marker(4.0)

    assert _marker_names(second) == [(4.0, 'M1')]
    assert window.btn_delete_markers.isEnabled()


def test_a_tab_added_later_has_the_markers(qapp, window):
    window.plot_panel.add_marker(2.5)

    later = _new_tab(qapp, window)

    assert _marker_names(later) == [(2.5, 'M1')]


def test_deleting_markers_in_one_tab_deletes_them_in_every_tab(qapp, window):
    first = window.plot_panel
    first.add_marker(1.0)
    second = _new_tab(qapp, window)

    second.remove_all_markers()

    assert first.markers() == []
    assert not window.btn_delete_markers.isEnabled()


def test_a_renamed_or_dragged_marker_changes_in_every_tab(qapp, window):
    first = window.plot_panel
    _plot(qapp, window, 'Speed')
    first.add_marker(1.0)
    second = _new_tab(qapp, window)

    second.rename_marker(second.markers()[0], 'Brake')
    assert _marker_names(first) == [(1.0, 'Brake')]

    line = next(line for _, line in first._marker_lines if line.marker is first.markers()[0])
    line.setPos(6.0)
    first._on_marker_moved(line)
    assert _marker_names(second) == [(6.0, 'Brake')]
    # The tab it was dragged in is not drawn again under the mouse.
    assert line in [item for _, item in first._marker_lines]


def test_a_new_measurement_takes_the_markers_from_every_tab(qapp, window):
    window.plot_panel.add_marker(1.0)
    second = _new_tab(qapp, window)

    window._reset_for_new_measurement()

    assert [panel.markers() for panel in window.plot_tabs.panels()] == [[], []]
    assert second.markers() == []


# ── Measurement events, the columns, the line mode, Rectangle Zoom ────────


def test_measurement_events_show_in_every_tab_or_in_none(qapp, window, monkeypatch):
    monkeypatch.setattr(window, '_measurement_file', lambda: 'run.mf4')
    monkeypatch.setattr(window, '_read_measurement_info',
                        lambda: SimpleNamespace(events=[SimpleNamespace(time_s=2.0, name='Start')]))
    first = window.plot_panel
    second = _new_tab(qapp, window)

    second.events_action.trigger()                  # ticked in the second tab's menu

    for panel in (first, second):
        assert panel.events_action.isChecked()
        assert panel.events() == [(2.0, 'Start')]
    later = _new_tab(qapp, window)
    assert later.events_action.isChecked() and later.events() == [(2.0, 'Start')]

    first.events_action.trigger()                   # unticked in the first

    assert not any(panel.events_action.isChecked() for panel in window.plot_tabs.panels())


def test_a_column_shown_in_one_tab_is_shown_in_every_tab(qapp, window):
    from gui.plot_widget import HEX_COLUMN
    first = window.plot_panel
    second = _new_tab(qapp, window)

    second.set_column_shown(HEX_COLUMN, True)

    assert first.column_choices()[HEX_COLUMN]
    assert _new_tab(qapp, window).column_choices()[HEX_COLUMN]   # from the settings


def test_every_tab_can_show_raw_values_in_hex(qapp, window):
    later = _new_tab(qapp, window)

    assert later._raw_format_lookup == window._raw_format_of


def test_the_line_mode_and_rectangle_zoom_are_the_same_in_every_tab(qapp, window):
    first = window.plot_panel
    second = _new_tab(qapp, window)

    window.btn_line_mode.click()                     # steps → straight lines
    window.btn_zoom_rect.setChecked(True)

    for panel in (first, second, _new_tab(qapp, window)):
        assert not panel.step_lines()
        assert panel._rect_zoom


# ── What each tab keeps for itself ────────────────────────────────────────


def test_the_table_view_belongs_to_the_tab_it_was_opened_in(qapp, window):
    first = window.plot_panel
    window.btn_table.setChecked(True)
    second = _new_tab(qapp, window)

    assert first.table_view_active() and not second.table_view_active()
    assert not window.btn_table.isChecked()

    _show(qapp, window, 1)
    window.btn_table.setChecked(True)               # acts on the tab on screen
    assert second.table_view_active()

    window.btn_table.setChecked(False)
    assert first.table_view_active() and not second.table_view_active()
    _show(qapp, window, 0)
    assert window.btn_table.isChecked()


# ── Each tab's menus reach the window ─────────────────────────────────────


def test_properties_and_remove_all_signals_work_from_a_later_tab(qapp, window, monkeypatch):
    shown = Mock()
    cleared = Mock()
    monkeypatch.setattr(window, 'show_signal_properties', shown)
    monkeypatch.setattr(window, 'clear_plot', cleared)
    second = _new_tab(qapp, window)

    second.propertiesRequested.emit('Speed')
    second.clearRequested.emit()

    shown.assert_called_once_with('Speed')
    cleared.assert_called_once_with()


def test_the_trace_follows_cursor_1_of_the_tab_on_screen_only(qapp, window):
    first = window.plot_panel
    second = _new_tab(qapp, window)
    followed = []
    window.plotCursor1Moved.connect(followed.append)

    first.cursor1Moved.emit(3.0)                    # a tab behind the one on screen
    second.cursor1Moved.emit(5.0)

    assert followed == [5.0]


# ── Saved: a configuration, and the last session ──────────────────────────


def test_a_configuration_keeps_every_tab_and_one_set_of_markers(qapp, window):
    _plot(qapp, window, 'Speed')
    window.plot_panel.add_marker(1.5)
    _new_tab(qapp, window)
    _plot(qapp, window, 'Torque')

    config = window._configuration_dict()

    assert [[s['key'] for s in tab['signals']] for tab in config['tabs']] == [['Speed'], ['Torque']]
    assert [s['key'] for s in config['signals']] == ['Speed']       # what older versions open
    assert config['markers'] == [{'time': 1.5, 'name': 'M1'}]

    window._reset_for_new_measurement()
    window.store = None
    window._apply_configuration(dict(config, measurement_path=None, blf_path=None), 'c.json')

    assert window.plot_tabs.count() == 2
    assert [_marker_names(panel) for panel in window.plot_tabs.panels()] == [[(1.5, 'M1')]] * 2


def test_group_colours_of_every_tab_are_saved(qapp, window):
    window.plot_panel.set_group_colors({'Engine': '#ff0000'})
    _new_tab(qapp, window).set_group_colors({'Motor': '#00ff00'})

    assert window._configuration_dict()['group_colors'] == {
        'Engine': '#ff0000', 'Motor': '#00ff00'}


def test_the_last_session_counts_the_signals_of_every_tab():
    config = {'signals': [], 'tabs': [{'signals': []}, {'signals': [{'key': 'Speed'}]}]}

    assert session_restore.holds_something(config)
    assert 'Plotted signals: 1, in 2 tabs' in session_restore.summary(config)
    assert 'Plotted signals: 1\n' in session_restore.summary({'signals': [{'key': 'Speed'}]})


def test_looking_at_another_tab_is_not_a_change_to_save():
    saved = {'signals': [], 'current_tab': 0}

    assert not session_restore.differs(dict(saved, current_tab=1), saved)
    assert session_restore.differs(dict(saved, signals=[{'key': 'Speed'}]), saved)
