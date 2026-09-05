# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from core.bus_types import BusType

SUPPORTED_DB_SUFFIXES = {'.dbc', '.arxml', '.ldf'}

_LABELS = {'.arxml': 'ARXML', '.ldf': 'LDF'}


def is_database_file(path: str) -> bool:
    """Return True if *path* has a supported database file extension."""
    return Path(path).suffix.lower() in SUPPORTED_DB_SUFFIXES


def db_format_label(path: str) -> str:
    """Return 'ARXML', 'LDF', or 'DBC' for everything else."""
    return _LABELS.get(Path(path).suffix.lower(), 'DBC')


def ldf_support_available() -> tuple[bool, str]:
    """
    Return ``(available, message)`` for LDF parsing support.

    ``canmatrix`` registers its LDF handler only if ``ldfparser`` imports
    successfully, and it dispatches through ``sys.modules[...]`` with no
    guard — so a missing ldfparser surfaces as a bare ``KeyError`` from deep
    inside asammdf, which the reader then reports as a generic extraction
    failure.  Checking up front turns that into something actionable.

    The failure that matters is the frozen build: ldfparser ships its ``lark``
    grammars and ``jinja2`` templates as package data, which PyInstaller does
    not collect automatically, so an LDF can parse fine in a source run and
    fail only in the packaged application.
    """
    try:
        import canmatrix.formats
    except Exception as exc:                      # pragma: no cover - env dependent
        return False, f"canmatrix is not available: {exc}"

    if 'ldf' in getattr(canmatrix.formats, 'loadedFormats', ()):
        return True, ""

    try:
        import ldfparser  # noqa: F401
    except Exception as exc:
        return False, (
            "LDF support requires the 'ldfparser' package, which canmatrix "
            f"uses to read .ldf files ({exc}).\n"
            "Install it with:  pip install ldfparser"
        )
    return False, (
        "canmatrix did not register its LDF handler even though 'ldfparser' "
        "is importable. In a packaged build this usually means ldfparser's "
        "grammar files were not bundled."
    )


def _ldf_matrix(db_path: str):
    """Load an LDF into a flat canmatrix, or raise with an actionable message."""
    available, message = ldf_support_available()
    if not available:
        raise RuntimeError(message)
    import canmatrix.formats
    matrix = canmatrix.formats.loadp_flat(str(db_path), import_type='ldf')
    if matrix is None or not matrix.frames:
        raise RuntimeError(f"No LIN cluster found in '{Path(db_path).name}'.")
    return matrix


@lru_cache(maxsize=16)
def _ldf_to_dbc_cached(db_path: str, _mtime_ns: int, _size: int) -> str:
    matrix = _ldf_matrix(db_path)
    expected = (len(matrix.frames), sum(len(f.signals) for f in matrix.frames))

    import canmatrix.formats
    buffer = io.BytesIO()
    canmatrix.formats.dump(matrix, buffer, export_type='dbc')
    text = buffer.getvalue().decode('utf-8', errors='replace')

    # A silent drop here would look like a database that simply does not match
    # the measurement, which is the hardest kind of wrong to diagnose. DBC
    # identifier rules are stricter than LDF's, so this is not hypothetical.
    import cantools
    database = cantools.database.load_string(text, database_format='dbc', strict=False)
    produced = (
        len(database.messages),
        sum(len(m.signals) for m in database.messages),
    )
    if produced != expected:
        raise RuntimeError(
            f"Converting '{Path(db_path).name}' from LDF lost content: "
            f"{expected[0]} frames / {expected[1]} signals became "
            f"{produced[0]} / {produced[1]}."
        )
    return text


def ldf_to_dbc_string(db_path: str) -> str:
    """Return an LDF's content as DBC text, for the CAN decoding pipeline.

    LIN and CAN differ on the wire, not in how a signal is laid out inside a
    frame, so once an LDF is expressed as DBC the existing decoder, vectorised
    decoder, and match scoring all apply unchanged — no second signal decoder
    to write, and no second one to keep correct.

    The conversion is faithful where it matters: frame IDs, byte order, bit
    positions, scaling, and value tables all survive, and the result decodes
    identically to asammdf's own LDF handling on the same file. That identity
    is what keeps a signal key such as ``LIN1::DoorCmd::WindowPos`` stable
    whether the measurement was an MF4 or a BLF.

    Cached on (path, mtime, size) because it runs per load and per match
    refresh, and canmatrix's parse is not cheap.
    """
    path = Path(db_path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise RuntimeError(f"Cannot read '{path.name}': {exc}") from exc
    return _ldf_to_dbc_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def database_message_ids(db_path: str) -> frozenset[int]:
    """
    Return the frame/message IDs declared by a database file.

    Dispatches by format because the two parsers do not overlap: cantools
    reads DBC and ARXML but has no LDF support, while canmatrix reads LDF
    (via ldfparser) and is already a dependency for asammdf's bus extraction.
    Without this split every LDF would report "can't read database" in the
    Database Manager's match column.
    """
    return frozenset(database_message_lengths(db_path))


def database_message_lengths(db_path: str) -> dict[int, int]:
    """Return ``{frame id: message length}`` declared by a database file.

    The length matters for matching, not just for decoding: two LIN clusters
    routinely use the same low frame IDs, so an ID-only comparison cannot tell
    them apart, while their frame lengths usually can.
    """
    if Path(db_path).suffix.lower() == '.ldf':
        # canmatrix rather than cantools: an LDF reaches the decoder as
        # converted DBC text, but reading its frames directly avoids paying
        # for the conversion just to list IDs.
        return {
            int(frame.arbitration_id.id) & 0x1FFFFFFF: int(frame.size)
            for frame in _ldf_matrix(db_path).frames
        }

    from core.dbc_decoder import load_database_file
    db, _load_messages = load_database_file(db_path, strict_first=False)
    return {
        int(message.frame_id) & 0x1FFFFFFF: int(message.length)
        for message in db.messages
    }


def compatibility_warning(db_path: str, bus: BusType) -> str | None:
    """
    Return a warning when a database looks wrong for *bus*, else ``None``.

    This warns; it never blocks.  The asymmetry is deliberate:

    * A ``.dbc`` on a LIN channel is **legitimate** — LIN signal layouts are
      routinely distributed as DBC, and asammdf's own documented workflow
      converts LDF to DBC.  No warning.
    * An ``.ldf`` on a CAN channel is almost certainly a misassignment, since
      an LDF describes a LIN cluster. Worth flagging, but the user may still
      know something we do not.
    """
    suffix = Path(db_path).suffix.lower()
    if suffix == '.ldf' and bus is not BusType.LIN:
        return (
            f"'{Path(db_path).name}' is an LDF, which describes a LIN cluster, "
            f"but it is assigned to a {bus.value} channel. "
            "This is usually a mistake — assign it to a LIN channel instead."
        )
    return None
