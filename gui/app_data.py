# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

r"""Where the application keeps its logs and settings.

The packaged application keeps them per user::

    %LOCALAPPDATA%\Osvanta\BusLogAnalyzer\
        osvanta_user_settings.json      preferences
        osvanta_temp_plot_config.json   the last plot layout
        logs\
            busloganalyzer_applog.log   the Log panel, warnings, errors
            BLA_crash.log               why the application ended

The logs name every measurement and database opened, and the plot layout
names their signals. Next to the executable, anyone who could read that
folder could read them: a shared tools folder or a network share is common.
Windows lets only the user, and administrators, read their own
%LOCALAPPDATA%. It is also writable without elevation, so an installation in
Program Files no longer sends the logs to the temp folder.

OSVANTA_DATA_DIR names another folder, for an IT department that keeps user
data somewhere central. Run from source, everything stays next to app.py,
where developers look for it.

The first start that uses the folder moves in the files earlier versions
kept next to the executable, so neither the settings nor the last logs are
lost, and none stay behind where others can read them.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

DATA_DIR_ENV_VAR = 'OSVANTA_DATA_DIR'

_VENDOR = 'Osvanta'
_PRODUCT = 'BusLogAnalyzer'
_LOGS = 'logs'

# What versions up to v00.02.02 kept next to the executable, and the folder
# (relative to the data folder) each now belongs in.
_EARLIER_FILES = {
    'osvanta_user_settings.json': '',
    'osvanta_temp_plot_config.json': '',
    'busloganalyzer_applog.log': _LOGS,
    'busloganalyzer_applog.log.1': _LOGS,
    'BLA_crash.log': _LOGS,
    'BLA_crash.log.1': _LOGS,
}


def _executable_folder() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _per_user_folder() -> Path:
    if os.environ.get(DATA_DIR_ENV_VAR):
        return Path(os.environ[DATA_DIR_ENV_VAR])
    local = os.environ.get('LOCALAPPDATA')
    root = Path(local) if local else Path.home() / 'AppData' / 'Local'
    return root / _VENDOR / _PRODUCT


def folder() -> Path:
    """The settings folder, created, with earlier versions' files moved in.

    Next to app.py when run from source, unless OSVANTA_DATA_DIR is set.
    """
    if not getattr(sys, 'frozen', False) and not os.environ.get(DATA_DIR_ENV_VAR):
        return _executable_folder()
    path = _per_user_folder()
    # The logs folder marks the first start, not the folder itself: the
    # licence and add-on folders sit in it too, made by whichever came first.
    if not (path / _LOGS).is_dir():
        try:
            (path / _LOGS).mkdir(parents=True, exist_ok=True)
        except OSError:
            return path  # opening a file there fails, and the caller copes
        if getattr(sys, 'frozen', False):
            _move_earlier_files(_executable_folder(), path)
    return path


def logs_folder() -> Path:
    """The logs' folder, created; next to app.py when run from source."""
    base = folder()
    if base == _executable_folder():
        return base
    path = base / _LOGS
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _move_earlier_files(source: Path, target: Path) -> None:
    """Move what an earlier version left next to the executable. Never raises.

    A file another running copy holds open stays where it is. A move, not a
    rename: the executable may be on another drive than %LOCALAPPDATA%.
    """
    for name, subfolder in _EARLIER_FILES.items():
        old = source / name
        new = target / subfolder / name
        try:
            if not old.is_file() or new.exists():
                continue
            old.replace(new)
        except OSError:
            # Another drive, or held open: copy, and keep the copy only
            # if the original goes, so that each file is in one place.
            try:
                shutil.copy2(old, new)
                old.unlink()
            except OSError:
                new.unlink(missing_ok=True)
