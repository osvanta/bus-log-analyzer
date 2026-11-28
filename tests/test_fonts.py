# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for gui/fonts.py: the named typefaces, with a fallback per OS."""
from __future__ import annotations

from PySide6.QtGui import QFont

from gui import fonts


def test_the_windows_family_comes_first_so_nothing_changes_there(qapp):
    assert fonts.monospace_font().families()[0] == 'Consolas'
    assert fonts.interface_font().families()[0] == 'Segoe UI'


def test_each_font_names_a_fallback_for_other_desktops(qapp):
    assert 'DejaVu Sans Mono' in fonts.monospace_font().families()
    assert fonts.monospace_font().styleHint() == QFont.StyleHint.Monospace
    assert 'DejaVu Sans' in fonts.interface_font().families()
    assert fonts.interface_font().styleHint() == QFont.StyleHint.SansSerif


def test_the_point_size_is_the_one_asked_for(qapp):
    assert fonts.monospace_font(9).pointSize() == 9
