# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Drive one release-test scenario inside a running copy of the application.

The runner starts the application with ``--release-test-scenario <file>``.
Once the main window is up, the driver works it the way a user does: Open
File, Open Database, Load + Decode, the plot buttons, closing the window.

Only the file dialog is answered directly, because it is the operating
system's window, not the application's. Every dialog the application opens
itself stays on screen for a moment, so the real platform paints it, and is
then answered: OK in the Database Manager, the cancelling button in a message
box. Each dialog's title is recorded against the step that raised it.

The result file is rewritten after every step, so the runner still has every
step up to a crash.
"""

from __future__ import annotations

import ctypes
import gc
import json
import os
import sys
import threading
import time
import tracemalloc
import weakref
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from shiboken6 import Shiboken

from core.bus_types import decode_key
from core.channel_config import ChannelConfig
from gui.dbc_manager import DBCManagerDialog

TICK_MS = 20
DIALOG_SECONDS = 0.4        # how long a dialog stays up before it is answered
LOAD_TIMEOUT = 900.0
CLOSE_AFTER_SECONDS = 0.1   # after the load thread starts: small files load in 0.5 s
PLOTTED_SIGNALS = 8
TIMED_PLOT_SIGNALS = 5      # plotted in Stacked by a timing scenario
# A timing scenario loads once the main window's background import of the
# readers has finished, as it would after a user has picked a file.
PRELOAD_THREAD = 'Measurement support preload'
SETTLE_SECONDS = 1.0
# A message box is answered with the first of these its buttons have.
_ANSWER_ROLES = (
    QMessageBox.ButtonRole.RejectRole, QMessageBox.ButtonRole.NoRole,
    QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole,
)


class ScenarioDriver(QObject):
    def __init__(self, window, spec: dict) -> None:
        super().__init__(window)
        self._window = window
        self._actions = window._toolbar_actions
        self._files: list[dict] = spec['files']
        self._steps: list[dict] = spec['steps']
        self._result_path = Path(spec['result'])
        # The driver starts right after the main window is shown. Start-up
        # times are these stamps less the runner's launch time: both read
        # time.perf_counter(), the system's performance counter.
        splash = getattr(window, '_splash', None)
        self._result = {
            'pid': os.getpid(), 'memory_checks': bool(sys.flags.dev_mode),
            'steps': [], 'finished': False,
            'splash_at': getattr(splash, 'shown_at', None),
            'window_at': time.perf_counter(),
        }
        self._timing = bool(spec.get('timing'))
        self._listed_at = 0.0
        self._current_file: dict | None = None
        self._pending_paths: list[str] = []
        self._dialogs: list[str] = []
        self._modal_address = 0
        self._modal_since = 0.0
        # How long dialogs waited on screen for their answer: a person's time,
        # left out of what a timing scenario reports.
        self._modal_shown_at = 0.0
        self._dialog_ms = 0.0
        self._stores: list[weakref.ref] = []
        self._stepping = False
        if sys.flags.dev_mode:
            # The memory checks name the damaged block; this names the code
            # that allocated it. Only what is allocated from here on.
            tracemalloc.start(5)
        QFileDialog.getOpenFileName = staticmethod(self._answer_file_dialog)
        self._script = self._run()
        # Two timers: Qt does not fire a timer again while its own slot runs,
        # and a step that opens a modal dialog is still inside that slot.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(TICK_MS)
        self._dialog_timer = QTimer(self)
        self._dialog_timer.timeout.connect(self._answer_dialog)
        self._dialog_timer.start(TICK_MS)
        # Runs once the event loop does: the window answers from then on.
        QTimer.singleShot(0, self._note_answering)

    # ── Event loop ───────────────────────────────────────────────────────

    def _step(self) -> None:
        # processEvents() inside a step must not start the next one, and the
        # script waits while a dialog is open.
        if self._stepping or QApplication.activeModalWidget() is not None:
            return
        self._stepping = True
        try:
            next(self._script)
        except StopIteration:
            self._stop()
        finally:
            self._stepping = False

    def _note_answering(self) -> None:
        self._result['answering_at'] = time.perf_counter()

    def _stop(self) -> None:
        self._timer.stop()
        self._dialog_timer.stop()

    def _answer_file_dialog(self, *_args, **_kwargs) -> tuple[str, str]:
        if not self._pending_paths:
            self._dialogs.append('unexpected file dialog, cancelled')
            return '', ''
        return self._pending_paths.pop(0), ''

    def _answer_dialog(self) -> None:
        """Answer the open modal dialog once it has been up a moment."""
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            self._modal_address = 0
            return
        address = Shiboken.getCppPointer(dialog)[0]
        if address != self._modal_address:
            self._modal_address, self._modal_since = address, time.monotonic()
            self._modal_shown_at = time.perf_counter()
            return
        if time.monotonic() - self._modal_since < DIALOG_SECONDS:
            return
        self._modal_address = 0
        self._dialog_ms += (time.perf_counter() - self._modal_shown_at) * 1000
        self._dialogs.append(dialog.windowTitle() or type(dialog).__name__)
        if isinstance(dialog, DBCManagerDialog):
            dialog.accept()
        elif isinstance(dialog, QMessageBox):
            buttons = {dialog.buttonRole(button): button for button in dialog.buttons()}
            role = next((role for role in _ANSWER_ROLES if role in buttons), None)
            if role is None:
                dialog.reject()
            else:
                buttons[role].click()
        else:
            dialog.reject()

    def _wait(self, seconds: float):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            yield

    # ── Scenario ─────────────────────────────────────────────────────────

    def _run(self):
        if not self._timing:  # a timing scenario opens its file at once
            yield from self._wait(1.0)  # the splash hands over to the window
        for step in self._steps:
            record = {'op': step['op']}
            self._dialogs = []
            started = time.monotonic()
            if step['op'] == 'open':
                self._open(self._files[step['file']], record)
            elif step['op'] == 'settle':
                yield from self._settle()
            elif step['op'] == 'load':
                yield from self._load(record)
            elif step['op'] == 'plot':
                yield from self._plot(record)
            elif step['op'] == 'plot_stacked':
                self._plot_stacked(record)
            elif step['op'] == 'close_during_load':
                yield from self._close_during_load(record)
                return
            yield from self._wait(0.2)
            self._finish_step(record, started)
        yield from self._wait(1.0)
        self._result['finished'] = True
        self._save()
        self._stop()
        self._window.close()

    def _finish_step(self, record: dict, started: float) -> None:
        record['seconds'] = round(time.monotonic() - started, 1)
        record['dialogs'] = list(self._dialogs)
        record.update(_memory())
        self._result['steps'].append(record)
        self._save()

    def _open(self, file: dict, record: dict) -> None:
        window = self._window
        record['file'] = file['alias']
        self._current_file = file
        self._pending_paths.append(file['path'])
        self._dialog_ms = 0.0
        self._actions['Open File'].trigger()
        record['opened_at'] = time.perf_counter()
        record['open_dialog_ms'] = round(self._dialog_ms, 1)
        record['opened'] = window.measurement_path == file['path']
        planned = ChannelConfig(
            name='Release test',
            channels={decode_key(key): path for key, path in file['databases'].items()},
        )
        if planned.channels == window.channel_config.channels:
            return  # a user keeps the databases already assigned
        if planned.is_empty():
            # The Database Manager removes rows one by one; the result is this.
            window.channel_config = ChannelConfig()
            return
        # As if loaded through the Database Manager's Load Channel Config.
        window.channel_config = planned
        self._actions['Open Database'].trigger()
        record['databases_assigned'] = window.channel_config.channels == planned.channels

    def _load(self, record: dict):
        window = self._window
        record['file'] = self._current_file['alias'] if self._current_file else None
        action = self._actions['Load + Decode']
        if not action.isEnabled():
            record['outcome'] = 'Load + Decode was greyed out'
            return
        self._listed_at = 0.0
        clicked = time.perf_counter()
        action.trigger()
        if window._worker is not None:
            # Connected after the window's own slot, so it runs once the
            # signal tree has been given the signals.
            window._worker.finished.connect(self._on_load_finished)
        deadline = time.monotonic() + LOAD_TIMEOUT
        while window._thread is not None:
            if time.monotonic() > deadline:
                record['outcome'] = 'timed out'
                return
            yield
        yield from self._wait(0.3)  # results shown, any dialog answered
        if self._listed_at:
            record['load_ms'] = round((self._listed_at - clicked) * 1000, 1)
        store = window.store
        if store is None:
            record['outcome'] = 'failed' if 'Load failed' in self._dialogs else 'not loaded'
            return
        record.update(
            outcome='loaded', signals=len(store.all_keys()), frames=store.total_frames,
            decoded=store.decoded_frames, samples=store.total_samples,
            warnings=len(getattr(store, 'load_warnings', ()) or ()),
        )
        # Each Load + Decode must free the measurement it replaces.
        gc.collect()
        self._stores.append(weakref.ref(store))
        record['stores_alive'] = sum(ref() is not None for ref in self._stores)

    def _on_load_finished(self, _store) -> None:
        self._window.signal_tree.repaint()
        self._listed_at = time.perf_counter()

    def _settle(self):
        deadline = time.monotonic() + LOAD_TIMEOUT
        while (any(thread.name == PRELOAD_THREAD for thread in threading.enumerate())
               and time.monotonic() < deadline):
            yield
        yield from self._wait(SETTLE_SECONDS)

    def _plot_stacked(self, record: dict) -> None:
        """Plot five signals in Stacked and time it until they are drawn."""
        window = self._window
        record['file'] = self._current_file['alias'] if self._current_file else None
        store = window.store
        if store is None:
            record['outcome'] = 'nothing to plot'
            return
        window.btn_stacked.setChecked(True)
        keys = sorted(store.all_keys())[:TIMED_PLOT_SIGNALS]
        started = time.perf_counter()
        window.add_signals_to_plot(keys)
        QApplication.processEvents()
        window.plot_panel.repaint()
        record['plot_ms'] = round((time.perf_counter() - started) * 1000, 1)
        record['plotted'] = len(window.plot_panel.plotted_keys())
        record['outcome'] = 'plotted'

    def _plot(self, record: dict):
        window = self._window
        record['file'] = self._current_file['alias'] if self._current_file else None
        store = window.store
        if store is None:
            record['outcome'] = 'nothing to plot'
            return
        window.add_signals_to_plot(sorted(store.all_keys())[:PLOTTED_SIGNALS])
        record['plotted'] = len(window.plot_panel.plotted_keys())
        yield from self._wait(0.3)
        for button in (window.btn_stacked, window.btn_multi_axis, window.btn_multistack,
                       window.btn_cursor1, window.btn_cursor2):
            for _ in range(2):  # on, then off
                button.click()
                yield from self._wait(0.2)
        self._actions['Clear Plots'].trigger()
        record['outcome'] = 'plotted'

    def _close_during_load(self, record: dict):
        window = self._window
        record['file'] = self._current_file['alias'] if self._current_file else None
        started = time.monotonic()
        self._actions['Load + Decode'].trigger()
        # Timed from the thread's start, not the click: a file the system
        # has cached loads in under two seconds.
        while window._thread is not None and not window._thread.isRunning():
            yield
        yield from self._wait(CLOSE_AFTER_SECONDS)
        record['load_running_at_close'] = window._thread is not None
        record['closed_at'] = time.time()
        self._finish_step(record, started)
        self._result['finished'] = True
        self._save()
        self._stop()
        # Nothing answers a dialog from here on: one would hold the
        # application open until the runner gives up on it.
        window.close()

    def _save(self) -> None:
        temporary = self._result_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self._result, indent=1), encoding='utf-8')
        temporary.replace(self._result_path)


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
        ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
        ('PrivateUsage', ctypes.c_size_t),
    ]


def _memory() -> dict:
    """This process's memory now and at its peak, in MB."""
    if sys.platform != 'win32':
        return {}
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return {}
    return {
        'memory_mb': round(counters.PrivateUsage / 1e6),
        'peak_mb': round(counters.PeakPagefileUsage / 1e6),
    }


def drive(window, spec_path: Path) -> ScenarioDriver:
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    return ScenarioDriver(window, spec)
