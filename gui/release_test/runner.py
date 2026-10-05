# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Run every scenario in a fresh copy of this build and write the report.

Each run starts the application again with ``--release-test-scenario``, so a
crash ends one run, not the test, and every run has its own crash-log
session. The memory-checked scenario starts the build's second executable,
which runs with Python's memory checks on; from source it starts Python with
``-X dev``.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from gui import crash_log
from gui.release_test import SCENARIO_FLAG, timing
from gui.release_test.driver import LOAD_TIMEOUT
from gui.release_test.plan import build_scenarios, find_measurements, select
from gui.release_test.report import Run, anonymise, crash_log_session, files_list, judge, render

APP_EXE = 'BusLogAnalyzer.exe'
MEMCHECK_EXE = 'appdebugger.exe'          # named in BusLogAnalyzer.spec
REPORTS_FOLDER = 'release_test_reports'   # in the test folder, beside the measurements
START_SECONDS = 120.0       # per run, on top of LOAD_TIMEOUT per load
MEMCHECK_SLOWDOWN = 3


def launcher(memcheck: bool) -> list[str] | None:
    """The command that starts this build, or its memory-checked twin."""
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable)
        if memcheck:
            target = exe.with_name(MEMCHECK_EXE)
        elif exe.name.lower() == MEMCHECK_EXE:
            target = exe.with_name(APP_EXE)
        else:
            target = exe
        return [str(target)] if target.exists() else None
    app_py = Path(__file__).resolve().parents[2] / 'app.py'
    return [sys.executable, *(['-X', 'dev'] if memcheck else []), str(app_py)]


class ReleaseTest(QObject):
    """Run the scenarios one after another, each in its own process."""

    progress = Signal(str)
    finished = Signal()

    def __init__(self, measurements, scenarios, rounds: int, work_dir: Path) -> None:
        super().__init__()
        self.measurements = measurements
        self.runs = [Run(scenario, number) for number in range(1, rounds + 1) for scenario in scenarios]
        self._queue = list(self.runs)
        self._work_dir = work_dir
        self._process: QProcess | None = None
        self._stopped = False

    def start(self) -> None:
        QTimer.singleShot(0, self._next)

    def stop(self) -> None:
        """End the current run and skip the rest."""
        self._stopped = True
        for run in self._queue:
            run.skipped = 'stopped before it started'
        self._queue.clear()
        if self._process is not None:
            self._process.kill()

    def _next(self) -> None:
        if not self._queue:
            self.finished.emit()
            return
        run = self._queue.pop(0)
        command = launcher(run.scenario.memcheck)
        if command is None:
            run.skipped = 'the memory-checked build is missing next to this one'
            self.progress.emit(f'round {run.round}  {run.scenario.title}: SKIP, {run.skipped}')
            QTimer.singleShot(0, self._next)
            return
        number = self.runs.index(run)
        spec_path = self._work_dir / f'run{number}.json'
        result_path = self._work_dir / f'run{number}.result.json'
        spec_path.write_text(json.dumps({
            'files': [m.to_json() for m in self.measurements],
            'steps': [step.to_json() for step in run.scenario.steps],
            'result': str(result_path),
            'timing': run.scenario.family == timing.FAMILY,
        }), encoding='utf-8')

        process = QProcess(self)
        process.setProgram(command[0])
        process.setArguments([*command[1:], SCENARIO_FLAG, str(spec_path)])
        process.setWorkingDirectory(str(Path(command[-1]).parent))
        # Unread pipes would fill up and stall the application.
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setStandardErrorFile(QProcess.nullDevice())
        timeout = START_SECONDS + LOAD_TIMEOUT * run.scenario.loads
        if run.scenario.memcheck:
            timeout *= MEMCHECK_SLOWDOWN
        watchdog = QTimer(process)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(lambda: self._time_out(run, process))
        process.finished.connect(lambda code, _status: self._on_finished(run, process, code, result_path))
        process.errorOccurred.connect(lambda error: self._on_error(run, process, error))
        process.started.connect(lambda: setattr(run, 'pid', process.processId()))
        self._process = process
        self.progress.emit(f'round {run.round}  {run.scenario.title} …')
        run.started = time.monotonic()
        run.launched_at = time.perf_counter()
        process.start()
        watchdog.start(int(timeout * 1000))

    def _time_out(self, run: Run, process: QProcess) -> None:
        run.timed_out = True
        process.kill()

    def _on_error(self, run: Run, process: QProcess, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(run, process, None, None)

    def _on_finished(self, run: Run, process: QProcess, code: int, result_path: Path) -> None:
        self._complete(run, process, code & 0xFFFFFFFF, result_path)

    def _complete(self, run: Run, process: QProcess, code, result_path) -> None:
        if process is not self._process:
            return
        self._process = None
        run.exited_at = time.time()
        run.seconds = time.monotonic() - run.started
        if self._stopped and not run.timed_out:
            run.skipped = 'stopped while it ran'
        else:
            run.exit_code = None if run.timed_out else code
        if result_path is not None and result_path.exists():
            run.result = json.loads(result_path.read_text(encoding='utf-8'))
        run.session = _find_session(run.result.get('pid') or run.pid)
        if not run.skipped:
            judge(run, self.measurements)
        self.progress.emit(
            f'round {run.round}  {run.scenario.title}: {run.verdict}'
            + (f'  ({run.seconds:.0f} s)' if not run.skipped else '')
            + ''.join(f'\n      {failure}' for failure in run.failures[:3])
        )
        process.deleteLater()
        QTimer.singleShot(0, self._next)


def _find_session(pid: int) -> str | None:
    for path in (crash_log.default_log_path(), Path(tempfile.gettempdir()) / crash_log.LOG_NAME):
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        session = crash_log_session(text, pid)
        if session is not None:
            return session
    return None


def _total_memory_gb() -> float:
    class Status(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    status = Status()
    status.dwLength = ctypes.sizeof(status)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except AttributeError:
        return 0.0
    return status.ullTotalPhys / 1e9


class RunnerWindow(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(820, 520)
        self.status = QLabel()
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setFont(QFont('Consolas'))
        self.stop_button = QPushButton('Stop')
        self.report_button = QPushButton('Open report')
        self.report_button.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.report_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)

    def say(self, text: str) -> None:
        self.log.appendPlainText(text)
        if sys.stdout is not None:
            try:
                print(text, flush=True)
            except (OSError, ValueError):
                pass


def _write(folder: Path, name: str, text: str) -> Path:
    """Into the test folder's reports folder, or the temp folder when that
    cannot be written."""
    for directory in (folder / REPORTS_FOLDER, Path(tempfile.gettempdir())):
        try:
            directory.mkdir(exist_ok=True)
            path = directory / name
            path.write_text(text, encoding='utf-8')
            return path
        except OSError:
            continue
    raise OSError(f'Cannot write {name}')


def run(argv: list[str], app_name: str, app_version: str) -> int:
    """Entry point for ``--release-test <folder>``; returns the exit code."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--release-test', dest='folder', type=Path, required=True)
    parser.add_argument('--heavy', action='store_true')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--only', default='')
    parser.add_argument('--known-good', dest='known_good', type=Path)
    parser.add_argument('--quit-when-done', action='store_true')
    args, _unknown = parser.parse_known_args(argv[1:])
    if sys.stdout is not None:
        # A console's code page lacks the arrows in the scenario titles.
        sys.stdout.reconfigure(errors='replace')

    app = QApplication(argv)
    app.setApplicationName(app_name)
    app.setApplicationVersion(app_version)
    window = RunnerWindow(f'{app_name} {app_version} — release test')
    window.show()

    folder = args.folder.resolve()
    measurements = find_measurements(folder) if folder.is_dir() else []
    scenarios = build_scenarios(measurements, args.heavy)
    known = None
    problems: list[str] = []
    states: list[timing.PcState] = []   # before the runs, and after them
    if args.known_good is not None:
        known = timing.read_known_good(args.known_good)
        timed, problems = timing.timing_scenarios(measurements, known.references, folder)
        scenarios += timed
    scenarios = select(scenarios, args.only)
    packaged = getattr(sys, 'frozen', False)
    started = time.time()
    header = [
        f'{app_name} release test',
        f'Build: {app_version}, '
        + (f'packaged ({Path(sys.executable).name})' if packaged else 'run from source'),
        'Memory-checked build: ' + ('found' if launcher(True) else 'MISSING'),
        f'PC: {platform.platform()}, {os.cpu_count()} logical CPUs, {_total_memory_gb():.1f} GB RAM',
        f'Started: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started))}',
        f'Tier: {"heavy" if args.heavy else "standard"}   Rounds: {args.repeat}',
    ]
    if known is not None:
        header.append(f'Known-good file: {known.path.name}')
        header += [f'  Known-good file: {problem}' for problem in problems]
    for line in header[1:]:
        window.say(line)
    for m in measurements:
        window.say(f'  {m.describe()}')
    if not scenarios:
        window.say(f'Nothing to test in {folder}: no measurement that can be loaded.')
        window.status.setText('Nothing to test.')
        window.stop_button.setEnabled(False)
        if args.quit_when_done:
            return 2
        return app.exec()

    work_dir = Path(tempfile.mkdtemp(prefix='osvanta_release_test_'))
    test = ReleaseTest(measurements, scenarios, max(1, args.repeat), work_dir)
    test.progress.connect(window.say)
    window.stop_button.clicked.connect(test.stop)
    outcome = {'code': 1}

    def finish() -> None:
        runs = test.runs
        took = time.time() - started
        header.append(f'Took: {int(took // 60)} min {int(took % 60)} s')
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(started))
        sections = []
        if known is not None:
            states.append(timing.pc_state())
            sections.append(timing.summary(runs, known, packaged, states))
            for line in sections[-1][0]:
                window.say(line)
        text = anonymise(render(header, measurements, runs, sections), measurements, folder)
        report = _write(folder, f'release_test_{stamp}_report.txt', text)
        _write(folder, f'release_test_{stamp}_files.txt', files_list(measurements))
        shutil.rmtree(work_dir, ignore_errors=True)
        passed = not any(run.verdict == 'FAIL' for run in runs) and any(
            run.verdict in ('PASS', 'WARN') for run in runs) and all(ok for _, ok in sections)
        outcome['code'] = 0 if passed else 1
        verdict = 'PASS' if passed else 'FAIL'
        window.say(f'\nRESULT: {verdict}\nReport: {report}')
        window.status.setText(f'Release test {verdict}.  Report: {report.name}')
        window.stop_button.setEnabled(False)
        window.report_button.setEnabled(True)
        window.report_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(report))))
        if args.quit_when_done:
            app.exit(outcome['code'])

    test.finished.connect(finish)
    app.aboutToQuit.connect(test.stop)
    if known is not None:
        states.append(timing.pc_state())
    test.start()
    code = app.exec()
    return outcome['code'] if args.quit_when_done else code
