# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The release test: the built application, driven through real measurements.

Every release is tested as a user gets it: the packaged build, real
measurements, the sequences that crashed before (A → B → A, one file five
times, closing the window during Load + Decode) and a run with Python's
memory checks, since damaged memory usually goes unnoticed until a later load.

It is hidden, and needs nothing but the release zip::

    BusLogAnalyzer.exe --release-test <folder> [--heavy] [--repeat N]

``--heavy`` adds loading one file five times and the memory-checked run.
``--repeat`` runs every scenario N times. ``--only a_b_a,each_once`` limits
the scenarios, and ``--quit-when-done`` closes the runner at the end with exit
code 0 when every run passed. See ``plan`` for how the folder is laid out, and
``report`` for what passes.

The report, ``release_test_<time>_report.txt``, is written into the folder.
It holds no signal names or data, and names each measurement only by an alias
that the separate ``_files.txt`` list resolves.

This module stays light: app.py imports it on every start.
"""

from __future__ import annotations

from pathlib import Path

RUNNER_FLAG = '--release-test'
SCENARIO_FLAG = '--release-test-scenario'


def requested(argv: list[str]) -> bool:
    """Whether the application was started to run the release test."""
    return RUNNER_FLAG in argv


def run(argv: list[str], app_name: str, app_version: str) -> int:
    """Run the release test instead of the application."""
    from gui.release_test.runner import run as run_release_test
    return run_release_test(argv, app_name, app_version)


def drive_if_requested(window, argv: list[str]):
    """Drive *window* through one scenario when a release test started it."""
    if SCENARIO_FLAG not in argv:
        return None
    from gui.release_test.driver import drive
    return drive(window, Path(argv[argv.index(SCENARIO_FLAG) + 1]))
