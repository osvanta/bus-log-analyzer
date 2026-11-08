# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A measurement let go by the next one takes its CAN Trace files with it.

Closed, the Trace window is still the main window's child, and the frames it
shows kept the last measurement's temporary files on disk until the
application ended: 6 GB for a 3 GB BLF, doubled when it was opened again.
"""
from __future__ import annotations

import gc
import os
import time

import numpy as np
import pytest


def _frames(count: int = 2_000):
    from core.raw_frame_store import RawFrameStore
    frames = RawFrameStore()
    frames.append_numpy_batch(
        np.arange(count) * 0.001, np.ones(count, dtype=np.uint8),
        np.full(count, 0x123, dtype=np.uint32), np.full(count, 8, dtype=np.uint8),
        np.zeros(count, dtype=np.uint8), np.zeros(count, dtype=np.uint8),
        np.zeros((count, 8), dtype=np.uint8))
    frames.seal()
    return frames


def _paths(frames) -> list[str]:
    paths = [frames._payload.short_path, frames._payload.long_path]
    paths += [column.path for column in (frames._file_columns or {}).values()]
    return paths


def _settle(qapp) -> None:
    """Run what the event loop would: queued events and deleteLater()."""
    from PySide6.QtCore import QCoreApplication, QEvent
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _gone(qapp, paths, seconds: float = 3.0) -> bool:
    # A delete-on-close file goes with its last handle, which an antivirus
    # can hold a moment.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _settle(qapp)
        gc.collect()
        if not any(os.path.exists(path) for path in paths):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from core.signal_store import SignalStore
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    store = SignalStore()
    store.raw_frame_store = _frames()
    w.store = store
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _trace_windows(window):
    from gui.raw_frame_dialog import RawFrameDialog
    return window.findChildren(RawFrameDialog)


def test_the_next_measurement_lets_the_closed_trace_window_go(window, qapp):
    window.show_raw_frames()
    window._raw_frame_dialog.close()            # closed by the user, still alive

    window._reset_for_new_measurement()         # what Open does
    _settle(qapp)

    assert window._raw_frame_dialog is None
    assert _trace_windows(window) == []


def test_the_next_measurement_deletes_the_trace_files(window, qapp):
    frames = window.store.raw_frame_store
    paths = _paths(frames)
    held = window.store          # anything still holding the old measurement
    window.show_raw_frames()
    window._raw_frame_dialog.close()
    assert all(os.path.exists(path) for path in paths)
    del frames

    window._reset_for_new_measurement()

    assert _gone(qapp, paths)
    assert held is not None


def test_opening_the_trace_again_leaves_one_trace_window(window, qapp):
    window.show_raw_frames()
    window.show_raw_frames()
    _settle(qapp)

    assert len(_trace_windows(window)) == 1
    assert _trace_windows(window)[0] is window._raw_frame_dialog
