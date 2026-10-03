# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A silent record of what the application said, in a file the user can send.

The application opens no console window: the packaged build is windowed, and
run_dev.bat starts it with pythonw. ``AppLog`` appends to ``osvanta_app.log``
next to the executable (next to app.py when run from source):

- Every line of the Log panel.
- Where the application has no console, everything a console would have
  shown: Python's warnings, a library's logging, every Qt message (the crash
  log keeps only the first of each kind) and the traceback of an uncaught
  exception. Run from a terminal, these stay on the terminal.

Each line starts with the time it was written, each session with a ``====``
header. Why the application ended is the crash log's business
(gui/crash_log.py), which records what this log cannot: a native crash, a Qt
fatal error and a frozen window.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

LOG_NAME = 'osvanta_app.log'
# Rotated at start-up, so a log never grows past this by more than a session.
MAX_BYTES = 1_000_000
# A message repeated many times a second must not fill the disk.
MAX_SESSION_BYTES = 5_000_000

_PREAMBLE = (
    'Osvanta Bus Log Analyzer app log.\n'
    "The Log panel's lines and, where the application has no console, what a\n"
    'console would have shown. Each session starts with a ==== header.\n'
    'Why the application ended is recorded in osvanta_crash.log.\n'
)

_running: AppLog | None = None


def default_log_path() -> Path:
    """Next to the executable when packaged, next to app.py from source.

    OSVANTA_APP_LOG names another file: tests that start the application
    keep the developer's own log clean with it.
    """
    if os.environ.get('OSVANTA_APP_LOG'):
        return Path(os.environ['OSVANTA_APP_LOG'])
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / LOG_NAME
    return Path(__file__).resolve().parents[1] / LOG_NAME


def record(message: str) -> None:
    """One line in the running log; nothing when none runs, as in tests."""
    if _running is not None:
        _running.write(message + '\n')


def log_path() -> Path | None:
    """The running log's file, or None when none runs."""
    return _running.path if _running is not None else None


class _Console(io.TextIOBase):
    """sys.stdout or sys.stderr where the application has no console."""

    def __init__(self, log: AppLog) -> None:
        super().__init__()
        self._log = log

    @property
    def encoding(self) -> str:
        return 'utf-8'

    @property
    def errors(self) -> str:
        return 'replace'

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._log.write(text)
        return len(text)


class AppLog:
    """Open the log, and stand in for a missing console until close().

    Create it once at start-up, after the crash log, which takes over the C
    runtime's stderr only while sys.stderr is missing. Keep it alive until
    exit.
    """

    def __init__(self, app_name: str, app_version: str, path: Path | None = None) -> None:
        global _running
        self._lock = threading.Lock()
        self._at_line_start = True
        self._room = MAX_SESSION_BYTES
        self.path, self._file = self._open(path or default_log_path())
        self._put(
            f'\n==== {time.strftime("%Y-%m-%d %H:%M:%S")}  '
            f'{app_name} {app_version}  pid {os.getpid()} ====\n'
        )
        self._replaced = [name for name in ('stdout', 'stderr') if getattr(sys, name) is None]
        for name in self._replaced:
            setattr(sys, name, _Console(self))
        _running = self

    def close(self) -> None:
        """Give back the console streams and close the file."""
        global _running
        for name in self._replaced:
            if isinstance(getattr(sys, name), _Console):
                setattr(sys, name, None)
        if _running is self:
            _running = None
        with self._lock:
            self._file.close()

    @staticmethod
    def _open(path: Path):
        # The executable's folder may be read-only (Program Files, a network
        # share); the temp folder always takes the log.
        for candidate in (path, Path(tempfile.gettempdir()) / LOG_NAME):
            try:
                if candidate.exists() and candidate.stat().st_size > MAX_BYTES:
                    candidate.replace(candidate.with_name(candidate.name + '.1'))
            except OSError:
                pass  # another running instance holds it open: append to it
            try:
                new = not candidate.exists()
                handle = candidate.open('a', encoding='utf-8', errors='replace')
            except OSError:
                continue
            if new:
                handle.write(_PREAMBLE)
            return candidate, handle
        raise OSError(f'Cannot open an app log at {path} or in the temp folder')

    def write(self, text: str) -> None:
        """Append text, each line after the time it was written."""
        stamp = time.strftime('%H:%M:%S ')
        # A timeout, never a deadlock: a thread may have died holding it.
        locked = self._lock.acquire(timeout=2)
        try:
            pieces = []
            for line in text.splitlines(keepends=True):
                if self._at_line_start:
                    pieces.append(stamp)
                pieces.append(line)
                self._at_line_start = line.endswith(('\n', '\r'))
            self._put(''.join(pieces))
        finally:
            if locked:
                self._lock.release()

    def _put(self, text: str) -> None:
        if not text or self._room <= 0:
            return
        if len(text) > self._room:
            text = (
                f'\n{time.strftime("%H:%M:%S")} The log stops here for this session: '
                f'it wrote more than {MAX_SESSION_BYTES / 1_000_000:g} MB.\n'
            )
            self._room = 0
        else:
            self._room -= len(text)
        try:
            self._file.write(text)
            self._file.flush()
        except (OSError, ValueError):
            pass
