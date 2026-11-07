# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Right-click a signal ▸ Properties (TODO feature 18): the entry in the signal
tree's menu and in the plot's signal menu, the window, and the main window
opening it.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from core.channel_config import ChannelConfig
from core.signal_properties import describe_signal
from gui.signal_properties_dialog import SignalPropertiesDialog
from tests.test_signal_properties import _dbc, _series

KEY = "CH1::ACC_Ctrl::ACC_ComfBandLower"


def _action(menu, text="Properties…"):
    return next((a for a in menu.actions() if a.text() == text), None)


# ── Signal tree ───────────────────────────────────────────────────────────


@pytest.fixture()
def signal_tree(qapp):
    from gui.signal_tree import SignalTreeWidget
    widget = SignalTreeWidget()
    widget.set_payload({1: {"ACC_Ctrl": ["ACC_ComfBandLower", "ACC_Temp"]}})
    yield widget
    widget.close()


def test_the_signal_trees_menu_offers_properties_for_the_signal_under_the_mouse(signal_tree):
    requested = []
    signal_tree.propertiesRequested.connect(requested.append)
    message_item = signal_tree.tree.topLevelItem(0).child(0)

    menu = signal_tree._build_context_menu(message_item.child(0), KEY, [KEY])
    _action(menu).trigger()

    assert requested == [KEY]


def test_the_signal_trees_menu_offers_properties_for_a_calculated_signal(signal_tree):
    key = "CH?::Generate Signals::Doubled"
    signal_tree.set_generated_signals([(key, "Doubled", "formula")])
    requested = []
    signal_tree.propertiesRequested.connect(requested.append)
    generated_item = signal_tree.tree.topLevelItem(0).child(0)

    _action(signal_tree._build_context_menu(generated_item, key, [key])).trigger()

    assert requested == [key]


def test_no_properties_for_a_message_row_or_several_signals(signal_tree):
    message_item = signal_tree.tree.topLevelItem(0).child(0)
    other = "CH1::ACC_Ctrl::ACC_Temp"

    assert _action(signal_tree._build_context_menu(message_item, None, [])) is None
    assert _action(signal_tree._build_context_menu(message_item, None, [KEY, other])) is None


# ── The plot's signal menu (signal table and stacked plot) ────────────────


class _RecordingMenu(QMenu):
    """Records itself instead of opening, as exec() would."""
    shown: list = []

    def exec(self, *_args):
        _RecordingMenu.shown.append(self)


@pytest.fixture()
def panel(qapp, monkeypatch):
    from gui.plot_widget import PlotPanel
    p = PlotPanel()
    p.add_series(KEY, _series("ACC_ComfBandLower"))
    p.add_series("CH1::ACC_Ctrl::ACC_Temp", _series("ACC_Temp"))
    _RecordingMenu.shown = []
    monkeypatch.setattr(p, "_make_menu", lambda parent=None: _RecordingMenu())
    yield p
    p.close()


def test_the_plot_menu_offers_properties_for_one_signal(panel):
    requested = []
    panel.propertiesRequested.connect(requested.append)

    panel._show_signal_menu([KEY], None)
    action = _action(_RecordingMenu.shown[-1])
    action.trigger()

    assert action.isEnabled()
    assert requested == [KEY]


def test_the_plot_menu_greys_properties_for_several_signals(panel):
    panel._show_signal_menu([KEY, "CH1::ACC_Ctrl::ACC_Temp"], None)

    assert not _action(_RecordingMenu.shown[-1]).isEnabled()


# ── The window ────────────────────────────────────────────────────────────


@pytest.fixture()
def config(tmp_path):
    return ChannelConfig.from_single_dbc(_dbc(tmp_path))


def test_the_window_shows_the_signal_and_its_value_table(qapp, config):
    dialog = SignalPropertiesDialog(describe_signal(KEY, _series("ACC_ComfBandLower"), config))

    assert dialog.windowTitle() == "Properties — ACC_ComfBandLower"
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ["Signal", "Value table (2)"]
    text = dialog.as_text()
    assert "Factor\t0.01" in text
    assert "0\t-10.23\tDefault" in text
    dialog.copy_all()
    assert QApplication.clipboard().text() == text
    dialog.close()


def test_a_signal_without_a_value_table_has_one_tab(qapp, config):
    dialog = SignalPropertiesDialog(describe_signal(KEY, _series("ACC_Temp", unit="degC"), config))

    assert dialog.tabs.count() == 1
    dialog.close()


# ── The main window ───────────────────────────────────────────────────────


class _Store:
    def __init__(self, series) -> None:
        self._series = {item.key: item for item in series}
        self.raw_frame_store = None

    def all_keys(self):
        return sorted(self._series)

    def get_series(self, key):
        return self._series.get(key)

    def time_axes(self):
        return list(self._series.values())


@pytest.fixture()
def window(qapp, monkeypatch, config):
    from gui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    widget = MainWindow("Osvanta Bus Log Analyzer", "00.00.99")
    widget.store = _Store([_series("ACC_ComfBandLower")])
    widget.channel_config = config
    yield widget
    widget.close()
    qapp.processEvents()


@pytest.mark.parametrize("menu", ["signal_tree", "plot_panel"])
def test_either_menu_opens_the_properties_window(window, menu):
    getattr(window, menu).propertiesRequested.emit(KEY)

    dialog = window._properties_dialog
    assert dialog.isVisible()
    assert dict(dialog.properties.rows)["Database"] == "acc.dbc"
    assert dialog.tabs.tabText(1) == "Value table (2)"
    dialog.close()


def test_a_calculated_signal_opens_with_its_formula(window):
    from core.calculated_signals import CalculatedSignalDefinition
    definition = CalculatedSignalDefinition("Doubled", f"`{KEY}` * 2", "m/s2")
    window.calculated_signals._definitions[definition.key] = definition

    window.show_signal_properties(definition.key)

    assert dict(window._properties_dialog.properties.rows)["Formula"] == f"`{KEY}` * 2"
    window._properties_dialog.close()
