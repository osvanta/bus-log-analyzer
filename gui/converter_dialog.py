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

from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout,
)

from core.converter import (
    MDF_INPUTS, MDF_OUTPUTS, RAW_INPUTS, RAW_OUTPUTS, Cancelled, ConvertError, ConvertJob,
    convert, list_signals,
)

_INPUT_FILTER = 'Measurements (*.mf4 *.mdf *.dat *.blf *.asc);;All files (*)'
_OUTPUT_FILTERS = {'.mf4': 'MF4 (*.mf4)', '.csv': 'CSV (*.csv)',
                   '.blf': 'BLF (*.blf)', '.asc': 'ASC (*.asc)'}


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
    """The signals of an MDF file, ticked to keep."""

    def __init__(self, signals, chosen, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Signals to keep')
        self.resize(420, 480)
        self.list = QListWidget()
        chosen = set(chosen)
        for name, group, index, group_name in signals:
            item = QListWidgetItem(f'{group_name} · {name}')
            item.setData(Qt.ItemDataRole.UserRole, (name, group, index))
            item.setCheckState(Qt.CheckState.Checked if (name, group, index) in chosen
                               else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        ok = QPushButton('OK')
        ok.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Tick the signals to keep; none ticked keeps them all.'))
        layout.addWidget(self.list)
        layout.addWidget(ok, alignment=Qt.AlignmentFlag.AlignRight)

    def chosen(self) -> list[tuple[str, int, int]]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


class ConverterDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Convert files')
        self.resize(560, 420)
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
        picker = SignalPicker(list_signals(self.inputs()[0]), self.keep, self)
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

