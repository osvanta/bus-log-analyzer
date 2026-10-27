# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A saved signal selection, as Convert files keeps it: signal names, matched by
name (not by group or position) so one selection works on every file. An
entry with * or ? is a pattern. Case is ignored.

As a text file, one entry a line; blank lines and lines starting with # are
left out, so a team can share and annotate a list.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path


def _matches(entry: str, name: str) -> bool:
    entry, name = entry.casefold(), name.casefold()
    if any(c in entry for c in '*?'):
        return fnmatchcase(name, entry)
    return entry == name


def apply_selection(entries, names) -> tuple[list[int], list[str]]:
    """Which of *names* the *entries* pick, as positions in *names*, and the
    entries that pick none of them."""
    entries = [e.strip() for e in entries if e and e.strip()]
    picked: list[int] = []
    missing: list[str] = []
    for entry in entries:
        hits = [i for i, name in enumerate(names) if _matches(entry, name)]
        if not hits:
            missing.append(entry)
        picked.extend(hits)
    return sorted(set(picked)), missing


def selection_of(names) -> list[str]:
    """The entries that save *names*: each name once, in order."""
    return list(dict.fromkeys(name for name in names if name))


def read_selection(path: str | Path) -> list[str]:
    text = Path(path).read_text(encoding='utf-8-sig')
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def write_selection(path: str | Path, entries) -> None:
    Path(path).write_text(''.join(f'{entry}\n' for entry in entries), encoding='utf-8')
