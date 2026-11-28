# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The two typefaces the application names, with a fallback per OS.

The look was designed on Windows, in Segoe UI and Consolas. Naming a family
Qt cannot find gets whatever its font matching picks, which on Linux is the
serif default. Each font here lists the Windows family first, so nothing
changes there, then the usual family of macOS and of the Linux desktops, and
a style hint for anything else.
"""
from __future__ import annotations

from PySide6.QtGui import QFont

_MONOSPACE = ('Consolas', 'Menlo', 'DejaVu Sans Mono', 'Liberation Mono', 'Noto Sans Mono')
_INTERFACE = ('Segoe UI', 'SF Pro Text', 'Helvetica Neue', 'Noto Sans', 'DejaVu Sans',
              'Cantarell', 'Ubuntu')


def monospace_font(point_size: int | None = None) -> QFont:
    """Consolas, or the OS's monospace where there is none."""
    font = QFont()
    font.setFamilies(list(_MONOSPACE))
    font.setStyleHint(QFont.StyleHint.Monospace)
    if point_size is not None:
        font.setPointSize(point_size)
    return font


def interface_font() -> QFont:
    """Segoe UI, or the OS's interface typeface where there is none."""
    font = QFont()
    font.setFamilies(list(_INTERFACE))
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font
