# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""THIRD_PARTY_NOTICES.md must agree with requirements.txt, with the release
lock requirements-release.txt, and with licenses/.

Qt is used under the LGPL, which requires the notice and the licence texts to
travel with the application, for the version actually shipped. These checks
catch the drift that made the notices name versions a release no longer
contained.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTICES = (REPO_ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
LICENCES = REPO_ROOT / 'licenses'

# "| `name` | version | licence | project |" rows of the component tables.
ROWS = re.findall(r'^\| `([^`]+)` \| ([^ |]+) \| ([^|]+) \|', NOTICES, re.MULTILINE)


def _normalised(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def _locked() -> dict[str, str]:
    """Every package a release is built with, and its version."""
    lock = (REPO_ROOT / 'requirements-release.txt').read_text(encoding='utf-8')
    return {_normalised(name): version
            for name, version in re.findall(r'^([A-Za-z0-9_.\-]+)==(\S+)', lock, re.MULTILINE)}


def _pinned(package: str) -> str:
    requirements = (REPO_ROOT / 'requirements.txt').read_text(encoding='utf-8')
    match = re.search(rf'^{re.escape(package)}==(\S+)', requirements, re.MULTILINE)
    assert match, f'{package} is not pinned in requirements.txt'
    return match.group(1)


def test_the_notices_name_the_qt_version_requirements_pin():
    version = _pinned('PySide6')
    listed = {name: v for name, v, _ in ROWS}
    for package in ('PySide6', 'PySide6_Essentials', 'shiboken6'):
        assert listed.get(package) == version, f'{package}: notices say {listed.get(package)}, pinned {version}'


def test_the_notices_name_the_lgpl_library_versions_requirements_pin():
    listed = {name: v for name, v, _ in ROWS}
    for package in ('python-can', 'asammdf'):
        version = _pinned(package)
        assert listed.get(package) == version, f'{package}: notices say {listed.get(package)}, pinned {version}'


def test_every_listed_component_has_its_licence_file():
    assert len(ROWS) > 20  # the tables were parsed
    missing = [f'{name}-{version}.txt' for name, version, _ in ROWS
               if not (LICENCES / f'{name}-{version}.txt').is_file()]
    assert missing == []


def test_the_lgpl_text_is_in_the_notices_and_in_licenses():
    assert 'GNU LESSER GENERAL PUBLIC LICENSE' in NOTICES
    assert (LICENCES / 'GNU-LGPL-3.0.txt').is_file()
    assert (LICENCES / 'GNU-GPL-3.0.txt').is_file()


def test_no_component_is_listed_under_a_gpl_only_licence():
    gpl_only = [(name, licence) for name, _, licence in ROWS
                if 'GPL' in licence and 'LGPL' not in licence]
    assert gpl_only == []


def test_every_licence_file_names_the_version_a_release_is_built_with():
    # A licence file per component and version (see the test above); the
    # release lock is what CI installs, so it decides which version ships.
    locked = _locked()
    assert len(locked) > 20  # the lock was parsed
    stale = []
    for path in LICENCES.glob('*.txt'):
        match = re.fullmatch(r'(.+)-(\d[\w.]*)', path.stem)
        if match and _normalised(match.group(1)) in locked:
            name, version = match.groups()
            if locked[_normalised(name)] != version:
                stale.append(f'{path.name}: the release lock pins {locked[_normalised(name)]}')
    assert stale == []


def test_every_listed_component_is_in_the_release_lock():
    locked = _locked()
    missing = [name for name, _, _ in ROWS if _normalised(name) not in locked]
    assert missing == []
