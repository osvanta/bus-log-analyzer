# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A channel that holds several values per sample (see test_array_channels),
taken through the window: every plot mode, the table view, statistics,
cursors, properties, a calculated signal, export, save as MF4, the converter.
Nothing may raise, in a call or in a slot.
"""
from __future__ import annotations

import sys
import time
import warnings

import numpy as np
import pytest

from PySide6.QtWidgets import QApplication, QMessageBox

asammdf = pytest.importorskip("asammdf")

from core.calculated_signals import CalculatedSignalDefinition  # noqa: E402
from core.readers.mdf_reader import MDFReader  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402

N = 40
T = np.arange(N, dtype=np.float64) * 0.05


def _frames() -> np.recarray:
    frames = np.zeros(N, dtype=[
        ("LIN_Frame.BusChannel", "u1"), ("LIN_Frame.ID", "u1"),
        ("LIN_Frame.DataLength", "u1"), ("LIN_Frame.DataBytes", "(8,)u1"),
    ])
    frames["LIN_Frame.BusChannel"] = 1
    frames["LIN_Frame.ID"] = 0x20 + np.arange(N) % 3
    frames["LIN_Frame.DataLength"] = 8
    frames["LIN_Frame.DataBytes"] = np.arange(N * 8).reshape(N, 8) % 256
    return np.rec.array(frames)


@pytest.fixture()
def array_file(tmp_path):
    """LIN frames as a logger writes them, and a group with a byte array."""
    from asammdf import MDF, Signal

    path = tmp_path / "arrays.mf4"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mdf = MDF(version="4.10")
        mdf.append([Signal(_frames(), T, name="LIN_Frame")], comment="LIN")
        mdf.append([
            Signal(np.sin(T) * 10.0, T, name="Plain", unit="V"),
            Signal(np.arange(N * 4, dtype=np.uint8).reshape(N, 4), T, name="Bytes4"),
        ], comment="signals")
        mdf.save(path, overwrite=True)
        mdf.close()
    MDFReader._content_cache.clear()
    return path


def _load(path):
    from gui.load_worker import LoadWorker

    worker = LoadWorker(str(path))
    result = {}
    worker.finished.connect(lambda store: result.setdefault("store", store))
    worker.failed.connect(lambda error: result.setdefault("error", error))
    worker.run()
    assert "error" not in result, result.get("error")
    return result["store"]


@pytest.fixture()
def uncaught(monkeypatch):
    """Errors raised inside Qt slots and paint events: they do not reach the
    test, the interpreter hands them to sys.excepthook."""
    errors = []
    monkeypatch.setattr(
        sys, "excepthook",
        lambda kind, value, _trace: errors.append(f"{kind.__name__}: {value}"))
    return errors


@pytest.fixture()
def window(qapp, monkeypatch, array_file, uncaught):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(
            QMessageBox, name,
            lambda *args, _name=name, **kwargs: uncaught.append(
                f"message box ({_name}): {args[1:3]}") or QMessageBox.StandardButton.Ok)
    widget = MainWindow("Osvanta Bus Log Analyzer", "00.00.99")
    widget.store = _load(array_file)
    widget._update_action_states()
    widget.resize(1100, 700)
    widget.show()
    qapp.processEvents()
    yield widget
    _settle(widget, qapp)
    store = widget.store
    widget.close()
    qapp.processEvents()
    if store is not None:
        store.close()


def _settle(window, qapp, seconds: float = 0.05) -> None:
    deadline = time.monotonic() + 10.0
    while window._calc_thread is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    window.plot_panel.repaint()
    qapp.processEvents()


def _key(window, signal: str) -> str:
    return next(k for k in window.store.all_keys() if k.endswith("::" + signal))


def _array_keys(window) -> list[str]:
    return [_key(window, "LIN_Frame.DataBytes"), _key(window, "Bytes4")]


def test_the_array_channels_are_text_signals(window):
    for key in _array_keys(window):
        series = window.store.get_series(key)
        assert series.has_labels
        assert series.numpy_values().shape == series.numpy_timestamps().shape == (N,)


@pytest.mark.parametrize("mode", [None, "btn_stacked", "btn_multi_axis", "btn_multistack",
                                  "btn_table"])
def test_plotting_in_every_mode(window, qapp, uncaught, mode):
    stacked = window.btn_stacked
    if mode is None:
        if stacked.isChecked():
            stacked.click()             # the plain single plot
    elif not getattr(window, mode).isChecked():
        getattr(window, mode).click()
    _settle(window, qapp)

    keys = _array_keys(window) + [_key(window, "Plain"), _key(window, "LIN_Frame.ID")]
    window.add_signals_to_plot(keys)
    _settle(window, qapp)
    assert sorted(window.plot_panel.plotted_keys()) == sorted(keys)

    window.plot_panel.set_column_choices(
        {c: True for c in ("Min", "Max", "Mean", "Std dev", "RMS", "Samples", "Hex")})
    for name in ("btn_fit", "btn_fit_v", "btn_cursor1", "btn_cursor2", "btn_points",
                 "btn_line_mode", "btn_fit"):
        button = getattr(window, name, None)
        if button is not None:
            button.click()
            _settle(window, qapp)
    # Leave the mode and come back: the plot is rebuilt both ways.
    if mode is not None:
        getattr(window, mode).click()
        _settle(window, qapp)
        getattr(window, mode).click()
        _settle(window, qapp)
    assert uncaught == []


def test_the_cursor_shows_the_bytes(window, qapp, uncaught):
    key = _key(window, "LIN_Frame.DataBytes")
    window.add_signals_to_plot([key])
    window.btn_cursor1.click()
    _settle(window, qapp)
    table = window.plot_panel.table
    texts = {
        table.item(row, column).text()
        for row in range(table.rowCount()) for column in range(table.columnCount())
        if table.item(row, column) is not None
    }
    series = window.store.get_series(key)
    assert texts & set(series.raw_values[:]), texts
    assert uncaught == []


def test_properties(window, qapp, uncaught):
    for key in _array_keys(window):
        window.show_signal_properties(key)
        _settle(window, qapp)
        window._properties_dialog.close()
    assert uncaught == []


def test_a_calculated_signal_on_an_array_channel(window, qapp, uncaught):
    definitions = []
    for index, key in enumerate(_array_keys(window)):
        for name, formula in (("sum", f"`{key}` + 1"), ("d", f"derivative(`{key}`)"),
                              ("r", f"rolling_mean(`{key}`, 5)")):
            definitions.append(CalculatedSignalDefinition(f"calc_{name}{index}", formula))
            window._queue_calculation(definitions[-1], "create", plot_after=False)
            _settle(window, qapp)
    # A text signal's number is its sample's position.
    first = window.calculated_signals.cached_series(definitions[0].key)
    assert list(first.values) == [float(i + 1) for i in range(N)]
    window.add_signals_to_plot([definition.key for definition in definitions])
    _settle(window, qapp)
    assert uncaught == []
    assert len(window.plot_panel.plotted_keys()) == 6


@pytest.mark.parametrize("suffix, writer", [
    (".csv", "_write_export_csv"), (".xlsx", "_write_export_xlsx"),
    (".mf4", "_write_export_mf4"),
])
def test_export(window, qapp, uncaught, tmp_path, suffix, writer):
    keys = _array_keys(window) + [_key(window, "Plain")]
    window.add_signals_to_plot(keys)
    _settle(window, qapp)
    path = tmp_path / f"export{suffix}"
    assert getattr(window, writer)(window.plot_panel.plotted_series(), str(path), None)
    assert path.stat().st_size > 0
    assert uncaught == []
    if suffix == ".csv":
        assert "00 01 02 03" in path.read_text(encoding="utf-8-sig")


def test_save_as_mf4_and_read_it_back(window, qapp, uncaught, tmp_path):
    path = tmp_path / "saved.mf4"
    series = [window.store.get_series(key) for key in window.store.all_keys()]
    assert window._write_mf4(series, str(path), "Saving decoded measurement")
    assert uncaught == []

    MDFReader._content_cache.clear()
    saved = _load(path)
    try:
        for key in saved.all_keys():
            got = saved.get_series(key)
            assert got.numpy_values().shape == got.numpy_timestamps().shape, key
        payload = next(saved.get_series(k) for k in saved.all_keys()
                       if k.endswith("::LIN_Frame.DataBytes"))
        assert payload.display_value_at(1) == "08 09 0A 0B 0C 0D 0E 0F"
    finally:
        saved.close()


@pytest.mark.parametrize("suffix", [".mf4", ".csv"])
def test_the_converter(array_file, tmp_path, suffix):
    from core.converter import ConvertJob, convert, list_signals

    assert {"LIN_Frame.DataBytes", "Bytes4"} <= {row[0] for row in list_signals(array_file)}
    output = tmp_path / f"converted{suffix}"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        convert(ConvertJob(inputs=[array_file], output=output))
    written = [output] if output.exists() else list(tmp_path.glob("converted*"))
    assert written and all(p.stat().st_size > 0 for p in written)
    if suffix == ".mf4":
        MDFReader._content_cache.clear()
        store = _load(output)
        try:
            for key in store.all_keys():
                got = store.get_series(key)
                assert got.numpy_values().shape == got.numpy_timestamps().shape, key
        finally:
            store.close()
