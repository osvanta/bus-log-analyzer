# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Judge each scenario run and write the release test report.

A run passes when the application exited by itself with code 0, its crash-log
session ends with "Session ended normally." and holds nothing fatal, and every
step did what it should: a good file loaded with signals, a broken one was
refused or reported. A freeze, a new kind of Qt warning, or a measurement
still in memory after the next load is a warning, not a failure.

The report names no measurement, database or signal. Files appear by alias
(F1, F2, …); the aliases are listed in a separate file that stays on the PC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from gui.release_test.plan import Measurement, Scenario

# Crash-log lines that fail a run.
FATAL_MARKERS = (
    'Qt fatal error', 'Qt critical:', 'Uncaught exception', 'Windows fatal exception',
    'Fatal Python error', 'Debug memory block', 'Shutting down with',
)
FREEZE_MARKER = 'Timeout ('
QT_WARNING_MARKER = 'Qt warning (first of its kind): '
ENDED_NORMALLY = 'Session ended normally.'
_ENTRY = re.compile(r'\d\d:\d\d:\d\d ')
FAIL_FAST = 0xC0000409
# Dialogs a good file may raise: shown on the step line, not warned about.
EXPECTED_DIALOGS = {'Database Manager — Channel Configuration', 'Mixed MDF content detected'}


@dataclass
class Run:
    """One scenario, run once."""
    scenario: Scenario
    round: int
    exit_code: int | None = None       # None: never started, or killed
    timed_out: bool = False
    pid: int = 0
    started: float = 0.0               # monotonic
    launched_at: float = 0.0           # perf_counter, which the application shares
    seconds: float = 0.0
    exited_at: float = 0.0             # wall clock, to time an exit after a close
    result: dict = field(default_factory=dict)
    session: str | None = None         # its crash-log section
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: str = ''

    @property
    def startup(self) -> dict[str, float]:
        """Milliseconds from launch until the splash, the main window and its
        event loop: the application's own stamps less the launch time."""
        found = {}
        for name, key in (('splash', 'splash_at'), ('window', 'window_at'),
                          ('answering', 'answering_at')):
            if self.launched_at and self.result.get(key):
                found[name] = round((self.result[key] - self.launched_at) * 1000, 1)
        return found

    @property
    def verdict(self) -> str:
        if self.skipped:
            return 'SKIP'
        return 'FAIL' if self.failures else 'WARN' if self.warnings else 'PASS'


def crash_log_session(text: str, pid: int) -> str | None:
    """The section of a crash log written by process *pid*."""
    sessions = text.split('\n==== ')
    for session in reversed(sessions[1:]):
        header = session.split('\n', 1)[0]
        if re.search(rf'  pid {pid}  ', header):
            return '==== ' + session
    return None


def judge(run: Run, measurements: list[Measurement]) -> None:
    """Fill in the run's failures and warnings."""
    failures, warnings = run.failures, run.warnings
    if run.timed_out:
        failures.append('did not finish in time and was stopped')
    elif run.exit_code is None:
        failures.append('did not start')
    elif run.exit_code != 0:
        name = '  (Windows fail-fast: a Qt fatal error or a native crash)' if run.exit_code == FAIL_FAST else ''
        failures.append(f'exit code 0x{run.exit_code:08X}{name}')

    if run.session is None:
        failures.append('no crash-log session found for this run')
    else:
        for line in run.session.splitlines():
            if any(marker in line for marker in FATAL_MARKERS):
                failures.append(f'crash log: {line.strip()}')
            elif FREEZE_MARKER in line:
                warnings.append(f'crash log: window froze: {line.strip()}')
            elif QT_WARNING_MARKER in line:
                message = line.split(QT_WARNING_MARKER, 1)[1].strip()
                warnings.append(f'Qt warning: {_shorten(message)}  (listed at the end)')
        if not run.timed_out and ENDED_NORMALLY not in run.session:
            failures.append('crash log: session did not end normally')

    steps = run.result.get('steps', [])
    if run.result and not run.result.get('finished'):
        failures.append(f'stopped after step {len(steps)} of {len(run.scenario.steps)}')
    by_alias = {m.alias: m for m in measurements}
    for number, step in enumerate(steps, 1):
        broken = by_alias[step['file']].broken if step.get('file') in by_alias else False
        _judge_step(number, step, broken, failures, warnings)
    if run.scenario.memcheck and run.result and not run.result.get('memory_checks'):
        failures.append('ran without memory checks')
    close = next((s for s in steps if s['op'] == 'close_during_load'), None)
    if close and run.exited_at and not run.timed_out:
        close['exit_seconds'] = round(run.exited_at - close['closed_at'], 1)
        if not close.get('load_running_at_close'):
            warnings.append('the load had already finished when the window closed')


def _judge_step(number: int, step: dict, broken: bool, failures: list, warnings: list) -> None:
    where = f"step {number} ({step['op']} {step.get('file') or ''})".replace(' )', ')')
    if step['op'] == 'open' and not step.get('opened') and not broken:
        failures.append(f'{where}: the file was not opened')
    if step.get('databases_assigned') is False:
        failures.append(f'{where}: the Database Manager did not keep the databases')
    if step['op'] == 'load':
        outcome = step.get('outcome')
        if outcome == 'timed out':
            failures.append(f'{where}: Load + Decode did not finish')
        elif broken:
            pass  # any outcome but a crash or a hang
        elif outcome != 'loaded':
            failures.append(f'{where}: {outcome}')
        elif not step.get('signals'):
            failures.append(f'{where}: loaded, but no signals decoded')
        if step.get('stores_alive', 1) > 1:
            warnings.append(f"{where}: {step['stores_alive'] - 1} earlier measurement(s) still in memory")
    for title in step.get('dialogs', []):
        if not broken and title not in EXPECTED_DIALOGS:
            warnings.append(f'{where}: dialog "{title}"')


def render(header: list[str], measurements: list[Measurement], runs: list[Run],
           sections: list[tuple[list[str], bool]] = ()) -> str:
    """The report, before names are replaced by aliases.

    Each section (its lines, and whether it passed) follows the file list;
    a failed one fails the report.
    """
    failed = sum(run.verdict == 'FAIL' for run in runs)
    verdict = 'FAIL' if failed or not all(passed for _, passed in sections) else 'PASS'
    counted = [run for run in runs if run.verdict != 'SKIP']
    lines = [
        *header,
        '',
        f'RESULT: {verdict}   {len(counted) - failed} of {len(counted)} runs passed'
        + (f', {failed} failed' if failed else '')
        + ('' if all(passed for _, passed in sections) else '; the known-good timing did not pass'),
        '',
        'Files (names are in the separate files list, which stays on this PC):',
        *(f'  {m.describe()}' for m in measurements),
        '',
    ]
    for section_lines, _passed in sections:
        lines += section_lines
    for run in runs:
        lines.append(
            f'[{run.verdict}] round {run.round}  {run.scenario.title}'
            + (f'   {run.skipped}' if run.skipped else
               f'   exit {_exit_text(run.exit_code)}, {run.seconds:.0f} s')
        )
        if run.startup:
            lines.append('    start-up            ' + ', '.join(
                f'{name} {run.startup[name]:,.0f} ms'
                for name in ('splash', 'window', 'answering') if name in run.startup))
        for step in run.result.get('steps', []):
            lines.append('    ' + _step_text(step))
        for failure in run.failures:
            lines.append(f'    FAIL  {failure}')
        for warning in run.warnings:
            lines.append(f'    warn  {warning}')
        lines.append('')
    details = [run for run in runs if run.failures and run.session]
    if details:
        lines.append('Crash-log sessions of the failed runs:')
        for run in details:
            lines += ['', f'--- round {run.round}  {run.scenario.title}', run.session.rstrip()]
        lines.append('')
    warned = _first_warnings(runs)
    if warned:
        lines.append('Each kind of Qt warning, and the code that raised it first:')
        lines += ['', *warned]
    return '\n'.join(lines)


def _shorten(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _exit_text(code: int | None) -> str:
    return 'none' if code is None else str(code) if code < 0x10000 else f'0x{code:08X}'


def _step_text(step: dict) -> str:
    parts = [f"{step['op']:18}", f"{step.get('file') or '':4}", f"{step.get('seconds', 0):6.1f} s"]
    if 'outcome' in step:
        parts.append(step['outcome'])
    if 'signals' in step:
        parts.append(f"{step['signals']:,} signals, {step['samples']:,} samples")
    if 'load_ms' in step:
        parts.append(f"tree listed {step['load_ms']:,.0f} ms after the click")
    if 'plot_ms' in step:
        parts.append(f"drawn in {step['plot_ms']:,.0f} ms")
    if step.get('warnings'):
        parts.append(f"{step['warnings']} skipped item(s)")
    if 'plotted' in step:
        parts.append(f"{step['plotted']} plotted")
    if 'load_running_at_close' in step:
        parts.append('closed while loading' if step['load_running_at_close'] else 'closed after the load')
    if 'exit_seconds' in step:
        parts.append(f"exited {step['exit_seconds']} s after the close")
    if 'memory_mb' in step:
        parts.append(f"memory {step['memory_mb']:,} MB (peak {step['peak_mb']:,})")
    if step.get('dialogs'):
        parts.append('dialogs: ' + ', '.join(f'"{title}"' for title in step['dialogs']))
    return '  '.join(parts)


def _first_warnings(runs: list[Run]) -> list[str]:
    """Each distinct Qt warning once, with the calls that raised it."""
    blocks: dict[str, list[str]] = {}
    seen_in: dict[str, int] = {}
    for run in runs:
        lines = (run.session or '').splitlines()
        for index, line in enumerate(lines):
            if QT_WARNING_MARKER not in line:
                continue
            message = line.split(QT_WARNING_MARKER, 1)[1].strip()
            seen_in[message] = seen_in.get(message, 0) + 1
            if message in blocks:
                continue
            # Up to the next timestamped entry: a Qt message can span lines.
            block = [message]
            for following in lines[index + 1:]:
                if _ENTRY.match(following) or following.startswith('===='):
                    break
                block.append(following)
            blocks[message] = block
    counted = sum(run.session is not None for run in runs)
    return [
        line for message, block in blocks.items()
        for line in (*block, f'  (in {seen_in[message]} of {counted} runs)', '')
    ]


def anonymise(text: str, measurements: list[Measurement], folder: Path) -> str:
    """Replace every measurement and database name in *text* by its alias."""
    names: dict[str, str] = {}
    databases = sorted({path for m in measurements for path in m.databases.values()})
    for number, database in enumerate(databases, 1):
        names[database] = f'D{number}'
    for m in measurements:
        names[str(m.path)] = m.alias
    for path, alias in list(names.items()):
        names.setdefault(Path(path).name, alias)
        names.setdefault(path.replace('\\', '/'), alias)
    names[str(folder)] = '<test folder>'
    names[str(folder).replace('\\', '/')] = '<test folder>'
    names[str(Path.home())] = '~'
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(re.escape(name), names[name], text, flags=re.IGNORECASE)
    return text


def files_list(measurements: list[Measurement]) -> str:
    """Which file each alias stands for: kept on this PC, never shared."""
    databases = sorted({path for m in measurements for path in m.databases.values()})
    return '\n'.join([
        'Release test files. Keep this list on this PC: the report uses only the aliases.',
        '',
        *(f'{m.alias}  {m.path}' for m in measurements),
        *(f'D{number}  {path}' for number, path in enumerate(databases, 1)),
        '',
    ])
