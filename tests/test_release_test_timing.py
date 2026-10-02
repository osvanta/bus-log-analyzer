# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Known-good timing: the limits file, the verdicts, and a real timed run."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from gui.release_test import timing
from gui.release_test.plan import Measurement, Scenario, Step, build_scenarios, select
from gui.release_test.report import Run, render

REPO_ROOT = Path(__file__).resolve().parents[1]

KNOWN_GOOD = """# Known-good baseline

Reference files:

| Ref | File (relative to the test folder) | Database |
|---|---|---|
| R1 | `signals.mf4` | none |
| R2 | can\\drive.blf | the folder's DBC |

| ID | Requirement | From source: baseline | From source: limit | Packaged: baseline | Packaged: limit |
|---|---|---|---|---|---|
| KG-01 | Splash appears | 300 ms | 530 ms | 480 ms | 730 ms |
| KG-02 | Main window appears | 1.2 s | 1,520 ms | | |
| KG-07 | R1 decode result | 120 signals, 0 frames, 0 decoded, 6,000 samples | 120 signals, 0 frames, 0 decoded, 6,000 samples | | |
"""


def _known(tmp_path, text=KNOWN_GOOD) -> timing.KnownGood:
    path = tmp_path / 'Known_good_baseline.md'
    path.write_text(text, encoding='utf-8')
    return timing.read_known_good(path)


def test_the_known_good_file_gives_references_and_fixed_values(tmp_path):
    known = _known(tmp_path)

    assert known.references == {'R1': 'signals.mf4', 'R2': 'can/drive.blf'}
    assert known.value('KG-01', 'source', 'baseline') == 300
    assert known.value('KG-01', 'source', 'limit') == 530
    assert known.value('KG-01', 'packaged', 'limit') == 730
    assert known.value('KG-02', 'source', 'baseline') == 1200   # "1.2 s"
    assert known.value('KG-02', 'source', 'limit') == 1520      # "1,520 ms"
    assert known.value('KG-02', 'packaged', 'limit') is None    # empty cell
    assert known.value('KG-07', 'source', 'limit') == {
        'signals': 120, 'frames': 0, 'decoded': 0, 'samples': 6000}


def test_items_are_numbered_start_up_first_then_four_per_reference():
    items = timing.items_for(['R1', 'R2'])

    assert [(i.id, i.measure, i.reference) for i in items] == [
        ('KG-01', 'splash', ''), ('KG-02', 'window', ''), ('KG-03', 'answering', ''),
        ('KG-04', 'opened', 'R1'), ('KG-05', 'listed', 'R1'), ('KG-06', 'drawn', 'R1'),
        ('KG-07', 'decoded', 'R1'),
        ('KG-08', 'opened', 'R2'), ('KG-09', 'listed', 'R2'), ('KG-10', 'drawn', 'R2'),
        ('KG-11', 'decoded', 'R2'),
    ]


def test_each_reference_gets_its_own_timing_scenario(tmp_path):
    measurements = [
        Measurement(tmp_path / 'other.mf4', 'F1', 10),
        Measurement(tmp_path / 'signals.mf4', 'F2', 10),
        Measurement(tmp_path / 'can' / 'drive.blf', 'F3', 10, {'CAN:0': 'x.dbc'}),
    ]

    scenarios, problems = timing.timing_scenarios(
        measurements, {'R1': 'signals.mf4', 'R2': 'can/drive.blf', 'R3': 'gone.mf4'}, tmp_path)

    assert [(s.name, s.reference, s.family) for s in scenarios] == [
        ('timing_R1', 'R1', 'timing'), ('timing_R2', 'R2', 'timing')]
    assert [step.op for step in scenarios[0].steps] == ['open', 'settle', 'load', 'plot_stacked']
    assert scenarios[1].steps[0].file == 2
    assert problems == ['R3: gone.mf4 is not in the test folder']


def test_only_selects_scenarios_by_name_or_by_family(tmp_path):
    standard = build_scenarios([Measurement(tmp_path / 'a.mf4', 'F1', 10)], heavy=False)
    timed = [Scenario('timing_R1', 'Timing R1', (Step('open', 0),), family='timing')]
    every = standard + timed

    assert select(every, '') == every
    assert select(every, 'timing') == timed
    assert [s.name for s in select(every, 'a_b_a,timing')] == ['a_b_a', 'timing_R1']


def _timed_run(reference, round_, *, startup, opened_after, load_ms, plot_ms, counts):
    scenario = Scenario(f'timing_{reference}', f'Timing {reference} = F1',
                        (Step('open', 0), Step('settle'), Step('load'), Step('plot_stacked')),
                        family='timing', reference=reference)
    launched = 1000.0
    dialog_ms = 420.0   # a dialog waited this long on screen for its answer: not counted
    stamps = {f'{name}_at': launched + ms / 1000 for name, ms in startup.items()}
    return Run(scenario, round_, exit_code=0, launched_at=launched, result={
        'finished': True, **stamps, 'steps': [
            {'op': 'open', 'opened_at': launched + (opened_after + dialog_ms) / 1000,
             'open_dialog_ms': dialog_ms},
            {'op': 'settle'},
            {'op': 'load', 'load_ms': load_ms, **counts},
            {'op': 'plot_stacked', 'plot_ms': plot_ms},
        ]})


COUNTS = {'signals': 120, 'frames': 0, 'decoded': 0, 'samples': 6000}


def _rounds(splash_ms, counts=COUNTS):
    return [
        _timed_run('R1', number, startup={'splash': splash, 'window': 1100.0, 'answering': 1110.0},
                   opened_after=1500, load_ms=900, plot_ms=80, counts=counts)
        for number, splash in enumerate(splash_ms, 1)
    ]


def test_each_item_takes_the_median_of_its_runs(tmp_path):
    items = timing.items_for(['R1'])

    values = timing.collect(_rounds([310, 900, 320]), items)

    assert values['KG-01'] == [310, 900, 320]
    assert values['KG-04'] == [1500, 1500, 1500]   # less the dialog's 420 ms on screen
    assert values['KG-05'] == [900, 900, 900]
    assert values['KG-06'] == [80, 80, 80]
    assert values['KG-07'] == [COUNTS] * 3


def test_a_median_over_its_fixed_limit_fails(tmp_path):
    known = _known(tmp_path)

    within, ok = timing.summary(_rounds([310, 900, 320]), known, packaged=False)
    over, failed = timing.summary(_rounds([540, 550, 320]), known, packaged=False)

    assert ok and not failed
    assert any(line.startswith('KG-01') and line.endswith('PASS') for line in within)
    # Median 320 ms: 20 ms of the 230 ms between baseline and limit.
    assert any('KG-01' in line and '9 % of the margin used' in line for line in within)
    assert any(line.startswith('KG-01') and line.endswith('FAIL') for line in over)
    assert over[0].startswith('Known-good timing: FAIL')


def test_the_limit_stays_fixed_however_often_it_is_nearly_reached(tmp_path):
    # A limit never follows the last run: 520 ms passes every time, and the
    # report shows how little margin is left rather than moving the limit.
    known = _known(tmp_path)
    for _ in range(3):
        lines, ok = timing.summary(_rounds([520, 520, 520]), known, packaged=False)
        assert ok
        assert any('KG-01' in line and '96 % of the margin used' in line for line in lines)
    assert known.value('KG-01', 'source', 'limit') == 530


def test_an_item_without_a_limit_reports_what_its_first_limit_would_be(tmp_path):
    known = _known(tmp_path)

    lines, ok = timing.summary(_rounds([310, 320, 330]), known, packaged=False)

    # KG-04 (R1 opened) has no row: 1,500 ms → 1,500 + 10 % + 200 ms.
    assert ok
    assert any(line.startswith('KG-04') and 'a first limit would be 1,850 ms' in line
               for line in lines)
    assert timing.first_limit(1500) == 1850
    assert timing.first_limit(311) == 550   # rounded up to 10 ms


def test_a_decode_result_must_match_exactly(tmp_path):
    known = _known(tmp_path)

    _lines, ok = timing.summary(_rounds([310]), known, packaged=False)
    changed, failed = timing.summary(_rounds([310], counts={**COUNTS, 'samples': 5999}),
                                     known, packaged=False)

    assert ok and not failed
    assert any(line.startswith('KG-07') and 'known good: 120 signals' in line for line in changed)


def test_a_decode_result_that_changes_between_runs_fails(tmp_path):
    known = _known(tmp_path, KNOWN_GOOD.replace('| KG-07', '| KG-99'))   # no known result
    runs = _rounds([310, 310])
    runs[1].result['steps'][2]['samples'] = 6001

    lines, ok = timing.summary(runs, known, packaged=False)

    assert not ok
    assert any(line.startswith('KG-07') and 'differs between runs' in line for line in lines)


SPEED_ROW = '| KG-00 | PC speed | 40.0 ms | 44.0 ms | | |\n'   # appended to the limits table


def test_times_are_not_judged_on_battery(tmp_path):
    known = _known(tmp_path, KNOWN_GOOD + SPEED_ROW)

    lines, ok = timing.summary(_rounds([900, 900, 900]), known, packaged=False,
                               states=[timing.PcState(40.0, on_battery=True)])

    assert not ok   # not a pass either: nothing was checked
    assert lines[0].startswith('Known-good timing: NOT JUDGED')
    assert 'the PC ran on battery: plug it in' in lines[1]
    assert any(line.startswith('KG-01') and line.endswith('not judged') for line in lines)
    assert not any(line.endswith('FAIL') for line in lines)


def test_times_are_not_judged_on_a_pc_slower_than_at_the_baseline(tmp_path):
    known = _known(tmp_path, KNOWN_GOOD + SPEED_ROW)
    rounds = _rounds([310, 320, 330])

    slow, slow_ok = timing.summary(rounds, known, packaged=False, states=[
        timing.PcState(40.0, False), timing.PcState(45.0, False)])   # slowed during the runs
    usual, usual_ok = timing.summary(rounds, known, packaged=False, states=[
        timing.PcState(41.0, False), timing.PcState(43.9, False)])

    assert not slow_ok and 'slower than at the baseline (KG-00)' in slow[1]
    assert usual_ok and usual[0].startswith('Known-good timing: PASS')
    assert any(line.startswith('KG-00') and '41.0 ms, 43.9 ms' in line
               and 'limit 44.0 ms (baseline 40.0 ms)' in line for line in usual)


def test_a_decode_result_is_judged_even_when_the_times_are_not(tmp_path):
    known = _known(tmp_path, KNOWN_GOOD + SPEED_ROW)

    lines, ok = timing.summary(_rounds([310], counts={**COUNTS, 'samples': 1}), known,
                               packaged=False, states=[timing.PcState(40.0, on_battery=True)])

    assert not ok
    assert lines[0].startswith('Known-good timing: FAIL')
    assert any(line.startswith('KG-07') and line.endswith('FAIL') for line in lines)


def test_the_pc_speed_gets_its_limit_from_a_first_baseline(tmp_path):
    lines, ok = timing.summary(_rounds([310]), _known(tmp_path), packaged=False, states=[
        timing.PcState(15.4, False), timing.PcState(16.0, False)])

    assert ok
    assert any(line.startswith('KG-00') and '15.4 ms, 16.0 ms' in line
               and 'a first limit would be 17.6 ms' in line for line in lines)
    assert timing.first_speed_limit(16.0) == 17.6


def test_the_pc_check_times_a_fixed_computation_and_reads_the_power_source():
    state = timing.pc_state()

    assert state.speed_ms > 0
    assert isinstance(state.on_battery, bool)


def test_a_failed_timing_section_fails_the_report(tmp_path):
    runs = _rounds([310])

    text = render(['header'], [], runs, [(['Known-good timing: FAIL'], False)])

    assert 'RESULT: FAIL   1 of 1 runs passed; the known-good timing did not pass' in text
    assert 'Known-good timing: FAIL' in text
    assert 'tree listed 900 ms after the click' in text
    assert 'drawn in 80 ms' in text
    assert 'start-up            splash 310 ms, window 1,100 ms, answering 1,110 ms' in text


def test_the_timing_scenarios_time_a_real_run(tmp_path, blf_path, sample_dbc_path):
    folder = tmp_path / 'measurements'
    (folder / 'can').mkdir(parents=True)
    shutil.copy(blf_path, folder / 'can' / 'Kestrel_drive.blf')
    shutil.copy(sample_dbc_path, folder / 'can' / 'Kestrel_vehicle.dbc')
    shutil.copy(REPO_ROOT / 'tests' / 'fixtures' / 'sample_narrow.csv', folder / 'Kestrel_wheels.csv')
    known = tmp_path / 'Known_good_baseline.md'
    known.write_text(
        '| Ref | File |\n|---|---|\n| R1 | Kestrel_wheels.csv |\n| R2 | can/Kestrel_drive.blf |\n',
        encoding='utf-8')

    completed = subprocess.run(
        [sys.executable, 'app.py', '--release-test', str(folder), '--only', 'timing',
         '--known-good', str(known), '--quit-when-done'],
        cwd=REPO_ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(REPO_ROOT),
                 PYTHONIOENCODING='utf-8', OSVANTA_CRASH_LOG=str(tmp_path / 'crash.log')),
        capture_output=True, text=True, timeout=300,
    )

    reports = list(folder.glob('release_test_*_report.txt'))
    assert len(reports) == 1, completed.stdout[-4000:] + completed.stderr[-4000:]
    report = reports[0].read_text(encoding='utf-8')
    if 'the PC ran on battery' in report:
        # A laptop on battery: reported, but not judged, so not a pass.
        assert completed.returncode == 1, report
        assert 'Known-good timing: NOT JUDGED' in report
    else:
        assert completed.returncode == 0, report
        assert 'RESULT: PASS' in report
        assert 'Known-good timing: PASS' in report
    assert any(line.startswith('KG-00  PC speed') for line in report.splitlines())
    # WARN, not PASS: the offscreen platform warns that it has no fonts.
    assert report.count('] round 1  Timing') == 2
    assert '[FAIL]' not in report
    lines = report.splitlines()
    for item_id in ('KG-01', 'KG-02', 'KG-03', 'KG-04', 'KG-05', 'KG-06', 'KG-08', 'KG-09', 'KG-10'):
        line = next(line for line in lines if line.startswith(item_id))
        assert ' ms  (runs ' in line and 'a first limit would be' in line, line
    for item_id in ('KG-07', 'KG-11'):
        line = next(line for line in lines if line.startswith(item_id))
        assert ' signals, ' in line and 'no known-good result yet' in line, line
    assert 'Kestrel' not in report
