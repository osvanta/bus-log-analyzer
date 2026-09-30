# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Rebuilding the plot must not make Qt warn.

Each rebuild removed the plot's legend from the scene and asked pyqtgraph for
a new one. pyqtgraph returns the legend a plot already has, and after the
first rebuild that one was no longer in any scene, so every later rebuild made
Qt warn "QGraphicsScene::removeItem: item's scene (0x0) is different from
this scene". The main window rebuilds while it is built, so the legend was
never visible either.
"""
from __future__ import annotations

import array
from contextlib import contextmanager

from PySide6.QtCore import qInstallMessageHandler

from core.signal_store import SignalSeries
from gui.plot_widget import PlotPanel


@contextmanager
def _qt_messages():
    messages: list[str] = []
    previous = qInstallMessageHandler(
        lambda _mode, _context, message: messages.append(message)
    )
    try:
        yield messages
    finally:
        qInstallMessageHandler(previous)


def _series(signal_name: str) -> SignalSeries:
    return SignalSeries(
        channel=1,
        message_name="EngineControl",
        message_id=0x100,
        signal_name=signal_name,
        unit="rpm",
        timestamps=array.array("d", [0.0, 1.0, 2.0]),
        values=array.array("d", [10.0, 20.0, 30.0]),
    )


def test_rebuilding_the_plot_raises_no_qt_warning(qapp):
    panel = PlotPanel()
    with _qt_messages() as messages:
        for name in ("EngSpeed", "EngTorque"):
            series = _series(name)
            panel.add_series(series.key, series)
        for enabled in (True, False, True, False):
            panel.set_stacked(enabled)
            panel.set_multi_axis(enabled)
        panel.clear_all()
        panel.clear_all()
        qapp.processEvents()

    assert [m for m in messages if "removeItem" in m] == []
