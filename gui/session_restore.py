# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The last session: the configuration the application was closed with, kept so
that the next start can offer it back.

On close, a session that holds something and differs from the configuration
last saved or opened asks Save / Don't save / Cancel. Unless the answer is
Cancel, the session is written to osvanta_last_session.json in the settings
folder. While the user works it is rewritten once a minute when it has
changed, so a crash loses at most a minute of it. Every start that finds the
file offers to reopen it.

The file is a configuration as File ▸ Save configuration writes it, plus a
'last_session' entry naming the file it was saved as, if any, so File ▸ Open
configuration opens it too. It names the measurement, the databases and the
plotted signals, so it is kept where the logs are, in the user's own folder.

OSVANTA_SESSION_RESTORE=0 turns all of it off, and a release-test run neither
asks nor writes: its timings and its dialog answering must not meet a
question that only some starts show.
"""
from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QWidget

from gui import release_test

FILE_NAME = 'osvanta_last_session.json'
DISABLE_VARIABLE = 'OSVANTA_SESSION_RESTORE'
REFRESH_INTERVAL_MS = 60_000
# After the window has painted, and ahead of the survey request, which waits
# for the next start when it finds this question open.
OFFER_DELAY_MS = 500
_SESSION_KEY = 'last_session'
# Not something the user changed: each version writes its own number.
_IGNORED_KEYS = frozenset({'version', _SESSION_KEY})


class CloseAnswer(Enum):
    SAVE = 'save'
    DISCARD = 'discard'
    CANCEL = 'cancel'


class OfferAnswer(Enum):
    REOPEN = 'reopen'
    LATER = 'later'


def enabled(argv: list[str]) -> bool:
    """Whether this run asks on close, keeps the session and offers it."""
    return (os.environ.get(DISABLE_VARIABLE, '1') != '0'
            and release_test.SCENARIO_FLAG not in argv)


def as_saved(config: dict) -> dict:
    """*config* as a file holds it once read back: lists for tuples, string
    keys. Compare configurations only in this form."""
    return json.loads(json.dumps(config))


def holds_something(config: dict) -> bool:
    """Whether *config* is worth keeping: a measurement, plotted signals or
    calculated signals."""
    return bool(config.get('measurement_path') or config.get('signals')
                or config.get('generated_signals'))


def differs(config: dict, snapshot: dict | None) -> bool:
    """Whether *config* holds a change from *snapshot*, the configuration last
    saved or opened (None when there is none). A key the snapshot lacks was
    written by a later version than the file's and does not count."""
    if snapshot is None:
        return True
    current = as_saved(config)
    return any(
        value != snapshot[key]
        for key, value in current.items()
        if key not in _IGNORED_KEYS and key in snapshot
    )


def read(path: Path) -> dict | None:
    """The last session kept at *path*; None when there is none. Raises
    OSError when it cannot be read and ValueError when it is not a
    configuration."""
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f'{path.name} holds no configuration')
    return data


def write(path: Path, config: dict, saved_to: str | None) -> None:
    """Keep *config* at *path* as the last session, saved as *saved_to* or
    not saved. Written beside it and then moved over it, so a crash part-way
    leaves the previous session whole. Raises OSError."""
    data = dict(config)
    data[_SESSION_KEY] = {'saved_to': saved_to}
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def saved_to(data: dict) -> str | None:
    """The file the last session in *data* was saved as; None when it was not
    saved."""
    session = data.get(_SESSION_KEY)
    where = session.get('saved_to') if isinstance(session, dict) else None
    return where if isinstance(where, str) and where else None


def summary(data: dict) -> str:
    """What the last session in *data* holds, in a few lines for the user."""
    measurement = data.get('measurement_path') or data.get('blf_path')
    signals = data.get('signals')
    count = len(signals) if isinstance(signals, list) else 0
    where = saved_to(data)
    return '\n'.join((
        f'Measurement: {Path(measurement).name if measurement else "none"}',
        f'Plotted signals: {count}',
        f'Saved as: {Path(where).name}' if where else 'Not saved',
    ))


def ask_to_save(parent: QWidget | None) -> CloseAnswer:
    """Ask, on close, whether to save the configuration. Closing the question
    counts as Cancel."""
    box = QMessageBox(
        QMessageBox.Icon.Question,
        'Save configuration',
        'Save the configuration before closing?',
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel,
        parent,
    )
    box.setInformativeText(
        'It holds the measurement, the databases and the plotted signals. '
        "If you don't save it, the next start still offers to reopen this session."
    )
    box.button(QMessageBox.StandardButton.Discard).setText("Don't save")
    box.setDefaultButton(QMessageBox.StandardButton.Save)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    clicked = box.standardButton(box.clickedButton()) if box.clickedButton() else None
    if clicked == QMessageBox.StandardButton.Save:
        return CloseAnswer.SAVE
    if clicked == QMessageBox.StandardButton.Discard:
        return CloseAnswer.DISCARD
    return CloseAnswer.CANCEL


def ask_to_reopen(parent: QWidget | None, held: str) -> OfferAnswer:
    """Offer, at start, to reopen the last session, which holds *held*.
    Closing the question counts as Not now."""
    box = QMessageBox(QMessageBox.Icon.Question, 'Last session',
                      'Reopen the last session?', parent=parent)
    box.setInformativeText(f'{held}\n\nThe measurement is read and decoded again.')
    reopen = box.addButton('Reopen', QMessageBox.ButtonRole.AcceptRole)
    later = box.addButton('Not now', QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(reopen)
    box.setEscapeButton(later)
    box.exec()
    return OfferAnswer.REOPEN if box.clickedButton() is reopen else OfferAnswer.LATER
