# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for gui/splash.py — status text compositing.

The splash is the only progress indicator during a slow startup, so an
unreadable status line is a real defect rather than a cosmetic one. These
tests pin the compositing rule: every render starts from the pristine
artwork, so each message replaces the last instead of stacking on top of it.

Deliberately *not* asserted by comparing rendered text: the headless CI
platform has no Consolas, so status glyphs rasterise to nothing there and any
image comparison of two different messages would pass whether or not the bug
is present. These tests check where the render sources its pixels from, which
holds regardless of font availability.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPixmap

from gui.splash import SplashScreen

_BASE_FILL = '#101010'
# Must never survive a render — it stands in for a previous frame's content.
_STALE_FILL = '#ff00ff'


@pytest.fixture()
def splash(qapp):
    s = SplashScreen(version='v00.00.99')
    yield s
    s.close()
    qapp.processEvents()


def _flat(size, colour: str) -> QPixmap:
    pm = QPixmap(size)
    pm.fill(QColor(colour))
    return pm


def test_render_starts_from_cached_artwork_not_the_current_pixmap(splash):
    """Regression: _render() used to copy the widget's *current* pixmap.

    That pixmap already carries the previously drawn status text, so every
    update composited the new message on top of the old one — 'Starting...'
    stayed smeared under 'Loading UI components...'. Here the widget pixmap is
    poisoned with a colour that only survives if the render reads from it.
    """
    size = splash._base_pixmap.size()
    splash._base_pixmap = _flat(size, _BASE_FILL)
    splash.setPixmap(_flat(size, _STALE_FILL))

    splash.set_status('any status at all')

    img = splash.pixmap().toImage()
    # (5, 5) is outside both the status box and the version overlay, so it
    # reflects only the pixmap the render started from.
    assert img.pixelColor(5, 5).name() == _BASE_FILL

    sampled = {
        img.pixelColor(x, y).name()
        for x in range(0, img.width(), 25)
        for y in range(0, img.height(), 25)
    }
    assert _STALE_FILL not in sampled


def test_render_does_not_mutate_the_cached_artwork(splash):
    """The cached artwork must survive every render untouched."""
    before = splash._base_pixmap.toImage()

    splash.set_status('some long status message')
    splash.set_status('another one entirely')

    assert splash._base_pixmap.toImage() == before
