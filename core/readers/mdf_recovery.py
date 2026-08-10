"""Bounded, non-destructive recovery helpers for partially corrupt MDF4 files.

Only structural chain links that point outside the physical file are repaired.
The original measurement is never modified: a temporary copy is created and
the broken link is replaced with the MDF null-link value.  This lets asammdf
load every complete data/channel group that precedes a truncated tail.
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import struct
import tempfile


_BLOCK_HEADER = struct.Struct("<4s4sQQ")
_HD_ADDRESS = 64
_MAX_GROUPS = 4096


def _header(stream, file_size: int, address: int, expected: bytes, min_links: int):
    if not address:
        return None, "null link"
    if address % 8 or address < 0 or address + _BLOCK_HEADER.size > file_size:
        return None, f"address 0x{address:X} is outside the file"
    stream.seek(address)
    raw = stream.read(_BLOCK_HEADER.size)
    if len(raw) != _BLOCK_HEADER.size:
        return None, f"short block header at 0x{address:X}"
    block_id, _reserved, block_len, links_nr = _BLOCK_HEADER.unpack(raw)
    if block_id != expected:
        return None, (
            f"0x{address:X} references {block_id!r}, expected {expected!r}"
        )
    if links_nr < min_links or links_nr > 1024:
        return None, f"invalid link count {links_nr} at 0x{address:X}"
    minimum_len = _BLOCK_HEADER.size + links_nr * 8
    if block_len < minimum_len or address + block_len > file_size:
        return None, f"block at 0x{address:X} extends outside the file"
    links_raw = stream.read(links_nr * 8)
    if len(links_raw) != links_nr * 8:
        return None, f"short link table at 0x{address:X}"
    links = list(struct.unpack(f"<{links_nr}Q", links_raw))
    return links, ""


def _link_offset(block_address: int, link_index: int) -> int:
    return block_address + _BLOCK_HEADER.size + link_index * 8


def _patch_null(stream, block_address: int, link_index: int) -> None:
    stream.seek(_link_offset(block_address, link_index))
    stream.write(struct.pack("<Q", 0))


def make_bounded_mdf4_copy(path: str | Path) -> tuple[Path | None, list[str]]:
    """Return a temporary MDF4 copy with invalid DG/CG tail links truncated.

    ``(None, [])`` means the structural chain is already bounded or the file is
    not an MDF4 file that this deliberately narrow recovery can handle.
    """
    source = Path(path)
    if source.suffix.lower() not in {".mf4", ".mdf"} or not source.exists():
        return None, []
    file_size = source.stat().st_size
    if file_size < _HD_ADDRESS + _BLOCK_HEADER.size:
        return None, []

    repairs: list[tuple[int, int, str]] = []
    with source.open("rb") as stream:
        hd_links, _problem = _header(
            stream, file_size, _HD_ADDRESS, b"##HD", 1
        )
        if hd_links is None:
            return None, []

        first_dg = int(hd_links[0])
        if first_dg:
            _links, problem = _header(stream, file_size, first_dg, b"##DG", 2)
            if problem:
                repairs.append((_HD_ADDRESS, 0, f"first data group: {problem}"))
                first_dg = 0

        dg_address = first_dg
        seen_dg: set[int] = set()
        for _ in range(_MAX_GROUPS):
            if not dg_address or dg_address in seen_dg:
                break
            seen_dg.add(dg_address)
            dg_links, problem = _header(
                stream, file_size, dg_address, b"##DG", 2
            )
            if dg_links is None:
                break

            next_dg = int(dg_links[0])
            if next_dg:
                _links, problem = _header(
                    stream, file_size, next_dg, b"##DG", 2
                )
                if problem:
                    repairs.append((
                        dg_address, 0,
                        f"data-group link after 0x{dg_address:X}: {problem}",
                    ))
                    next_dg = 0

            first_cg = int(dg_links[1])
            if first_cg:
                _links, problem = _header(
                    stream, file_size, first_cg, b"##CG", 1
                )
                if problem:
                    repairs.append((
                        dg_address, 1,
                        f"first channel group in DG 0x{dg_address:X}: {problem}",
                    ))
                    first_cg = 0

            cg_address = first_cg
            seen_cg: set[int] = set()
            for _ in range(_MAX_GROUPS):
                if not cg_address or cg_address in seen_cg:
                    break
                seen_cg.add(cg_address)
                cg_links, problem = _header(
                    stream, file_size, cg_address, b"##CG", 1
                )
                if cg_links is None:
                    break
                next_cg = int(cg_links[0])
                if next_cg:
                    _links, problem = _header(
                        stream, file_size, next_cg, b"##CG", 1
                    )
                    if problem:
                        repairs.append((
                            cg_address, 0,
                            f"channel-group link after 0x{cg_address:X}: {problem}",
                        ))
                        next_cg = 0
                cg_address = next_cg
            dg_address = next_dg

    if not repairs:
        return None, []

    fd, temp_name = tempfile.mkstemp(prefix="canscope_recovered_", suffix=source.suffix)
    os.close(fd)
    recovered = Path(temp_name)
    try:
        shutil.copyfile(source, recovered)
        with recovered.open("r+b") as stream:
            for block_address, link_index, _reason in repairs:
                _patch_null(stream, block_address, link_index)
    except Exception:
        recovered.unlink(missing_ok=True)
        raise

    warnings = [
        "MDF structural recovery used a temporary copy; the original file was not changed."
    ]
    warnings.extend(
        f"Skipped unreachable MDF content ({reason})."
        for _block, _index, reason in repairs
    )
    return recovered, warnings


def remove_recovery_copy(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
