# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Saved signal selections in Convert files ▸ Choose signals: saved by name in
the user settings, matched by signal name (patterns allowed) on any file,
read from and written to text files, and the last one offered again.

Requires a Qt platform plugin for the picker tests; skipped (not failed) if
none is available.
"""
from __future__ import annotations

import json

import pytest

from core.signal_selection import (apply_selection, read_selection, selection_of,
                                   write_selection)

NAMES = ['EngSpeed', 'VehSpeed', 'engtemp', 'Gear', 'EngSpeed']


# ── Matching ─────────────────────────────────────────────────────────────


def test_names_match_whole_and_ignoring_case():
    picked, missing = apply_selection(['vehspeed', 'Eng'], NAMES)
    assert (picked, missing) == ([1], ['Eng'])          # no partial matches


def test_patterns_match_and_every_hit_counts():
    picked, missing = apply_selection(['Eng*', 'Nothing?'], NAMES)
    assert picked == [0, 2, 4]                            # both EngSpeeds, engtemp
    assert missing == ['Nothing?']


def test_blank_entries_are_ignored():
    assert apply_selection(['', '  ', 'Gear'], NAMES) == ([3], [])


def test_a_saved_selection_names_each_signal_once():
    assert selection_of(['A', 'B', 'A', '', 'C']) == ['A', 'B', 'C']


def test_list_files_skip_blank_lines_and_comments(tmp_path):
    path = tmp_path / 'list.txt'
    path.write_text('﻿# powertrain\nEngSpeed\n\n  Veh*  \n', encoding='utf-8')
    assert read_selection(path) == ['EngSpeed', 'Veh*']
    write_selection(path, ['A', 'B'])
    assert path.read_text(encoding='utf-8') == 'A\nB\n'


# ── The picker ───────────────────────────────────────────────────────────


SIGNALS = [('EngSpeed', 1, 1, 'Engine'), ('EngTemp', 1, 2, 'Engine'),
           ('VehSpeed', 2, 1, 'Vehicle'), ('Gear', 3, 1, 'Gearbox')]


@pytest.fixture()
def settings(tmp_path):
    return tmp_path / 'settings.json'


def _picker(settings, chosen=()):
    from gui.converter_dialog import SignalPicker
    return SignalPicker(SIGNALS, chosen=list(chosen), settings_path=settings)


def _ticked(picker):
    return sorted(name for name, _g, _i in picker.chosen())


def test_save_a_selection_by_name(qapp, settings, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    picker = _picker(settings, chosen=[('EngSpeed', 1, 1), ('Gear', 3, 1)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Powertrain', True))
    picker.save_selection.click()
    saved = json.loads(settings.read_text(encoding='utf-8'))
    assert saved['converter_selections'] == {'Powertrain': ['EngSpeed', 'Gear']}
    assert saved['converter_last_selection'] == 'Powertrain'
    assert picker.selection.currentText() == 'Powertrain'
    assert picker.applied.text() == 'Saved "Powertrain": 2 signals'


def test_choosing_a_selection_ticks_exactly_its_signals(qapp, settings):
    settings.write_text(json.dumps({'converter_selections': {
        'Speeds': ['*Speed', 'Brake']}}), encoding='utf-8')
    picker = _picker(settings, chosen=[('Gear', 3, 1)])
    assert picker.selection.currentText() == '—'
    assert _ticked(picker) == ['Gear']                    # nothing applied yet
    picker.selection.setCurrentText('Speeds')
    assert _ticked(picker) == ['EngSpeed', 'VehSpeed']
    assert picker.applied.text() == 'Applied "Speeds": 1 of 2 found · 1 not in this file'
    assert 'Brake' in picker.applied.toolTip()
    assert picker.count.text() == '2 of 4 ticked'
    saved = json.loads(settings.read_text(encoding='utf-8'))
    assert saved['converter_last_selection'] == 'Speeds'         # offered next time


def test_the_last_selection_is_applied_when_nothing_is_ticked(qapp, settings):
    settings.write_text(json.dumps({'converter_selections': {'Gear only': ['gear']},
                                    'converter_last_selection': 'Gear only'}),
                        encoding='utf-8')
    fresh = _picker(settings)
    assert fresh.selection.currentText() == 'Gear only'
    assert _ticked(fresh) == ['Gear']
    chosen = _picker(settings, chosen=[('EngTemp', 1, 2)])
    assert chosen.selection.currentText() == 'Gear only'
    assert _ticked(chosen) == ['EngTemp']                 # the user's ticks win


def test_delete_a_selection(qapp, settings):
    settings.write_text(json.dumps({'converter_selections': {'A': ['Gear'], 'B': ['x']},
                                    'converter_last_selection': 'A'}), encoding='utf-8')
    picker = _picker(settings, chosen=[('Gear', 3, 1)])
    assert picker.delete_selection.isEnabled()
    picker.delete_selection.click()
    saved = json.loads(settings.read_text(encoding='utf-8'))
    assert saved['converter_selections'] == {'B': ['x']}
    assert saved['converter_last_selection'] is None
    assert [picker.selection.itemText(i) for i in range(picker.selection.count())] == ['—', 'B']
    assert not picker.delete_selection.isEnabled()


def test_import_and_export_a_text_list(qapp, settings, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    source = tmp_path / 'team.txt'
    source.write_text('# shared\nVehSpeed\nGEAR\nMissing\n', encoding='utf-8')
    picker = _picker(settings)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(source), ''))
    picker.import_selection.click()
    assert _ticked(picker) == ['Gear', 'VehSpeed']
    assert picker.applied.text() == 'Applied "team.txt": 2 of 3 found · 1 not in this file'
    target = tmp_path / 'out.txt'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    picker.export_selection.click()
    assert target.read_text(encoding='utf-8') == 'VehSpeed\nGear\n'


def test_nothing_ticked_saves_and_exports_nothing(qapp, settings, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    asked = []
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: asked.append(1))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: asked.append(2))
    picker = _picker(settings)
    picker.save_selection.click()
    picker.export_selection.click()
    assert asked == [] and not settings.exists()


def test_without_settings_the_picker_still_works(qapp):
    from gui.converter_dialog import SignalPicker
    picker = SignalPicker(SIGNALS, chosen=[])
    assert picker.saved_selections() == {}
    picker.apply_entries(['Gear'], 'a list')
    assert _ticked(picker) == ['Gear']


def test_the_converter_passes_its_settings_on(qapp, settings, monkeypatch, tmp_path):
    from gui import converter_dialog
    seen = {}

    class Picker:
        def __init__(self, signals, chosen, parent=None, *, settings_path=None):
            seen['settings_path'] = settings_path

        def exec(self):
            return False

    monkeypatch.setattr(converter_dialog, 'SignalPicker', Picker)
    monkeypatch.setattr(converter_dialog, 'list_signals', lambda path: [])
    dialog = converter_dialog.ConverterDialog(settings_path=settings)
    dialog.set_inputs([tmp_path / 'a.mf4'])
    dialog.pick_signals()
    assert seen == {'settings_path': settings}


def test_the_main_window_gives_the_converter_its_settings(qapp, monkeypatch, tmp_path):
    from gui import app_data
    from gui.main_window import MainWindow
    monkeypatch.setenv(app_data.DATA_DIR_ENV_VAR, str(tmp_path))
    window = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    try:
        window.show_converter()
        assert window._converter_dialog._settings_path == window._user_settings_path
        window._converter_dialog.close()
    finally:
        window.close()
        window.deleteLater()
