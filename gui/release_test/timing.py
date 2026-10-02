# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Known-good timing: what a user waits for, against fixed limits.

``--known-good <file>`` adds one timing scenario per reference measurement.
Each starts the application afresh and times:

- the splash, the main window, and its event loop running, so that it
  answers: stamped by the application, less the runner's launch time;
- Open File, clicked as soon as the window is up: start to file opened,
  less the time any dialog waited on screen for its answer;
- Load + Decode, from the click until the signal tree lists the signals;
- five signals plotted in Stacked, until they are drawn.

The decode result (signals, frames, decoded frames, samples) must match
exactly. Every time comes from ``time.perf_counter``, the system's
performance counter, which all processes on the PC share.

The known-good file is Markdown. Two of its tables are read::

    | Ref | File (relative to the test folder) | ... |
    | R1  | signals.mf4                        | ... |

    | ID    | ... | From source: baseline | From source: limit | Packaged: baseline | Packaged: limit |
    | KG-01 | ... | 310 ms                | 550 ms             | ...                | ...             |

Columns are found by their headings. A time is ``310 ms`` or ``1.25 s``; a
decode result is ``12,550 signals, 0 frames, 0 decoded, 21,394,230 samples``.
A limit is written once, from a baseline, and stays until the owner changes
it: it never follows the latest run. An empty limit only reports the value,
with the limit a first baseline would give (baseline + 10 % + 200 ms).

Fixed limits only mean something on a PC in the state the baseline was taken
in. A laptop on battery runs far slower, and so does a PC busy with other
programs. Before and after the runs the check therefore notes the power
source and times a fixed computation (KG-00, the PC speed). On battery, or
slower than KG-00's limit, the times are reported but not judged, and the
check does not pass. The decode results are judged either way.
"""

from __future__ import annotations

import ctypes
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from gui.release_test.plan import Measurement, Scenario, Step

FAMILY = 'timing'
BUILDS = ('source', 'packaged')
COUNTS = ('signals', 'frames', 'decoded', 'samples')
LIMIT_FACTOR = 1.10
LIMIT_MARGIN_MS = 200
SPEED_ID = 'KG-00'
SPEED_GATE_FACTOR = 1.10    # KG-00's limit: at most 10 % slower than at the baseline
_STARTUP = (('KG-01', 'splash', 'Splash appears'),
            ('KG-02', 'window', 'Main window appears'),
            ('KG-03', 'answering', 'Main window answers'))
_PER_REFERENCE = ('opened', 'listed', 'drawn', 'decoded')
_TIME = re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*(ms|s)\b')
_COUNT = re.compile(r'(\d[\d,]*)\s+(' + '|'.join(COUNTS) + r')\b')
_REFERENCE_ID = re.compile(r'R\d+$')
_ITEM_ID = re.compile(r'KG-\d+$')


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    measure: str                # splash, window, answering, opened, listed, drawn, decoded
    reference: str = ''         # R1, R2, …; empty for the start-up items

    @property
    def is_count(self) -> bool:
        return self.measure == 'decoded'


def items_for(references: list[str]) -> list[Item]:
    """KG-01 to KG-03 for start-up, then four per reference, in order."""
    found = [Item(item_id, title, measure) for item_id, measure, title in _STARTUP]
    titles = {
        'opened': 'opened, from start-up',
        'listed': 'Load + Decode until the signal tree lists it',
        'drawn': 'five signals plotted in Stacked',
        'decoded': 'decode result',
    }
    for reference in references:
        for measure in _PER_REFERENCE:
            found.append(Item(f'KG-{len(found) + 1:02}', f'{reference} {titles[measure]}',
                              measure, reference))
    return found


@dataclass
class KnownGood:
    path: Path
    references: dict[str, str] = field(default_factory=dict)   # R1 → relative path
    # Item id → build → ('baseline' | 'limit') → milliseconds, or a count dict.
    values: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)

    def value(self, item_id: str, build: str, which: str):
        return self.values.get(item_id, {}).get(build, {}).get(which)


def read_known_good(path: Path) -> KnownGood:
    """The reference files and the fixed limits in a known-good file."""
    known = KnownGood(path)
    headings: list[str] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.lstrip().startswith('|'):
            headings = []
            continue
        cells = [cell.strip().strip('`').strip() for cell in line.strip().strip('|').split('|')]
        if not cells or set(cells[0]) <= set('-: '):
            continue
        if not headings:
            headings = [cell.lower() for cell in cells]
            continue
        if _REFERENCE_ID.match(cells[0]) and len(cells) > 1:
            known.references[cells[0]] = cells[1].replace('\\', '/')
        elif _ITEM_ID.match(cells[0]):
            for column, heading in enumerate(headings[:len(cells)]):
                build = next((b for b in BUILDS if b in heading), None)
                which = next((w for w in ('baseline', 'limit') if w in heading), None)
                if build and which:
                    parsed = _parse_cell(cells[column])
                    if parsed is not None:
                        known.values.setdefault(cells[0], {}).setdefault(build, {})[which] = parsed
    return known


def _parse_cell(text: str):
    counts = {name: int(number.replace(',', '')) for number, name in _COUNT.findall(text)}
    if counts:
        return counts
    match = _TIME.search(text)
    if match is None:
        return None
    number = float(match.group(1).replace(',', ''))
    return number * 1000 if match.group(2) == 's' else number


def timing_scenarios(measurements: list[Measurement], references: dict[str, str],
                     folder: Path) -> tuple[list[Scenario], list[str]]:
    """One scenario per reference measurement, and why any is missing."""
    scenarios, problems = [], []
    by_path = {m.path.resolve(): index for index, m in enumerate(measurements)}
    for reference, relative in references.items():
        index = by_path.get((folder / relative).resolve())
        if index is None:
            problems.append(f'{reference}: {relative} is not in the test folder')
            continue
        if measurements[index].skipped or measurements[index].broken:
            problems.append(f'{reference}: {relative} cannot be loaded in a release test')
            continue
        scenarios.append(Scenario(
            f'{FAMILY}_{reference}',
            f'Timing {reference} = {measurements[index].alias}',
            (Step('open', index), Step('settle'), Step('load'), Step('plot_stacked')),
            family=FAMILY, reference=reference,
        ))
    return scenarios, problems


# ── The PC's state ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class PcState:
    speed_ms: float             # a fixed computation, timed (KG-00)
    on_battery: bool


def pc_state() -> PcState:
    return PcState(pc_speed_ms(), _on_battery())


def pc_speed_ms() -> float:
    """How long a fixed computation takes: the median of seven tries, in ms.

    It runs on one core, as most of the application's work does, so a CPU
    slowed by power saving, heat or other programs takes longer.
    """
    tries = []
    for _ in range(7):
        started = time.perf_counter()
        total = 0
        for number in range(300_000):
            total += number * number % 7
        tries.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(tries), 1)


class _PowerStatus(ctypes.Structure):
    _fields_ = [('ACLineStatus', ctypes.c_ubyte), ('BatteryFlag', ctypes.c_ubyte),
                ('BatteryLifePercent', ctypes.c_ubyte), ('SystemStatusFlag', ctypes.c_ubyte),
                ('BatteryLifeTime', ctypes.c_ulong), ('BatteryFullLifeTime', ctypes.c_ulong)]


def _on_battery() -> bool:
    if sys.platform != 'win32':
        return False
    status = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return False
    return status.ACLineStatus == 0


def first_speed_limit(speed_ms: float) -> float:
    """KG-00's limit from its baseline: 10 % slower, rounded up to 0.1 ms."""
    return math.ceil(round(speed_ms * SPEED_GATE_FACTOR * 10, 3)) / 10


def _not_judged(states: list[PcState], known: KnownGood, build: str) -> tuple[str, list[str]]:
    """Why the times cannot be judged (empty when they can), and KG-00's line."""
    baseline = known.value(SPEED_ID, build, 'baseline')
    gate = known.value(SPEED_ID, build, 'limit')
    speeds = ', '.join(f'{state.speed_ms:,.1f} ms' for state in states)
    line = f'{SPEED_ID}  {"PC speed: a fixed computation, before and after":50}{speeds}'
    if gate is None:
        slowest = max((state.speed_ms for state in states), default=0.0)
        line += f'   no limit yet; a first limit would be {first_speed_limit(slowest):,.1f} ms'
    else:
        line += f'   limit {gate:,.1f} ms' + (f' (baseline {baseline:,.1f} ms)' if baseline else '')
    if any(state.on_battery for state in states):
        return 'the PC ran on battery: plug it in and run again', [line]
    if gate is not None and any(state.speed_ms > gate for state in states):
        return ('the PC was slower than at the baseline (KG-00): close other programs, '
                'let it cool down and run again'), [line]
    return '', [line]


# ── Results ──────────────────────────────────────────────────────────────

def collect(runs, items: list[Item]) -> dict[str, list]:
    """Every value each item got, one per run that measured it."""
    values: dict[str, list] = {item.id: [] for item in items}
    by_measure = {(item.reference, item.measure): item.id for item in items}
    for run in runs:
        if run.scenario.family != FAMILY or run.skipped:
            continue
        startup = run.startup
        for measure in ('splash', 'window', 'answering'):
            if measure in startup:
                values[by_measure[('', measure)]].append(startup[measure])
        reference = run.scenario.reference
        for step in run.result.get('steps', []):
            if step['op'] == 'open' and 'opened_at' in step and run.launched_at:
                values[by_measure[(reference, 'opened')]].append(round(
                    (step['opened_at'] - run.launched_at) * 1000 - step.get('open_dialog_ms', 0), 1))
            elif step['op'] == 'load' and 'load_ms' in step:
                values[by_measure[(reference, 'listed')]].append(step['load_ms'])
                values[by_measure[(reference, 'decoded')]].append(
                    {name: step.get(name, 0) for name in COUNTS})
            elif step['op'] == 'plot_stacked' and 'plot_ms' in step:
                values[by_measure[(reference, 'drawn')]].append(step['plot_ms'])
    return values


def first_limit(baseline_ms: float) -> int:
    """The limit a baseline gives: + 10 % + 200 ms, rounded up to 10 ms."""
    # Rounded first, or 1500 * 1.1 = 1650.0000000000002 rounds up to 1860.
    return int(math.ceil(round(baseline_ms * LIMIT_FACTOR + LIMIT_MARGIN_MS, 3) / 10) * 10)


@dataclass
class Verdict:
    item: Item
    text: str                   # the line for the report
    failed: bool


def judge_items(values: dict[str, list], items: list[Item], known: KnownGood,
                build: str, judge_times: bool = True) -> list[Verdict]:
    verdicts = []
    for item in items:
        got = values.get(item.id, [])
        baseline = known.value(item.id, build, 'baseline')
        limit = known.value(item.id, build, 'limit')
        if item.is_count:
            verdicts.append(_judge_count(item, got, limit))
        else:
            verdicts.append(_judge_time(item, got, baseline, limit, judge_times))
    return verdicts


def _judge_time(item: Item, got: list[float], baseline, limit, judge: bool = True) -> Verdict:
    label = f'{item.id}  {item.title:50}'
    if not got:
        return Verdict(item, f'{label}  not measured', limit is not None and judge)
    median = statistics.median(got)
    spread = f'{min(got):,.0f}–{max(got):,.0f}'
    measured = f'{median:7,.0f} ms  (runs {spread} ms)'
    if limit is None:
        return Verdict(item, f'{label}{measured}   no limit yet; a first limit would be '
                             f'{first_limit(median):,} ms', False)
    margin = ''
    if baseline is not None and limit > baseline:
        used = (median - baseline) / (limit - baseline) * 100
        margin = f', {used:.0f} % of the margin used' if used > 0 else ', faster than baseline'
    if not judge:
        return Verdict(item, f'{label}{measured}   limit {limit:,.0f} ms{margin}   not judged',
                       False)
    failed = median > limit
    return Verdict(item, f'{label}{measured}   limit {limit:,.0f} ms{margin}   '
                         f'{"FAIL" if failed else "PASS"}', failed)


def _judge_count(item: Item, got: list[dict], limit) -> Verdict:
    label = f'{item.id}  {item.title:50}'
    if not got:
        return Verdict(item, f'{label}  not measured', limit is not None)
    text = ', '.join(f'{got[-1][name]:,} {name}' for name in COUNTS)
    if any(result != got[0] for result in got):
        return Verdict(item, f'{label}{text}   differs between runs   FAIL', True)
    if limit is None:
        return Verdict(item, f'{label}{text}   no known-good result yet', False)
    expected = {name: limit.get(name, 0) for name in COUNTS}
    if got[0] != expected:
        known = ', '.join(f'{expected[name]:,} {name}' for name in COUNTS)
        return Verdict(item, f'{label}{text}   known good: {known}   FAIL', True)
    return Verdict(item, f'{label}{text}   matches   PASS', False)


def summary(runs, known: KnownGood, packaged: bool,
            states: list[PcState] = ()) -> tuple[list[str], bool]:
    """The report's timing section, and whether the check passed: every
    item kept its limit, on a PC whose times could be judged."""
    build = 'packaged' if packaged else 'source'
    items = items_for(list(known.references))
    references = {run.scenario.reference: run.scenario.title.split('= ')[-1]
                  for run in runs if run.scenario.family == FAMILY}
    reason, speed_lines = _not_judged(list(states), known, build)
    verdicts = judge_items(collect(runs, items), items, known, build, judge_times=not reason)
    failed = [verdict for verdict in verdicts if verdict.failed]
    rounds = max((run.round for run in runs if run.scenario.family == FAMILY), default=0)
    outcome = 'FAIL' if failed else 'NOT JUDGED' if reason else 'PASS'
    lines = [
        f'Known-good timing: {outcome}   '
        f'({"packaged" if packaged else "from source"}, median of {rounds} round(s), '
        f'limits for "{build}" in {known.path.name})',
        *([f'Times not judged: {reason}.'] if reason else []),
        'References: ' + ', '.join(f'{ref} = {alias}' for ref, alias in references.items()),
        '',
        *(speed_lines if states else []),
        *(verdict.text for verdict in verdicts),
        '',
    ]
    return lines, not failed and not reason
