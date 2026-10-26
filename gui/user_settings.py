# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""One setting at a time in the user's settings file (a JSON object), the
others kept as they are. A missing or unreadable file reads as defaults."""
from __future__ import annotations

import json
from pathlib import Path


def _settings(path: Path) -> dict:
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def read_setting(path: Path, key: str, default=None):
    return _settings(path).get(key, default)


def write_setting(path: Path, key: str, value) -> None:
    """Raises OSError when the file cannot be written."""
    settings = _settings(path)
    settings[key] = value
    path.write_text(json.dumps(settings, indent=2), encoding='utf-8')
