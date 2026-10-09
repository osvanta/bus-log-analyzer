# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""core/ must not load Qt or pyqtgraph, directly or through another module.

core/ holds the logic that has to run, and be tested, without a GUI: readers,
decoders and stores. Anything that needs Qt, such as a QObject worker or a
signal, lives in gui/.

Two checks. The source check finds a Qt import written anywhere in core/,
inside a function included. The import check imports every core/ module in a
fresh interpreter (this test session loaded PySide6 long ago) and catches Qt
arriving through another module. Both walk core/ on disk rather than the files
git tracks, so every core package present in a checkout is checked.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every Qt binding, the binding generators behind them, and pyqtgraph.
FORBIDDEN = (
    'PySide6', 'shiboken6', 'PySide2', 'shiboken2',
    'PyQt6', 'PyQt5', 'sip', 'qtpy', 'pyqtgraph',
)

# Imports each module named on the command line in turn and reports, for each,
# the forbidden libraries that appeared during its import.
_PROBE = r"""
import importlib, json, sys
forbidden = set(sys.argv[1].split(','))
def loaded():
    return {name.split('.')[0] for name in list(sys.modules)} & forbidden
report = {'gui': {}, 'errors': {}}
seen = loaded()
for module in sys.argv[2:]:
    try:
        importlib.import_module(module)
    except Exception as exc:
        report['errors'][module] = f'{type(exc).__name__}: {exc}'[:300]
    now = loaded()
    if now - seen:
        report['gui'][module] = sorted(now - seen)
        seen = now
print(json.dumps(report))
"""


def _core_files(root: Path) -> list[Path]:
    return [path for path in sorted((root / 'core').rglob('*.py'))
            if '__pycache__' not in path.parts]


def core_modules(root: Path) -> list[str]:
    modules = []
    for path in _core_files(root):
        parts = list(path.relative_to(root).with_suffix('').parts)
        if parts[-1] == '__init__':
            parts.pop()
        modules.append('.'.join(parts))
    return modules


def static_offenders(root: Path) -> dict[str, list[str]]:
    """Qt imports written anywhere in core/, by file."""
    found: dict[str, list[str]] = {}
    for path in _core_files(root):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split('.')[0] in FORBIDDEN:
                    found.setdefault(path.relative_to(root).as_posix(), []).append(
                        f'line {node.lineno}: {name}')
    return found


def _probe(root: Path, modules: list[str]) -> dict:
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)  # only the checkout under test is importable
    completed = subprocess.run(
        [sys.executable, '-c', _PROBE, ','.join(FORBIDDEN), *modules],
        cwd=root, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


def runtime_offenders(root: Path, modules: list[str]) -> tuple[dict, dict]:
    """(modules whose import loads Qt, modules that fail to import).

    One interpreter imports them all, which is quick. Only when that finds Qt
    is each module imported again on its own, so a failure names every
    offender rather than the first.
    """
    report = _probe(root, modules)
    if not report['gui']:
        return {}, report['errors']
    gui = {}
    for module in modules:
        alone = _probe(root, [module])['gui']
        if module in alone:
            gui[module] = alone[module]
    return gui, report['errors']


# ── The rule ────────────────────────────────────────────────────────────────

def test_no_qt_import_is_written_anywhere_in_core():
    assert static_offenders(REPO_ROOT) == {}, (
        'Qt or pyqtgraph is imported in core/. Move the Qt part to gui/.')


def test_importing_any_core_module_loads_no_qt():
    modules = core_modules(REPO_ROOT)
    assert 'core.signal_store' in modules  # the walk found the package

    gui, errors = runtime_offenders(REPO_ROOT, modules)

    assert errors == {}, f'core modules that cannot be imported on their own: {errors}'
    assert gui == {}, (
        f'core modules that load Qt or pyqtgraph when imported: {gui}. '
        'Move the Qt part to gui/.')


# ── The guard itself: it must be able to fail ─────────────────────────────

@pytest.fixture
def fake_checkout(tmp_path):
    core = tmp_path / 'core'
    (core / 'feature').mkdir(parents=True)
    (core / '__init__.py').write_text('')
    (core / 'clean.py').write_text('import json\n')
    (core / 'direct.py').write_text('from PySide6.QtCore import QObject\n')
    (core / 'feature' / '__init__.py').write_text('')
    (core / 'feature' / 'through.py').write_text('import core.direct\n')
    (core / 'lazy.py').write_text('def plot():\n    import pyqtgraph\n')
    return tmp_path


def test_the_source_check_finds_direct_and_function_level_qt_imports(fake_checkout):
    assert static_offenders(fake_checkout) == {
        'core/direct.py': ['line 1: PySide6.QtCore'],
        'core/lazy.py': ['line 2: pyqtgraph'],
    }


def test_the_import_check_finds_qt_loaded_directly_or_through_another_module(fake_checkout):
    modules = core_modules(fake_checkout)
    assert modules == ['core', 'core.clean', 'core.direct', 'core.feature',
                       'core.feature.through', 'core.lazy']

    gui, errors = runtime_offenders(fake_checkout, modules)

    assert errors == {}
    assert gui == {
        'core.direct': ['PySide6', 'shiboken6'],
        'core.feature.through': ['PySide6', 'shiboken6'],
    }
