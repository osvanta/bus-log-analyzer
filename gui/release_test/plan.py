# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Which measurements a release test uses, and what it does with them.

Every folder under the one given to ``--release-test`` is a group: the
databases in a folder decode the measurements in that folder.

- A single database (.dbc, .arxml or .ldf) is assigned to every channel of its
  bus, as choosing it in the Database Manager does.
- A saved channel configuration (.osvanta_ch) assigns several, and wins over
  loose databases. A database it names that no longer exists at the saved
  path is looked up by name in the same folder, so a configuration saved on
  another PC still works.
- Several databases and no channel configuration: the folder's measurements
  are skipped, because nothing says which database belongs to which channel.
- A measurement that needs no database is loaded without one.
- Measurements in a folder named ``broken`` are expected to fail. They pass
  when the application reports the failure and then loads a good file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core.bus_types import BusType, all_channels_key, encode_key
from core.channel_config import ChannelConfig
from core.readers import ALL_SUFFIXES

DATABASE_SUFFIXES = {'.dbc', '.arxml', '.ldf'}
CHANNEL_CONFIG_SUFFIXES = {ChannelConfig.FILE_EXTENSION, ChannelConfig.LEGACY_FILE_EXTENSION}
BROKEN_FOLDER = 'broken'


@dataclass(frozen=True)
class Measurement:
    path: Path
    alias: str                  # F1, F2, … — the report names files only by this
    size: int
    # Encoded channel key ("CAN:0") → database path; empty when none is needed.
    databases: dict[str, str] = field(default_factory=dict)
    broken: bool = False
    skipped: str = ''           # why it cannot be tested; empty when it can

    def describe(self) -> str:
        databases = len(set(self.databases.values()))
        size = f'{self.size / 1e6:8.1f} MB' if self.size >= 100_000 else f'{self.size / 1e3:8.1f} KB'
        return (
            f'{self.alias:4} {self.path.suffix.lower():5} {size}  '
            + (f'{databases} database(s)' if databases else 'no database')
            + ('  broken on purpose' if self.broken else '')
            + (f'  SKIPPED: {self.skipped}' if self.skipped else '')
        )

    def to_json(self) -> dict:
        return {
            'path': str(self.path), 'alias': self.alias,
            'databases': dict(self.databases), 'broken': self.broken,
        }


@dataclass(frozen=True)
class Step:
    op: str                     # open, load, plot, close_during_load
    file: int | None = None     # index into the measurements, for open

    def to_json(self) -> dict:
        return {'op': self.op, 'file': self.file}


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    steps: tuple[Step, ...]
    memcheck: bool = False      # run in the build with Python's memory checks

    @property
    def loads(self) -> int:
        return sum(step.op in ('load', 'close_during_load') for step in self.steps)


def find_measurements(folder: Path) -> list[Measurement]:
    """Every measurement under *folder*, with the databases that decode it."""
    found: list[tuple[Path, int, dict[str, str], bool, str]] = []
    for directory, subfolders, names in os.walk(folder):
        subfolders[:] = sorted(name for name in subfolders if not name.startswith('.'))
        here = Path(directory)
        files = sorted(here / name for name in names)
        measurements = [path for path in files if path.suffix.lower() in ALL_SUFFIXES]
        if not measurements:
            continue
        databases, skipped = _databases_for(here, files)
        broken = any(part.lower() == BROKEN_FOLDER for part in here.relative_to(folder).parts)
        for path in measurements:
            found.append((path, path.stat().st_size, databases, broken, skipped))
    return [
        Measurement(path, f'F{number}', size, databases, broken, skipped)
        for number, (path, size, databases, broken, skipped) in enumerate(found, 1)
    ]


def _databases_for(folder: Path, files: list[Path]) -> tuple[dict[str, str], str]:
    configs = [path for path in files if path.suffix.lower() in CHANNEL_CONFIG_SUFFIXES]
    databases = [path for path in files if path.suffix.lower() in DATABASE_SUFFIXES]
    if configs:
        if len(configs) > 1:
            return {}, 'several channel configurations in one folder'
        try:
            config = ChannelConfig.load(configs[0])
        except (OSError, ValueError) as exc:
            return {}, f'channel configuration unreadable: {exc}'
        channels = {}
        for key, saved in config.channels.items():
            path = Path(saved)
            if not path.exists() and (folder / path.name).exists():
                path = folder / path.name
            if not path.exists():
                return {}, f'database named in the channel configuration is missing: {path.name}'
            channels[encode_key(key)] = str(path)
        return channels, ''
    if len(databases) > 1:
        return {}, 'several databases and no channel configuration (.osvanta_ch) to assign them'
    if databases:
        bus = BusType.LIN if databases[0].suffix.lower() == '.ldf' else BusType.CAN
        return {encode_key(all_channels_key(bus)): str(databases[0])}, ''
    return {}, ''


def build_scenarios(measurements: list[Measurement], heavy: bool) -> list[Scenario]:
    """The standard scenarios, and with *heavy* the ones that load far more."""
    good = [index for index, m in enumerate(measurements) if not m.broken and not m.skipped]
    broken = [index for index, m in enumerate(measurements) if m.broken and not m.skipped]
    if not good:
        return []
    by_size = sorted(good, key=lambda index: measurements[index].size, reverse=True)
    a, b = by_size[0], by_size[1] if len(by_size) > 1 else by_size[0]
    smallest = by_size[-1]

    def load(index: int) -> tuple[Step, ...]:
        return Step('open', index), Step('load'), Step('plot')

    a_b_a = (*load(a), Step('load'), Step('plot'), *load(b), *load(a))
    scenarios = [
        Scenario('each_once', 'Every file once',
                 tuple(step for index in good for step in load(index))),
        Scenario('a_b_a', f'A → A → B → A  (A = {measurements[a].alias}, B = {measurements[b].alias})',
                 a_b_a),
        Scenario('close_during_load', f'Close during Load + Decode of {measurements[a].alias}',
                 (Step('open', a), Step('close_during_load'))),
    ]
    if broken:
        scenarios.append(Scenario(
            'broken_recovery', 'Broken files, then a good one',
            (*(step for index in broken for step in (Step('open', index), Step('load'))),
             *load(smallest)),
        ))
    if heavy:
        scenarios.append(Scenario(
            'b_five_times', f'B five times  (B = {measurements[b].alias})',
            tuple(step for _ in range(5) for step in load(b)),
        ))
        scenarios.append(Scenario(
            'memcheck_a_b_a', 'A → A → B → A with memory checks', a_b_a, memcheck=True,
        ))
    return scenarios
