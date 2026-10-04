# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The application opens no console; what one would show goes to a log file.

The packaged build is windowed and run_dev.bat starts it with pythonw, so
Python has no stdout or stderr. A warning, a library's logging, a Qt message
or a print was lost there, and a source run showed a console window beside
the application. The app log keeps them, with every line of the Log panel.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from gui import app_log
from gui.app_log import MAX_BYTES, AppLog

REPO_ROOT = Path(__file__).resolve().parents[1]
STAMP = r'\d\d:\d\d:\d\d '


def _session(text: str) -> str:
    """The last session's lines, without the file's explanatory preamble."""
    return text.rsplit('\n==== ', 1)[1]


def _start(path: Path) -> AppLog:
    return AppLog('Osvanta Bus Log Analyzer', 'v00.00.99', path)


_APP_SCRIPT = r"""
import sys
sys.stdout = sys.stderr = None  # as under pythonw and in the packaged build
import logging, warnings
from pathlib import Path
from PySide6.QtCore import qWarning
from PySide6.QtWidgets import QApplication
import gui.crash_log
gui.crash_log.default_log_path = lambda: Path(sys.argv[1])
import gui.app_log
gui.app_log.default_log_path = lambda: Path(sys.argv[2])
import app as entry

def run(self):
    print("printed by the application")
    warnings.warn("a library's warning", RuntimeWarning)
    logging.getLogger("some.library").warning("a library's logging")
    qWarning("the same Qt warning")
    qWarning("the same Qt warning")
    return 0

QApplication.exec = run
entry.main()
"""


def test_without_a_console_the_application_writes_its_output_here(tmp_path):
    crash_log, log = tmp_path / 'crash.log', tmp_path / 'app.log'
    completed = subprocess.run(
        [sys.executable, '-c', _APP_SCRIPT, str(crash_log), str(log)],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120,
    )

    assert completed.returncode == 0, crash_log.read_text(encoding='utf-8')[-4000:]
    assert completed.stdout == ''
    session = _session(log.read_text(encoding='utf-8'))
    assert re.match(r'\S+ \S+  Osvanta Bus Log Analyzer v\S+  pid \d+ ====\n', session)
    # The Log panel's lines.
    assert re.search(rf'^{STAMP}Osvanta Bus Log Analyzer v\S+ started\.$', session, re.M)
    assert f'App log file: {log}' in session
    # What a console would have shown.
    assert re.search(rf'^{STAMP}printed by the application$', session, re.M)
    assert "RuntimeWarning: a library's warning" in session
    assert "a library's logging" in session
    assert session.count('the same Qt warning') == 2

    # The crash log keeps the first of each Qt warning and none of the rest.
    crashes = _session(crash_log.read_text(encoding='utf-8'))
    assert crashes.count('the same Qt warning') == 1
    assert 'printed by the application' not in crashes
    assert crashes.rstrip().endswith('Session ended normally.')


def test_with_a_console_its_output_stays_there(tmp_path, capsys):
    path = tmp_path / 'app.log'
    log = _start(path)
    try:
        print('on the console')
        app_log.record('a Log panel line')
    finally:
        log.close()

    assert 'on the console' in capsys.readouterr().out
    session = _session(path.read_text(encoding='utf-8'))
    assert 'on the console' not in session
    assert re.search(rf'^{STAMP}a Log panel line$', session, re.M)


def test_the_console_streams_are_given_back(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    path = tmp_path / 'app.log'
    log = _start(path)
    try:
        print('to stdout')
        print('to stderr', file=sys.stderr)
    finally:
        log.close()

    assert sys.stdout is None and sys.stderr is None
    session = _session(path.read_text(encoding='utf-8'))
    assert re.search(rf'^{STAMP}to stdout\n{STAMP}to stderr$', session, re.M)


def test_each_line_starts_with_the_time_it_was_written(tmp_path):
    path = tmp_path / 'app.log'
    log = _start(path)
    try:
        log.write('one line, ')
        log.write('written in two parts\nand a second line\n')
        app_log.record('a Log panel line\nof two lines')
    finally:
        log.close()

    lines = _session(path.read_text(encoding='utf-8')).splitlines()[1:]
    assert len(lines) == 4
    assert all(re.match(STAMP, line) for line in lines)
    assert [line[9:] for line in lines] == [
        'one line, written in two parts', 'and a second line',
        'a Log panel line', 'of two lines',
    ]


def test_nothing_is_written_while_no_log_runs(tmp_path):
    log = _start(tmp_path / 'app.log')
    assert app_log.log_path() == tmp_path / 'app.log'
    log.close()

    app_log.record('after the log has closed')
    assert app_log.log_path() is None
    assert 'after the log has closed' not in (tmp_path / 'app.log').read_text(encoding='utf-8')


def test_an_oversized_log_is_rotated(tmp_path):
    path = tmp_path / 'app.log'
    path.write_text('x' * (MAX_BYTES + 1), encoding='utf-8')

    _start(path).close()

    assert (tmp_path / 'app.log.1').stat().st_size == MAX_BYTES + 1
    assert path.read_text(encoding='utf-8').startswith('Osvanta Bus Log Analyzer app log.')


def test_a_log_another_instance_holds_open_is_still_appended_to(tmp_path):
    # Windows refuses to rename a file open elsewhere: a second window of the
    # application writes on in the same log, not in the temp folder.
    path = tmp_path / 'app.log'
    path.write_text('x' * (MAX_BYTES + 1), encoding='utf-8')

    with path.open('a', encoding='utf-8'):
        log = _start(path)
        log.close()

    assert log.path == path
    assert 'Osvanta Bus Log Analyzer v00.00.99' in _session(path.read_text(encoding='utf-8'))


def test_a_flood_of_messages_stops_at_the_session_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(app_log, 'MAX_SESSION_BYTES', 2_000)
    path = tmp_path / 'app.log'
    log = _start(path)
    try:
        for _ in range(100):
            app_log.record('the same message, many times a second ' * 2)
    finally:
        log.close()

    session = _session(path.read_text(encoding='utf-8'))
    assert len(session) < 2_200
    assert session.rstrip().endswith('wrote more than 0.002 MB.')
    assert session.count('The log stops here for this session') == 1
