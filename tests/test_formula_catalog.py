# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The Insert Function window of the calculated-signal dialog: every function,
operator and constant of the formula language, by category, with how to use
each, inserted into the formula with placeholders to fill.

The window tests require a Qt platform plugin; skipped (not failed) if none
is available.
"""
from __future__ import annotations

import re

import pytest

from core.calculated_signals import _FUNCTION_ALIASES, _FUNCTIONS, parse_formula
from core.formula_catalog import CATEGORY_ORDER, EXAMPLE_SIGNAL, formula_catalog

SIGNAL = 'CH1::Message::A'


def _entry(name):
    return next(e for e in formula_catalog() if e.name == name)


# ── The catalog ──────────────────────────────────────────────────────────


def test_every_function_is_listed_once_and_aliases_are_not():
    names = [e.name for e in formula_catalog()]
    for spec in _FUNCTIONS.values():
        assert names.count(spec.name) == 1, spec.name
    for alias in _FUNCTION_ALIASES:
        assert alias not in names


def test_every_argument_of_every_function_is_explained():
    for spec in _FUNCTIONS.values():
        entry = _entry(spec.name)
        assert len(entry.arguments) == spec.max_args, spec.name
        for name, text in entry.arguments:
            assert re.search(rf'\b{name}\b', spec.signature), (spec.name, name)
            assert text.strip(), (spec.name, name)


def test_every_example_is_a_valid_formula():
    for entry in formula_catalog():
        parse_formula(entry.example, [EXAMPLE_SIGNAL])


def test_categories_come_in_the_proposed_order():
    seen = []
    for entry in formula_catalog():
        if entry.category not in seen:
            seen.append(entry.category)
    assert seen == list(CATEGORY_ORDER) == [
        'Math', 'Rounding', 'Selection', 'Conditional', 'Comparison and logic',
        'Bitwise', 'Trigonometry', 'Time', 'Diagnostic', 'Constants']
    rounding = [e.name for e in formula_catalog() if e.category == 'Rounding']
    assert rounding == ['round', 'floor', 'ceil', 'trunc']


def test_every_insertion_becomes_valid_once_its_placeholders_are_filled():
    for entry in formula_catalog():
        text, start, end = entry.insertion(f'`{SIGNAL}`')
        for param in entry.params[1:]:
            text = re.sub(rf'\b{param}\b', '1', text, count=1)
        if not entry.params:                     # a constant needs a signal beside it
            text = f'{text} * `{SIGNAL}`'
        parse_formula(text, [SIGNAL])


def test_insertion_selects_the_first_placeholder_to_fill():
    text, start, end = _entry('clamp').insertion()
    assert (text, text[start:end]) == ('clamp(x, lo, hi)', 'x')
    text, start, end = _entry('clamp').insertion('`A` * 2')
    assert (text, text[start:end]) == ('clamp(`A` * 2, lo, hi)', 'lo')
    text, start, end = _entry('abs').insertion('`A`')
    assert (text, start, end) == ('abs(`A`)', 8, 8)
    text, start, end = _entry('>=').insertion('`A`')
    assert (text, text[start:end]) == ('`A` >= b', 'b')
    assert _entry('NOT').insertion('`A` > 0')[0] == 'NOT (`A` > 0)'
    assert _entry('pi').insertion() == ('pi', 2, 2)
    assert _entry('round').insertion()[0] == 'round(x)'     # n is optional


def test_search_finds_words_a_user_would_type():
    def found(word):
        return {e.name for e in formula_catalog() if e.matches(word)}
    assert 'rolling_mean' in found('average')
    assert 'clamp' in found('limit')
    assert 'derivative' in found('slope')
    assert found('') == {e.name for e in formula_catalog()}


def test_the_help_window_uses_the_same_category_order():
    from gui.calculated_signal_dialog import _formula_help_examples
    function_categories = {s.category for s in _FUNCTIONS.values()}
    headings = [h for h, _ in _formula_help_examples() if h in function_categories]
    assert headings == [c for c in CATEGORY_ORDER if c in function_categories]
    assert headings[:3] == ['Math', 'Rounding', 'Selection']


# ── The window ───────────────────────────────────────────────────────────


@pytest.fixture()
def picker(qapp):
    from gui.insert_function_dialog import InsertFunctionDialog
    dialog = InsertFunctionDialog()
    yield dialog
    dialog.close()


def test_categories_are_listed_with_all_first(picker):
    names = [picker.category_list.item(i).text() for i in range(picker.category_list.count())]
    assert names == ['All', *CATEGORY_ORDER]
    assert picker.shown_names()[:3] == ['abs', 'sqrt', 'exp']


def test_a_category_shows_its_functions(picker):
    picker.category_list.setCurrentRow(2)                    # Rounding
    assert picker.shown_names() == ['round', 'floor', 'ceil', 'trunc']


def test_search_looks_in_every_category(picker):
    picker.category_list.setCurrentRow(7)                    # Trigonometry
    picker.search_edit.setText('average')
    assert picker.shown_names() == ['rolling_mean']
    picker.search_edit.setText('no such thing')
    assert picker.shown_names() == [] and not picker.insert_button.isEnabled()
    assert 'No function matches' in picker.help_view.toPlainText()
    picker.search_edit.clear()
    assert picker.shown_names()[0] == 'sin'


def test_the_help_shows_how_to_use_the_selected_one(picker):
    picker.search_edit.setText('IF')
    picker.function_list.setCurrentRow(picker.shown_names().index('IF'))
    text = picker.help_view.toPlainText()
    for expected in ('IF(condition, then_value, else_value)', 'then_value',
                     'the result everywhere else', f'IF(`{EXAMPLE_SIGNAL}` > 3000, 1, 0)',
                     'Write IF in capitals', 'Tip: select part of the formula'):
        assert expected in text


def test_insert_and_double_click_hand_over_the_entry_and_close(picker):
    asked = []
    picker.insertRequested.connect(asked.append)
    picker.show()
    picker.category_list.setCurrentRow(3)                    # Selection
    picker.insert_button.click()
    assert [e.name for e in asked] == ['min'] and not picker.isVisible()
    picker.show()
    picker.function_list.itemDoubleClicked.emit(picker.function_list.item(2))
    assert [e.name for e in asked] == ['min', 'clamp']


# ── In the calculated-signal dialog ──────────────────────────────────────


@pytest.fixture()
def dialog(qapp):
    from gui.calculated_signal_dialog import CalculatedSignalDialog
    d = CalculatedSignalDialog([SIGNAL])
    d.name_edit.setText('Result')
    yield d
    d.close()


def _formula(dialog):
    cursor = dialog.formula_edit.textCursor()
    return dialog.formula_edit.toPlainText(), cursor.selectedText()


def test_the_button_sits_beside_the_formula_and_opens_the_window(dialog, qapp):
    from gui.insert_function_dialog import InsertFunctionDialog
    button = dialog.insert_function_button
    assert button.text() == 'ƒx Insert function…'
    assert button.shortcut().toString() == 'Shift+F3'
    assert button.parentWidget() is dialog.help_button.parentWidget()
    button.click()
    qapp.processEvents()
    assert isinstance(dialog._insert_function_dialog, InsertFunctionDialog)
    assert dialog._insert_function_dialog.isVisible()
    dialog._insert_function_dialog.close()


def test_the_shortcut_is_in_help_keyboard_shortcuts():
    from gui.main_window import MainWindow
    assert 'Shift + F3' in dict(MainWindow._SHORTCUTS)


def test_a_function_goes_in_with_its_placeholder_selected(dialog):
    dialog.insert_catalog_entry(_entry('abs'))
    assert _formula(dialog) == ('abs(x)', 'x')
    dialog.signal_list.setCurrentRow(2)                      # under the header and Time
    assert dialog.signal_list.currentItem().text() == SIGNAL
    dialog._insert_selected_signal()
    assert _formula(dialog)[0] == f'abs(`{SIGNAL}`)'
    assert dialog.save_button.isEnabled()


def test_a_selection_becomes_the_first_argument(dialog):
    dialog.formula_edit.setPlainText(f'`{SIGNAL}` / 3.6')
    dialog.formula_edit.selectAll()
    dialog.insert_catalog_entry(_entry('pow'))
    assert _formula(dialog) == (f'pow(`{SIGNAL}` / 3.6, b)', 'b')


def test_spacing_after_existing_text(dialog):
    dialog.formula_edit.setPlainText(f'`{SIGNAL}` *')
    dialog.formula_edit.moveCursor(dialog.formula_edit.textCursor().MoveOperation.End)
    dialog.insert_catalog_entry(_entry('sqrt'))
    assert _formula(dialog)[0] == f'`{SIGNAL}` * sqrt(x)'
    dialog.formula_edit.setPlainText('max(')
    dialog.formula_edit.moveCursor(dialog.formula_edit.textCursor().MoveOperation.End)
    dialog.insert_catalog_entry(_entry('abs'))
    assert _formula(dialog)[0] == 'max(abs(x)'
