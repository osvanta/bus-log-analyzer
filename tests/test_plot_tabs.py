# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tests for gui/plot_tabs.py: tabs of plots in the main window.

Each tab has its own signals, signal table and plot mode. Synchronized tabs,
the default, show the time and the cursors of the tab shown before them;
unsynchronized, each keeps its own.
"""
from __future__ import annotations

import array
from unittest.mock import Mock

import pytest


class _Store:
    """Just enough of a SignalStore to plot from."""

    def __init__(self, duration: float, names: tuple[str, ...]) -> None:
        from core.signal_store import SignalSeries
        ts = array.array('d', (i * duration / 1000 for i in range(1001)))
        vs = array.array('d', (float(i) for i in range(1001)))
        self._series = {name: SignalSeries(None, 'Msg', 1, name, 'km/h', ts, vs)
                        for name in names}

    def get_series(self, key):
        return self._series.get(key)


@pytest.fixture()
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    monkeypatch.setattr(w, 'load_data', Mock())
    w.store = _Store(20.0, ('Speed', 'Torque', 'Current', 'Voltage'))
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def _plot(qapp, window, *keys):
    window.add_signals_to_plot(list(keys))
    qapp.processEvents()


def _new_tab(qapp, window):
    window.plot_tabs.add_button.click()
    qapp.processEvents()
    return window.plot_panel


def _show(qapp, window, index):
    window.plot_tabs.bar.setCurrentIndex(index)
    qapp.processEvents()


def _range(panel):
    return tuple(round(x, 6) for x in panel.time_range())


def test_the_window_starts_with_one_tab_that_cannot_be_closed(window):
    tabs = window.plot_tabs
    assert tabs.names() == ['Tab 1']
    assert window.plot_panel is tabs.panels()[0]
    assert not tabs.bar.tabsClosable()
    tabs.close_tab(0)
    assert tabs.count() == 1


def test_a_new_tab_is_empty_stacked_and_shown_while_the_first_keeps_its_signals(qapp, window):
    _plot(qapp, window, 'Speed', 'Torque')
    first = window.plot_panel

    second = _new_tab(qapp, window)

    assert window.plot_tabs.names() == ['Tab 1', 'Tab 2']
    assert second is not first and window.plot_panel is second
    assert second.plotted_keys() == []
    assert first.plotted_keys() == ['Speed', 'Torque']
    assert second._stacked_mode and window.btn_stacked.isChecked()
    assert window.plot_tabs.tables.currentWidget() is second.table_panel
    assert window.plot_tabs.plots.currentWidget() is second
    assert window.plot_tabs.bar.tabsClosable()


def test_signals_go_to_the_tab_on_screen(qapp, window):
    _plot(qapp, window, 'Speed')
    second = _new_tab(qapp, window)
    _plot(qapp, window, 'Current', 'Voltage')

    first = window.plot_tabs.panels()[0]
    assert first.plotted_keys() == ['Speed']
    assert second.plotted_keys() == ['Current', 'Voltage']


def test_each_tab_keeps_its_plot_mode_and_the_buttons_follow_it(qapp, window):
    _plot(qapp, window, 'Speed')
    window.btn_multi_axis.setChecked(True)
    qapp.processEvents()
    second = _new_tab(qapp, window)
    _plot(qapp, window, 'Current')
    window.btn_points.setChecked(True)

    _show(qapp, window, 0)
    first = window.plot_panel
    assert first._multi_axis and not first._show_points
    assert window.btn_multi_axis.isChecked() and not window.btn_stacked.isChecked()
    assert not window.btn_points.isChecked() and not window.btn_hide_line.isEnabled()

    _show(qapp, window, 1)
    assert second._stacked_mode and second._show_points
    assert window.btn_stacked.isChecked() and not window.btn_multi_axis.isChecked()
    assert window.btn_points.isChecked() and window.btn_hide_line.isEnabled()


def test_synchronized_tabs_show_the_time_and_cursors_of_the_tab_left(qapp, window):
    _plot(qapp, window, 'Speed')
    first = window.plot_panel
    second = _new_tab(qapp, window)
    _plot(qapp, window, 'Current')
    _show(qapp, window, 0)
    first.zoom_to_time(8.0, 9.0, margin=0.0)
    window.btn_cursor1.setChecked(True)
    first.move_cursor1(8.5)

    _show(qapp, window, 1)

    assert _range(second) == (8.0, 9.0)
    assert second.cursor_state()[:2] == (True, 8.5)
    assert window.btn_cursor1.isChecked()

    second.zoom_to_time(3.0, 4.0, margin=0.0)
    window.btn_cursor1.setChecked(False)
    _show(qapp, window, 0)
    assert _range(first) == (3.0, 4.0)
    assert first.cursor_state()[0] is False


def test_unsynchronized_tabs_keep_their_own_time(qapp, window):
    window.btn_sync_tabs.setChecked(False)
    _plot(qapp, window, 'Speed')
    first = window.plot_panel
    first.zoom_to_time(8.0, 9.0, margin=0.0)
    second = _new_tab(qapp, window)
    _plot(qapp, window, 'Current')
    second.zoom_to_time(3.0, 4.0, margin=0.0)

    _show(qapp, window, 0)
    assert _range(first) == (8.0, 9.0)
    _show(qapp, window, 1)
    assert _range(second) == (3.0, 4.0)


def test_a_synchronized_tab_plotted_first_shows_the_shared_time(qapp, window):
    _plot(qapp, window, 'Speed')
    window.plot_panel.zoom_to_time(8.0, 9.0, margin=0.0)
    second = _new_tab(qapp, window)

    _plot(qapp, window, 'Current', 'Voltage')

    assert _range(second) == (8.0, 9.0)


def test_an_unsynchronized_tab_plotted_first_shows_all_its_time(qapp, window):
    window.btn_sync_tabs.setChecked(False)
    _plot(qapp, window, 'Speed')
    window.plot_panel.zoom_to_time(8.0, 9.0, margin=0.0)
    second = _new_tab(qapp, window)

    _plot(qapp, window, 'Current')

    x0, x1 = second.time_range()
    assert x0 < 0.5 and x1 > 19.5


def test_fit_acts_on_the_tab_on_screen(qapp, window):
    window.btn_sync_tabs.setChecked(False)
    _plot(qapp, window, 'Speed')
    first = window.plot_panel
    first.zoom_to_time(8.0, 9.0, margin=0.0)
    second = _new_tab(qapp, window)
    _plot(qapp, window, 'Current')
    second.zoom_to_time(3.0, 4.0, margin=0.0)

    window.btn_fit.click()
    qapp.processEvents()

    x0, x1 = second.time_range()
    assert x0 < 0.5 and x1 > 19.5
    assert _range(first) == (8.0, 9.0)


def test_closing_a_tab_with_signals_asks_first(qapp, window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    _new_tab(qapp, window)
    _plot(qapp, window, 'Current')
    ask = Mock(return_value=QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, 'question', ask)

    window.plot_tabs.close_tab(1)
    assert window.plot_tabs.count() == 2 and ask.call_count == 1

    ask.return_value = QMessageBox.StandardButton.Yes
    window.plot_tabs.close_tab(1)
    qapp.processEvents()
    assert window.plot_tabs.names() == ['Tab 1']
    assert window.plot_panel is window.plot_tabs.panels()[0]
    assert window.plot_tabs.tables.count() == 1 and window.plot_tabs.plots.count() == 1
    assert not window.plot_tabs.bar.tabsClosable()


def test_an_empty_tab_closes_without_asking(qapp, window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    _new_tab(qapp, window)
    ask = Mock()
    monkeypatch.setattr(QMessageBox, 'question', ask)
    window.plot_tabs.close_tab(1)
    assert window.plot_tabs.count() == 1 and ask.call_count == 0


def test_new_tabs_take_the_next_free_name(qapp, window):
    _new_tab(qapp, window)
    _new_tab(qapp, window)
    window.plot_tabs.close_tab(1)
    _new_tab(qapp, window)
    assert window.plot_tabs.names() == ['Tab 1', 'Tab 3', 'Tab 4']


@pytest.mark.parametrize('key, kept', [('Return', True), ('Escape', False)])
def test_a_tab_is_renamed_in_place(qapp, window, key, kept):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    window.plot_tabs.rename_tab(0)
    editor = window.plot_tabs.bar.findChild(QLineEdit)
    assert editor is not None and editor.text() == 'Tab 1'
    QTest.keyClicks(editor, 'Motor')        # replaces the selected name
    QTest.keyClick(editor, getattr(Qt.Key, f'Key_{key}'))
    qapp.processEvents()

    assert window.plot_tabs.names() == (['Motor'] if kept else ['Tab 1'])


def test_another_measurement_empties_every_tab_and_forgets_their_time(qapp, window):
    _plot(qapp, window, 'Speed')
    window.plot_panel.zoom_to_time(8.0, 9.0, margin=0.0)
    second = _new_tab(qapp, window)
    _plot(qapp, window, 'Current')

    window._reset_for_new_measurement()
    qapp.processEvents()

    assert all(panel.plotted_keys() == [] for panel in window.plot_tabs.panels())
    window.store = _Store(20.0, ('Voltage',))   # the next measurement, decoded
    _plot(qapp, window, 'Voltage')
    x0, x1 = second.time_range()
    assert x0 < 0.5 and x1 > 19.5          # all of it, not the old 8 to 9 s


def test_the_background_and_the_tag_are_the_same_in_every_tab(qapp, window, monkeypatch):
    from gui.plot_tag import PlotTag

    monkeypatch.setattr('gui.main_window.save_plot_tag', Mock())
    first = window.plot_panel
    second = _new_tab(qapp, window)

    second.set_background_color('#ffffff')
    assert first.background_color() == '#ffffff'

    tag = PlotTag(text='Bench 3')
    first.set_tag(tag)
    first.tagChanged.emit(tag)
    assert second._tag == tag
    assert _new_tab(qapp, window)._tag == tag


def test_the_sync_button_sits_at_the_right_end_of_the_plot_buttons(qapp, window):
    row = window.plot_button_row
    button = window.btn_sync_tabs
    assert button.isCheckable() and button.isChecked()
    assert button.geometry().right() > max(
        b.geometry().right() for b in row._buttons if b.isVisible())

    window.resize(700, 800)
    qapp.processEvents()
    assert button.isVisible()
    assert button not in row.overflowed_buttons()


def test_ctrl_tab_shows_the_next_tab_round_the_end(qapp, window):
    _new_tab(qapp, window)
    _new_tab(qapp, window)
    tabs = window.plot_tabs
    _show(qapp, window, 0)

    tabs.show_neighbour(1)
    assert tabs.bar.currentIndex() == 1
    tabs.show_neighbour(-1)
    tabs.show_neighbour(-1)
    assert tabs.bar.currentIndex() == 2


# ── The look of the tabs ────────────────────────────────────────────────


def _strip_image(qapp, window):
    """The tab strip as drawn, one image pixel to a pixel of the strip."""
    from PySide6.QtGui import QImage, QPalette
    qapp.processEvents()
    strip = window.plot_tabs.strip
    image = QImage(strip.size(), QImage.Format.Format_ARGB32)
    image.fill(strip.palette().color(QPalette.ColorRole.Window))
    strip.render(image)
    return image


def _close_enough(color, expected) -> bool:
    return all(abs(a - b) <= 3 for a, b in zip(color.getRgb()[:3], expected.getRgb()[:3]))


def test_the_line_along_the_strip_opens_under_the_selected_tab_only(qapp, window):
    from PySide6.QtGui import QPalette
    from gui.edge_tab import _mix
    from gui.plot_tabs import _OUTLINE
    _new_tab(qapp, window)
    _show(qapp, window, 0)
    tabs = window.plot_tabs
    palette = tabs.strip.palette()
    background = palette.color(QPalette.ColorRole.Window)
    line = _mix(background, palette.color(QPalette.ColorRole.WindowText), _OUTLINE)

    image = _strip_image(qapp, window)
    foot = image.height() - 1

    def under(index):
        return tabs.bar.mapTo(tabs.strip, tabs.bar.tabRect(index).center()).x()

    assert _close_enough(image.pixelColor(under(0), foot), background)
    assert _close_enough(image.pixelColor(under(1), foot), line)
    assert _close_enough(image.pixelColor(2, foot), line)           # before the tabs
    assert _close_enough(image.pixelColor(image.width() - 3, foot), line)  # at the far end

    _show(qapp, window, 1)
    image = _strip_image(qapp, window)
    assert _close_enough(image.pixelColor(under(0), foot), line)
    assert _close_enough(image.pixelColor(under(1), foot), background)


def test_each_tab_has_a_small_close_button_raised_beside_its_name(qapp, window):
    from PySide6.QtWidgets import QTabBar
    from gui.plot_tabs import CLOSE_SIZE, TAB_FLARE, TAB_PADDING
    bar = window.plot_tabs.bar
    right = QTabBar.ButtonPosition.RightSide
    assert bar.tabButton(0, right) is None          # the last tab cannot close

    _new_tab(qapp, window)
    window.plot_tabs.bar.setTabText(1, 'A longer name')
    qapp.processEvents()

    for index in range(bar.count()):
        button = bar.tabButton(index, right)
        tab = bar.tabRect(index)
        name_end = (tab.left() + TAB_FLARE + TAB_PADDING
                    + bar.fontMetrics().horizontalAdvance(bar.tabText(index)))
        assert button.size().width() == button.size().height() == CLOSE_SIZE
        # Above the middle of the name, as a superscript sits, and after it.
        assert button.geometry().center().y() < tab.center().y()
        assert button.geometry().left() >= name_end
        assert tab.contains(button.geometry())


def test_the_close_button_closes_its_own_tab(qapp, window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QTabBar
    _new_tab(qapp, window)
    _new_tab(qapp, window)
    bar = window.plot_tabs.bar

    QTest.mouseClick(bar.tabButton(1, QTabBar.ButtonPosition.RightSide),
                     Qt.MouseButton.LeftButton)
    qapp.processEvents()

    assert window.plot_tabs.names() == ['Tab 1', 'Tab 3']


def test_a_tab_name_fits_its_tab_and_an_ampersand_is_kept(qapp, window):
    from PySide6.QtWidgets import QStyle, QStyleOptionTab
    bar = window.plot_tabs.bar
    bar.setTabText(0, 'Brakes & ABS')
    qapp.processEvents()

    option = QStyleOptionTab()
    bar.initStyleOption(option, 0)

    assert option.text == 'Brakes & ABS'            # not shortened
    name = bar.style().subElementRect(QStyle.SubElement.SE_TabBarTabText, option, bar)
    assert name.width() >= bar.fontMetrics().horizontalAdvance('Brakes & ABS')
