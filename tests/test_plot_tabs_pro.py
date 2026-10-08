# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Several tabs of plots are a Pro feature; gui.plot_tabs.multiple_tabs_allowed()
decides. Without it the window has one plot, as before tabs: no tab strip, no
"+", no Synchronize Tabs and no "Move to tab", and a configuration with
several tabs opens its first. Allowed later, as Pro will be, they all appear;
taken away while tabs are open, those tabs stay to be closed, but no tab is
added.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import numpy as np
import pytest

from PySide6.QtWidgets import QFileDialog, QMessageBox


def _store(*names: str):
    from core.signal_store import SignalStore
    store = SignalStore()
    for name in names:
        store.add_series_bulk(
            channel=1, message_name='Msg', message_id=1, signal_name=name, unit='V',
            timestamps=np.linspace(0.0, 10.0, 101), values=np.linspace(0.0, 1.0, 101),
            raw_values=[], has_labels=False,
        )
    return store


def _key(name: str) -> str:
    return _store(name).all_keys()[0]


@pytest.fixture()
def window(qapp, monkeypatch):
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w.store = _store('Speed', 'Torque', 'Current')
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def _shows_tabs(window) -> bool:
    tabs = window.plot_tabs
    shown = (not tabs.strip.isHidden(), not tabs.add_button.isHidden(),
             not window.btn_sync_tabs.isHidden(), window.plot_panel.move_targets is not None)
    assert len(set(shown)) == 1, f'strip, "+", sync button, Move to tab: {shown}'
    return shown[0]


# ── The free application ─────────────────────────────────────────────────


def test_no_one_has_several_tabs_until_the_licence_is_asked():
    from gui.plot_tabs import multiple_tabs_allowed

    assert multiple_tabs_allowed() is False


def test_the_window_has_one_plot_and_nothing_of_tabs(window):
    assert window.plot_tabs.count() == 1
    assert not window.plot_tabs.multiple_allowed
    assert not _shows_tabs(window)


def test_the_tab_strip_takes_no_room_above_the_table(qapp, window):
    table = window.plot_tabs.tables
    centre = window.centralWidget()
    margin = centre.layout().contentsMargins().top()
    assert table.mapTo(centre, table.rect().topLeft()).y() == margin


def test_signals_dropped_on_plus_add_no_tab(qapp, window):
    dropped = Mock()
    window.plot_tabs.signalsDropped.connect(dropped)
    moved = Mock()
    window.plot_tabs.signalsMoved.connect(moved)

    window.plot_tabs._dropped([_key('Speed')], False, -1)
    window.plot_tabs._dropped([_key('Speed')], True, -1)

    assert window.plot_tabs.count() == 1
    dropped.assert_not_called()
    moved.assert_not_called()


def test_a_configuration_with_several_tabs_opens_the_first(qapp, window, monkeypatch, tmp_path):
    config = {
        'tabs': [
            {'name': 'Engine', 'plot_type': 'multi_axis',
             'signals': [{'key': _key('Speed')}, {'key': _key('Torque')}]},
            {'name': 'Motor', 'plot_type': 'stacked', 'signals': [{'key': _key('Current')}]},
        ],
        'current_tab': 1,
    }
    path = tmp_path / 'load.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'question',
                        Mock(return_value=QMessageBox.StandardButton.Yes))  # keep current data

    window.load_configuration()
    qapp.processEvents()

    assert window.plot_tabs.count() == 1
    assert window.plot_panel.plotted_keys() == [_key('Speed'), _key('Torque')]
    assert window.plot_panel._multi_axis
    assert not _shows_tabs(window)
    assert '2 tabs saved: the first is opened' in window.log_box.toPlainText()


def test_the_last_measurements_tabs_come_back_as_the_first(qapp, window):
    window._temporary_plot_handoff = {
        'version': 3,
        'tabs': [{'name': 'Tab 1', 'signals': [{'key': _key('Speed')}]},
                 {'name': 'Tab 2', 'signals': [{'key': _key('Current')}]}],
        'current_tab': 1,
    }

    window._arm_temporary_plot_handoff()

    assert window.plot_tabs.count() == 1
    assert [tab['name'] for tab in window._pending_tabs.tabs] == ['Tab 1']
    assert window._pending_tabs.current == 0


# ── Allowed, as Pro will be, and taken away ──────────────────────────────


def test_allowing_several_tabs_shows_them_all(qapp, window):
    window.plot_tabs.set_multiple_allowed(True)
    qapp.processEvents()

    assert _shows_tabs(window)
    window.plot_tabs.add_button.click()
    assert window.plot_tabs.count() == 2


def test_taken_away_open_tabs_stay_until_closed(qapp, window, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', Mock(return_value=QMessageBox.StandardButton.Yes))
    tabs = window.plot_tabs
    tabs.set_multiple_allowed(True)
    tabs.add_tab()
    window.add_signals_to_plot([_key('Current')])

    tabs.set_multiple_allowed(False)
    qapp.processEvents()

    # Both tabs can still be reached, and closed; none can be added.
    assert not tabs.strip.isHidden()
    assert tabs.add_button.isHidden()
    assert window.btn_sync_tabs.isHidden()
    assert window.plot_panel.move_targets is None

    tabs.close_tab(1)
    qapp.processEvents()

    assert tabs.count() == 1
    assert not _shows_tabs(window)
