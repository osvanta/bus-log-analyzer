# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A configuration as it comes out of a file, made safe to apply.

A configuration file is edited by hand, written by another version, or
damaged. Whatever it holds is checked once, here, before any of it reaches
the window: File ▸ Open configuration and the last session offered at start
both read through parse(), and the window normalises again what it is handed.

A file that is not a JSON object is refused: ConfigurationError says why, and
nothing has been applied. Inside an object, a value of the wrong kind (a
colour that is not a colour, a number where a list belongs) is left out, so
the window uses its default as it does for a file that never named it; a path
that is not text becomes null, as a path not set is saved. Keys
this version does not know are passed through untouched: a later version
wrote them. The file format is not changed; a good file comes back equal.
"""
from __future__ import annotations

import json
import math

import pyqtgraph as pg
from PySide6.QtGui import QColor

from core.bus_types import decode_key

_MAX_COLUMN_WIDTH = 10_000      # pixels; beyond any screen
_MAX_COLUMNS = 32               # the table has 5; a later version may have more
_MAX_INDEX = 1_000_000          # a tab or MultiStack row number

_FLAGS = ('step_lines', 'show_data_points', 'hide_plot_lines', 'multi_axis', 'stacked',
          'multistack', 'cursor1', 'cursor2', 'name_show_channel', 'name_show_message',
          'synchronize_tabs')
_PATHS = ('measurement_path', 'blf_path', 'dbc_path')
_TAB_FLAGS = ('show_data_points', 'hide_plot_lines', 'cursor1', 'cursor2')
_SIGNAL_FLAGS = ('visible', 'axis_visible', 'own_axis')


class ConfigurationError(ValueError):
    """The file holds no configuration. Its text is the reason, for the user."""


def parse(text: str) -> dict:
    """The configuration in *text*, the content of a file, normalised.
    Raises ConfigurationError when it holds none."""
    try:
        data = json.loads(text)
    except RecursionError:
        raise ConfigurationError(
            'The file is nested too deeply to be a configuration.') from None
    except ValueError as exc:
        raise ConfigurationError(f'The file is not valid JSON: {exc}') from exc
    return normalised(data)


def normalised(data) -> dict:
    """*data* with every value this version reads of the kind it expects;
    the others are left out. Raises ConfigurationError when *data* is not an
    object. Normalising twice changes nothing more."""
    if not isinstance(data, dict):
        raise ConfigurationError(
            'The file holds no configuration: a configuration is a JSON object, '
            'as File ▸ Save configuration writes it.')
    out: dict = {}
    for name, value in data.items():
        if not isinstance(name, str):
            continue
        reader = _READERS.get(name)
        if reader is None:
            out[name] = value       # not read by this version
            continue
        value = reader(value)
        # A path that is not set is saved as null, and stays one.
        if value is not None or name in _PATHS:
            out[name] = value
    return out


def usable_colour(value) -> str | None:
    """*value* as a colour both the widgets and the plot can draw; None when
    it names none."""
    if not isinstance(value, str) or not value.strip():
        return None
    by_name = QColor(value)
    try:
        drawn = pg.mkColor(value)
    except Exception:   # pyqtgraph raises several kinds for what it cannot read
        drawn = None
    if by_name.isValid():
        return value if drawn is not None else by_name.name()
    return drawn.name() if drawn is not None else None


# ── One value ────────────────────────────────────────────────────────────


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _flag(value) -> bool | None:
    if isinstance(value, bool):
        return value
    # 0 and 1 stand for a flag in a file written by hand.
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _whole(value, lowest: int, highest: int) -> int | None:
    """*value* as a whole number within the limits; None when it is none."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            return None
        value = int(value)
    return value if lowest <= value <= highest else None


def _index(value) -> int | None:
    return _whole(value, 0, _MAX_INDEX)


def _colours(value) -> dict | None:
    """Colours by name: of signals, or of groups."""
    if not isinstance(value, dict):
        return None
    colours = {name: usable_colour(colour) for name, colour in value.items()
               if isinstance(name, str)}
    return {name: colour for name, colour in colours.items() if colour is not None}


def _column_widths(value) -> list | None:
    """Widths in pixels; 0, which the table ignores, for one that is none."""
    if not isinstance(value, list):
        return None
    return [_whole(width, 1, _MAX_COLUMN_WIDTH) or 0 for width in value[:_MAX_COLUMNS]]


def _markers(value) -> list | None:
    """The entries a marker can be read from; PlotPanel.set_markers reads
    each and leaves out one without a time."""
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, (dict, list))]


def _generated_signals(value) -> list | None:
    """Each definition with only text for its name, formula and unit: one
    left without a name or a formula is reported as skipped when applied."""
    if not isinstance(value, list):
        return None
    return [
        {name: field for name, field in item.items()
         if name not in ('name', 'formula', 'unit') or isinstance(field, str)}
        if isinstance(item, dict) else item
        for item in value
    ]


def _channel_config(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    out = {name: field for name, field in value.items()
           if name not in ('name', 'channels')}
    if isinstance(value.get('name'), str):
        out['name'] = value['name']
    channels = value.get('channels')
    out['channels'] = {
        key: path for key, path in (channels.items() if isinstance(channels, dict) else ())
        if isinstance(path, str) and _is_channel(key)
    }
    return out


def _is_channel(key) -> bool:
    if isinstance(key, bool) or not isinstance(key, (str, int)):
        return False
    try:
        decode_key(key)
    except (ValueError, TypeError):
        return False
    return True


def _signal(entry, plain_key: bool = False):
    """A plotted signal with its look; None when it names no signal. The
    top-level list of a file saved before tabs may hold bare keys."""
    if plain_key and isinstance(entry, str):
        return entry or None
    if not isinstance(entry, dict) or not isinstance(entry.get('key'), str) or not entry['key']:
        return None
    out = {}
    for name, value in entry.items():
        if name == 'color':
            value = usable_colour(value)
        elif name in _SIGNAL_FLAGS:
            value = _flag(value)
        elif name in ('group', 'line_style'):
            value = _text(value)
        elif name == 'multistack_id':
            value = _whole(value, -1, _MAX_INDEX)
        if value is not None:
            out[name] = value
    return out


def _signals(value, plain_keys: bool = False) -> list | None:
    if not isinstance(value, list):
        return None
    signals = (_signal(entry, plain_keys) for entry in value)
    return [entry for entry in signals if entry is not None]


def _top_level_signals(value) -> list | None:
    return _signals(value, plain_keys=True)


def _tab(tab: dict) -> dict:
    out = {}
    for name, value in tab.items():
        if name in ('name', 'plot_type'):
            value = _text(value)
        elif name in _TAB_FLAGS:
            value = _flag(value)
        elif name == 'signals':
            value = _signals(value)
        if value is not None:
            out[name] = value
    return out


def _tabs(value) -> list | None:
    if not isinstance(value, list):
        return None
    return [_tab(tab) for tab in value if isinstance(tab, dict)]


def _last_session(value) -> dict | None:
    """Where the last session was saved: see gui.session_restore."""
    if not isinstance(value, dict):
        return None
    return {**value, 'saved_to': _text(value.get('saved_to'))}


_READERS = {
    **dict.fromkeys(_PATHS, _text),
    **dict.fromkeys(_FLAGS, _flag),
    'channel_config': _channel_config,
    'signals': _top_level_signals,
    'generated_signals': _generated_signals,
    'plot_background_color': usable_colour,
    'signal_colors': _colours,
    'group_colors': _colours,
    'table_column_widths': _column_widths,
    'markers': _markers,
    'tabs': _tabs,
    'current_tab': _index,
    'last_session': _last_session,
}
