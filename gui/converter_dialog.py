# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tools ▸ Convert files: the dialog for core.converter. Files to join, in order;
an optional time range; for MF4/MDF, signals to keep and a resampling raster;
the output file, whose extension picks the format. Runs off the GUI thread.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout,
)

from core.converter import (
    MDF_INPUTS, MDF_OUTPUTS, RAW_INPUTS, RAW_OUTPUTS, Cancelled, ConvertError, ConvertJob,
    convert, list_signals,
)
from core.signal_selection import (apply_selection, read_selection, selection_of,
                                   write_selection)
from gui.user_settings import read_setting, write_setting

_INPUT_FILTER = 'Measurements (*.mf4 *.mdf *.dat *.blf *.asc);;All files (*)'
_OUTPUT_FILTERS = {'.mf4': 'MF4 (*.mf4)', '.csv': 'CSV (*.csv)',
                   '.blf': 'BLF (*.blf)', '.asc': 'ASC (*.asc)'}
_LIST_FILTER = 'Signal lists (*.txt);;All files (*)'
#: User settings: the saved selections, {name: [entries]}, and the last used.
_SELECTIONS_KEY = 'converter_selections'
_LAST_SELECTION_KEY = 'converter_last_selection'
_NO_SELECTION = '—'


class _Job(QThread):
    progressed = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, job: ConvertJob, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _progress(self, done: int, total: int) -> None:
        if self._cancelled:
            raise Cancelled
        self.progressed.emit(done, total)

    def run(self) -> None:
        try:
            path = convert(self.job, self._progress)
        except Cancelled:
            self.failed.emit('Cancelled; nothing was written.')
            return
        except Exception as exc:  # noqa: BLE001 - shown in the dialog
            self.failed.emit(str(exc))
            return
        self.done.emit(str(path))


class SignalPicker(QDialog):
    """The signals of an MDF file, ticked to keep, with a search that
    narrows the list; ticks outside the search are kept.

    A selection of signal names can be saved by name (in the user settings,
    with *settings_path*), applied to any file, and read from or written to a
    text file. The last one used is offered again, and applied when nothing
    is ticked yet."""

    def __init__(self, signals, chosen, parent=None, *, settings_path=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Signals to keep')
        self.resize(520, 520)
        self._settings_path = settings_path
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search signals… (* and ? wildcards)')
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        chosen = set(chosen)
        for name, group, index, group_name in signals:
            item = QListWidgetItem(f'{group_name} · {name}')
            item.setData(Qt.ItemDataRole.UserRole, (name, group, index))
            item.setCheckState(Qt.CheckState.Checked if (name, group, index) in chosen
                               else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.selection = QComboBox()
        self.selection.setMinimumContentsLength(16)
        self.selection.setToolTip('A saved selection: choosing one ticks its signals')
        self.save_selection = QPushButton('Save as…')
        self.save_selection.setToolTip('Save the ticked signals as a selection, by name')
        self.delete_selection = QPushButton('Delete')
        self.import_selection = QPushButton('Import .txt…')
        self.import_selection.setToolTip(
            'Tick the signals a text file lists: one name a line, * and ? allowed')
        self.export_selection = QPushButton('Export .txt…')
        self.export_selection.setToolTip('Write the ticked signal names to a text file')
        self.applied = QLabel()
        self.applied.setWordWrap(True)
        self.tick_shown = QPushButton('Tick shown')
        self.tick_shown.setToolTip('Tick every signal the search shows')
        self.untick_all = QPushButton('Untick all')
        self.count = QLabel()
        ok = QPushButton('OK')
        ok.clicked.connect(self.accept)
        self.tick_shown.clicked.connect(lambda: self._set_checked(self._shown(), True))
        self.untick_all.clicked.connect(lambda: self._set_checked(self._items(), False))
        self.search.textChanged.connect(self._filter)
        self.list.itemChanged.connect(self._show_count)
        self.save_selection.clicked.connect(self._save_selection)
        self.delete_selection.clicked.connect(self._delete_selection)
        self.import_selection.clicked.connect(self._import_selection)
        self.export_selection.clicked.connect(self._export_selection)
        selection_row = QHBoxLayout()
        selection_row.addWidget(QLabel('Selection:'))
        selection_row.addWidget(self.selection, 1)
        for button in (self.save_selection, self.delete_selection,
                       self.import_selection, self.export_selection):
            selection_row.addWidget(button)
        buttons = QHBoxLayout()
        buttons.addWidget(self.tick_shown)
        buttons.addWidget(self.untick_all)
        buttons.addWidget(self.count, 1)
        buttons.addWidget(ok)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Tick the signals to keep; none ticked keeps them all.'))
        layout.addLayout(selection_row)
        layout.addWidget(self.applied)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        self._fill_selections(read_setting(settings_path, _LAST_SELECTION_KEY)
                              if settings_path else None)
        self.selection.currentTextChanged.connect(self._on_selection_chosen)
        if not chosen and self.selection.currentText() != _NO_SELECTION:
            self._on_selection_chosen(self.selection.currentText())
        self._show_count()
        self.search.setFocus()

    # ── Saved selections ─────────────────────────────────────────────────

    def saved_selections(self) -> dict[str, list[str]]:
        saved = (read_setting(self._settings_path, _SELECTIONS_KEY, {})
                 if self._settings_path else {})
        if not isinstance(saved, dict):
            return {}
        return {str(name): [str(e) for e in entries]
                for name, entries in saved.items() if isinstance(entries, list)}

    def _store(self, selections: dict[str, list[str]], last: str | None) -> None:
        if self._settings_path is None:
            return
        try:
            write_setting(self._settings_path, _SELECTIONS_KEY, selections)
            write_setting(self._settings_path, _LAST_SELECTION_KEY, last)
        except OSError as exc:
            self.applied.setText(f'The selection could not be saved: {exc}')

    def _fill_selections(self, current: str | None) -> None:
        names = sorted(self.saved_selections(), key=str.casefold)
        with QSignalBlocker(self.selection):
            self.selection.clear()
            self.selection.addItems([_NO_SELECTION, *names])
            self.selection.setCurrentText(current if current in names else _NO_SELECTION)
        self.delete_selection.setEnabled(self.selection.currentText() != _NO_SELECTION)

    def _on_selection_chosen(self, name: str) -> None:
        self.delete_selection.setEnabled(name != _NO_SELECTION)
        if name == _NO_SELECTION:
            return
        entries = self.saved_selections().get(name)
        if entries is not None:
            self.apply_entries(entries, f'"{name}"')
            self._store(self.saved_selections(), name)

    def apply_entries(self, entries, label: str) -> None:
        """Tick exactly the signals *entries* name, and say how many of the
        entries this file has; the ones it lacks are in the tooltip."""
        items = self._items()
        picked, missing = apply_selection(
            entries, [item.data(Qt.ItemDataRole.UserRole)[0] for item in items])
        picked = set(picked)
        with QSignalBlocker(self.list):
            for number, item in enumerate(items):
                item.setCheckState(Qt.CheckState.Checked if number in picked
                                   else Qt.CheckState.Unchecked)
        total = len([e for e in entries if e.strip()])
        text = f'Applied {label}: {total - len(missing)} of {total} found'
        if missing:
            text += f' · {len(missing)} not in this file'
        self.applied.setText(text)
        self.applied.setToolTip('Not in this file:\n' + '\n'.join(missing) if missing else '')
        self._show_count()

    def ticked_names(self) -> list[str]:
        return selection_of(name for name, _group, _index in self.chosen())

    def _save_selection(self) -> None:
        names = self.ticked_names()
        if not names:
            self.applied.setText('Tick the signals to save first.')
            return
        current = self.selection.currentText()
        name, ok = QInputDialog.getText(
            self, 'Save selection', 'Name of the selection:',
            text='' if current == _NO_SELECTION else current)
        name = name.strip()
        if not ok or not name or name == _NO_SELECTION:
            return
        selections = self.saved_selections()
        selections[name] = names
        self._store(selections, name)
        self._fill_selections(name)
        self.applied.setText(f'Saved "{name}": {len(names)} signals')

    def _delete_selection(self) -> None:
        name = self.selection.currentText()
        selections = self.saved_selections()
        if selections.pop(name, None) is None:
            return
        self._store(selections, None)
        self._fill_selections(None)
        self.applied.setText(f'Deleted "{name}"')

    def _import_selection(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Import signal list', '', _LIST_FILTER)
        if not path:
            return
        try:
            entries = read_selection(path)
        except (OSError, UnicodeDecodeError) as exc:
            self.applied.setText(f'The list could not be read: {exc}')
            return
        self.apply_entries(entries, f'"{Path(path).name}"')

    def _export_selection(self) -> None:
        names = self.ticked_names()
        if not names:
            self.applied.setText('Tick the signals to export first.')
            return
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export signal list', 'signals.txt', _LIST_FILTER)
        if not path:
            return
        try:
            write_selection(path, names)
        except OSError as exc:
            self.applied.setText(f'The list could not be written: {exc}')
            return
        self.applied.setText(f'Exported {len(names)} signal names to {Path(path).name}')

    def _items(self) -> list[QListWidgetItem]:
        return [self.list.item(i) for i in range(self.list.count())]

    def _shown(self) -> list[QListWidgetItem]:
        return [item for item in self._items() if not item.isHidden()]

    def _filter(self, text: str) -> None:
        """Show the signals whose name contains the text, ignoring case;
        with * or ?, the signals the pattern matches."""
        needle = text.strip().casefold()
        wildcard = any(c in needle for c in '*?')
        for item in self._items():
            name = item.text().casefold()
            if not needle:
                hidden = False
            elif wildcard:
                hidden = not (fnmatchcase(name, needle) or fnmatchcase(name, f'*{needle}*'))
            else:
                hidden = needle not in name
            item.setHidden(hidden)
        self._show_count()

    def _set_checked(self, items, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        with QSignalBlocker(self.list):
            for item in items:
                item.setCheckState(state)
        self._show_count()

    def _show_count(self, *_args) -> None:
        ticked = sum(item.checkState() == Qt.CheckState.Checked for item in self._items())
        shown = len(self._shown())
        total = self.list.count()
        text = f'{ticked} of {total} ticked'
        if shown != total:
            text += f' · {shown} shown'
        self.count.setText(text)

    def chosen(self) -> list[tuple[str, int, int]]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


class ConverterDialog(QDialog):
    def __init__(self, parent=None, *, settings_path=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Convert files')
        self.resize(560, 420)
        self._settings_path = settings_path
        self._job: _Job | None = None
        self.keep: list[tuple[str, int, int]] = []

        self.files = QListWidget()
        add = QPushButton('Add files…')
        remove = QPushButton('Remove')
        up = QPushButton('Up')
        down = QPushButton('Down')
        add.clicked.connect(self.add_files)
        remove.clicked.connect(self.remove_selected)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        file_buttons = QVBoxLayout()
        for button in (add, remove, up, down):
            file_buttons.addWidget(button)
        file_buttons.addStretch(1)
        file_row = QHBoxLayout()
        file_row.addWidget(self.files, 1)
        file_row.addLayout(file_buttons)

        self.use_range = QCheckBox('Only from')
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for box in (self.start, self.end):
            box.setRange(0, 1e7)
            box.setDecimals(3)
            box.setSuffix(' s')
        self.end.setValue(10.0)
        self.choose_signals = QPushButton('Choose signals…')
        self.choose_signals.clicked.connect(self.pick_signals)
        self.signals_note = QLabel('All signals')
        self.use_raster = QCheckBox('Resample every')
        self.raster = QDoubleSpinBox()
        self.raster.setRange(0.1, 1e6)
        self.raster.setDecimals(1)
        self.raster.setSuffix(' ms')
        self.raster.setValue(10.0)
        # One option a row, each kept together at the left.
        options = QVBoxLayout()
        for widgets in ((self.use_range, self.start, QLabel('to'), self.end),
                        (self.choose_signals, self.signals_note),
                        (self.use_raster, self.raster)):
            row = QHBoxLayout()
            for widget in widgets:
                row.addWidget(widget)
            row.addStretch(1)
            options.addLayout(row)

        self.output_label = QLabel('Output: not chosen')
        self.output_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.output: Path | None = None
        choose_output = QPushButton('Save as…')
        choose_output.clicked.connect(self.choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_label, 1)
        output_row.addWidget(choose_output)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.convert_button = QPushButton('Convert')
        self.convert_button.setDefault(True)
        self.convert_button.clicked.connect(self.start_convert)
        self.cancel_button = QPushButton('Close')
        self.cancel_button.clicked.connect(self._cancel_or_close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(self.convert_button)
        bottom.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Files, joined in this order:'))
        layout.addLayout(file_row, 1)
        layout.addLayout(options)
        layout.addLayout(output_row)
        layout.addWidget(self.status)
        layout.addLayout(bottom)
        self._update_options()

    # ── Inputs ───────────────────────────────────────────────────────────

    def inputs(self) -> list[Path]:
        return [Path(self.files.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.files.count())]

    def set_inputs(self, paths) -> None:
        self.files.clear()
        for path in paths:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.files.addItem(item)
        self.keep = []
        self._update_options()

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, 'Files to convert', '', _INPUT_FILTER)
        if paths:
            self.set_inputs(self.inputs() + [Path(p) for p in paths])

    def remove_selected(self) -> None:
        for item in self.files.selectedItems():
            self.files.takeItem(self.files.row(item))
        self.keep = []
        self._update_options()

    def _move(self, step: int) -> None:
        row = self.files.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.files.count():
            return
        item = self.files.takeItem(row)
        self.files.insertItem(target, item)
        self.files.setCurrentRow(target)

    def _kind(self) -> str | None:
        suffixes = {p.suffix.lower() for p in self.inputs()}
        if suffixes and suffixes <= set(MDF_INPUTS):
            return 'mdf'
        if len(suffixes) == 1 and suffixes <= set(RAW_INPUTS):
            return 'raw'
        return None

    def _update_options(self) -> None:
        mdf = self._kind() == 'mdf'
        for widget in (self.choose_signals, self.use_raster, self.raster):
            widget.setEnabled(mdf)
        self.signals_note.setText(
            (f'{len(self.keep)} signals chosen' if self.keep else 'All signals') if mdf
            else 'BLF and ASC hold frames: no signals to choose')
        kind = self._kind()
        if self.inputs() and kind is None:
            self.status.setText('Join files of one format only: all MF4/MDF, all BLF or all ASC.')
        else:
            outputs = MDF_OUTPUTS if kind == 'mdf' else RAW_OUTPUTS
            self.status.setText(
                f'Output can be {", ".join(s.upper().lstrip(".") for s in outputs)}.'
                if kind else 'Add the files to convert.')

    def pick_signals(self) -> None:
        if self._kind() != 'mdf':
            return
        picker = SignalPicker(list_signals(self.inputs()[0]), self.keep, self,
                              settings_path=self._settings_path)
        if picker.exec():
            self.keep = picker.chosen()
            self._update_options()

    def choose_output(self) -> None:
        kind = self._kind()
        outputs = MDF_OUTPUTS if kind == 'mdf' else RAW_OUTPUTS
        filters = ';;'.join(_OUTPUT_FILTERS[s] for s in outputs)
        first = self.inputs()[0] if self.inputs() else Path('converted')
        path, chosen = QFileDialog.getSaveFileName(
            self, 'Save converted file as', str(first.with_name(first.stem + '_converted')),
            filters)
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() not in outputs:
            suffix = next((s for s in outputs if _OUTPUT_FILTERS[s] == chosen), outputs[0])
            path = path.with_suffix(suffix)
        self.set_output(path)

    def set_output(self, path) -> None:
        self.output = Path(path)
        self.output_label.setText(f'Output: {self.output}')

    def job(self) -> ConvertJob:
        return ConvertJob(
            inputs=self.inputs(), output=self.output or Path(''),
            start=self.start.value() if self.use_range.isChecked() else None,
            end=self.end.value() if self.use_range.isChecked() else None,
            keep=list(self.keep) if self._kind() == 'mdf' else [],
            raster=(self.raster.value() / 1000.0
                    if self.use_raster.isChecked() and self._kind() == 'mdf' else None))

    # ── Running ──────────────────────────────────────────────────────────

    def start_convert(self) -> None:
        if self.output is None:
            self.status.setText('Choose where to save the converted file.')
            return
        job = self.job()
        try:
            job.check()
        except ConvertError as exc:
            self.status.setText(str(exc))
            return
        self._job = _Job(job, self)
        self._job.progressed.connect(self._on_progress)
        self._job.done.connect(self._on_done)
        self._job.failed.connect(self._on_failed)
        self._job.finished.connect(self._on_finished)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.convert_button.setEnabled(False)
        self.cancel_button.setText('Cancel')
        self.status.setText('Converting…')
        self._job.start()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setValue(int(100 * done / total) if total else 0)

    def _on_done(self, path: str) -> None:
        self.status.setText(f'Written: {path}')

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)

    def _on_finished(self) -> None:
        self.progress.setVisible(False)
        self.convert_button.setEnabled(True)
        self.cancel_button.setText('Close')

    def _cancel_or_close(self) -> None:
        if self._job is not None and self._job.isRunning():
            self._job.cancel()
        else:
            self.close()

    def closeEvent(self, event) -> None:
        if self._job is not None and self._job.isRunning():
            self._job.cancel()
            self._job.wait()
        super().closeEvent(event)

