# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The calculated signal dialog's layout, top to bottom: Load and Save under
their own header; Signal name and Unit, taller; Insert function and Help just
above a four-line formula box; the signal search with Add signal beside it;
and the signal list taking the rest of the height.

Requires a Qt platform plugin; skipped (not failed) if none is available.
"""
from __future__ import annotations

import pytest

from PySide6.QtWidgets import QLineEdit

SIGNAL = 'CH1::Message::A'


@pytest.fixture()
def dialog(qapp):
    from gui.calculated_signal_dialog import CalculatedSignalDialog
    d = CalculatedSignalDialog([SIGNAL], library_definitions=lambda: [],
                               import_definitions=lambda *_args: None)
    d.resize(760, 640)
    d.show()
    qapp.processEvents()
    yield d
    d.close()


def _top(widget, dialog):
    return widget.mapTo(dialog, widget.rect().topLeft()).y()


def _centre(widget, dialog):
    return widget.mapTo(dialog, widget.rect().center()).y()


def test_the_parts_come_in_the_agreed_order(dialog):
    order = [dialog.library_box, dialog.name_edit, dialog.unit_edit,
             dialog.insert_function_button, dialog.formula_edit, dialog.search_edit,
             dialog.signal_list, dialog.validation_label, dialog.button_box]
    tops = [_top(w, dialog) for w in order]
    assert tops == sorted(tops) and len(set(tops)) == len(tops)


def test_load_and_save_sit_under_their_header(dialog):
    box = dialog.library_box
    assert box.title() == 'Load and Save Formula for Calculated Signal'
    assert box.isAncestorOf(dialog.load_library_button)
    assert box.isAncestorOf(dialog.save_library_button)


def test_signal_name_and_unit_are_one_and_a_half_times_as_tall(dialog):
    plain = QLineEdit().sizeHint().height()
    for edit in (dialog.name_edit, dialog.unit_edit):
        assert edit.height() == round(plain * 1.5)


def test_their_labels_are_level_with_them(dialog):
    from PySide6.QtWidgets import QFormLayout
    form = next(item.layout() for item in (dialog.layout().itemAt(i)
                                           for i in range(dialog.layout().count()))
                if isinstance(item.layout(), QFormLayout))
    for edit in (dialog.name_edit, dialog.unit_edit):
        label = form.labelForField(edit)
        assert label is not None
        assert abs(_centre(label, dialog) - _centre(edit, dialog)) <= 2


def test_the_formula_box_is_four_lines_tall(dialog):
    lines = dialog.formula_edit.height() / dialog.formula_edit.fontMetrics().lineSpacing()
    assert 4 <= lines < 5.5


def test_insert_function_and_help_sit_just_above_the_formula(dialog):
    f_top = _top(dialog.formula_edit, dialog)
    for button in (dialog.insert_function_button, dialog.help_button):
        bottom = _top(button, dialog) + button.height()
        assert 0 <= f_top - bottom <= 20
    assert _centre(dialog.insert_function_button, dialog) == _centre(dialog.help_button, dialog)
    assert dialog.insert_function_button.font().bold()
    assert not dialog.help_button.font().bold()


def test_add_signal_sits_right_of_the_search(dialog):
    button = dialog.insert_button
    assert button.text() == 'Add signal'
    assert abs(_centre(button, dialog) - _centre(dialog.search_edit, dialog)) <= 2
    search_right = dialog.search_edit.mapTo(dialog, dialog.search_edit.rect().topRight()).x()
    assert button.mapTo(dialog, button.rect().topLeft()).x() > search_right


def test_a_taller_window_goes_to_the_signal_list(dialog, qapp):
    formula, signals = dialog.formula_edit.height(), dialog.signal_list.height()
    dialog.resize(760, 840)
    qapp.processEvents()
    assert dialog.formula_edit.height() == formula
    assert dialog.signal_list.height() >= signals + 190


def test_add_signal_still_adds_the_selected_signal(dialog):
    dialog.signal_list.setCurrentRow(2)            # under the header and Time
    dialog.insert_button.click()
    assert dialog.formula_edit.toPlainText() == f'`{SIGNAL}`'
