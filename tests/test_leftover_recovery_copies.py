# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A repaired MDF copy a crashed session left in the temp folder is removed
at the next start-up: it is a whole copy of the user's measurement."""

from __future__ import annotations

import struct
import sys
import tempfile

import pytest

from core.readers.mdf_recovery import make_bounded_mdf4_copy
from gui.main_window import remove_leftover_recovery_copies


def _truncated_mf4(path):
    data = bytearray(512)
    for address, block_id, links in ((64, b"##HD", [128]),
                                     (128, b"##DG", [0x5000, 192]),
                                     (192, b"##CG", [0])):
        block_len = 24 + len(links) * 8
        data[address:address + 24] = struct.pack("<4s4sQQ", block_id, b"\0" * 4, block_len, len(links))
        data[address + 24:address + block_len] = struct.pack(f"<{len(links)}Q", *links)
    path.write_bytes(data)


def test_a_copy_left_by_a_crash_is_removed_and_other_files_are_kept(tmp_path, monkeypatch):
    source_dir = tmp_path / "measurements"
    temp_dir = tmp_path / "temp"
    source_dir.mkdir()
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    source = source_dir / "truncated.mf4"
    _truncated_mf4(source)

    # The session that made this copy crashed before closing it.
    recovered, _warnings = make_bounded_mdf4_copy(source)
    assert recovered is not None and recovered.parent == temp_dir
    kept = [temp_dir / "osvanta_crash.log", temp_dir / "recovered.mf4",
            temp_dir / "osvanta_a1b2.rawdata"]
    for path in kept:
        path.write_bytes(b"keep")

    removed, freed = remove_leftover_recovery_copies()

    assert (removed, freed) == (1, 512)
    assert sorted(temp_dir.iterdir()) == sorted(kept)
    assert source.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to delete an open file")
def test_a_copy_another_running_copy_has_open_is_skipped(tmp_path):
    copy = tmp_path / "osvanta_recovered_x.mf4"
    copy.write_bytes(bytes(64))

    with copy.open("rb"):
        assert remove_leftover_recovery_copies(tmp_path) == (0, 0)
        assert copy.exists()

    assert remove_leftover_recovery_copies(tmp_path) == (1, 64)
