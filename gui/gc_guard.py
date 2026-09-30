# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Run Python's cyclic garbage collector on the GUI thread only.

CPython runs an automatic collection on whichever thread happens to cross the
allocation threshold. In this application that is usually the Load + Decode
worker: reading an MF4 with hundreds of channel groups allocates far more than
the GUI thread does while it waits. A collection frees every unreachable
cycle, and pyqtgraph items are cycles. The plot's cursor lines are replaced on
every load, and curves, axes and view boxes whenever the plot is cleared.
Freed on the worker thread, their Qt destructors run there while the GUI
thread is painting or filling the signal tree, and the application crashes
after a few loads of a large measurement.

``GuiThreadGarbageCollector`` turns automatic collection off and collects from
a timer on the GUI thread instead, using the interpreter's own thresholds, so a
Qt object is always destroyed on the thread that owns it. A timer slot also
never runs inside a paint event, which an allocation-triggered collection can.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer


class GuiThreadGarbageCollector(QObject):
    """Replace automatic garbage collection with a GUI-thread timer.

    Create it on the GUI thread, parented to the application, and keep it
    alive for the whole session. An explicit ``gc.collect()`` still works
    anywhere; only the automatic, allocation-triggered collection is moved.
    """

    def __init__(self, parent: QObject | None = None, interval_ms: int = 1000) -> None:
        super().__init__(parent)
        self._thresholds = gc.get_threshold()
        self._was_enabled = gc.isenabled()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.collect_if_due)
        self._timer.start(interval_ms)

    def collect_if_due(self) -> None:
        """Collect the generation automatic collection would have collected.

        The allocation counts keep running while collection is disabled, so
        comparing them with the saved thresholds reproduces the interpreter's
        schedule, only on this thread and at this point in the event loop.
        """
        count0, count1, count2 = gc.get_count()
        threshold0, threshold1, threshold2 = self._thresholds
        # A zero first threshold is how gc.set_threshold() disables collection.
        if threshold0 == 0 or count0 <= threshold0:
            return
        generation = 0
        if count1 > threshold1:
            generation = 1
            if count2 > threshold2:
                generation = 2
        gc.collect(generation)

    def stop(self) -> None:
        """Stop collecting and give automatic collection back to the interpreter."""
        self._timer.stop()
        if self._was_enabled:
            gc.enable()
