# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter,
    QPalette, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractSlider, QApplication, QButtonGroup, QCheckBox, QDialog,
    QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core import trace_query
from core.bus_types import BusType, channel_label
from core.raw_frame_store import FrameSearch, RawFrameStore
from core.trace_differences import previous_frames, window_differences
from core.trace_query import ColourRule, Colouring, TraceCatalog, TraceFilter
from gui.user_settings import read_setting, write_setting

_WINDOW = 5_000
_STEP   = 1_000
_COL    = 8
# Typing in Search waits this long after the last key before searching.
_SEARCH_PAUSE_MS = 250
# Cursor 1 dragged on the plot: the Trace follows once it rests this long.
_FOLLOW_PAUSE_MS = 60
# The time bar dragged: the Trace follows once it rests this long.
_TIME_BAR_PAUSE_MS = 30

#: Columns by number.
_TIME, _DELTA, _CHN, _ID, _NAME, _DIR, _DLC, _DATA = range(_COL)
_HEADERS = ['Time (s)', 'Δt (ms)', 'Chn', 'ID', 'Name', 'Dir', 'DLC', 'Data / Value']

#: Item data of the Data cell: which bytes changed, as a string of 0 and 1.
_CHANGED_ROLE = Qt.ItemDataRole.UserRole + 1
#: Item data of a signal row's Name cell: (message, signal, number, shown).
_SIGNAL_ROLE = Qt.ItemDataRole.UserRole + 2

#: The user setting holding the Trace's choices, and their defaults.
_OPTIONS_KEY = 'trace_options'
_DEFAULT_OPTIONS = {'delta_column': True, 'highlight_changes': True, 'follow_cursor': True}

#: Colours a rule can take, in the order new rules take them.
COLOURS = [('Amber', '#d29922'), ('Red', '#f85149'), ('Green', '#3fb950'),
           ('Blue', '#58a6ff'), ('Violet', '#bc8cff'), ('Pink', '#f778ba')]
#: A search hit, in the table and on the time bar.
HIT_COLOUR = '#e3b341'
#: How strongly a rule's colour tints its rows (of 255).
_ROW_ALPHA = 70
_HIT_ALPHA = 150


def _fmt_t(t: float | None) -> str:
    return f'{t:.3f} s' if t is not None else '—'


def _row(name: str = '', data: str = '') -> list[str]:
    """The texts of a row under a frame: a name and a value, the rest empty."""
    texts = [''] * _COL
    texts[_NAME] = name
    texts[_DATA] = data
    return texts


def _swatch(colour: str, size: int = 12) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(colour))
    return QIcon(pixmap)


def _tint(colour: str, alpha: int) -> QBrush:
    c = QColor(colour)
    c.setAlpha(alpha)
    return QBrush(c)


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


class TimeBar(QWidget):
    """
    The whole measurement, top to bottom: the big steps of the Trace's two
    vertical scrolls (the table's own scrollbar makes the small ones).

    A box marks the frames loaded in the table, a line Cursor 1, and ticks
    where search hits and coloured frames are. Clicking or dragging asks for
    a time (*timeRequested*); the wheel asks for a big step (*stepRequested*,
    +1 later, −1 earlier).
    """

    timeRequested = Signal(float)
    stepRequested = Signal(int)

    _MARGIN = 6
    _TRACK_X, _TRACK_W = 4, 14

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(66)
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('The whole measurement. Click or drag to go to a time; '
                        'the wheel moves in big steps (Ctrl+PgUp / Ctrl+PgDn).')
        self._t0 = 0.0
        self._t1 = 0.0
        self._window: tuple[float, float] | None = None
        self._cursor: float | None = None
        self._ticks: list[tuple[QColor, np.ndarray]] = []

    # ── State ────────────────────────────────────────────────────────────

    def set_range(self, t0: float, t1: float) -> None:
        self._t0, self._t1 = float(t0), float(t1)
        self.update()

    def set_window(self, start: float | None, end: float | None) -> None:
        self._window = None if start is None or end is None else (start, end)
        self.update()

    def set_cursor(self, t: float | None) -> None:
        self._cursor = t
        self.update()

    def set_ticks(self, ticks: list[tuple[str, np.ndarray]]) -> None:
        """*ticks*: (colour, bins) pairs; bins a bool per stretch of time."""
        self._ticks = [(QColor(colour), bins) for colour, bins in ticks]
        self.update()

    def window(self) -> tuple[float, float] | None:
        return self._window

    # ── Geometry ─────────────────────────────────────────────────────────

    def _track(self) -> QRect:
        return QRect(self._TRACK_X, self._MARGIN, self._TRACK_W,
                     max(1, self.height() - 2 * self._MARGIN))

    def y_of(self, t: float) -> int:
        track = self._track()
        span = self._t1 - self._t0
        share = 0.0 if span <= 0 else (t - self._t0) / span
        return track.top() + round(max(0.0, min(1.0, share)) * track.height())

    def t_of(self, y: int) -> float:
        track = self._track()
        share = (y - track.top()) / max(1, track.height())
        return self._t0 + max(0.0, min(1.0, share)) * (self._t1 - self._t0)

    # ── Painting ─────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        palette = self.palette()
        light = palette.color(QPalette.ColorRole.Base).lightness() > 128
        painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Window))
        track = self._track()
        painter.fillRect(track, QColor('#d0d7de' if light else '#1e2e3e'))
        # Each colour in a lane of its own, so a busy one hides no other.
        lanes = [tick for tick in self._ticks if len(tick[1])]
        for lane, (colour, bins) in enumerate(lanes):
            left = track.left() + lane * track.width() // len(lanes)
            width = max(2, track.width() // len(lanes))
            count = len(bins)
            for b in np.flatnonzero(bins).tolist():
                y = track.top() + int(b * track.height() / count)
                painter.fillRect(left, y, width, max(2, track.height() // count + 1), colour)
        if self._window is not None:
            top = self.y_of(self._window[0])
            bottom = max(top + 8, self.y_of(self._window[1]))
            box = QRect(track.left() - 3, top, track.width() + 6, bottom - top)
            fill = QColor('#58a6ff')
            fill.setAlpha(110)
            painter.fillRect(box, fill)
            pen = painter.pen()
            pen.setColor(QColor('#0b3d91' if light else '#ffffff'))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(box.adjusted(1, 1, -1, -1))
        if self._cursor is not None and self._t1 > self._t0:
            y = self.y_of(self._cursor)
            painter.fillRect(0, y - 1, track.right() + 4, 2, QColor(HIT_COLOUR))
        # Times down the right-hand side.
        font = QFont(self.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.8))
        painter.setFont(font)
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        metrics = QFontMetrics(font)
        left = track.right() + 4
        for share in (0.0, 0.25, 0.5, 0.75, 1.0):
            t = self._t0 + share * (self._t1 - self._t0)
            text = _short_time(t, self._t1 - self._t0)
            y = track.top() + round(share * track.height())
            y = max(metrics.ascent(), min(self.height() - metrics.descent(),
                                          y + metrics.ascent() // 2))
            painter.drawText(left, y, text)
        painter.end()

    # ── Mouse ────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.timeRequested.emit(self.t_of(int(event.position().y())))

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.timeRequested.emit(self.t_of(int(event.position().y())))

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.stepRequested.emit(-1 if delta > 0 else 1)
        event.accept()


def _short_time(t: float, span: float) -> str:
    if span >= 100:
        return f'{t:.0f} s'
    if span >= 10:
        return f'{t:.1f} s'
    return f'{t:.2f} s'


class FilterPopup(QFrame):
    """What the Filter button opens: a short list of checkboxes, applied as
    they change."""

    def __init__(self, dialog: 'RawFrameDialog') -> None:
        super().__init__(dialog, Qt.WindowType.Popup)
        self._dialog = dialog
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(2)
        self.boxes: dict[str, QCheckBox] = {}
        self.build()

    def build(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.boxes = {}
        dialog = self._dialog
        flt = dialog.trace_filter

        def heading(text: str) -> None:
            label = QLabel(text)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            self._layout.addSpacing(4)
            self._layout.addWidget(label)

        def box(name: str, text: str, checked: bool, toggled, icon: QIcon | None = None) -> None:
            check = QCheckBox(text)
            if icon is not None:
                check.setIcon(icon)
            check.setChecked(checked)
            check.toggled.connect(toggled)
            self.boxes[name] = check
            self._layout.addWidget(check)

        keys = dialog.channel_keys()
        if len(keys) > 1:
            heading('Channels')
            for key in keys:
                box(f'channel:{channel_label(key)}', channel_label(key),
                    flt.channels is None or key in flt.channels,
                    lambda on, k=key: dialog.set_channel_shown(k, on))
        heading('Direction')
        box('rx', 'Rx', flt.rx, lambda on: dialog.set_filter(rx=on))
        box('tx', 'Tx', flt.tx, lambda on: dialog.set_filter(tx=on))
        heading('Frames')
        box('data', 'Data frames', flt.data, lambda on: dialog.set_filter(data=on))
        box('errors', 'Error frames', flt.errors, lambda on: dialog.set_filter(errors=on))
        box('remote', 'Remote frames', flt.remote, lambda on: dialog.set_filter(remote=on))
        if flt.only_ids or flt.hidden_ids:
            heading('IDs (untick to undo)')
            for key in sorted(flt.only_ids):
                box(f'only:{key}', f'Only {dialog.id_text(key)}', True,
                    lambda on, k=key: on or dialog.forget_id(k))
            for key in sorted(flt.hidden_ids):
                box(f'hide:{key}', f'Hide {dialog.id_text(key)}', True,
                    lambda on, k=key: on or dialog.forget_id(k))
        rules = dialog.colour_rules
        if rules:
            heading('Colours')
            counts = dialog.colour_counts()
            for number, rule in enumerate(rules, 1):
                row = QHBoxLayout()
                check = QCheckBox(f'{rule.label} · {counts[number]:,}')
                check.setIcon(_swatch(rule.colour))
                check.setChecked(number not in flt.hidden_colours)
                check.toggled.connect(lambda on, n=number: dialog.set_colour_shown(n, on))
                self.boxes[f'colour:{number}'] = check
                remove = QToolButton()
                remove.setText('✕')
                remove.setAutoRaise(True)
                remove.setToolTip('Remove this colour')
                remove.clicked.connect(lambda _=False, n=number: self._remove(n))
                row.addWidget(check, 1)
                row.addWidget(remove)
                self._layout.addLayout(row)
            box('colour:0', f'Not coloured · {counts[0]:,}', 0 not in flt.hidden_colours,
                lambda on: dialog.set_colour_shown(0, on), _swatch('#808080'))
        reset = QPushButton('Reset all')
        reset.setFlat(True)
        reset.clicked.connect(self._reset)
        self._layout.addSpacing(6)
        self._layout.addWidget(reset)
        self.adjustSize()

    def _remove(self, number: int) -> None:
        self._dialog.remove_colour_rule(number)
        self.build()

    def _reset(self) -> None:
        self._dialog.reset_filter()
        self.build()


class ColourWhenDialog(QDialog):
    """Right-click a signal ▸ Colour when…: a condition and a colour."""

    def __init__(self, condition: str, colour: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Colour when…')
        self.edit = QLineEdit(condition)
        self.edit.setMinimumWidth(320)
        self.group = QButtonGroup(self)
        swatches = QHBoxLayout()
        for number, (name, value) in enumerate(COLOURS):
            button = QToolButton()
            button.setCheckable(True)
            button.setIcon(_swatch(value, 16))
            button.setToolTip(name)
            button.setProperty('colour', value)
            self.group.addButton(button, number)
            swatches.addWidget(button)
            if value == colour:
                button.setChecked(True)
        swatches.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Colour the frames where'))
        layout.addWidget(self.edit)
        layout.addLayout(swatches)
        layout.addWidget(buttons)

    def condition(self) -> str:
        return self.edit.text().strip()

    def colour(self) -> str:
        button = self.group.checkedButton()
        return button.property('colour') if button is not None else COLOURS[0][1]


class RawFrameDialog(QDialog):
    """
    The Trace: every raw CAN and LIN frame, on-disk sliding window, no cap.

    - Two vertical scrolls: the time bar on the right is the whole
      measurement (big steps); the table's scrollbar moves within the 5,000
      frames loaded (small steps), and loads the next ones at its end.
    - Search *finds*: rows stay, hits are marked, F3 / Shift+F3 step through
      them, a hit inside a signal opens its frame. It reaches signal names,
      value labels and conditions (``Signal > 60``); the funnel button shows
      only the matching frames instead.
    - Filter hides channels, a direction, kinds of frame, IDs and colours.
      Colours come from the right-click menu (this ID, or a signal condition).

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

        # _matching: None = all frames; else the shown frames' indices.
        self._matching: np.ndarray | None = None
        # Search: the frames found (sorted indices) and those of them shown.
        self._catalog = TraceCatalog(raw_store)
        self._query: trace_query.Query | None = None
        self._query_text = ''
        self._hits: np.ndarray | None = None
        self._shown_hits: np.ndarray | None = None
        self._hit_bins: np.ndarray | None = None
        self._hit_number: int | None = None
        # Filter and colours.
        self._filter = TraceFilter()
        self._rules: list[ColourRule] = []
        self._colouring: Colouring | None = None
        # What the next piece of work must redo, and the frame to keep in view.
        self._query_dirty = False
        self._colours_dirty = False
        self._new_query = False
        self._anchor: int | None = None
        self._filter_popup: FilterPopup | None = None

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

        # ── Toolbar: Search, its hits, Filter, Follow ────────────────────
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            'Search ID, name, data, signal — or Signal > 60')
        self.search_edit.setToolTip(
            'Finds frames by ID, name, data bytes, direction or time, and by the '
            'signals they carry: a signal name, "Signal = Label", or a condition '
            'such as "Signal > 60". Join terms with and / or.\n'
            'Enter or F3: next hit · Shift+F3: previous hit')
        self.search_edit.setClearButtonEnabled(True)
        self.hit_label = QLabel()
        self.hit_label.setMinimumWidth(70)
        self.prev_button = QToolButton()
        self.prev_button.setText('▲')
        self.prev_button.setToolTip('Previous hit (Shift+F3)')
        self.next_button = QToolButton()
        self.next_button.setText('▼')
        self.next_button.setToolTip('Next hit (F3)')
        self.only_hits_button = QToolButton()
        self.only_hits_button.setText('⛉')
        self.only_hits_button.setCheckable(True)
        self.only_hits_button.setToolTip('Show only the frames the search finds')
        self.filter_button = QPushButton('Filter ▾')
        self.filter_button.setToolTip(
            'Hide channels, Rx or Tx, error or remote frames, IDs or colours')
        self.follow_button = QPushButton('⇄ Follow Cursor 1')
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(self._options['follow_cursor'])
        self.follow_button.setToolTip(
            'On: picking a frame moves plot Cursor 1 to it, and moving Cursor 1 '
            'brings its frame into view.')

        top_row = QHBoxLayout()
        top_row.addWidget(self.search_edit, 1)
        top_row.addWidget(self.hit_label)
        top_row.addWidget(self.prev_button)
        top_row.addWidget(self.next_button)
        top_row.addWidget(self.only_hits_button)
        top_row.addSpacing(8)
        top_row.addWidget(self.filter_button)
        top_row.addWidget(self.follow_button)

        # ── Status ────────────────────────────────────────────────────────
        self.status_label = QLabel()
        self.status_label.setStyleSheet('color: #8B949E; font-size: 11px;')
        self.cursor_label = QLabel()
        self.cursor_label.setStyleSheet('color: #8B949E; font-size: 11px;')
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.cursor_label)

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

        # ── Time bar (big steps) and jump to time ────────────────────────
        self.time_bar = TimeBar()
        timestamps = np.frombuffer(raw_store.timestamps, dtype=np.float64)
        if len(raw_store):
            self.time_bar.set_range(float(timestamps[0]), float(timestamps[len(raw_store) - 1]))
        self._jump_edit = QLineEdit()
        self._jump_edit.setFixedWidth(self.time_bar.width())
        self._jump_edit.setPlaceholderText('time (s)')
        self._jump_edit.setToolTip('Type a time in seconds and press Enter')
        time_column = QVBoxLayout()
        time_column.setSpacing(2)
        time_column.addWidget(self._jump_edit)
        time_column.addWidget(self.time_bar, 1)

        body = QHBoxLayout()
        body.setSpacing(2)
        body.addWidget(self.tree, 1)
        body.addLayout(time_column)

        # ── Layout ────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addLayout(status_row)
        layout.addLayout(body, 1)

        # ── Connections ───────────────────────────────────────────────────
        # Typing waits for a pause: each search looks at every frame, and a
        # word typed letter by letter would otherwise search once per letter.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(_SEARCH_PAUSE_MS)
        self._search_timer.timeout.connect(self._on_filter_changed)
        self.search_edit.textChanged.connect(self._search_timer.start)
        # A large measurement is searched a chunk of frames per step, the
        # window answering between steps; the frames shown stay until done.
        self._search: FrameSearch | None = None
        self._search_step_timer = QTimer(self)
        self._search_step_timer.setInterval(0)
        self._search_step_timer.timeout.connect(self._search_step)
        self.search_edit.returnPressed.connect(self._on_return)
        self.prev_button.clicked.connect(lambda: self.find(forward=False))
        self.next_button.clicked.connect(lambda: self.find(forward=True))
        self.only_hits_button.toggled.connect(lambda _on: self._refresh())
        self.filter_button.clicked.connect(self.show_filter)
        for keys, slot in ((QKeySequence.StandardKey.Find, self._focus_search),
                           (QKeySequence(Qt.Key.Key_F3), lambda: self.find(True)),
                           (QKeySequence('Shift+F3'), lambda: self.find(False))):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self._jump_edit.returnPressed.connect(self._go_jump)
        self._bar_time: float | None = None
        self._bar_timer = QTimer(self)
        self._bar_timer.setSingleShot(True)
        self._bar_timer.setInterval(_TIME_BAR_PAUSE_MS)
        self._bar_timer.timeout.connect(self._go_bar_time)
        self.time_bar.timeRequested.connect(self._on_bar_time)
        self.time_bar.stepRequested.connect(self.big_step)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.follow_button.toggled.connect(self._on_follow_toggled)
        # Small steps past the frames loaded slide the window on.
        self.tree.installEventFilter(self)
        self.tree.viewport().installEventFilter(self)
        self.tree.verticalScrollBar().actionTriggered.connect(self._on_scroll_action)

        # A file that fits one step is indexed at once; a larger one in
        # steps, its Δt filled in when done.
        if self._previous.progress == 0 and self._previous.count <= _small_file():
            self._previous.build()
        self._refresh()
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

    # ── Search, filter and colours: one piece of work, in steps ──────────

    def _on_filter_changed(self) -> None:
        """The search text changed (after a pause, or Enter): search again."""
        self._search_timer.stop()
        self._refresh(query=True)

    def _on_return(self) -> None:
        if self.search_edit.text().strip() == self._query_text and self._search is None:
            self.find(forward=True)
        else:
            self._on_filter_changed()

    def _focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _refresh(self, *, query: bool = False, colours: bool = False) -> None:
        """Work out again what the search, the filter and the colours make of
        the frames: at once on a small file, else a chunk per step. Work under
        way is replaced; the frames shown stay until done."""
        if self.search_edit.text().strip() != self._query_text:
            query = True                # typed, its pause not yet over
            self._search_timer.stop()
        self._query_dirty |= query
        self._colours_dirty |= colours
        if self._search is None:
            self._anchor = self._anchor_frame()
        self._search = FrameSearch(self._work_steps())
        self._index_timer.stop()
        if len(self._raw_store) <= _small_file():
            try:
                self._search.run()
            except RuntimeError:            # the measurement was closed
                self._end_search()
                return
            self._finish(self._search)
            return
        # Searching 64 M frames at once held the window for up to 8 s. The
        # Δt index waits for the search: the user is waiting for the search.
        self._search_step()
        if self._search is not None:
            self._search_step_timer.start()

    def _work_steps(self):
        store = self._raw_store
        if self._query_dirty:
            text = self.search_edit.text().strip()
            if text:
                running = trace_query.search(store, self._catalog, text)
                while not running.step():
                    yield 0.6 * running.progress
                self._hits, self._query = running.result, running.query
                self._hit_bins = trace_query.time_bins(store, self._hits)
            else:
                self._hits = self._query = self._hit_bins = None
            self._query_text = text
            self._query_dirty = False
            self._new_query = True
        if self._colours_dirty:
            running = trace_query.colour_search(store, self._catalog, self._rules)
            while not running.step():
                yield 0.6 + 0.2 * running.progress
            self._colouring = running.result
            self._colours_dirty = False
        within = self._hits if self.only_hits_button.isChecked() else None
        numbers = self._colouring.numbers if self._colouring is not None else None
        running = trace_query.filter_search(store, self._filter, numbers, within)
        while not running.step():
            yield 0.8 + 0.2 * running.progress
        return running.result

    def _search_step(self) -> None:
        search = self._search
        if search is None:
            self._end_search()
            return
        try:
            done = search.step()
        except RuntimeError:            # the measurement was closed
            self._end_search()
            return
        if done:
            self._finish(search)
        else:
            self._show_status(keep_times=True)

    def _end_search(self) -> None:
        self._search = None
        self._search_step_timer.stop()
        if not self._previous.ready and not self._index_timer.isActive():
            self._index_timer.start()

    def _finish(self, search: FrameSearch) -> None:
        self._end_search()
        self._matching = search.result
        hits = self._hits
        if hits is None or self._matching is None:
            self._shown_hits = hits
        else:
            self._shown_hits = np.intersect1d(hits, self._matching, assume_unique=True)
        self._hit_number = None
        self._update_ticks()
        self._update_filter_button()
        anchor, self._anchor = self._anchor, None
        new_query, self._new_query = self._new_query, False
        shown = self._shown_hits
        if new_query and shown is not None and len(shown):
            # A new search goes to its first hit from where the user was,
            # drawing the window once.
            at = 0 if anchor is None else int(np.searchsorted(shown, anchor[0]))
            at %= len(shown)
            self._set_window(self._position_of(int(shown[at])) - _WINDOW // 2)
            self._go_to_hit(at)
        else:
            self._show_frame(anchor)
        self._update_hit_label()

    def _anchor_frame(self) -> tuple[int, bool] | None:
        """The frame to keep in view when the frames shown change, and
        whether it is the current one (else the first loaded)."""
        current = self._current_frame()
        if current is not None:
            return current, True
        if self.tree.topLevelItemCount():
            return self._frame_index(self._win_start), False
        return None

    def _show_frame(self, anchor: tuple[int, bool] | None) -> None:
        """Redraw, once, with the *anchor* frame (or the next shown) in view:
        the current one centred and still current, else first."""
        n = self._total_matching()
        if anchor is None or n == 0:
            self._set_window(0)
            return
        index, current = anchor
        position = self._position_of(index)
        if not current:
            self._set_window(position)
            return
        self._set_window(position - _WINDOW // 2)
        if self._frame_index(position) == index:
            self._select_frame(index)

    # ── Hits ──────────────────────────────────────────────────────────────

    @property
    def query(self) -> trace_query.Query | None:
        return self._query

    def hits(self) -> np.ndarray | None:
        """The frames the search found and the filter shows."""
        return self._shown_hits

    def find(self, forward: bool = True) -> bool:
        """Go to the next (or previous) hit from the current frame."""
        if self._search is not None and self._query_dirty:
            return False
        if self.search_edit.text().strip() != self._query_text:
            self._on_filter_changed()
            return True
        shown = self._shown_hits
        if shown is None or not len(shown):
            return False
        current = self._current_frame()
        if current is None:
            at = 0 if forward else len(shown) - 1
        elif forward:
            at = int(np.searchsorted(shown, current, side='right')) % len(shown)
        else:
            at = (int(np.searchsorted(shown, current, side='left')) - 1) % len(shown)
        self._go_to_hit(at)
        self._update_hit_label()
        return True

    def _go_to_hit(self, at: int) -> None:
        index = int(self._shown_hits[at])
        self._hit_number = at
        query = self._query
        expand = query is not None and query.found_in_signal(index)
        self.go_to_frame(index, expand=expand)
        if self.follow_button.isChecked():
            self.timeClicked.emit(self._frame_time(index))

    def go_to_frame(self, index: int, *, expand: bool = False) -> bool:
        """Bring frame *index* into view and make it current."""
        n = self._total_matching()
        if not n:
            return False
        position = self._position_of(index)
        if position >= n or self._frame_index(position) != index:
            return False
        if not self._win_start <= position < self._win_start + _WINDOW:
            self._set_window(position - _WINDOW // 2)
        if not self._select_frame(index):
            return False
        if expand:
            item = self.tree.currentItem()
            item.setExpanded(True)
            for child in range(item.childCount()):
                sub = item.child(child)
                if sub.data(_NAME, _SIGNAL_ROLE) is not None and self._marks(sub):
                    self.tree.scrollToItem(sub)
                    break
        return True

    def _marks(self, child: QTreeWidgetItem) -> bool:
        data = child.data(_NAME, _SIGNAL_ROLE)
        if self._query is None or not data:
            return False
        message, signal, number, shown = data
        return self._query.marks(message, signal, number, shown)

    def _update_hit_label(self) -> None:
        if not self._query_text:
            self.hit_label.setText('')
            return
        shown = self._shown_hits
        total = 0 if self._hits is None else len(self._hits)
        hidden = total - (0 if shown is None else len(shown))
        if shown is None or not len(shown):
            text = 'No match'
        elif self._hit_number is None:
            text = f'{len(shown):,} found'
        else:
            text = f'{self._hit_number + 1:,} of {len(shown):,}'
        if hidden > 0:
            text += f' (+{hidden:,} filtered)'
        self.hit_label.setText(text)

    # ── Filter ────────────────────────────────────────────────────────────

    @property
    def trace_filter(self) -> TraceFilter:
        return self._filter

    def channel_keys(self) -> list:
        return self._raw_store.channel_keys()

    def set_filter(self, **changes) -> None:
        """Change what the Filter hides; the frames shown follow."""
        flt = replace(self._filter, **changes)
        if flt != self._filter:
            self._filter = flt
            self._refresh()

    def set_channel_shown(self, key, on: bool) -> None:
        every = set(self.channel_keys())
        shown = set(every if self._filter.channels is None else self._filter.channels)
        shown = shown | {key} if on else shown - {key}
        self.set_filter(channels=None if shown >= every else frozenset(shown))

    def set_colour_shown(self, number: int, on: bool) -> None:
        hidden = set(self._filter.hidden_colours)
        hidden = hidden - {number} if on else hidden | {number}
        self.set_filter(hidden_colours=frozenset(hidden))

    def hide_id(self, key: int) -> None:
        key = trace_query.id_key(key)
        self.set_filter(hidden_ids=self._filter.hidden_ids | {key},
                        only_ids=self._filter.only_ids - {key})

    def show_only_id(self, key: int) -> None:
        key = trace_query.id_key(key)
        self.set_filter(only_ids=frozenset({key}),
                        hidden_ids=self._filter.hidden_ids - {key})

    def forget_id(self, key: int) -> None:
        self.set_filter(hidden_ids=self._filter.hidden_ids - {key},
                        only_ids=self._filter.only_ids - {key})

    def reset_filter(self) -> None:
        self.set_filter(**{name: getattr(TraceFilter(), name) for name in (
            'channels', 'rx', 'tx', 'data', 'errors', 'remote', 'hidden_ids',
            'only_ids', 'hidden_colours')})

    def id_text(self, key: int) -> str:
        arb_id, _channel, extended, bus = trace_query.split_key(key)
        text = f'ID {arb_id:X}' + ('x' if extended else '')
        return f'LIN {text}' if bus is BusType.LIN else text

    def show_filter(self) -> FilterPopup:
        if self._filter_popup is not None:
            self._filter_popup.close()
            self._filter_popup.deleteLater()
        popup = self._filter_popup = FilterPopup(self)
        popup.move(self.filter_button.mapToGlobal(QPoint(0, self.filter_button.height())))
        popup.show()
        return popup

    def _update_filter_button(self) -> None:
        count = self._filter.restrictions()
        self.filter_button.setText(f'Filter ({count}) ▾' if count else 'Filter ▾')
        font = self.filter_button.font()
        font.setBold(bool(count))
        self.filter_button.setFont(font)

    # ── Colours ───────────────────────────────────────────────────────────

    @property
    def colour_rules(self) -> list[ColourRule]:
        return list(self._rules)

    def colour_counts(self) -> list[int]:
        if self._colouring is None:
            return [len(self._raw_store)] + [0] * len(self._rules)
        return list(self._colouring.counts)

    def next_colour(self) -> str:
        used = {rule.colour for rule in self._rules}
        return next((value for _name, value in COLOURS if value not in used),
                    COLOURS[len(self._rules) % len(COLOURS)][1])

    def add_colour_rule(self, rule: ColourRule) -> None:
        self._rules.append(rule)
        self._refresh(colours=True)

    def colour_id(self, key: int, colour: str) -> None:
        key = trace_query.id_key(key)
        self.add_colour_rule(ColourRule(colour, self.id_text(key), id_key=key))

    def colour_when(self, condition: str, colour: str) -> None:
        if condition:
            self.add_colour_rule(ColourRule(colour, condition, query=condition))

    def remove_colour_rule(self, number: int) -> None:
        """Remove rule *number* (from 1); later rules move up one."""
        del self._rules[number - 1]
        hidden = {c - 1 if c > number else c
                  for c in self._filter.hidden_colours if c != number}
        self._filter = replace(self._filter, hidden_colours=frozenset(hidden))
        self._refresh(colours=True)

    def _update_ticks(self) -> None:
        ticks = []
        if self._colouring is not None:
            for number, (rule, bins) in enumerate(zip(self._rules, self._colouring.bins), 1):
                if number not in self._filter.hidden_colours:
                    ticks.append((rule.colour, bins))
        if self._hit_bins is not None:
            ticks.append((HIT_COLOUR, self._hit_bins))
        self.time_bar.set_ticks(ticks)

    # ── Positions ────────────────────────────────────────────────────────

    def _total_matching(self) -> int:
        if self._matching is None:
            return len(self._raw_store)
        return len(self._matching)

    def _frame_index(self, pos: int) -> int:
        """Map a position in the matching list to a RawFrameStore index."""
        if self._matching is None:
            return pos
        return int(self._matching[pos])

    def _position_of(self, index: int) -> int:
        """Position of frame *index* among those shown, or of the next one."""
        if self._matching is None:
            return int(index)
        return int(min(np.searchsorted(self._matching, index), max(0, len(self._matching) - 1)))

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

    # ── Navigation: the two scrolls ──────────────────────────────────────

    def _go_start(self) -> None: self._set_window(0)
    def _go_end(self) -> None: self._set_window(max(0, self._total_matching() - _WINDOW))

    def big_step(self, direction: int) -> None:
        """A big step: the next (+1) or previous (−1) frames loaded."""
        self._set_window(self._win_start + direction * _WINDOW)

    def slide(self, direction: int) -> bool:
        """A small step went past the frames loaded: load the next (+1) or
        previous (−1) 1,000, keeping the rows on screen where they are."""
        n = self._total_matching()
        start = max(0, min(self._win_start + direction * _STEP, max(0, n - _WINDOW)))
        if start == self._win_start:
            return False
        top = self.tree.itemAt(QPoint(1, 1))
        while top is not None and top.parent() is not None:
            top = top.parent()
        top_index = None if top is None else top.data(0, Qt.ItemDataRole.UserRole)
        current = self._current_frame()
        self._set_window(start)
        if current is not None:
            self._select_frame(current, scroll=False)
        if top_index is not None:
            item = self._item_for(int(top_index))
            if item is not None:
                self.tree.scrollToItem(item, QTreeWidget.ScrollHint.PositionAtTop)
        return True

    def _on_scroll_action(self, action: int) -> None:
        bar = self.tree.verticalScrollBar()
        adds = (QAbstractSlider.SliderAction.SliderSingleStepAdd,
                QAbstractSlider.SliderAction.SliderPageStepAdd)
        subs = (QAbstractSlider.SliderAction.SliderSingleStepSub,
                QAbstractSlider.SliderAction.SliderPageStepSub)
        action = QAbstractSlider.SliderAction(action)
        if action in adds and bar.value() >= bar.maximum():
            self.slide(+1)
        elif action in subs and bar.value() <= bar.minimum():
            self.slide(-1)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.tree.viewport() and event.type() == QEvent.Type.Wheel:
            bar = self.tree.verticalScrollBar()
            delta = event.angleDelta().y()
            if delta < 0 and bar.value() >= bar.maximum():
                self.slide(+1)
            elif delta > 0 and bar.value() <= bar.minimum():
                self.slide(-1)
        elif watched is self.tree and event.type() == QEvent.Type.KeyPress:
            return self._tree_key(event)
        return super().eventFilter(watched, event)

    def _tree_key(self, event) -> bool:
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self.big_step(+1 if key == Qt.Key.Key_PageDown else -1)
            return True
        if ctrl and key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            n = self._total_matching()
            if n:
                self.go_to_frame(self._frame_index(0 if key == Qt.Key.Key_Home else n - 1))
            return True
        if not ctrl and key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown,
                                Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            item = self.tree.currentItem()
            while item is not None and item.parent() is not None:
                item = item.parent()
            if item is not None:
                row = self.tree.indexOfTopLevelItem(item)
                last = self.tree.topLevelItemCount() - 1
                page = max(1, self.tree.viewport().height() // max(
                    1, self.tree.visualItemRect(item).height() or 1))
                reach = 1 if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) else page
                if key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown) and row + reach > last:
                    self.slide(+1)
                elif key in (Qt.Key.Key_Up, Qt.Key.Key_PageUp) and row - reach < 0:
                    self.slide(-1)
        return False

    def _on_bar_time(self, t: float) -> None:
        self._bar_time = t
        self._bar_timer.start()

    def _go_bar_time(self) -> None:
        if self._bar_time is not None:
            self.go_to_time(self._bar_time, select=False)

    def go_to_time(self, target_t: float, *, select: bool = True) -> None:
        """Load the frames around *target_t*, its frame centred."""
        n = self._total_matching()
        if n == 0:
            return
        position = self._position_at(target_t)
        self._set_window(max(0, min(position - _WINDOW // 2, n - _WINDOW)))
        if select:
            self._select_frame(self._frame_index(position))

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
        self.go_to_time(target_t)

    def _set_window(self, start: int, update_scrollbar: bool = True) -> None:
        n = self._total_matching()
        self._win_start = max(0, min(start, max(0, n - _WINDOW)))
        if self._is_expanded:
            self.tree.collapseAll()
            self._is_expanded = False
        self._render_window()

    def _update_scrollbar(self) -> None:
        """Kept for callers of the old window: the table scrolls itself."""

    # ── Render window ─────────────────────────────────────────────────────

    def _render_window(self) -> None:
        n       = self._total_matching()
        w_start = self._win_start
        w_end   = min(w_start + _WINDOW, n)

        if self._matching is None:
            indices = list(range(w_start, w_end))
        else:
            indices = self._matching[w_start:w_end].tolist()

        # Fetch records from store (disk read for data bytes)
        records = self._raw_store.get_window(indices)

        delta = changed = None
        if indices and self._previous.ready:
            delta, changed = window_differences(
                self._raw_store, self._previous.previous, indices)

        index_array = np.asarray(indices, dtype=np.int64)
        numbers = None
        if self._colouring is not None and self._colouring.numbers is not None and indices:
            numbers = self._colouring.numbers[index_array]
        is_hit = None
        if self._hits is not None and len(self._hits) and indices:
            at = np.searchsorted(self._hits, index_array)
            at = np.minimum(at, len(self._hits) - 1)
            is_hit = self._hits[at] == index_array
        brushes = [None] + [_tint(rule.colour, _ROW_ALPHA) for rule in self._rules]
        hit_brush = _tint(HIT_COLOUR, _HIT_ALPHA)

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
            if numbers is not None and numbers[position] and numbers[position] < len(brushes):
                brush = brushes[numbers[position]]
                for col in range(_COL):
                    top.setBackground(col, brush)
            if is_hit is not None and is_hit[position]:
                top.setBackground(_NAME, hit_brush)
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
            # With a current row, clear() took 10 s for the 5,000 rows
            # instead of 0.02 s, and a search, a Follow Cursor 1 step and the
            # finished Δt index all redraw with a row selected.
            self.tree.selectionModel().clear()
            self.tree.clear()
            self.tree.insertTopLevelItems(0, top_items)
        finally:
            self._following = False
        self.tree.setUpdatesEnabled(True)

        for col in range(_COL):
            self.tree.resizeColumnToContents(col)

        ts_arr = np.frombuffer(self._raw_store.timestamps, dtype=np.float64)
        t_win_s = float(ts_arr[indices[0]]) if indices else None
        t_win_e = float(ts_arr[indices[-1]]) if indices else None
        self.time_bar.set_window(t_win_s, t_win_e)
        self._show_status(t_win_s, t_win_e)

    def _show_status(self, t_win_s=None, t_win_e=None, *, keep_times: bool = False) -> None:
        if not keep_times:
            self._window_times = (t_win_s, t_win_e)
        t_win_s, t_win_e = self._window_times
        n = self._total_matching()
        w_end = min(self._win_start + _WINDOW, n)
        text = (f'Showing frames {self._win_start+1:,}–{w_end:,} of {n:,} shown  '
                f'({_fmt_t(t_win_s)} → {_fmt_t(t_win_e)})  '
                f'| Total in file: {len(self._raw_store):,}')
        if self._search is not None:
            text += f'  | Searching… {self._search.progress:.0%}'
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

    def _item_for(self, index: int) -> QTreeWidgetItem | None:
        """The row of store frame *index*, if loaded."""
        count = self.tree.topLevelItemCount()
        if not count:
            return None
        guess = self._position_of(index) - self._win_start
        if 0 <= guess < count:
            item = self.tree.topLevelItem(guess)
            if item.data(0, Qt.ItemDataRole.UserRole) == index:
                return item
        for row in range(count):
            item = self.tree.topLevelItem(row)
            if item.data(0, Qt.ItemDataRole.UserRole) == index:
                return item
        return None

    def _select_frame(self, index: int, *, scroll: bool = True) -> bool:
        """Make the row of store frame *index* current, if on screen."""
        item = self._item_for(index)
        if item is None:
            return False
        self._following = True
        try:
            self.tree.setCurrentItem(item)
        finally:
            self._following = False
        if scroll:
            self.tree.scrollToItem(item, QTreeWidget.ScrollHint.PositionAtCenter)
        return True

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
        self.time_bar.set_cursor(t)
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

    # ── Right-click ──────────────────────────────────────────────────────

    def _show_row_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        index = self._current_frame()
        if index is None:
            return
        self.row_menu(self._frame_time(index), item).exec(
            self.tree.viewport().mapToGlobal(pos))

    def row_menu(self, t: float, item: QTreeWidgetItem | None = None) -> QMenu:
        menu = QMenu(self)
        menu.addAction('Set Cursor 1 here', lambda: self.timeClicked.emit(t))
        menu.addAction('Set Cursor 2 here', lambda: self.cursor2Requested.emit(t))
        if item is None:
            return menu
        signal = item.data(_NAME, _SIGNAL_ROLE) if item.parent() is not None else None
        frame = item
        while frame.parent() is not None:
            frame = frame.parent()
        index = frame.data(0, Qt.ItemDataRole.UserRole)
        menu.addSeparator()
        if signal:
            _message, name, number, shown = signal
            if number is None or not _is_number(shown):
                condition = f'{name} = {shown}'         # a value label
            else:
                condition = f'{name} > {number:g}'
            menu.addAction('Colour when…', lambda: self._ask_colour_when(condition))
            menu.addAction('Find this signal', lambda: self._find_text(name))
        elif index is not None and index >= 0:
            key = int(trace_query.frame_keys(self._raw_store, index, index + 1)[0])
            colours = menu.addMenu('Colour this ID')
            for colour_name, value in COLOURS:
                colours.addAction(_swatch(value), colour_name,
                                  lambda v=value: self.colour_id(key, v))
            menu.addAction('Hide this ID', lambda: self.hide_id(key))
            menu.addAction('Show only this ID', lambda: self.show_only_id(key))
        menu.addSeparator()
        menu.addAction('Expand all on screen', self._expand_window)
        menu.addAction('Collapse all', self._collapse_all)
        return menu

    def _ask_colour_when(self, condition: str) -> None:
        dialog = ColourWhenDialog(condition, self.next_colour(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.colour_when(dialog.condition(), dialog.colour())

    def _find_text(self, text: str) -> None:
        self.search_edit.setText(text)
        self._on_filter_changed()

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

        hit_brush = _tint(HIT_COLOUR, _HIT_ALPHA)
        for s in samples:
            val_str = f'{s.value} {s.unit}'.strip()
            child = QTreeWidgetItem(_row(name=s.signal_name, data=val_str))
            child.setData(0, Qt.ItemDataRole.UserRole, -2)
            child.setData(_NAME, _SIGNAL_ROLE,
                          (s.message_name, s.signal_name, s.numeric_value, str(s.value)))
            if self._marks(child):
                child.setBackground(_NAME, hit_brush)
                child.setBackground(_DATA, hit_brush)
            item.addChild(child)

    # ── Expand / Collapse ─────────────────────────────────────────────────

    def _expand_window(self) -> None:
        self._is_expanded = True
        self.tree.expandAll()

    def _collapse_all(self) -> None:
        self._is_expanded = False
        self.tree.collapseAll()


def _is_number(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _small_file() -> int:
    """Frames the Trace works on at once, before it opens."""
    from core import trace_differences
    return trace_differences.CHUNK
