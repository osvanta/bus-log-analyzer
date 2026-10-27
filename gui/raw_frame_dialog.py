# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QScrollBar, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.bus_types import channel_label
from core.raw_frame_store import RawFrameStore
from core.trace_differences import previous_frames, window_differences
from gui.user_settings import read_setting, write_setting

_WINDOW = 5_000
_STEP   = 1_000
_COL    = 8
# Typing in Search waits this long after the last key before searching.
_SEARCH_PAUSE_MS = 250
# Cursor 1 dragged on the plot: the Trace follows once it rests this long.
_FOLLOW_PAUSE_MS = 60

#: Columns by number.
_TIME, _DELTA, _CHN, _ID, _NAME, _DIR, _DLC, _DATA = range(_COL)
_HEADERS = ['Time (s)', 'Δt (ms)', 'Chn', 'ID', 'Name', 'Dir', 'DLC', 'Data / Value']

#: Item data of the Data cell: which bytes changed, as a string of 0 and 1.
_CHANGED_ROLE = Qt.ItemDataRole.UserRole + 1

#: The user setting holding the Trace's choices, and their defaults.
_OPTIONS_KEY = 'trace_options'
_DEFAULT_OPTIONS = {'delta_column': True, 'highlight_changes': True, 'follow_cursor': True}


def _fmt_t(t: float | None) -> str:
    return f'{t:.3f} s' if t is not None else '—'


def _row(name: str = '', data: str = '') -> list[str]:
    """The texts of a row under a frame: a name and a value, the rest empty."""
    texts = [''] * _COL
    texts[_NAME] = name
    texts[_DATA] = data
    return texts


class _NavButton(QWidget):
    """Button with a time label displayed above it."""
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self.time_lbl = QLabel('—')
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.time_lbl.setStyleSheet('color: #6ea8d8; font-size: 10px;')
        self.btn = QPushButton(label)
        self.btn.setFixedWidth(max(72, len(label) * 8))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        lay.addWidget(self.time_lbl)
        lay.addWidget(self.btn)

    def set_time(self, t: float | None) -> None:
        self.time_lbl.setText(_fmt_t(t))

    def clicked_connect(self, slot) -> None:
        self.btn.clicked.connect(slot)


class _DataDelegate(QStyledItemDelegate):
    """The Data column, with the bytes that changed since the previous frame
    of the same kind drawn bold in the highlight colour."""

    def __init__(self, dialog: 'RawFrameDialog') -> None:
        super().__init__(dialog)
        self._dialog = dialog

    def _changes(self, index) -> str:
        if not self._dialog.highlight_changes:
            return ''
        changed = index.data(_CHANGED_ROLE)
        return changed if isinstance(changed, str) and '1' in changed else ''

    @staticmethod
    def colour(palette: QPalette) -> QColor:
        """Orange, darker on a light background so it reads on both."""
        light = palette.color(QPalette.ColorRole.Base).lightness() > 128
        return QColor('#C25E00' if light else '#FFA640')

    def paint(self, painter, option, index) -> None:
        changed = self._changes(index)
        if not changed:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ''
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        plain = opt.palette.color(QPalette.ColorRole.HighlightedText if selected
                                  else QPalette.ColorRole.Text)
        bold = QFont(opt.font)
        bold.setBold(True)
        accent = self.colour(opt.palette)
        painter.save()
        x = rect.left() + 3
        for number, byte in enumerate(text.split(' ')):
            hit = number < len(changed) and changed[number] == '1'
            font = bold if hit else opt.font
            metrics = QFontMetrics(font)
            painter.setFont(font)
            painter.setPen(accent if hit else plain)
            width = metrics.horizontalAdvance(byte + ' ')
            painter.drawText(QRect(x, rect.top(), width, rect.height()),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                             byte)
            x += width
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        if self._changes(index):
            bold = QFont(option.font)
            bold.setBold(True)
            size.setWidth(max(size.width(),
                              QFontMetrics(bold).horizontalAdvance(str(index.data())) + 12))
        return size


class RawFrameDialog(QDialog):
    """
    Raw CAN Frame viewer — on-disk sliding window, no frame cap.

    Accepts a RawFrameStore (Option B architecture):
    - Filtering uses vectorised numpy on in-memory arrays — no disk access
    - Rendering reads only the visible 5,000-frame window from disk
    - Signals decoded on-demand when a row is expanded

    Δt is the time since the previous frame of the same ID on the same
    channel, over the whole file whatever the filter; the bytes that changed
    since that frame are highlighted. While "Follow Cursor 1" is on, picking a
    frame asks for Cursor 1 there (*timeClicked*), and follow_time() brings
    the frame at Cursor 1 into view.
    """

    #: A frame was picked while following: Cursor 1 belongs at this time.
    timeClicked = Signal(float)
    #: "Set Cursor 2 here" on a frame.
    cursor2Requested = Signal(float)

    def __init__(self, raw_store: RawFrameStore, parent=None, *,
                 settings_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Trace — Raw CAN and LIN Frames')
        self.resize(1260, 750)

        self._raw_store = raw_store
        self._win_start = 0
        self._window_times: tuple[float | None, float | None] = (None, None)
        self._is_expanded = False
        self._settings_path = settings_path
        saved = read_setting(settings_path, _OPTIONS_KEY, {}) if settings_path else {}
        self._options = {name: bool(saved.get(name, default))
                         if isinstance(saved, dict) else default
                         for name, default in _DEFAULT_OPTIONS.items()}

        # _matching: None = all frames; else the matching frames' indices
        self._matching: np.ndarray | None = None

        # The previous frame of each frame's kind, worked out in steps on a
        # large file (the event loop runs between them).
        self._previous = previous_frames(raw_store)
        self._index_timer = QTimer(self)
        self._index_timer.setInterval(0)
        self._index_timer.timeout.connect(self._index_step)

        # Following Cursor 1: the time asked for, set while the Trace moves
        # its own selection so that move does not ask for a cursor in turn.
        self._follow_time: float | None = None
        self._following = False
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.setInterval(_FOLLOW_PAUSE_MS)
        self._follow_timer.timeout.connect(self._follow_now)

        # ── Filter controls ───────────────────────────────────────────────
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            'Search frame name, ID, data, direction…'
        )

        self.channel_combo = QComboBox()
        self.channel_combo.addItem('All Channels', None)
        # Bus-tagged: CAN 1 and LIN 1 are different channels that happen to
        # share a number, so filtering on the number alone would mix them.
        for key in raw_store.channel_keys():
            self.channel_combo.addItem(channel_label(key), key)

        self.follow_button = QPushButton('⇄ Follow Cursor 1')
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(self._options['follow_cursor'])
        self.follow_button.setToolTip(
            'On: picking a frame moves plot Cursor 1 to it, and moving Cursor 1 '
            'brings its frame into view.')
        self.expand_btn   = QPushButton('Expand All')
        self.collapse_btn = QPushButton('Collapse All')

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel('Search'))
        top_row.addWidget(self.search_edit, 1)
        top_row.addWidget(QLabel('Channel'))
        top_row.addWidget(self.channel_combo)
        top_row.addWidget(self.follow_button)
        top_row.addWidget(self.expand_btn)
        top_row.addWidget(self.collapse_btn)

        # ── Status ────────────────────────────────────────────────────────
        self.status_label = QLabel()
        self.status_label.setStyleSheet('color: #8B949E; font-size: 11px;')
        self.cursor_label = QLabel()
        self.cursor_label.setStyleSheet('color: #8B949E; font-size: 11px;')
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.cursor_label)

        # ── Navigation bar ────────────────────────────────────────────────
        self._nb_start = _NavButton('◀◀ Start')
        self._nb_back  = _NavButton('◀ −1000')
        self._nb_fwd   = _NavButton('+1000 ▶')
        self._nb_end   = _NavButton('End ▶▶')

        self._scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self._scrollbar.setMinimum(0)
        self._scrollbar.setPageStep(_WINDOW)
        self._scrollbar.setSingleStep(_STEP)
        self._scrollbar.setMinimumHeight(30)
        self._scrollbar.setStyleSheet("""
            QScrollBar:horizontal {
                border: 1px solid #4a6080; background: #1a2a3a;
                height: 30px; border-radius: 4px;
            }
            QScrollBar::handle:horizontal {
                background: #3a6090; min-width: 24px;
                border-radius: 3px; border: 1px solid #5080b0;
            }
            QScrollBar::handle:horizontal:hover  { background: #5090c0; }
            QScrollBar::handle:horizontal:pressed { background: #60a0d0; }
            QScrollBar::add-line:horizontal {
                border: 1px solid #4a6080; background: #253545;
                width: 20px; border-radius: 2px;
                subcontrol-position: right; subcontrol-origin: margin;
            }
            QScrollBar::sub-line:horizontal {
                border: 1px solid #4a6080; background: #253545;
                width: 20px; border-radius: 2px;
                subcontrol-position: left; subcontrol-origin: margin;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: #1e2e3e;
            }
        """)

        self._jump_edit = QLineEdit()
        self._jump_edit.setFixedWidth(90)
        self._jump_edit.setPlaceholderText('time (s)')
        self._jump_edit.setToolTip('Type a time in seconds and press Enter or Go')
        self._jump_btn = QPushButton('Go')
        self._jump_btn.setFixedWidth(36)

        nav_row = QHBoxLayout()
        nav_row.addWidget(self._nb_start)
        nav_row.addWidget(self._nb_back)
        nav_row.addWidget(self._scrollbar, 1)
        nav_row.addWidget(self._nb_fwd)
        nav_row.addWidget(self._nb_end)
        nav_row.addSpacing(8)
        nav_row.addWidget(QLabel('Jump:'))
        nav_row.addWidget(self._jump_edit)
        nav_row.addWidget(self._jump_btn)

        # ── Tree ──────────────────────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setColumnCount(_COL)
        self.tree.setHeaderLabels(_HEADERS)
        self.tree.headerItem().setToolTip(
            _DELTA, 'Time since the previous frame with the same ID on the same '
                    'channel, in milliseconds, over the whole file.')
        self.tree.headerItem().setToolTip(
            _DATA, 'Bytes that changed since the previous frame with the same ID '
                   'on the same channel are bold and coloured.')
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemDelegateForColumn(_DATA, _DataDelegate(self))
        self.tree.setColumnHidden(_DELTA, not self._options['delta_column'])
        header = self.tree.header()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos: self.header_menu().exec(header.mapToGlobal(pos)))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_row_menu)

        # ── Layout ────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addLayout(status_row)
        layout.addLayout(nav_row)
        layout.addWidget(self.tree, 1)

        # ── Connections ───────────────────────────────────────────────────
        # Typing waits for a pause: each search looks at every frame, and a
        # word typed letter by letter would otherwise search once per letter.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(_SEARCH_PAUSE_MS)
        self._search_timer.timeout.connect(self._on_filter_changed)
        self.search_edit.textChanged.connect(self._search_timer.start)
        self.search_edit.returnPressed.connect(self._on_filter_changed)
        self.channel_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._nb_start.clicked_connect(self._go_start)
        self._nb_back.clicked_connect(self._go_back)
        self._nb_fwd.clicked_connect(self._go_fwd)
        self._nb_end.clicked_connect(self._go_end)
        self._scrollbar.valueChanged.connect(self._on_scroll)
        self._jump_btn.clicked.connect(self._go_jump)
        self._jump_edit.returnPressed.connect(self._go_jump)
        self.expand_btn.clicked.connect(self._expand_window)
        self.collapse_btn.clicked.connect(self._collapse_all)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.follow_button.toggled.connect(self._on_follow_toggled)

        # A file that fits one step is indexed at once; a larger one in
        # steps, its Δt filled in when done.
        if self._previous.progress == 0 and self._previous.count <= _small_file():
            self._previous.build()
        self._on_filter_changed()
        if not self._previous.ready:
            self._index_timer.start()

    # ── Options ───────────────────────────────────────────────────────────

    @property
    def highlight_changes(self) -> bool:
        return self._options['highlight_changes']

    def delta_column_shown(self) -> bool:
        return not self.tree.isColumnHidden(_DELTA)

    def set_option(self, name: str, on: bool) -> None:
        """Turn one of the Trace's choices on or off, and remember it."""
        if self._options.get(name) == on:
            return
        self._options[name] = on
        if name == 'delta_column':
            self.tree.setColumnHidden(_DELTA, not on)
            if on:
                self.tree.resizeColumnToContents(_DELTA)
        elif name == 'highlight_changes':
            self.tree.viewport().update()
            self.tree.resizeColumnToContents(_DATA)
        elif name == 'follow_cursor' and self.follow_button.isChecked() != on:
            self.follow_button.setChecked(on)
        if self._settings_path is not None:
            try:
                write_setting(self._settings_path, _OPTIONS_KEY, dict(self._options))
            except OSError:
                pass        # the choice still holds for this session

    def header_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for name, text, tip in (
                ('delta_column', 'Δt column',
                 'Time since the previous frame with the same ID on the same channel.'),
                ('highlight_changes', 'Highlight changed bytes',
                 'Bytes that changed since that frame, bold and coloured.')):
            action = QAction(text, menu)
            action.setCheckable(True)
            action.setChecked(self._options[name])
            action.setToolTip(tip)
            action.toggled.connect(lambda on, n=name: self.set_option(n, on))
            menu.addAction(action)
        return menu

    # ── Filter ────────────────────────────────────────────────────────────

    def _on_filter_changed(self) -> None:
        self._search_timer.stop()
        needle         = self.search_edit.text().strip().lower()
        channel_filter = self.channel_combo.currentData()

        # None when all frames match (zero extra RAM), else their indices.
        self._matching = self._raw_store.matching_indices(needle, channel_filter)

        self._win_start = 0
        self._update_scrollbar()
        self._render_window()

    def _total_matching(self) -> int:
        if self._matching is None:
            return len(self._raw_store)
        return len(self._matching)

    def _frame_index(self, pos: int) -> int:
        """Map a position in the matching list to a RawFrameStore index."""
        if self._matching is None:
            return pos
        return int(self._matching[pos])

    def _position_at(self, target_t: float, *, last: bool = False) -> int:
        """Position in the matching list of the first frame at or after
        *target_t*; with *last*, of the last frame at or before it (0 when
        there is none). Searched in halves: frames are in time order, or
        nearly so."""
        n = self._total_matching()
        ts_store = np.frombuffer(self._raw_store.timestamps, dtype=np.float64)
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            t = ts_store[self._frame_index(mid)]
            if t <= target_t if last else t < target_t:
                lo = mid + 1
            else:
                hi = mid
        return max(0, lo - 1) if last else min(lo, n - 1)

    # ── Navigation ────────────────────────────────────────────────────────

    def _go_start(self)  -> None: self._set_window(0)
    def _go_end(self)    -> None: self._set_window(max(0, self._total_matching() - _WINDOW))
    def _go_back(self)   -> None: self._set_window(max(0, self._win_start - _STEP))
    def _go_fwd(self)    -> None:
        self._set_window(min(max(0, self._total_matching() - _WINDOW),
                             self._win_start + _STEP))

    def _on_scroll(self, value: int) -> None:
        if value != self._win_start:
            self._set_window(value, update_scrollbar=False)

    def _go_jump(self) -> None:
        text = self._jump_edit.text().strip().rstrip('s').strip()
        if not text:
            return
        try:
            target_t = float(text)
        except ValueError:
            self._jump_edit.setStyleSheet('border: 1px solid #c04040;')
            return
        self._jump_edit.setStyleSheet('')

        n = self._total_matching()
        if n == 0:
            return
        lo = self._position_at(target_t)
        start = max(0, min(lo - _WINDOW // 2, n - _WINDOW))
        self._set_window(start)

    def _set_window(self, start: int, update_scrollbar: bool = True) -> None:
        n = self._total_matching()
        self._win_start = max(0, min(start, max(0, n - _WINDOW)))
        if update_scrollbar:
            self._scrollbar.blockSignals(True)
            self._scrollbar.setValue(self._win_start)
            self._scrollbar.blockSignals(False)
        if self._is_expanded:
            self.tree.collapseAll()
            self._is_expanded = False
        self._render_window()

    def _update_scrollbar(self) -> None:
        n       = self._total_matching()
        max_val = max(0, n - _WINDOW)
        self._scrollbar.setMaximum(max_val)
        self._scrollbar.setValue(0)

    # ── Render window ─────────────────────────────────────────────────────

    def _render_window(self) -> None:
        n       = self._total_matching()
        w_start = self._win_start
        w_end   = min(w_start + _WINDOW, n)

        # Collect frame indices for this window
        indices = [self._frame_index(i) for i in range(w_start, w_end)]

        # Fetch records from store (disk read for data bytes)
        records = self._raw_store.get_window(indices)

        delta = changed = None
        if indices and self._previous.ready:
            delta, changed = window_differences(
                self._raw_store, self._previous.previous, indices)

        top_items: list[QTreeWidgetItem] = []
        for rec in records:
            position = len(top_items)
            ch_text = channel_label(rec.channel_key)
            id_text = f'{rec.arbitration_id:X}'
            data_hex = ' '.join(f'{b:02X}' for b in rec.data[:rec.dlc])
            delta_text = ''
            if delta is not None and delta[position] == delta[position]:
                delta_text = f'{delta[position] * 1000:.3f}'

            top = QTreeWidgetItem([
                f'{rec.time_s:.6f}',
                delta_text,
                ch_text,
                id_text,
                rec.frame_name or ('(decoded)' if rec.decoded else '(unmatched)'),
                rec.direction,
                str(rec.dlc),
                data_hex,
            ])
            top.setTextAlignment(_DELTA, Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
            if changed is not None:
                shown = changed[position, :len(rec.data[:rec.dlc])]
                if shown.any():
                    top.setData(_DATA, _CHANGED_ROLE,
                                ''.join('1' if c else '0' for c in shown))
            # Store store-index in item data for lazy signal decode
            top.setData(0, Qt.ItemDataRole.UserRole, indices[position])
            # Placeholder child so the expand arrow appears for decoded frames
            if rec.decoded:
                placeholder = QTreeWidgetItem(['…'])
                placeholder.setData(0, Qt.ItemDataRole.UserRole, -1)  # sentinel
                top.addChild(placeholder)
            top_items.append(top)

        self.tree.setUpdatesEnabled(False)
        self._following = True          # clearing moves the current item
        try:
            self.tree.clear()
            self.tree.insertTopLevelItems(0, top_items)
        finally:
            self._following = False
        self.tree.setUpdatesEnabled(True)

        for col in range(_COL):
            self.tree.resizeColumnToContents(col)

        # Update nav button time labels
        ts_arr = np.frombuffer(self._raw_store.timestamps, dtype=np.float64)
        n_store = len(self._raw_store)

        def _ts(pos: int) -> float | None:
            idx = self._frame_index(pos) if 0 <= pos < n else None
            return float(ts_arr[idx]) if idx is not None and 0 <= idx < n_store else None

        t_win_s = _ts(w_start)
        t_win_e = _ts(w_end - 1)
        self._nb_start.set_time(_ts(0))
        self._nb_back.set_time(_ts(max(0, w_start - _STEP)))
        self._nb_fwd.set_time(_ts(min(n - 1, w_start + _WINDOW + _STEP - 1)))
        self._nb_end.set_time(_ts(n - 1))

        self._show_status(t_win_s, t_win_e)

    def _show_status(self, t_win_s=None, t_win_e=None, *, keep_times: bool = False) -> None:
        if not keep_times:
            self._window_times = (t_win_s, t_win_e)
        t_win_s, t_win_e = self._window_times
        n = self._total_matching()
        w_end = min(self._win_start + _WINDOW, n)
        text = (f'Showing frames {self._win_start+1:,}–{w_end:,} of {n:,} matching  '
                f'({_fmt_t(t_win_s)} → {_fmt_t(t_win_e)})  '
                f'| Total in file: {len(self._raw_store):,}')
        if not self._previous.ready:
            text += f'  | Δt: working out… {self._previous.progress:.0%}'
        self.status_label.setText(text)

    # ── Δt index, in steps ───────────────────────────────────────────────

    def _index_step(self) -> None:
        try:
            done = self._previous.step()
        except RuntimeError:            # the measurement was closed
            self._index_timer.stop()
            return
        if done:
            self._index_timer.stop()
            current = self._current_frame()
            self._render_window()
            if current is not None:
                self._select_frame(current)
        else:
            self._show_status(keep_times=True)

    # ── Cursor 1 ──────────────────────────────────────────────────────────

    def _current_frame(self) -> int | None:
        """Store index of the frame of the current row (or of its signal)."""
        item = self.tree.currentItem()
        while item is not None and item.parent() is not None:
            item = item.parent()
        if item is None:
            return None
        index = item.data(0, Qt.ItemDataRole.UserRole)
        return int(index) if index is not None and index >= 0 else None

    def _frame_time(self, index: int) -> float:
        return float(np.frombuffer(self._raw_store.timestamps, dtype=np.float64)[index])

    def _select_frame(self, index: int) -> bool:
        """Make the row of store frame *index* current, if on screen."""
        for row in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(row)
            if item.data(0, Qt.ItemDataRole.UserRole) == index:
                self._following = True
                try:
                    self.tree.setCurrentItem(item)
                finally:
                    self._following = False
                self.tree.scrollToItem(item, QTreeWidget.ScrollHint.PositionAtCenter)
                return True
        return False

    def _on_current_changed(self, current, _previous) -> None:
        if self._following or current is None or not self.follow_button.isChecked():
            return
        index = self._current_frame()
        if index is not None:
            self.timeClicked.emit(self._frame_time(index))

    def _on_follow_toggled(self, on: bool) -> None:
        self.set_option('follow_cursor', on)
        if on and self._follow_time is not None:
            self._follow_now()

    def follow_time(self, t: float) -> None:
        """Cursor 1 is at *t*: show its frame (the last at or before *t*)
        once Cursor 1 rests, while following and shown."""
        self._follow_time = t
        self.cursor_label.setText(f'Cursor 1: {t:.6f} s')
        self._follow_timer.start()

    def _follow_now(self) -> None:
        t = self._follow_time
        if t is None or not self.follow_button.isChecked() or not self.isVisible():
            return
        current = self._current_frame()
        if current is not None and self._frame_time(current) == t:
            return          # picked here: that frame stays the current one
        if not self._total_matching():
            return
        position = self._position_at(t, last=True)
        if not self._win_start <= position < self._win_start + _WINDOW:
            self._set_window(position - _WINDOW // 2)
        self._select_frame(self._frame_index(position))

    def _show_row_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        index = self._current_frame()
        if index is None:
            return
        self.row_menu(self._frame_time(index)).exec(self.tree.viewport().mapToGlobal(pos))

    def row_menu(self, t: float) -> QMenu:
        menu = QMenu(self)
        menu.addAction('Set Cursor 1 here', lambda: self.timeClicked.emit(t))
        menu.addAction('Set Cursor 2 here', lambda: self.cursor2Requested.emit(t))
        return menu

    # ── Lazy signal decode on expand ──────────────────────────────────────

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        """Decode signals for this frame on first expand."""
        # Check if placeholder is still present
        if item.childCount() == 0:
            return
        first_child = item.child(0)
        if first_child is None or first_child.data(0, Qt.ItemDataRole.UserRole) != -1:
            return   # already decoded

        store_idx = item.data(0, Qt.ItemDataRole.UserRole)
        if store_idx is None or store_idx < 0:
            return

        # Remove placeholder
        item.removeChild(first_child)

        # Fetch the record first so we have rec.channel for per-channel lookup
        from core.models import RawFrame
        records = self._raw_store.get_window([store_idx])
        if not records:
            return
        rec = records[0]

        # Prefer per-channel config (CAN1→DBC1, CAN2→DBC2, …); fall back to
        # the single all-channels decoder set at load time.
        channel_config = getattr(self._raw_store, 'channel_config', None)
        if channel_config is not None:
            decoder = channel_config.decoder_for(rec.bus, rec.channel)
        else:
            decoder = getattr(self._raw_store, 'decoder', None)

        if decoder is None:
            item.addChild(QTreeWidgetItem(_row(name='(no DBC)')))
            return

        raw_frame = RawFrame(
            timestamp=rec.time_s,
            channel=rec.channel,
            arbitration_id=rec.arbitration_id,
            is_extended_id=rec.is_extended,
            is_fd=rec.is_fd,
            dlc=rec.dlc,
            data=rec.data[:rec.dlc],
            direction=rec.direction,
        )
        try:
            samples = decoder.decode_frame(raw_frame)
        except Exception:
            samples = []

        if not samples:
            item.addChild(QTreeWidgetItem(_row(name='(no signals)')))
            return

        for s in samples:
            val_str = f'{s.value} {s.unit}'.strip()
            child = QTreeWidgetItem(_row(name=s.signal_name, data=val_str))
            child.setData(0, Qt.ItemDataRole.UserRole, -2)
            item.addChild(child)

    # ── Expand / Collapse ─────────────────────────────────────────────────

    def _expand_window(self) -> None:
        self._is_expanded = True
        self.tree.expandAll()

    def _collapse_all(self) -> None:
        self._is_expanded = False
        self.tree.collapseAll()


def _small_file() -> int:
    """Frames the Trace indexes at once, before it opens."""
    from core import trace_differences
    return trace_differences.CHUNK
