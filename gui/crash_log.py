# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Record why the application died, in a file the user can send.

The packaged application has no console, so each way it can end badly used to
leave nothing behind. A Qt fatal error ("QThread: Destroyed while thread is
still running" is one) ends the process through Windows fail-fast, which no
Python handler sees. An exception in a slot or a worker thread is printed to
a stderr that does not exist. A native crash leaves only an Event Viewer entry
with a module offset, and a frozen window leaves nothing at all.

``CrashLog`` appends each of these to ``osvanta_crash.log`` next to the
executable (next to app.py when run from source):

- Qt fatal and critical messages, a fatal one followed by every thread's
  Python stack. Qt ends the process as soon as the handler returns, so this
  is the only chance to record it.
- The first occurrence of each distinct Qt warning, with the Python calls
  that led to it: Qt's own message rarely says which code caused it.
- Uncaught Python exceptions, on the GUI thread and on any other thread.
- Native crashes such as an access violation, through faulthandler.
- Python's own fatal errors, which it writes to the C runtime's stderr: a
  windowed build has no console to show them.
- Every thread's stack once the GUI thread has not responded for
  ``freeze_seconds``, under the heading ``Timeout (0:00:20)!``.

Each session starts with a header line, which ends ``memory checks on`` in
the memory-checked build, and, when it exits normally, ends with
``Session ended normally.`` A header without that line is a session that
crashed, froze or was killed, even when nothing else was recorded. A session
that exits while a window still runs a thread ends with a warning instead:
Qt ends the process when shutdown destroys that window.
"""

from __future__ import annotations

import atexit
import faulthandler
import os
import platform
import sys
import tempfile
import threading
import time
import traceback
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication, QObject, QThread, QTimer, QtMsgType,
    qInstallMessageHandler, qVersion,
)

LOG_NAME = 'osvanta_crash.log'
FREEZE_SECONDS = 20.0
# Rotated at start-up, so a log never grows past this by more than a session.
MAX_BYTES = 1_000_000
# Warnings that embed addresses are all distinct; cap them per session.
MAX_DISTINCT_WARNINGS = 50

_PREAMBLE = (
    'Osvanta Bus Log Analyzer crash log.\n'
    'Each session starts with a ==== header. A session without a closing\n'
    '"Session ended normally." line crashed, froze or was killed.\n'
    '"Timeout (...)!" lists every thread while the window was frozen.\n'
)


def default_log_path() -> Path:
    """Next to the executable when packaged, next to app.py from source.

    OSVANTA_CRASH_LOG names another file: tests that start the application
    keep the developer's own log clean with it.
    """
    if os.environ.get('OSVANTA_CRASH_LOG'):
        return Path(os.environ['OSVANTA_CRASH_LOG'])
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / LOG_NAME
    return Path(__file__).resolve().parents[1] / LOG_NAME


def _os_name() -> str:
    """platform.platform(), without the WMI query that costs Windows 130 ms."""
    if sys.platform != 'win32':
        return platform.platform()
    version = sys.getwindowsversion()
    release = '11' if (version.major, version.build) >= (10, 22000) else str(version.major)
    return f'Windows-{release}-{version.major}.{version.minor}.{version.build}'


def _capture_native_stderr(path: Path) -> None:
    """Append the C runtime's stderr to the log when there is no console.

    Python reports its own fatal errors there, for example the memory
    allocator's "bad trailing pad byte" when a buffer was overrun. The
    packaged application is windowed, so without this the report is lost.
    The stdio API set is the C runtime python312.dll itself writes through.
    """
    if sys.platform != 'win32' or sys.stderr is not None:
        return
    try:
        import ctypes
        crt = ctypes.CDLL('api-ms-win-crt-stdio-l1-1-0')
        crt.__acrt_iob_func.restype = ctypes.c_void_p
        crt.__acrt_iob_func.argtypes = [ctypes.c_uint]
        crt._wfreopen.restype = ctypes.c_void_p
        crt._wfreopen.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p]
        crt.setvbuf.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_size_t]
        stream = crt.__acrt_iob_func(2)
        if crt._wfreopen(str(path), 'a', stream):
            crt.setvbuf(stream, None, 4, 0)  # _IONBF: nothing waits in a buffer
    except (OSError, AttributeError):
        pass


class CrashLog(QObject):
    """Install every recorder for the rest of the session.

    Create it on the GUI thread right after the application object, before
    anything that can fail, and keep it alive until exit.
    """

    def __init__(
        self,
        app: QCoreApplication,
        path: Path | None = None,
        freeze_seconds: float = FREEZE_SECONDS,
    ) -> None:
        super().__init__(app)
        self._lock = threading.Lock()
        self._seen_warnings: set[str] = set()
        self._freeze_seconds = freeze_seconds
        self.path, self._file = self._open(path or default_log_path())
        self._write(
            f'\n==== {time.strftime("%Y-%m-%d %H:%M:%S")}  '
            f'{app.applicationName()} {app.applicationVersion()}  '
            f'pid {os.getpid()}  Python {platform.python_version()}  '
            f'Qt {qVersion()}  {_os_name()}'
            f'{"  memory checks on" if sys.flags.dev_mode else ""} ====\n'
        )

        _capture_native_stderr(self.path)
        faulthandler.enable(file=self._file, all_threads=True)
        self._previous_qt_handler = qInstallMessageHandler(self._on_qt_message)
        self._previous_excepthook = sys.excepthook
        sys.excepthook = self._on_exception
        self._previous_thread_excepthook = threading.excepthook
        threading.excepthook = self._on_thread_exception

        # The GUI thread beats while its event loop runs; the watchdog thread
        # writes every stack once the beats have stopped for freeze_seconds.
        self._last_beat = time.monotonic()
        self._stopped = threading.Event()
        self._heartbeat = QTimer(self)
        self._heartbeat.timeout.connect(self._beat)
        self._heartbeat.start(int(min(1.0, freeze_seconds / 4) * 1000))
        threading.Thread(
            target=self._watch_for_freeze, name='CrashLog freeze watchdog',
            daemon=True,
        ).start()
        # atexit, not aboutToQuit: closing the window destroys its threads
        # after the event loop has returned, and that can still be fatal.
        atexit.register(self._on_exit)

    @staticmethod
    def _open(path: Path):
        # The executable's folder may be read-only (Program Files, a network
        # share); the temp folder always takes the log.
        for candidate in (path, Path(tempfile.gettempdir()) / LOG_NAME):
            try:
                if candidate.exists() and candidate.stat().st_size > MAX_BYTES:
                    candidate.replace(candidate.with_name(candidate.name + '.1'))
                new = not candidate.exists()
                handle = candidate.open('a', encoding='utf-8', errors='replace')
            except OSError:
                continue
            if new:
                handle.write(_PREAMBLE)
            return candidate, handle
        raise OSError(f'Cannot open a crash log at {path} or in the temp folder')

    def _write(self, text: str) -> None:
        # A timeout, never a deadlock: a fatal message must not wait forever
        # on a thread that died holding the lock.
        locked = self._lock.acquire(timeout=2)
        try:
            self._file.write(text)
            self._file.flush()
        except (OSError, ValueError):
            pass
        finally:
            if locked:
                self._lock.release()

    def _dump_all_threads(self) -> None:
        self._write('Python stacks of all threads (most recent call first):\n')
        try:
            faulthandler.dump_traceback(file=self._file, all_threads=True)
        except (OSError, ValueError):
            pass

    def _on_qt_message(self, mode, context, message: str) -> None:
        stamp = time.strftime('%H:%M:%S')
        if mode == QtMsgType.QtFatalMsg:
            self._write(f'{stamp} Qt fatal error, the application is terminated: {message}\n')
            self._dump_all_threads()
        elif mode == QtMsgType.QtCriticalMsg:
            self._write(f'{stamp} Qt critical: {message}\n{self._raised_from()}')
        elif (mode == QtMsgType.QtWarningMsg
              and message not in self._seen_warnings
              and len(self._seen_warnings) < MAX_DISTINCT_WARNINGS):
            self._seen_warnings.add(message)
            self._write(
                f'{stamp} Qt warning (first of its kind): {message}\n'
                f'{self._raised_from()}'
            )
        # Keep what a source run prints to its console unchanged.
        if self._previous_qt_handler is not None:
            self._previous_qt_handler(mode, context, message)
        elif sys.stderr is not None:
            try:
                print(message, file=sys.stderr)
            except (OSError, ValueError):
                pass

    @staticmethod
    def _raised_from() -> str:
        """The Python calls on this thread that led to a Qt message."""
        # Without this method and the message handler that called it.
        frames = traceback.format_stack()[:-2][-12:]
        if not frames:
            return '  Raised by Qt itself, outside any Python call.\n'
        return '  Raised from (most recent call last):\n' + ''.join(
            f'  {line}\n' for frame in frames for line in frame.rstrip('\n').split('\n')
        )

    def _write_exception(self, where: str, exc_type, exc, tb) -> None:
        self._write(
            f'{time.strftime("%H:%M:%S")} Uncaught exception in {where}:\n'
            + ''.join(traceback.format_exception(exc_type, exc, tb))
        )

    def _on_exception(self, exc_type, exc, tb) -> None:
        self._write_exception(
            f'thread {threading.current_thread().name}', exc_type, exc, tb,
        )
        try:
            self._previous_excepthook(exc_type, exc, tb)
        except Exception:
            pass  # a windowed build has no stderr to print to

    def _on_thread_exception(self, args) -> None:
        name = args.thread.name if args.thread is not None else '?'
        self._write_exception(
            f'thread {name}', args.exc_type, args.exc_value, args.exc_traceback,
        )
        try:
            self._previous_thread_excepthook(args)
        except Exception:
            pass

    def _beat(self) -> None:
        self._last_beat = time.monotonic()

    def _watch_for_freeze(self) -> None:
        # Not faulthandler.dump_traceback_later: its thread walks the other
        # threads' frames without the GIL and crashes when one of them frees
        # a frame under it, which a busy GUI thread does constantly. Holding
        # the GIL means a GUI thread stuck in C code that keeps it goes
        # unrecorded, but the log never kills the application it records.
        header = f'Timeout ({timedelta(seconds=self._freeze_seconds)})!\n'
        poll = min(1.0, self._freeze_seconds / 4)
        recorded_beat = None
        while not self._stopped.wait(poll):
            beat = self._last_beat
            if beat != recorded_beat and time.monotonic() - beat >= self._freeze_seconds:
                recorded_beat = beat  # once per freeze
                self._write(f'{time.strftime("%H:%M:%S")} {header}{self._format_stacks()}')

    @staticmethod
    def _format_stacks() -> str:
        """Every other thread's Python stack, laid out as faulthandler does."""
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        own = threading.get_ident()
        lines = []
        for ident, frame in sys._current_frames().items():
            if ident == own:
                continue
            name = f' "{names[ident]}"' if ident in names else ''
            lines.append(f'Thread 0x{ident:08x}{name} (most recent call first):')
            while frame is not None:
                code = frame.f_code
                lines.append(f'  File "{code.co_filename}", line {frame.f_lineno} in {code.co_name}')
                frame = frame.f_back
            lines.append('')
        return '\n'.join(lines)

    def _on_exit(self) -> None:
        self._stopped.set()
        running = self._running_window_threads()
        # Interpreter shutdown is no place to call into Python from Qt.
        qInstallMessageHandler(self._previous_qt_handler)
        stamp = time.strftime('%H:%M:%S')
        if running:
            # The window, and every thread it owns, is only destroyed later
            # in shutdown, where this log can no longer record the result.
            self._write(
                f'{stamp} Shutting down with {running} thread(s) still '
                'running. Qt ends the application with a fatal error when '
                'their window destroys them.\n'
            )
        else:
            self._write(f'{stamp} Session ended normally.\n')

    @staticmethod
    def _running_window_threads() -> int:
        app = QCoreApplication.instance()
        try:
            widgets = app.topLevelWidgets() if hasattr(app, 'topLevelWidgets') else []
            return sum(
                thread.isRunning()
                for widget in widgets
                for thread in widget.findChildren(QThread)
            )
        except RuntimeError:  # the application object is already gone
            return 0
