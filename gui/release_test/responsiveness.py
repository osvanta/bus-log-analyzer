# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Responsiveness: how long the window is busy for what a user does all the time.

``--known-good <file>`` adds one responsiveness scenario per measurement in
the file's table of responsiveness measurements (P1, P2, …). Each starts the
application afresh, decodes the measurement, plots six signals in Stacked and
then opens the Table, the CAN Trace, the XY plot, the histogram, the
residency map and the GPS track one after another. With the plot alone and
after each panel it times, as the median of five:

- Cursor 1 moved to another part of the measurement, and by a small step;
- a zoom, and the time shown moved on;
- the window resized, and repainted with nothing changed;
- the signal panel hidden or shown.

"Busy" is the time the GUI thread works after the action until it has been
quiet for 150 ms: what the user waits for, painting included. Opening each
panel and plotting the six signals are timed the same way.

The limits are in the known-good file::

    | Ref | File (relative to the test folder) | ... |
    | P1  | samples/drive.asc                  | ... |

    | ID     | ... | From source: baseline | From source: limit | Packaged: baseline | Packaged: limit |
    | RS-101 | ... | 240 ms                | 288 ms             | ...                | ...             |

An item's number is its measurement's (1 for P1) and its place in a fixed
order: plotting, the six panels opened, then the seven actions for each of
the seven states. A limit is its baseline + 20 %, rounded up to a whole
millisecond, written once and kept until the owner changes it. An item
without one is reported with the limit a first baseline would give, and the
report ends with the rows to put into the file.

Limits this tight only mean something on a PC running as it did for the
baseline. Besides the power source and the PC speed before and after the
runs (the timing check's KG-00), the PC speed is taken before and after each
state: a state measured while the PC was slower than KG-00's limit is left
out, and an item with no state left is not judged, which is not a pass.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from gui.release_test import timing
from gui.release_test.plan import Measurement, Scenario, Step

FAMILY = 'responsive'
LIMIT_FACTOR = 1.20
_REFERENCE_ID = re.compile(r'P\d+$')
_ITEM_ID = re.compile(r'RS-\d+$')

#: The states, in the order they are reached: the plot alone, then each panel
#: opened on top of the ones before.
STATES = (
    ('plot', 'plot only'),
    ('table', 'Table open'),
    ('trace', 'and CAN Trace'),
    ('xy', 'and XY plot'),
    ('histogram', 'and histogram'),
    ('residency', 'and residency map'),
    ('gps', 'and GPS track'),
)
#: What is timed in each state.
ACTIONS = (
    ('cursor_jump', 'Cursor 1 moved to another part'),
    ('cursor_step', 'Cursor 1 moved a small step'),
    ('zoom', 'zoom'),
    ('pan', 'time shown moved on'),
    ('resize', 'window resized'),
    ('repaint', 'window repainted'),
    ('signal_panel', 'signal panel hidden or shown'),
)
#: What is timed once: plotting, and each panel opening.
OPENED = (
    ('plot', 'six signals plotted in Stacked'),
    ('table', 'Table opened'),
    ('trace', 'CAN Trace opened'),
    ('xy', 'XY plot opened'),
    ('histogram', 'histogram opened'),
    ('residency', 'residency map opened'),
    ('gps', 'GPS track opened'),
)


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    reference: str              # P1, P2, …
    state: str                  # the state it is measured in, or reached by
    action: str                 # an action of ACTIONS; 'open' for OPENED


def items_for(references: list[str]) -> list[Item]:
    """RS-101, RS-102, … for P1, RS-201, … for P2: what is timed once, then
    every action in every state."""
    found = []
    for reference in references:
        block = int(reference[1:]) * 100
        number = 0
        for state, title in OPENED:
            number += 1
            found.append(Item(f'RS-{block + number}', f'{reference} {title}',
                              reference, state, 'open'))
        for state, state_title in STATES:
            for action, action_title in ACTIONS:
                number += 1
                found.append(Item(f'RS-{block + number}',
                                  f'{reference} {state_title}: {action_title}',
                                  reference, state, action))
    return found


def read_known_good(path: Path) -> timing.KnownGood:
    """The responsiveness measurements (P1, …) and limits (RS-…) in a
    known-good file, with the PC-speed gate (KG-00) the timing check uses."""
    known = timing.read_known_good(path, reference_id=_REFERENCE_ID, item_id=_ITEM_ID)
    gate = timing.read_known_good(path).values.get(timing.SPEED_ID)
    if gate:
        known.values[timing.SPEED_ID] = gate
    return known


def scenarios(measurements: list[Measurement], references: dict[str, str],
              folder: Path) -> tuple[list[Scenario], list[str]]:
    """One scenario per responsiveness measurement, and why any is missing."""
    found, problems = [], []
    by_path = {m.path.resolve(): index for index, m in enumerate(measurements)}
    for reference, relative in references.items():
        index = by_path.get((folder / relative).resolve())
        if index is None:
            problems.append(f'{reference}: {relative} is not in the test folder')
            continue
        if measurements[index].skipped or measurements[index].broken:
            problems.append(f'{reference}: {relative} cannot be loaded in a release test')
            continue
        found.append(Scenario(
            f'{FAMILY}_{reference}',
            f'Responsiveness {reference} = {measurements[index].alias}',
            (Step('open', index), Step('settle'), Step('load'), Step('responsive')),
            family=FAMILY, reference=reference,
        ))
    return found, problems


def first_limit(baseline_ms: float) -> int:
    """The limit a baseline gives: + 20 %, rounded up to a whole millisecond."""
    return int(math.ceil(baseline_ms * LIMIT_FACTOR))


# ── Results ──────────────────────────────────────────────────────────────

def collect(runs, items: list[Item], speed_gate: float | None) -> tuple[dict[str, list], dict[str, int]]:
    """Every value each item got, one per round that measured it on a PC at
    its usual speed; and for each item how many rounds were left out because
    the PC was slower than *speed_gate* while its state was measured."""
    values: dict[str, list] = {item.id: [] for item in items}
    left_out: dict[str, int] = {item.id: 0 for item in items}
    by_reference: dict[str, list[Item]] = {}
    for item in items:
        by_reference.setdefault(item.reference, []).append(item)
    for run in runs:
        if run.scenario.family != FAMILY or run.skipped:
            continue
        step = next((s for s in run.result.get('steps', []) if s['op'] == 'responsive'), None)
        if step is None:
            continue
        rows, opened = step.get('rows', {}), step.get('opened_ms', {})
        for item in by_reference.get(run.scenario.reference, []):
            row = rows.get(item.state)
            if row is None:
                continue
            value = opened.get(item.state) if item.action == 'open' else row.get(item.action)
            if value is None:
                continue
            if speed_gate is not None and row.get('speed_ms', 0.0) > speed_gate:
                left_out[item.id] += 1
            else:
                values[item.id].append(value)
    return values, left_out


@dataclass
class Verdict:
    item: Item
    text: str                   # the line for the report
    failed: bool
    judged: bool = True         # False: measured only while the PC was slow


def judge_items(values: dict[str, list], left_out: dict[str, int], items: list[Item],
                known: timing.KnownGood, build: str, judge_times: bool = True) -> list[Verdict]:
    verdicts = []
    for item in items:
        got = values.get(item.id, [])
        baseline = known.value(item.id, build, 'baseline')
        limit = known.value(item.id, build, 'limit')
        label = f'{item.id}  {item.title:58}'
        slow = left_out.get(item.id, 0)
        slow_note = f'   {slow} round(s) left out: PC slow' if slow else ''
        if not got:
            if slow:
                verdicts.append(Verdict(item, f'{label}  measured only while the PC was slow'
                                              '   not judged', False, judged=False))
            else:
                verdicts.append(Verdict(item, f'{label}  not measured',
                                        limit is not None and judge_times))
            continue
        median = statistics.median(got)
        measured = f'{median:7,.0f} ms  (runs {min(got):,.0f}–{max(got):,.0f} ms)'
        if limit is None:
            verdicts.append(Verdict(
                item, f'{label}{measured}   no limit yet; a first limit would be '
                      f'{first_limit(round(median)):,} ms{slow_note}', False))
            continue
        margin = ''
        if baseline is not None and limit > baseline:
            used = (median - baseline) / (limit - baseline) * 100
            margin = f', {used:.0f} % of the margin used' if used > 0 else ', faster than baseline'
        if not judge_times:
            verdicts.append(Verdict(
                item, f'{label}{measured}   limit {limit:,.0f} ms{margin}   not judged', False))
            continue
        failed = median > limit
        verdicts.append(Verdict(
            item, f'{label}{measured}   limit {limit:,.0f} ms{margin}   '
                  f'{"FAIL" if failed else "PASS"}{slow_note}', failed))
    return verdicts


def table_rows(values: dict[str, list], items: list[Item], known: timing.KnownGood,
               build: str) -> list[str]:
    """The limits table for the known-good file: what it holds already, and
    for an item of *build* without a limit its median as baseline with the
    limit that gives."""
    lines = ['| ID | Requirement | From source: baseline | From source: limit '
             '| Packaged: baseline | Packaged: limit |',
             '|---|---|---|---|---|---|']
    for item in items:
        cells = []
        for column in timing.BUILDS:
            baseline = known.value(item.id, column, 'baseline')
            limit = known.value(item.id, column, 'limit')
            if limit is None and column == build and values.get(item.id):
                baseline = round(statistics.median(values[item.id]))
                limit = first_limit(baseline)
            cells += ['' if baseline is None else f'{baseline:,.0f} ms',
                      '' if limit is None else f'{limit:,.0f} ms']
        lines.append(f'| {item.id} | {item.title} | ' + ' | '.join(cells) + ' |')
    return lines


def summary(runs, known: timing.KnownGood, packaged: bool,
            states: list[timing.PcState] = ()) -> tuple[list[str], bool]:
    """The report's responsiveness section, and whether the check passed:
    every item kept its limit, measured on a PC whose times could be judged."""
    build = 'packaged' if packaged else 'source'
    items = items_for(list(known.references))
    references = {run.scenario.reference: run.scenario.title.split('= ')[-1]
                  for run in runs if run.scenario.family == FAMILY}
    reason, speed_lines = timing._not_judged(list(states), known, build)
    gate = known.value(timing.SPEED_ID, build, 'limit')
    values, left_out = collect(runs, items, gate)
    verdicts = judge_items(values, left_out, items, known, build, judge_times=not reason)
    failed = [verdict for verdict in verdicts if verdict.failed]
    unjudged = [verdict for verdict in verdicts if not verdict.judged]
    if unjudged and not reason:
        reason = (f'{len(unjudged)} item(s) were measured only while the PC was slower than at '
                  'the baseline (KG-00): let it cool down and run again')
    rounds = max((run.round for run in runs if run.scenario.family == FAMILY), default=0)
    outcome = 'FAIL' if failed else 'NOT JUDGED' if reason else 'PASS'
    lines = [
        f'Responsiveness: {outcome}   '
        f'({"packaged" if packaged else "from source"}, median of {rounds} round(s), '
        f'limits for "{build}" in {known.path.name}: baseline + 20 %)',
        *([f'Times not judged: {reason}.'] if reason else []),
        'Measurements: ' + ', '.join(f'{ref} = {alias}' for ref, alias in references.items()),
        '',
        *(speed_lines if states else []),
        *(verdict.text for verdict in verdicts),
        '',
    ]
    without = [item for item in items
               if known.value(item.id, build, 'limit') is None and values.get(item.id)]
    if without:
        lines += [f'{len(without)} item(s) have no limit for "{build}" yet. The table for '
                  f'{known.path.name}, with their medians as baselines:',
                  *table_rows(values, items, known, build), '']
    return lines, not failed and not reason
