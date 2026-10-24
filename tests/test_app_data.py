# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for gui/app_data.py: logs and settings in the user's own folder."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gui import app_data, app_log, crash_log

EARLIER = ['osvanta_user_settings.json', 'osvanta_temp_plot_config.json',
           'busloganalyzer_applog.log', 'busloganalyzer_applog.log.1',
           'BLA_crash.log', 'BLA_crash.log.1']


@pytest.fixture()
def packaged(monkeypatch, tmp_path):
    """A packaged application in its own folder, and a user profile."""
    install = tmp_path / 'install'
    install.mkdir()
    local = tmp_path / 'profile' / 'AppData' / 'Local'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(install / 'BusLogAnalyzer.exe'))
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    for name in (app_data.DATA_DIR_ENV_VAR, 'OSVANTA_APP_LOG', 'OSVANTA_CRASH_LOG'):
        monkeypatch.delenv(name, raising=False)
    return install, local / 'Osvanta' / 'BusLogAnalyzer'


def test_packaged_logs_and_settings_are_in_the_users_folder(packaged):
    install, user = packaged

    assert app_data.folder() == user
    assert app_log.default_log_path() == user / 'logs' / app_log.LOG_NAME
    assert crash_log.default_log_path() == user / 'logs' / crash_log.LOG_NAME
    assert (user / 'logs').is_dir()
    assert list(install.iterdir()) == []


def test_the_first_start_moves_what_earlier_versions_kept_next_to_the_exe(packaged):
    install, user = packaged
    for name in EARLIER:
        (install / name).write_text(name, encoding='utf-8')
    (install / 'BusLogAnalyzer.exe').write_bytes(b'')

    app_data.folder()

    assert [path.name for path in install.iterdir()] == ['BusLogAnalyzer.exe']
    for name in EARLIER:
        moved = user / ('logs' if '.log' in name else '') / name
        assert moved.read_text(encoding='utf-8') == name


def test_a_later_start_moves_nothing_and_overwrites_nothing(packaged):
    install, user = packaged
    app_data.folder()
    (user / 'osvanta_user_settings.json').write_text('current', encoding='utf-8')
    # An earlier version, run again from the same folder.
    (install / 'osvanta_user_settings.json').write_text('earlier', encoding='utf-8')

    app_data.folder()

    assert (user / 'osvanta_user_settings.json').read_text(encoding='utf-8') == 'current'
    assert (install / 'osvanta_user_settings.json').read_text(encoding='utf-8') == 'earlier'


def test_a_folder_the_licence_made_first_does_not_skip_the_move(packaged):
    install, user = packaged
    (user / 'licensing').mkdir(parents=True)
    (install / 'BLA_crash.log').write_text('earlier', encoding='utf-8')

    app_data.folder()

    assert (user / 'logs' / 'BLA_crash.log').read_text(encoding='utf-8') == 'earlier'
    assert not (install / 'BLA_crash.log').exists()


def test_a_settings_file_already_in_the_users_folder_is_never_overwritten(packaged):
    install, user = packaged
    # The logs folder was deleted, so this start moves files again.
    user.mkdir(parents=True)
    (user / 'osvanta_user_settings.json').write_text('current', encoding='utf-8')
    (install / 'osvanta_user_settings.json').write_text('earlier', encoding='utf-8')

    app_data.folder()

    assert (user / 'osvanta_user_settings.json').read_text(encoding='utf-8') == 'current'

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows refuses to move an open file')
def test_a_log_another_running_copy_holds_open_stays(packaged):
    install, user = packaged
    held = install / 'busloganalyzer_applog.log'
    held.write_text('running', encoding='utf-8')
    (install / 'osvanta_user_settings.json').write_text('{}', encoding='utf-8')

    with held.open('a', encoding='utf-8'):
        app_data.folder()

    assert held.read_text(encoding='utf-8') == 'running'
    assert not (user / 'logs' / 'busloganalyzer_applog.log').exists()
    assert (user / 'osvanta_user_settings.json').exists()


def test_it_department_can_name_the_folder(packaged, monkeypatch, tmp_path):
    central = tmp_path / 'central'
    monkeypatch.setenv(app_data.DATA_DIR_ENV_VAR, str(central))

    assert app_data.folder() == central
    assert app_log.default_log_path() == central / 'logs' / app_log.LOG_NAME


def test_from_source_everything_stays_next_to_app_py(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    for name in (app_data.DATA_DIR_ENV_VAR, 'OSVANTA_APP_LOG', 'OSVANTA_CRASH_LOG'):
        monkeypatch.delenv(name, raising=False)
    root = Path(app_data.__file__).resolve().parents[1]

    assert app_data.folder() == root
    assert app_log.default_log_path() == root / app_log.LOG_NAME
    assert crash_log.default_log_path() == root / crash_log.LOG_NAME
