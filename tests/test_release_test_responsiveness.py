# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Responsiveness in the release test: the limits file, the verdicts (a limit
is its baseline + 20 %), states measured on a slow PC left out, and a real run."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from gui.release_test import responsiveness, timing
from gui.release_test.plan import Measurement, Scenario, Step, build_scenarios, select
from gui.release_test.report import Run, judge, render

REPO_ROOT = Path(__file__).resolve().parents[1]

KNOWN_GOOD = """# Known-good baseline

| Ref | File (relative to the test folder) | Database |
|---|---|---|
| R1 | `signals.mf4` | none |

| ID | Requirement | From source: baseline | From source: limit | Packaged: baseline | Packaged: limit |
|---|---|---|---|---|---|
| KG-00 | PC speed | 40.0 ms | 44.0 ms | | |
| KG-01 | Splash appears | 300 ms | 530 ms | 480 ms | 730 ms |

## Responsiveness

| Ref | File (relative to the test folder) | Database |
|---|---|---|
| P1 | can\\drive.blf | the folder's DBC |
| P2 | `long/night.mf4` | the folder's DBC |

| ID | Requirement | From source: baseline | From source: limit | Packaged: baseline | Packaged: limit |
|---|---|---|---|---|---|
| RS-101 | P1 six signals plotted in Stacked | 240 ms | 288 ms | 250 ms | 300 ms |
| RS-109 | P1 plot only: Cursor 1 moved a small step | 17 ms | 21 ms | | |
| RS-151 | P1 and GPS track: Cursor 1 moved a small step | 36 ms | 44 ms | | |
"""
USUAL = [timing.PcState(40.0, False), timing.PcState(41.0, False)]


def _known(tmp_path, text=KNOWN_GOOD) -> timing.KnownGood:
    path = tmp_path / 'Known_good_baseline.md'
    path.write_text(text, encoding='utf-8')
    return responsiveness.read_known_good(path)


def _row(step_ms=17.0, speed=40.0, **changes) -> dict:
    row = {'cursor_jump': 20.0, 'cursor_step': step_ms, 'zoom': 54.0, 'pan': 39.0,
           'resize': 89.0, 'repaint': 14.0, 'signal_panel': 90.0, 'speed_ms': speed}
    row.update(changes)
    return row


def _run(round_, rows=None, opened=None, reference='P1', only=False) -> Run:
    """A round in which every state was measured as usual, but for the *rows*
    and *opened* given; with *only*, nothing but those was measured."""
    scenario = Scenario(f'responsive_{reference}', f'Responsiveness {reference} = F7',
                        (Step('open', 0), Step('settle'), Step('load'), Step('responsive')),
                        family='responsive', reference=reference)
    states = [state for state, _title in responsiveness.STATES]
    if not only:
        rows = {state: _row(step_ms=36.0 if state == 'gps' else 17.0) for state in states} | (rows or {})
        opened = {state: 100.0 for state in states} | {'plot': 240.0} | (opened or {})
    return Run(scenario, round_, exit_code=0, result={'finished': True, 'steps': [
        {'op': 'open'}, {'op': 'settle'}, {'op': 'load'},
        {'op': 'responsive', 'outcome': 'measured', 'rows': rows, 'opened_ms': opened,
         'window': [1260, 780]}]})


def _line(lines, item_id) -> str:
    return next(line for line in lines if line.startswith(item_id + ' '))


# ── The known-good file ──────────────────────────────────────────────────

def test_the_file_gives_the_measurements_and_limits_of_this_check_only(tmp_path):
    known = _known(tmp_path)

    assert known.references == {'P1': 'can/drive.blf', 'P2': 'long/night.mf4'}
    assert known.value('RS-109', 'source', 'baseline') == 17
    assert known.value('RS-109', 'source', 'limit') == 21
    assert known.value('RS-101', 'packaged', 'limit') == 300
    assert known.value('RS-109', 'packaged', 'limit') is None
    assert known.value('KG-01', 'source', 'limit') is None      # the timing check's
    assert known.value('KG-00', 'source', 'limit') == 44.0      # the PC-speed gate, shared
    # And the timing check takes none of this check's rows.
    timed = timing.read_known_good(known.path)
    assert timed.references == {'R1': 'signals.mf4'}
    assert timed.value('RS-109', 'source', 'limit') is None


def test_items_are_numbered_by_measurement_then_in_a_fixed_order():
    items = responsiveness.items_for(['P1', 'P2'])

    assert len(items) == 2 * (7 + 7 * 7)
    assert [(item.id, item.state, item.action) for item in items[:9]] == [
        ('RS-101', 'plot', 'open'), ('RS-102', 'table', 'open'), ('RS-103', 'trace', 'open'),
        ('RS-104', 'xy', 'open'), ('RS-105', 'histogram', 'open'),
        ('RS-106', 'residency', 'open'), ('RS-107', 'gps', 'open'),
        ('RS-108', 'plot', 'cursor_jump'), ('RS-109', 'plot', 'cursor_step')]
    assert items[55].id == 'RS-156' and (items[55].state, items[55].action) == ('gps', 'signal_panel')
    assert items[56].id == 'RS-201' and items[56].reference == 'P2'
    assert items[8].title == 'P1 plot only: Cursor 1 moved a small step'
    assert items[50].title == 'P1 and GPS track: Cursor 1 moved a small step'
    assert len({item.id for item in items}) == len(items)


def test_each_measurement_gets_its_own_scenario(tmp_path):
    folder = tmp_path
    measurements = [
        Measurement(folder / 'can' / 'drive.blf', 'F1', 10),
        Measurement(folder / 'other.mf4', 'F2', 20),
        Measurement(folder / 'long' / 'night.mf4', 'F3', 30, skipped='several databases'),
    ]

    found, problems = responsiveness.scenarios(
        measurements, {'P1': 'can/drive.blf', 'P2': 'long/night.mf4', 'P3': 'gone.asc'}, folder)

    assert [(s.name, s.family, s.reference) for s in found] == [('responsive_P1', 'responsive', 'P1')]
    assert [step.op for step in found[0].steps] == ['open', 'settle', 'load', 'responsive']
    assert found[0].steps[0].file == 0 and found[0].title == 'Responsiveness P1 = F1'
    assert problems == ['P2: long/night.mf4 cannot be loaded in a release test',
                        'P3: gone.asc is not in the test folder']
    every = build_scenarios(measurements, heavy=False) + found
    assert select(every, 'responsive') == found
    assert [s.name for s in select(every, 'a_b_a,responsive_P1')] == ['a_b_a', 'responsive_P1']


# ── Limits and verdicts ──────────────────────────────────────────────────

def test_a_limit_is_its_baseline_plus_a_fifth_rounded_up():
    assert responsiveness.first_limit(17) == 21        # 20.4
    assert responsiveness.first_limit(15) == 18        # exactly: nothing to round up
    assert responsiveness.first_limit(36) == 44        # 43.2
    assert responsiveness.first_limit(100) == 120
    assert responsiveness.first_limit(240) == 288


def test_each_item_takes_the_median_of_its_rounds(tmp_path):
    known = _known(tmp_path)
    runs = [_run(n, rows={'plot': _row(step_ms=ms)}, opened={'plot': opened})
            for n, (ms, opened) in enumerate(((16.0, 230.0), (40.0, 900.0), (18.0, 250.0)), 1)]

    lines, ok = responsiveness.summary(runs, known, packaged=False, states=USUAL)

    assert ok and lines[0].startswith('Responsiveness: PASS   (from source, median of 3 round(s)')
    assert 'baseline + 20 %' in lines[0]
    step = _line(lines, 'RS-109')
    assert '18 ms  (runs 16–40 ms)' in step and 'limit 21 ms' in step and step.endswith('PASS')
    assert '50 % of the margin used' not in step and '25 % of the margin used' in step
    assert '250 ms  (runs 230–900 ms)' in _line(lines, 'RS-101')


def test_a_median_over_its_limit_fails_and_one_at_it_passes(tmp_path):
    known = _known(tmp_path)

    at, at_ok = responsiveness.summary(
        [_run(n, rows={'plot': _row(step_ms=21.0)}) for n in (1, 2, 3)], known, False, USUAL)
    over, over_ok = responsiveness.summary(
        [_run(n, rows={'plot': _row(step_ms=21.6)}) for n in (1, 2, 3)], known, False, USUAL)

    assert at_ok and _line(at, 'RS-109').endswith('PASS')
    assert not over_ok and over[0].startswith('Responsiveness: FAIL')
    assert _line(over, 'RS-109').endswith('FAIL')


def test_an_item_with_a_limit_that_was_not_measured_fails(tmp_path):
    known = _known(tmp_path)

    lines, ok = responsiveness.summary([_run(1, rows={'plot': _row()}, opened={}, only=True)],
                                       known, False, USUAL)

    assert not ok
    assert _line(lines, 'RS-101').rstrip().endswith('not measured')     # has a limit
    assert _line(lines, 'RS-151').rstrip().endswith('not measured')
    assert 'not measured' in _line(lines, 'RS-102')                     # no limit: reported only


def test_the_packaged_build_is_judged_by_its_own_limits(tmp_path):
    known = _known(tmp_path)
    runs = [_run(n, opened={'plot': 295.0}) for n in (1, 2, 3)]

    source, source_ok = responsiveness.summary(runs, known, packaged=False, states=USUAL)
    packaged, packaged_ok = responsiveness.summary(runs, known, packaged=True, states=USUAL)

    assert not source_ok and 'limit 288 ms' in _line(source, 'RS-101')
    assert packaged_ok and 'limit 300 ms' in _line(packaged, 'RS-101')
    assert 'limits for "packaged"' in packaged[0]


# ── A PC that cannot be judged ───────────────────────────────────────────

def test_a_state_measured_while_the_pc_was_slow_is_left_out(tmp_path):
    known = _known(tmp_path)
    # Round 2 was throttled while the plot's state was measured: twice as slow.
    runs = [_run(1, rows={'plot': _row(step_ms=17.0)}),
            _run(2, rows={'plot': _row(step_ms=35.0, speed=80.0)}),
            _run(3, rows={'plot': _row(step_ms=35.0, speed=80.0)}),
            _run(4, rows={'plot': _row(step_ms=19.0)})]

    lines, ok = responsiveness.summary(runs, known, False, USUAL)

    step = _line(lines, 'RS-109')
    assert ok, lines
    assert '18 ms  (runs 17–19 ms)' in step and step.endswith('2 round(s) left out: PC slow')
    # What is timed once belongs to the state it leads to.
    assert _line(lines, 'RS-101').endswith('2 round(s) left out: PC slow')


def test_an_item_measured_only_on_a_slow_pc_is_not_judged(tmp_path):
    known = _known(tmp_path)
    runs = [_run(n, rows={'plot': _row(step_ms=35.0, speed=80.0), 'gps': _row(step_ms=36.0)})
            for n in (1, 2)]

    lines, ok = responsiveness.summary(runs, known, False, USUAL)

    assert not ok   # not a pass either
    assert lines[0].startswith('Responsiveness: NOT JUDGED')
    assert 'slower than at the baseline (KG-00)' in lines[1]
    assert _line(lines, 'RS-109').endswith('not judged')
    assert _line(lines, 'RS-151').endswith('PASS')      # the state measured at the usual speed
    assert not any(line.endswith('FAIL') for line in lines)


def test_nothing_is_judged_on_battery(tmp_path):
    known = _known(tmp_path)
    runs = [_run(n, rows={'plot': _row(step_ms=60.0)}) for n in (1, 2, 3)]

    lines, ok = responsiveness.summary(runs, known, False, [timing.PcState(40.0, on_battery=True)])

    assert not ok and lines[0].startswith('Responsiveness: NOT JUDGED')
    assert 'the PC ran on battery' in lines[1]
    assert _line(lines, 'RS-109').endswith('not judged')
    assert not any(line.endswith('FAIL') for line in lines)


def test_without_a_speed_limit_every_round_counts(tmp_path):
    known = _known(tmp_path, KNOWN_GOOD.replace('| KG-00 | PC speed | 40.0 ms | 44.0 ms | | |\n', ''))
    runs = [_run(n, rows={'plot': _row(step_ms=20.0, speed=500.0)}) for n in (1, 2)]

    lines, ok = responsiveness.summary(runs, known, False, USUAL)

    assert ok and _line(lines, 'RS-109').endswith('PASS')


# ── A first baseline ─────────────────────────────────────────────────────

def test_an_item_without_a_limit_reports_what_its_first_limit_would_be(tmp_path):
    known = _known(tmp_path)
    # Cursor 1's small step, which has its limit, took 19 ms this time.
    runs = [_run(n, rows={'plot': _row(step_ms=19.0, zoom=ms)})
            for n, ms in enumerate((53.6, 54.4, 80.0), 1)]

    lines, ok = responsiveness.summary(runs, known, False, USUAL)

    assert ok   # nothing to fail yet
    zoom = _line(lines, 'RS-110')
    assert '54 ms' in zoom and 'no limit yet; a first limit would be 65 ms' in zoom
    # The table for the file: limits already there are kept as they are, both
    # builds; the others get this build's median and its limit.
    table = [line for line in lines if line.startswith('| RS-')]
    assert len(table) == 2 * 56
    assert '| RS-101 | P1 six signals plotted in Stacked | 240 ms | 288 ms | 250 ms | 300 ms |' in table
    assert '| RS-109 | P1 plot only: Cursor 1 moved a small step | 17 ms | 21 ms |  |  |' in table
    assert '| RS-110 | P1 plot only: zoom | 54 ms | 65 ms |  |  |' in table
    assert '| RS-201 | P2 six signals plotted in Stacked |  |  |  |  |' in table     # not run
    header = lines[lines.index(table[0]) - 2]
    assert header.startswith('| ID | Requirement | From source: baseline | From source: limit')


def test_a_packaged_first_baseline_fills_the_packaged_columns(tmp_path):
    known = _known(tmp_path)

    lines, _ok = responsiveness.summary([_run(1, rows={'plot': _row(step_ms=19.4)})],
                                        known, packaged=True, states=USUAL)

    table = [line for line in lines if line.startswith('| RS-')]
    assert '| RS-109 | P1 plot only: Cursor 1 moved a small step | 17 ms | 21 ms | 19 ms | 23 ms |' in table
    assert '| RS-101 | P1 six signals plotted in Stacked | 240 ms | 288 ms | 250 ms | 300 ms |' in table


def test_no_table_is_printed_once_every_item_has_its_limit(tmp_path):
    rows = '\n'.join(f'| {item.id} | {item.title} | 100 ms | 120 ms | | |'
                     for item in responsiveness.items_for(['P1']))
    known = _known(tmp_path, '| Ref | File |\n|---|---|\n| P1 | can/drive.blf |\n\n'
                             '| ID | Requirement | From source: baseline | From source: limit |\n'
                             '|---|---|---|---|\n' + rows + '\n')

    lines, ok = responsiveness.summary([_run(1), _run(2)], known, False, USUAL)

    assert not ok       # the plot took 240 ms against 120
    assert sum(line.endswith('FAIL') for line in lines) == 1
    assert sum(line.endswith('PASS') for line in lines) == 55
    assert not any(line.startswith('| RS-') for line in lines)


# ── In the report ────────────────────────────────────────────────────────

def test_a_run_that_did_not_measure_fails(tmp_path):
    measured, empty = _run(1), _run(1)
    empty.result['steps'][-1] = {'op': 'responsive', 'outcome': 'nothing to measure'}
    for run in (measured, empty):
        run.session = 'session\n  ended normally'
        judge(run, [])

    assert not any('responsive' in failure for failure in measured.failures)
    assert any('step 4 (responsive)' in failure and 'nothing to measure' in failure
               for failure in empty.failures)
    text = render(['header'], [], [measured])
    assert '7 state(s) timed in a window 1260 x 780' in text


def test_a_failed_responsiveness_section_fails_the_report(tmp_path):
    known = _known(tmp_path)
    runs = [_run(n, rows={'plot': _row(step_ms=30.0)}) for n in (1, 2, 3)]
    for run in runs:
        run.session = 'session\n  ended normally'

    section = responsiveness.summary(runs, known, False, USUAL)
    text = render(['header'], [], runs, [section])

    assert not section[1]
    assert 'RESULT: FAIL   3 of 3 runs passed; a known-good check did not pass' in text
    assert 'Responsiveness: FAIL' in text


# ── A real run ───────────────────────────────────────────────────────────

def test_the_responsiveness_scenario_times_a_real_run(tmp_path, blf_path, sample_dbc_path):
    folder = tmp_path / 'measurements'
    (folder / 'can').mkdir(parents=True)
    shutil.copy(blf_path, folder / 'can' / 'Kestrel_drive.blf')
    shutil.copy(sample_dbc_path, folder / 'can' / 'Kestrel_vehicle.dbc')
    known = tmp_path / 'Known_good_baseline.md'
    known.write_text(
        '| Ref | File |\n|---|---|\n| R1 | can/Kestrel_drive.blf |\n\n'
        '| Ref | File |\n|---|---|\n| P1 | can/Kestrel_drive.blf |\n', encoding='utf-8')

    completed = subprocess.run(
        [sys.executable, 'app.py', '--release-test', str(folder), '--only', 'responsive',
         '--known-good', str(known), '--quit-when-done'],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(REPO_ROOT),
                 PYTHONIOENCODING='utf-8', OSVANTA_CRASH_LOG=str(tmp_path / 'crash.log'),
                 OSVANTA_APP_LOG=str(tmp_path / 'app.log')),
        capture_output=True, text=True, timeout=600,
    )

    reports = list((folder / 'release_test_reports').glob('release_test_*_report.txt'))
    assert len(reports) == 1, completed.stdout[-4000:] + completed.stderr[-4000:]
    report = reports[0].read_text(encoding='utf-8')
    if 'the PC ran on battery' in report:
        assert completed.returncode == 1, report
        assert 'Responsiveness: NOT JUDGED' in report
    else:
        assert completed.returncode == 0, report
        assert 'RESULT: PASS' in report
        assert 'Responsiveness: PASS' in report
    # Only the check asked for: no timing section saying nothing was timed.
    assert 'Known-good timing' not in report
    assert report.count('] round 1  Responsiveness P1') == 1
    assert '[FAIL]' not in report
    lines = report.splitlines()
    assert any(line.startswith('KG-00  PC speed') for line in lines)
    measured = [line for line in lines if line.startswith('RS-1')]
    assert len(measured) == 56, report
    for line in measured:
        assert ' ms  (runs ' in line and 'a first limit would be' in line, line
    assert sum(line.startswith('| RS-1') for line in lines) == 56
    assert any('7 state(s) timed in a window 1260 x 780' in line for line in lines), report
    assert 'Kestrel' not in report
