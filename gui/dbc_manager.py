# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Database Manager dialog — assign DBC, ARXML or LDF database files to bus channels.

Bus type is discovered from the measurement file, never chosen here: the
dropdown lists the channels the file actually declares ("CAN 1", "LIN 1"), so a
database can only ever be attached to a channel whose bus is already known.

Layout:

    ┌──────────────────────────────────────────────────────────────────┐
    │  Database Manager                                         [Name: Truck ECU Setup] │
    ├─────────────────────┬──────────────────┬────────────────────────┤
    │  Database file      │  Assigned to     │  Match  (IDs in file)  │
    ├─────────────────────┼──────────────────┼────────────────────────┤
    │  Powertrain.dbc [×] │  [CAN 1       ▼] │  ████████░░  91%  34/37│
    │  Chassis.arxml  [×] │  [CAN 2       ▼] │  ███████░░░  78%  21/27│
    │                     │                  │                        │
    │  [+ Add Database…]  │                  │                        │
    ├─────────────────────┴──────────────────┴────────────────────────┤
    │  [Save Channel Config…]   [Load Channel Config…]   [OK] [Cancel]│
    └──────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QProgressBar, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from core.bus_types import (
    BusChannel,
    BusType,
    all_channels_key,
    channel_label,
    decode_key,
    encode_key,
    sort_key,
)
from core.channel_config import ChannelConfig


def _j1939_pgn(frame_id: int) -> int | None:
    """Return the PDU2 PGN used by the Database Manager match display."""
    pf = (frame_id >> 16) & 0xFF
    if frame_id > 0x7FF and pf >= 0xF0:
        return (frame_id >> 8) & 0xFFFF
    return None


@lru_cache(maxsize=32)
def _load_database_match_data(
    resolved_path: str,
    mtime_ns: int,
    file_size: int,
) -> tuple[frozenset[int], dict[int, int], dict[int, int]]:
    """Parse match-only database metadata once per unchanged file."""
    # mtime_ns and file_size intentionally participate in the cache key.
    del mtime_ns, file_size
    # Here, not at module level: importing core.readers loads cantools,
    # python-can and asammdf, a second of start-up the main window does not need.
    from core.readers.db_format import database_message_lengths
    dbc_lengths = database_message_lengths(resolved_path)
    dbc_ids = frozenset(dbc_lengths)
    dbc_pgn_by_id = {
        frame_id: pgn for frame_id in dbc_ids
        if (pgn := _j1939_pgn(frame_id)) is not None
    }
    return dbc_ids, dbc_pgn_by_id, dbc_lengths


class _DBCRow(QWidget):
    """One row in the DBC manager: filename label + channel dropdown + match bar + remove btn."""

    def __init__(
        self,
        dbc_path: str,
        channels: list[BusChannel],   # bus-tagged channels from the measurement
        assigned_to: BusChannel,      # initially assigned channel
        match_pct: float,             # 0.0–1.0 match quality
        match_text: str,              # e.g. "34 / 37 IDs"
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.dbc_path = dbc_path

        # File name label
        name_lbl = QLabel(Path(dbc_path).name)
        name_lbl.setToolTip(dbc_path)
        name_lbl.setMinimumWidth(180)
        name_lbl.setMaximumWidth(260)

        # Channel dropdown. One "All <bus>" entry per bus present in the
        # measurement, then that bus's concrete channels. The bus is shown
        # but never chosen: it comes from what the file declares.
        #
        # Item data is the encoded "CAN:1" string, not the (bus, number) tuple.
        # QComboBox.findData() compares wrapped Python objects by identity, so
        # an equal-but-distinct tuple silently returns -1 and every row falls
        # back to the first entry. A plain string round-trips through QVariant
        # by value, which is what findData() needs.
        self.channel_combo = QComboBox()
        # The assignment's own bus is always listed, even when the measurement
        # declares no channel on it — a BLF pre-scan finds nothing at all when
        # the log holds no CAN, and without this the only entry would be
        # "All CAN", quietly re-homing an LDF onto CAN.
        buses = sorted({bus for bus, _number in channels} | {assigned_to[0]})
        for bus in buses:
            fallback = all_channels_key(bus)
            self.channel_combo.addItem(channel_label(fallback), encode_key(fallback))
            for key in sorted(
                (k for k in channels if k[0] == bus and k[1] != 0), key=sort_key
            ):
                self.channel_combo.addItem(channel_label(key), encode_key(key))
        # Select initial assignment. A concrete channel the file never reported
        # is added rather than dropped: falling back to index 0 would show an
        # assignment the caller did not make, and it would be saved back.
        encoded_assignment = encode_key(assigned_to)
        idx = self.channel_combo.findData(encoded_assignment)
        if idx < 0:
            self.channel_combo.addItem(channel_label(assigned_to), encoded_assignment)
            idx = self.channel_combo.count() - 1
        self.channel_combo.setCurrentIndex(idx)
        self.channel_combo.setFixedWidth(130)

        # Match quality bar (stored as instance attrs for later refresh)
        self._bar = QProgressBar()
        self._bar.setMinimum(0)
        self._bar.setMaximum(100)
        self._bar.setValue(int(match_pct * 100))
        self._bar.setFixedWidth(120)
        self._bar.setFixedHeight(16)
        self._bar.setTextVisible(False)
        bar_style = (
            "QProgressBar { border: 1px solid #4a6080; border-radius: 3px;"
            "  background: #1a2a3a; }"
            "QProgressBar::chunk { background: #3a8060; border-radius: 2px; }"
        )
        self._bar.setStyleSheet(bar_style)
        bar = self._bar   # alias for layout

        self._match_lbl = QLabel(f"{int(match_pct*100)}%  {match_text}")
        self._match_lbl.setStyleSheet("color: #8ab4a0; font-size: 11px;")
        self._match_lbl.setFixedWidth(110)
        match_lbl = self._match_lbl   # alias for layout

        # Remove button
        rm_btn = QToolButton()
        rm_btn.setText("×")
        rm_btn.setFixedSize(22, 22)
        rm_btn.setStyleSheet(
            "QToolButton { color: #c07070; border: 1px solid #6a4040;"
            "  border-radius: 3px; background: #2a1a1a; }"
            "QToolButton:hover { background: #4a2020; }"
        )
        rm_btn.clicked.connect(self._on_remove)

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.addWidget(name_lbl)
        row.addWidget(self.channel_combo)
        row.addWidget(bar)
        row.addWidget(match_lbl)
        row.addStretch()
        row.addWidget(rm_btn)

    def assigned_channel(self) -> BusChannel | None:
        raw = self.channel_combo.currentData()
        return decode_key(raw) if raw else None

    def update_match(self, pct: float, text: str) -> None:
        """Refresh the match quality bar and label."""
        self._bar.setValue(int(pct * 100))
        self._match_lbl.setText(f'{int(pct*100)}%  {text}')

    def _on_remove(self) -> None:
        # Signal parent scroll container to remove this row
        parent = self.parent()
        while parent and not isinstance(parent, DBCManagerDialog):
            parent = parent.parent()
        if isinstance(parent, DBCManagerDialog):
            parent.remove_row(self)


class DBCManagerDialog(QDialog):
    """
    Database Manager — assign DBC, ARXML or LDF files to bus channels.

    Parameters
    ----------
    channel_config : ChannelConfig
        Current config (may be empty on first use).
    channels_in_file : list[BusChannel]
        Bus-tagged channels seen in the currently loaded measurement.
    ids_per_channel : dict[BusChannel, set[int]]
        Frame IDs seen per channel — used to compute match quality.
    parent : QWidget | None
    """

    def __init__(
        self,
        channel_config: ChannelConfig,
        channels_in_file: list[BusChannel],
        ids_per_channel: dict[BusChannel, set[int]],
        parent=None,
        data_provider=None,
        lengths_per_channel: dict[BusChannel, dict[int, int]] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        data_provider : callable() -> (list[BusChannel], dict[BusChannel, set[int]]) | None
            Called by Refresh Match to get fresh channel/ID data from the
            main window.  Allows refresh to work even if the measurement
            was decoded after the dialog was opened.
        """
        super().__init__(parent)
        self.setWindowTitle("Database Manager — Channel Configuration")
        self.setMinimumWidth(720)
        self.resize(760, 380)

        self._channels_in_file = (
            sorted(channels_in_file, key=sort_key) if channels_in_file else []
        )
        self._ids_per_channel  = ids_per_channel
        self._lengths_per_channel = lengths_per_channel or {}
        self._data_provider    = data_provider  # callable for refresh
        self._rows: list[_DBCRow] = []
        self._file_match_data = self._normalise_file_match_data(
            ids_per_channel, self._lengths_per_channel
        )
        # Databases that would not parse, keyed by path. _compute_match is
        # the only place that tries to read them, so it is where this is
        # filled in; the caller uses it to launch the forensic report.
        self._load_errors: dict[str, str] = {}

        # ── Config name ───────────────────────────────────────────────────
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Config name:"))
        self._name_edit = QLineEdit(channel_config.name)
        self._name_edit.setPlaceholderText("e.g. Truck ECU Setup")
        name_row.addWidget(self._name_edit, 1)

        # ── Rows container ────────────────────────────────────────────────
        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setSpacing(2)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_widget)
        scroll.setMinimumHeight(150)

        # Column headers
        hdr = QHBoxLayout()
        for text, width in [("Database file", 190), ("Assigned to", 140),
                             ("Match quality", 240), ("", 30)]:
            lbl = QLabel(text)
            lbl.setFixedWidth(width)
            lbl.setStyleSheet("color: #8090a0; font-size: 11px;")
            hdr.addWidget(lbl)
        hdr.addStretch()

        # Channel info banner
        self._channel_info = QLabel()
        self._channel_info.setStyleSheet("color: #a0a060; font-size: 11px; padding: 2px 4px;")
        self._update_channel_info()

        # Populate existing rows
        for ch, path in channel_config.channels.items():
            self._add_row(path, preferred_channel=ch)

        # ── Add DBC button ────────────────────────────────────────────────
        add_btn = QPushButton("+ Add Database…")
        add_btn.clicked.connect(self._on_add_dbc)
        add_btn.setFixedWidth(130)

        self._refresh_btn = QPushButton("↻ Refresh Match")
        self._refresh_btn.clicked.connect(self._refresh_all_matches)
        self._refresh_btn.setFixedWidth(120)
        self._refresh_btn.setToolTip(
            'Re-compute match quality for every database\n'
            'against the channel it is assigned to'
        )
        # Outcome of the last Refresh Match. The time is part of the text so
        # that a second press with nothing to change still visibly answers.
        self._refresh_result = QLabel()
        self._refresh_result.setStyleSheet("color: #80b0a0; font-size: 11px;")

        add_row = QHBoxLayout()
        add_row.addWidget(add_btn)
        add_row.addWidget(self._refresh_btn)
        add_row.addWidget(self._refresh_result)
        add_row.addStretch()

        # ── Bottom buttons ────────────────────────────────────────────────
        save_ch_btn = QPushButton("Save Channel Config…")
        save_ch_btn.clicked.connect(self._on_save_channel_config)
        load_ch_btn = QPushButton("Load Channel Config…")
        load_ch_btn.clicked.connect(self._on_load_channel_config)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(save_ch_btn)
        btn_row.addWidget(load_ch_btn)
        btn_row.addStretch()
        btn_row.addWidget(bbox)

        # ── Main layout ───────────────────────────────────────────────────
        main = QVBoxLayout(self)
        main.addLayout(name_row)
        main.addWidget(self._channel_info)
        main.addLayout(hdr)
        main.addWidget(scroll, 1)
        main.addLayout(add_row)
        main.addLayout(btn_row)

    # ── Channel info ────────────────────────────────────────────────────

    def _update_channel_info(self) -> None:
        if self._channels_in_file:
            ch_list = ", ".join(
                channel_label(key) for key in self._channels_in_file
            )
            self._channel_info.setText(
                f"Channels detected in measurement: {ch_list}"
            )
            self._channel_info.setStyleSheet(
                "color: #80b0a0; font-size: 11px; padding: 2px 4px;"
            )
        else:
            self._channel_info.setText(
                "No measurement loaded — open a measurement file first to detect channels"
            )
            self._channel_info.setStyleSheet(
                "color: #b0a060; font-size: 11px; padding: 2px 4px;"
            )

    # ── Public result ─────────────────────────────────────────────────────

    def load_errors(self) -> dict[str, str]:
        """Return {dbc_path: message} for databases that would not parse."""
        return dict(self._load_errors)

    def result_config(self) -> ChannelConfig:
        """Return the ChannelConfig as configured by the user."""
        cfg = ChannelConfig(name=self._name_edit.text().strip() or "Unnamed")
        for row in self._rows:
            key = row.assigned_channel()
            if key is None:
                continue
            # Last assignment wins if two rows assign the same channel
            cfg.channels[key] = row.dbc_path
        return cfg

    def compatibility_warnings(self) -> list[str]:
        """
        Return warnings for databases that look wrong for their bus.

        Advisory only — an LDF on a CAN channel is flagged, a DBC on a LIN
        channel is not, because LIN layouts are routinely shipped as DBC.
        """
        from core.readers.db_format import compatibility_warning
        warnings = []
        for row in self._rows:
            key = row.assigned_channel()
            if key is None:
                continue
            message = compatibility_warning(row.dbc_path, key[0])
            if message:
                warnings.append(message)
        return warnings

    # ── Row management ────────────────────────────────────────────────────

    def _add_row(
        self, dbc_path: str, preferred_channel: BusChannel | None = None
    ) -> None:
        """Add a database row, compute match quality, auto-suggest channel."""
        pct, text, best_ch = self._compute_match(dbc_path, preferred_channel)

        # Use preferred_channel if given, otherwise auto-suggest best match
        assigned = preferred_channel if preferred_channel is not None else best_ch

        row = _DBCRow(
            dbc_path=dbc_path,
            channels=self._channels_in_file,
            assigned_to=assigned,
            match_pct=pct,
            match_text=text,
            parent=self,
        )
        self._rows.append(row)
        self._rows_layout.addWidget(row)

    def remove_row(self, row: _DBCRow) -> None:
        self._rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.deleteLater()

    # ── Match quality computation ─────────────────────────────────────────

    def _refresh_all_matches(self) -> None:
        """
        Recompute match quality for every database row.

        Scores follow the channel each row is assigned to *now*, and every
        database is read afresh, so a swap between channels or a file
        replaced on disk both show. The button reads "Refreshing…" under a
        busy cursor while it runs, and the line beside it reports the result.
        """
        self._refresh_btn.setEnabled(False)
        idle_text = self._refresh_btn.text()
        self._refresh_btn.setText("Refreshing…")
        self._refresh_result.clear()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # Paint now: the work below runs on this thread, and a full scan of a
        # large measurement can take seconds before the event loop returns.
        self._refresh_btn.repaint()
        self._refresh_result.repaint()
        try:
            outcome = self._recompute_matches()
        finally:
            QApplication.restoreOverrideCursor()
            self._refresh_btn.setText(idle_text)
            self._refresh_btn.setEnabled(True)
        if outcome is None:
            self._refresh_result.setText("No measurement data")
            QMessageBox.information(
                self, 'No measurement data',
                'Load and decode a BLF/ASC measurement first,\n'
                'then click Refresh Match to see match quality.'
            )
            return
        stamp = QTime.currentTime().toString("HH:mm:ss")
        self._refresh_result.setText(f"{outcome} · {stamp}")

    def _recompute_matches(self) -> str | None:
        """Do Refresh Match's work; return its result line, or None if no data."""
        new_chs: list[BusChannel] = []
        scan_failed = False
        # Pull fresh data from the main window
        if self._data_provider is not None:
            try:
                fresh_channels, fresh_ids = self._data_provider()
                if fresh_channels:
                    self._ids_per_channel = fresh_ids
                    # Lengths still come from the pre-scan: the refreshed
                    # summary carries IDs only, and dropping them here would
                    # quietly return LIN scoring to frame-IDs-alone in the one
                    # flow a user reaches for to fix a bad assignment.
                    self._file_match_data = self._normalise_file_match_data(
                        fresh_ids, self._lengths_per_channel
                    )
                    # Update channel dropdowns to include any newly seen channels
                    new_chs = sorted(
                        set(fresh_channels) - set(self._channels_in_file),
                        key=sort_key,
                    )
                    if new_chs:
                        self._channels_in_file = sorted(
                            set(self._channels_in_file) | set(new_chs),
                            key=sort_key,
                        )
                        for row in self._rows:
                            for key in new_chs:
                                encoded = encode_key(key)
                                if row.channel_combo.findData(encoded) < 0:
                                    row.channel_combo.addItem(
                                        channel_label(key), encoded
                                    )
                        self._update_channel_info()
            except Exception:
                scan_failed = True

        if not self._ids_per_channel:
            return None

        # Refresh is the user asking to look again. A database copied over
        # another keeps its timestamp on Windows and often its size, which is
        # all the parse cache can see, so it is emptied rather than trusted.
        _load_database_match_data.cache_clear()

        changed = 0
        for row in self._rows:
            assigned = row.assigned_channel()
            before = (assigned, row._match_lbl.text())
            pct, text, best_ch = self._compute_match(row.dbc_path, assigned)
            # Auto-suggest best channel if user hasn't changed it
            if assigned == all_channels_key(BusType.CAN) and best_ch != assigned:
                idx = row.channel_combo.findData(encode_key(best_ch))
                if idx >= 0:
                    row.channel_combo.setCurrentIndex(idx)
                    pct, text, _best = self._compute_match(row.dbc_path, best_ch)
            row.update_match(pct, text)
            if (row.assigned_channel(), row._match_lbl.text()) != before:
                changed += 1

        parts = []
        if changed:
            parts.append(f"{changed} of {len(self._rows)} databases changed")
        if new_chs:
            plural = "s" if len(new_chs) != 1 else ""
            parts.append(f"{len(new_chs)} new channel{plural} found")
        result = "Match updated: " + ", ".join(parts) if parts else "No change"
        if scan_failed:
            result += " (measurement could not be re-read; earlier scan used)"
        return result

    def _compute_match(
        self, dbc_path: str, channel: BusChannel | None = None
    ) -> tuple[float, str, BusChannel]:
        """
        Return (pct, label_text, best_channel) for a DBC file.

        ``pct`` and ``label_text`` score the database against ``channel``, the
        one it is assigned to: a concrete channel on its own IDs, "All <bus>"
        by its best channel on that bus. ``best_channel`` is the channel in
        the file it suits best, for auto-suggesting an assignment; with no
        ``channel`` the score is that best channel's.

        Matching strategy (match quality display only — decode stays exact):
        1. Exact 29-bit ID match
        2. J1939 PGN fallback — strip source address byte for extended frames
           where PF >= 0xF0 (PDU2 format, PS = destination, SA = last byte).
           E.g. DBC has 0x18FEBD00, file has 0x18FEBDFE — same PGN 0xFEBD.

        This gives realistic match percentages for J1939 files where ECUs
        broadcast on different source addresses than the DBC template value.
        """
        # An LDF describes a LIN cluster, so its natural home is All LIN when
        # nothing better matches. Everything else defaults to All CAN.
        default_ch = all_channels_key(
            BusType.LIN if Path(dbc_path).suffix.lower() == '.ldf'
            else BusType.CAN
        )
        try:
            path = Path(dbc_path).resolve()
            stat = path.stat()
            dbc_ids, dbc_pgn_by_id, dbc_lengths = _load_database_match_data(
                str(path), stat.st_mtime_ns, stat.st_size
            )
        except Exception as exc:
            self._load_errors[dbc_path] = str(exc)
            return 0.0, "can't read database", default_ch
        self._load_errors.pop(dbc_path, None)

        if not dbc_ids or not self._ids_per_channel:
            return 0.0, "0 / 0 IDs", default_ch

        best_ch    = default_ch
        best_count = 0
        best_total = len(dbc_ids)
        hits_by_channel: dict[BusChannel, int] = {}

        for ch, (
            file_ids_norm, file_pgns, file_lengths,
        ) in self._file_match_data.items():

            # Pass 1: exact ID match, and where the measurement reported
            # frame lengths, the length has to agree too.
            matched_ids = dbc_ids & file_ids_norm
            if file_lengths:
                matched_ids = {
                    frame_id for frame_id in matched_ids
                    if file_lengths.get(frame_id) == dbc_lengths.get(frame_id)
                }
            exact_hits = len(matched_ids)

            # Pass 2: PGN fallback for IDs that didn't match exactly
            unmatched_dbc_pgns = {
                dbc_pgn_by_id[fid] for fid in (dbc_ids - matched_ids)
                if fid in dbc_pgn_by_id
            }
            pgn_hits = len(unmatched_dbc_pgns & file_pgns)

            hits = exact_hits + pgn_hits
            hits_by_channel[ch] = hits
            if hits > best_count:
                best_count = hits
                best_ch    = ch

        if channel is None:
            count = best_count
        elif channel[1] == 0:
            count = max(
                (hits for ch, hits in hits_by_channel.items()
                 if ch[0] == channel[0]),
                default=0,
            )
        else:
            count = hits_by_channel.get(channel, 0)
        pct  = count / best_total if best_total else 0.0
        text = f"{count} / {best_total} IDs"
        return pct, text, best_ch

    @staticmethod
    def _normalise_file_match_data(
        ids_per_channel: dict[BusChannel, set[int]],
        lengths_per_channel: dict[BusChannel, dict[int, int]] | None = None,
    ) -> dict[BusChannel, tuple[frozenset[int], frozenset[int], dict[int, int]]]:
        """
        Prepare measurement ID/PGN/length data for all configured database rows.

        The J1939 PGN pass is inert for LIN by construction: ``_j1939_pgn``
        only fires above 0x7FF and LIN frame IDs are 6-bit.

        Frame lengths are what tell two LIN clusters apart. They all number
        their frames from 0, so two unrelated LDFs routinely declare the same
        IDs and score 100% against the wrong channel — in the CANoe sample,
        Door.ldf matches every ID on both LIN channels and only the lengths
        say which one it describes. The lengths dict is empty for CAN, where
        the pre-scan does not collect them, so CAN scoring is untouched.
        """
        lengths_per_channel = lengths_per_channel or {}
        result: dict[
            BusChannel, tuple[frozenset[int], frozenset[int], dict[int, int]]
        ] = {}
        for channel, file_ids in ids_per_channel.items():
            normalised = frozenset(fid & 0x1FFFFFFF for fid in file_ids)
            pgns = frozenset(
                pgn for frame_id in normalised
                if (pgn := _j1939_pgn(frame_id)) is not None
            )
            lengths = {
                frame_id & 0x1FFFFFFF: length
                for frame_id, length in lengths_per_channel.get(channel, {}).items()
            }
            result[channel] = (normalised, pgns, lengths)
        return result

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_add_dbc(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add database file(s)", "",
            "Databases (*.dbc *.arxml *.ldf);;DBC files (*.dbc);;"
            "ARXML files (*.arxml);;LDF files (*.ldf);;All files (*)"
        )
        for path in paths:
            self._add_row(path)

    def _on_save_channel_config(self) -> None:
        cfg = self.result_config()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Channel Config", f"{cfg.name}{ChannelConfig.FILE_EXTENSION}",
            f"Channel Config (*{ChannelConfig.FILE_EXTENSION});;All files (*)"
        )
        if not path:
            return
        try:
            cfg.save(path)
            QMessageBox.information(self, "Saved",
                                    f"Channel config saved:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def _on_load_channel_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Channel Config", "",
            f"Channel Config (*{ChannelConfig.FILE_EXTENSION} "
            f"*{ChannelConfig.LEGACY_FILE_EXTENSION});;All files (*)"
        )
        if not path:
            return
        try:
            cfg = ChannelConfig.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return

        # Clear existing rows and repopulate
        for row in list(self._rows):
            self.remove_row(row)
        self._name_edit.setText(cfg.name)
        for ch, dbc_path in cfg.channels.items():
            self._add_row(dbc_path, preferred_channel=ch)
