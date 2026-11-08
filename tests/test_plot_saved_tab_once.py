# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A saved tab is plotted with its colours in one go.

Load configuration with ten tabs froze the window for 9 s on a 3 GB log: each
saved colour was set after its signal was added, and each one refreshed the
signal table, the Table view and the markers; every tab was then rebuilt a
second time whatever it held. The colours now come with the signals, and a tab
is rebuilt again only when a saved setting differs from what it has.
"""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest

from PySide6.QtWidgets import QMessageBox

NAMES = [f'S{n}' for n in range(12)]


def _store():
    from core.signal_store import SignalStore
    store = SignalStore()
    for name in NAMES:
        store.add_series_bulk(
            channel=1, message_name='Msg', message_id=1, signal_name=name, unit='V',
            timestamps=np.linspace(0.0, 10.0, 101), values=np.linspace(0.0, 1.0, 101),
            raw_values=[], has_labels=False)
    return store


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    w.store = _store()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _keys(window):
    return sorted(window.store.all_keys())


def _tab(window, colors=None, extra=None):
    colors, extra = colors or {}, extra or {}
    return {'signals': [dict({'key': key, 'visible': True}, **({'color': colors[key]} if key in colors else {}),
                             **extra.get(key, {}))
                        for key in _keys(window)[:10]]}


def _count(monkeypatch, panel, name):
    calls = []
    original = getattr(panel, name)
    monkeypatch.setattr(panel, name, lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    return calls


def test_a_saved_tab_keeps_every_colour(window):
    keys = _keys(window)
    colors = {key: f'#{n:02x}{n:02x}aa' for n, key in enumerate(keys[:10])}

    window._plot_saved_signals(_tab(window, colors))

    panel = window.plot_panel
    assert {key: panel._items[key].color for key in keys[:10]} == colors


def test_signals_added_later_get_the_colours_they_always_got(window):
    from gui.main_window import MainWindow
    keys = _keys(window)
    # A plot that was never coloured from a configuration, for comparison.
    plain = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        plain.store = window.store
        plain.add_signals_to_plot(keys[:10])
        plain.add_signals_to_plot(keys[10:])
        expected = [plain.plot_panel._items[key].color for key in keys[10:]]
    finally:
        plain.close()
        plain.deleteLater()

    # Seven of the ten coloured: not a whole turn of the palette.
    window._plot_saved_signals(_tab(window, {key: '#123456' for key in keys[:7]}))
    window.add_signals_to_plot(keys[10:])

    assert [window.plot_panel._items[key].color for key in keys[10:]] == expected


def test_the_table_is_refreshed_once_for_the_tab_not_once_per_signal(window, monkeypatch):
    keys = _keys(window)
    refreshes = _count(monkeypatch, window.plot_panel, '_refresh_table_and_cursors')

    window._plot_saved_signals(_tab(window, {key: '#123456' for key in keys[:10]}))

    assert 1 <= len(refreshes) <= 2, len(refreshes)


def test_a_tab_as_saved_is_built_once(window, monkeypatch):
    rebuilds = _count(monkeypatch, window.plot_panel, '_rebuild_curves')

    window._plot_saved_signals(_tab(window))

    assert len(rebuilds) == 1


def test_a_saved_setting_that_differs_is_still_applied(window, monkeypatch):
    keys = _keys(window)
    rebuilds = _count(monkeypatch, window.plot_panel, '_rebuild_curves')

    window._plot_saved_signals(_tab(window, extra={keys[3]: {'visible': False, 'group': 'Motor'}}))

    assert len(rebuilds) == 2
    assert window.plot_panel._items[keys[3]].visible is False
    assert window.plot_panel._items[keys[3]].group == 'Motor'
    assert all(window.plot_panel._items[key].visible for key in keys[:10] if key != keys[3])
