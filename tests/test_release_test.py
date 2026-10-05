# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The release test: its folder layout, its verdicts and its report.

The last test runs it for real, from source: it restarts the application,
answers the Database Manager and writes a report that names no file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from core.bus_types import BusType
from core.channel_config import ChannelConfig
from gui.release_test.plan import Measurement, build_scenarios, find_measurements
from gui.release_test.report import (
    FAIL_FAST, Run, anonymise, crash_log_session, judge, render,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\0' * size)
    return path


def test_each_folder_decides_the_databases_of_its_measurements(tmp_path):
    _touch(tmp_path / 'signals.mf4')
    _touch(tmp_path / 'can' / 'log.blf')
    one = _touch(tmp_path / 'can' / 'powertrain.dbc')
    _touch(tmp_path / 'lin' / 'log.mf4')
    ldf = _touch(tmp_path / 'lin' / 'body.ldf')
    _touch(tmp_path / 'two' / 'log.asc')
    _touch(tmp_path / 'two' / 'a.dbc')
    _touch(tmp_path / 'two' / 'b.dbc')
    _touch(tmp_path / 'configured' / 'log.blf')
    for name in ('a.dbc', 'b.dbc'):
        _touch(tmp_path / 'configured' / name)
    # Saved on another PC: the databases are found by name next to it.
    ChannelConfig('office', {
        (BusType.CAN, 1): r'D:\elsewhere\a.dbc', (BusType.CAN, 2): r'D:\elsewhere\b.dbc',
    }).save(tmp_path / 'configured' / 'setup.osvanta_ch')
    _touch(tmp_path / 'broken' / 'bad.mf4')

    found = {m.path.relative_to(tmp_path).as_posix(): m for m in find_measurements(tmp_path)}

    assert [m.alias for m in found.values()] == [f'F{n}' for n in range(1, 7)]
    assert found['signals.mf4'].databases == {}
    assert found['can/log.blf'].databases == {'CAN:0': str(one)}
    assert found['lin/log.mf4'].databases == {'LIN:0': str(ldf)}
    assert 'several databases' in found['two/log.asc'].skipped
    assert found['configured/log.blf'].databases == {
        'CAN:1': str(tmp_path / 'configured' / 'a.dbc'),
        'CAN:2': str(tmp_path / 'configured' / 'b.dbc'),
    }
    assert found['broken/bad.mf4'].broken
    assert not found['signals.mf4'].broken


def _measurement(alias: str, size: int, broken: bool = False, skipped: str = '') -> Measurement:
    return Measurement(Path(f'C:/logs/{alias.lower()}.mf4'), alias, size, broken=broken, skipped=skipped)


def test_a_and_b_are_the_two_largest_files_that_can_be_tested():
    measurements = [
        _measurement('F1', 30), _measurement('F2', 90, skipped='no database'),
        _measurement('F3', 50), _measurement('F4', 99, broken=True), _measurement('F5', 10),
    ]

    standard = {s.name: s for s in build_scenarios(measurements, heavy=False)}
    heavy = {s.name: s for s in build_scenarios(measurements, heavy=True)}

    assert list(standard) == ['each_once', 'a_b_a', 'close_during_load', 'broken_recovery']
    opened = [step.file for step in standard['a_b_a'].steps if step.op == 'open']
    assert opened == [2, 0, 2]  # A = F3, B = F1
    assert [s.file for s in standard['each_once'].steps if s.op == 'open'] == [0, 2, 4]
    assert standard['broken_recovery'].steps[0].file == 3
    assert list(heavy) == [*standard, 'b_five_times', 'memcheck_a_b_a']
    assert heavy['b_five_times'].loads == 5
    assert heavy['memcheck_a_b_a'].memcheck


_LOG = """Osvanta Bus Log Analyzer crash log.

==== 2026-10-01 10:00:00  Osvanta Bus Log Analyzer v00.01.02  pid 41  Python 3.12  Qt 6  Windows ====
10:00:05 Session ended normally.

==== 2026-10-01 10:01:00  Osvanta Bus Log Analyzer v00.01.02  pid 42  Python 3.12  Qt 6  Windows ====
10:01:09 Qt warning (first of its kind): QFont::setPointSize: Point size <= 0 (-1), must be greater than 0
  Raised from (most recent call last):
    File "gui\\dbc_manager.py", line 12, in __init__
10:01:30 Qt fatal error, the application is terminated: QThread: Destroyed while thread is still running
"""


def test_a_run_is_matched_to_its_own_crash_log_session():
    assert crash_log_session(_LOG, 41).rstrip().endswith('Session ended normally.')
    assert 'Qt fatal error' in crash_log_session(_LOG, 42)
    assert crash_log_session(_LOG, 4) is None


def _run(steps, *, exit_code=0, session=None, scenario_index=0):
    scenario = build_scenarios([_measurement('F1', 20), _measurement('F2', 10, broken=True)], False)
    run = Run(scenario[scenario_index], 1, exit_code=exit_code,
              result={'pid': 41, 'finished': True, 'steps': steps},
              session=crash_log_session(_LOG, 41) if session is None else session)
    judge(run, [_measurement('F1', 20), _measurement('F2', 10, broken=True)])
    return run


def test_a_clean_run_passes():
    run = _run([
        {'op': 'open', 'file': 'F1', 'opened': True, 'dialogs': []},
        {'op': 'load', 'file': 'F1', 'outcome': 'loaded', 'signals': 12, 'stores_alive': 1,
         'dialogs': []},
    ])
    assert (run.verdict, run.failures, run.warnings) == ('PASS', [], [])


def test_a_crash_fails_with_its_reasons():
    run = _run([], exit_code=FAIL_FAST, session=crash_log_session(_LOG, 42))

    assert run.verdict == 'FAIL'
    assert any('fail-fast' in failure for failure in run.failures)
    assert any('QThread: Destroyed' in failure for failure in run.failures)
    assert 'crash log: session did not end normally' in run.failures
    assert any('QFont::setPointSize' in warning for warning in run.warnings)


def test_a_good_file_must_load_but_a_broken_one_may_fail():
    failed_good = _run([{'op': 'load', 'file': 'F1', 'outcome': 'not loaded', 'dialogs': ['Load failed']}])
    failed_broken = _run([{'op': 'load', 'file': 'F2', 'outcome': 'failed', 'dialogs': ['Load failed']}])

    assert failed_good.failures == ['step 1 (load F1): not loaded']
    assert failed_good.warnings == ['step 1 (load F1): dialog "Load failed"']
    assert failed_broken.verdict == 'PASS'


def test_a_measurement_kept_after_the_next_load_is_a_warning():
    run = _run([{'op': 'load', 'file': 'F1', 'outcome': 'loaded', 'signals': 3, 'stores_alive': 2,
                 'dialogs': []}])
    assert run.verdict == 'WARN'
    assert run.warnings == ['step 1 (load F1): 1 earlier measurement(s) still in memory']


def test_the_report_names_no_measurement_or_database(tmp_path):
    folder = tmp_path / 'Secret Project'
    measurement = Measurement(folder / 'Prototype_X7_drive.MF4', 'F1', 5_000_000,
                              {'CAN:0': str(folder / 'Powertrain_X7.dbc')})
    session = crash_log_session(_LOG, 41).replace(
        'Session ended normally.',
        f'Uncaught exception: OSError: cannot read {folder}\\prototype_x7_drive.mf4\n'
        '10:00:06 Session ended normally.')
    run = Run(build_scenarios([measurement], False)[0], 1, exit_code=0,
              result={'finished': True, 'steps': []}, session=session)
    judge(run, [measurement])

    text = anonymise(render(['header'], [measurement], [run]), [measurement], folder)

    for secret in ('Prototype', 'prototype', 'Powertrain', 'Secret Project'):
        assert secret not in text
    assert 'cannot read F1' in text


def test_the_built_app_finds_appdebugger_beside_it_and_back(tmp_path, monkeypatch):
    from gui.release_test.runner import launcher
    app = _touch(tmp_path / 'BusLogAnalyzer.exe')
    debugger = _touch(tmp_path / 'appdebugger.exe')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)

    for running in (app, debugger):
        monkeypatch.setattr(sys, 'executable', str(running))
        assert launcher(False) == [str(app)]
        assert launcher(True) == [str(debugger)]

    debugger.unlink()
    assert launcher(True) is None               # reported as missing, not run


def test_the_release_test_drives_the_application(tmp_path, blf_path, sample_dbc_path):
    folder = tmp_path / 'measurements'
    (folder / 'can').mkdir(parents=True)
    shutil.copy(blf_path, folder / 'can' / 'Kestrel_drive.blf')
    shutil.copy(sample_dbc_path, folder / 'can' / 'Kestrel_vehicle.dbc')
    shutil.copy(REPO_ROOT / 'tests' / 'fixtures' / 'sample_narrow.csv', folder / 'Kestrel_wheels.csv')

    completed = subprocess.run(
        [sys.executable, 'app.py', '--release-test', str(folder), '--only', 'each_once',
         '--quit-when-done'],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(REPO_ROOT),
                 PYTHONIOENCODING='utf-8', OSVANTA_CRASH_LOG=str(tmp_path / 'crash.log'),
                 OSVANTA_APP_LOG=str(tmp_path / 'app.log')),
        capture_output=True, text=True, timeout=300,
    )

    reports = list((folder / 'release_test_reports').glob('release_test_*_report.txt'))
    assert len(reports) == 1, completed.stdout[-4000:] + completed.stderr[-4000:]
    report = reports[0].read_text(encoding='utf-8')
    assert completed.returncode == 0, report
    assert 'RESULT: PASS' in report
    # The Database Manager was opened, and answered, for the BLF.
    assert 'the Database Manager did not keep' not in report
    assert report.count(' loaded ') == 2
    for name in ('Kestrel', str(tmp_path)):
        assert name not in report
    assert not list(folder.glob('release_test_*.txt'))   # only in the reports folder
    files = next((folder / 'release_test_reports').glob('release_test_*_files.txt'))
    assert 'Kestrel_drive.blf' in files.read_text(encoding='utf-8')
