# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for the top row (gui/top_row.py and MainWindow._build_toolbar): its
order, its File menu, the Decode button, the shortcuts, and
Clear Plot's new places beside the plot."""
from __future__ import annotations

import pytest

# The top row's actions and the window methods they call.
HANDLERS = {
    'Open Measurement': 'choose_blf',
    'Databases': 'choose_dbc',
    'Decode': 'load_data',
    'Calculated Signal': 'new_generated_signal',
    'Open configuration': 'load_configuration',
    'Save configuration': 'save_configuration',
    'Export plotted signals': 'export_selected',
}


def _make_window(qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    return MainWindow('Osvanta Bus Log Analyzer', '00.00.99')


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    w = _make_window(qapp, monkeypatch, tmp_path)
    yield w
    w.deleteLater()
    qapp.processEvents()


@pytest.fixture()
def called(qapp, monkeypatch, tmp_path):
    """A window whose top-row handlers only record that they were called."""
    from gui.main_window import MainWindow

    calls = []
    for name in (*HANDLERS.values(), 'clear_plot'):
        monkeypatch.setattr(MainWindow, name,
                            lambda self, *args, _name=name, **kwargs: calls.append(_name))
    w = _make_window(qapp, monkeypatch, tmp_path)
    yield w, calls
    w.deleteLater()
    qapp.processEvents()


def _row(window):
    from PySide6.QtWidgets import QWidgetAction

    toolbar = window.title_row.toolbar
    items = []
    for action in toolbar.actions():
        if action.isSeparator():
            items.append('|')
        elif isinstance(action, QWidgetAction):
            items.append(toolbar.widgetForAction(action).defaultAction().text())
        else:
            items.append(action.text())
    return items


def test_the_row_reads_in_the_order_of_the_work(window):
    assert _row(window) == ['File', '|', 'Open Measurement', 'Databases', 'Decode', '|',
                            'Calculated Signal', 'Trace', '|', 'Help']


def test_the_file_menu_holds_what_is_done_now_and_then(window):
    from PySide6.QtWidgets import QToolButton

    menu = window.file_menu
    items = ['---' if a.isSeparator() else a.text() for a in menu.actions()]
    assert items == ['Open configuration', 'Save configuration', '---', 'Export plotted signals']
    button = window.title_row.toolbar.widgetForAction(menu.menuAction())
    assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup


def test_the_file_menu_greys_out_what_needs_decoded_signals(window, monkeypatch):
    actions = window._toolbar_actions
    later = [actions['Save configuration'], actions['Export plotted signals']]
    assert actions['Open configuration'].isEnabled()
    assert not any(action.isEnabled() for action in later)

    monkeypatch.setattr(window, 'store', object())
    window._update_action_states()

    assert all(action.isEnabled() for action in later)


@pytest.mark.parametrize('text, handler', HANDLERS.items())
def test_each_action_does_its_step(called, text, handler):
    window, calls = called
    action = window._toolbar_actions[text]
    action.setEnabled(True)

    action.trigger()

    assert calls == [handler]


@pytest.mark.parametrize('text, keys', [
    ('Open Measurement', 'Ctrl+O'),
    ('Decode', 'F9'),
    ('Save configuration', 'Ctrl+S'),
    ('Export plotted signals', 'Ctrl+E'),
])
def test_the_shortcuts_belong_to_the_actions(window, text, keys):
    from PySide6.QtGui import QKeySequence, QShortcut

    assert window._toolbar_actions[text].shortcut() == QKeySequence(keys)
    # A second shortcut on the same keys would make Qt fire neither.
    assert all(s.key() != QKeySequence(keys) for s in window.findChildren(QShortcut))
    assert keys.replace('+', ' + ') in dict(window._SHORTCUTS)


def test_the_menu_actions_are_the_windows_too(window):
    # So that Ctrl+S and Ctrl+E work while the File menu is closed.
    for text in ('Open configuration', 'Save configuration', 'Export plotted signals'):
        assert window._toolbar_actions[text] in window.actions()


def test_f9_decodes(qapp, called):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window, calls = called
    window._toolbar_actions['Decode'].setEnabled(True)
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window, 2000)

    QTest.keyClick(window, Qt.Key.Key_F9)

    assert calls == ['load_data']
    window.close()


def test_decode_stands_out_by_its_icon_and_bold_text(window):
    from gui.top_row import MainStepButton

    toolbar = window.title_row.toolbar
    button = window.decode_button
    neighbour = toolbar.widgetForAction(window._toolbar_actions['Databases'])
    assert isinstance(button, MainStepButton)
    assert button.defaultAction() is window._toolbar_actions['Decode']
    assert button.text() == 'Decode'
    assert not button.icon().isNull()
    assert 'F9' in button.toolTip()
    assert button.font().bold() and not neighbour.font().bold()
    # Only the weight differs: no fill, the theme's colours.
    assert button.font().family() == neighbour.font().family()
    assert button.font().pointSizeF() == neighbour.font().pointSizeF()
    assert button.styleSheet() == ''


def test_the_icon_sits_right_beside_the_word(qapp, window):
    window.show()
    qapp.processEvents()
    button = window.decode_button
    icon, text = button.content_rects()

    # Side by side, without the nine pixels Windows 11 leaves between them.
    assert text.left() == icon.right() + 1
    assert text.width() >= button.fontMetrics().horizontalAdvance('Decode')
    # Both inside the button, centred together.
    assert icon.left() >= 0 and text.right() < button.width()
    assert abs(icon.left() - (button.width() - 1 - text.right())) <= 2
    window.close()


def test_decode_is_a_little_taller_than_its_neighbours_not_the_whole_row(qapp, window):
    window.show()
    qapp.processEvents()
    toolbar = window.title_row.toolbar
    neighbour = toolbar.widgetForAction(window._toolbar_actions['Databases'])
    decode = window.decode_button

    assert neighbour.height() <= decode.height() <= neighbour.height() + 8
    # Centred on the same line as the text beside it.
    assert abs(decode.geometry().center().y() - neighbour.geometry().center().y()) <= 1
    window.close()


def test_decode_waits_for_a_measurement(window):
    decode = window._toolbar_actions['Decode']
    assert not decode.isEnabled()
    assert not window.decode_button.isEnabled()

    window.measurement_path = 'measurement.blf'
    window._update_action_states()

    assert decode.isEnabled()
    assert window.decode_button.isEnabled()


def test_clear_plot_sits_apart_at_the_end_of_the_plot_buttons(window):
    from PySide6.QtWidgets import QAbstractButton

    row = window.plot_button_row
    buttons = row.findChildren(QAbstractButton)
    assert window.btn_clear_plot in buttons
    assert window.btn_clear_plot.accessibleName() == 'Clear Plot'
    assert 'Ctrl+Z' in window.btn_clear_plot.toolTip()
    assert row._buttons[-1] is window.btn_clear_plot


def test_clear_plot_needs_decoded_signals_and_no_calculation(window, monkeypatch):
    assert not window.btn_clear_plot.isEnabled()

    monkeypatch.setattr(window, 'store', object())
    window._update_action_states()
    assert window.btn_clear_plot.isEnabled()

    monkeypatch.setattr(window, '_calc_thread', object())
    window._update_action_states()
    assert not window.btn_clear_plot.isEnabled()


def test_clear_plot_and_the_tables_remove_all_clear_the_plot(called):
    window, calls = called
    window.btn_clear_plot.setEnabled(True)

    window.btn_clear_plot.click()
    window.plot_panel.clearRequested.emit()

    assert calls == ['clear_plot', 'clear_plot']


def test_the_signal_menu_offers_remove_all_once_something_is_plotted(qapp):
    from PySide6.QtWidgets import QMenu
    from gui.plot_widget import PlotPanel

    panel = PlotPanel()
    try:
        requested = []
        panel.clearRequested.connect(lambda: requested.append(True))
        menu = QMenu()
        action = panel._add_remove_all_action(menu)
        assert action.text() == 'Remove all signals'
        assert not action.isEnabled()

        panel._items['CH1::Msg::Speed'] = object()
        menu = QMenu()
        action = panel._add_remove_all_action(menu)
        assert action.isEnabled()
        action.trigger()

        assert requested == [True]
    finally:
        panel._items.clear()
        panel.deleteLater()
        qapp.processEvents()
