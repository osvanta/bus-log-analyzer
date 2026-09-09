# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
Tests for the per-signal line style (solid / dashed / dotted / dash-dot).

Two layers are covered:

* the plot panel itself — the style reaches the curve's pen, survives undo,
  and can be queued for a series that is not plotted yet;
* the configuration round trip — the style is written per signal, read back,
  and a pre-v2 configuration without the key still loads.

Line style is a **base** feature, so nothing here involves entitlement.
"""
from __future__ import annotations

import array
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from PySide6.QtCore import Qt

from core.signal_store import SignalSeries
from gui.plot_widget import DEFAULT_LINE_STYLE, LINE_STYLES, pen_style


# ── helpers ────────────────────────────────────────────────────────────────

def _series(signal_name: str = "EngSpeed") -> SignalSeries:
    return SignalSeries(
        channel=1,
        message_name="EngineControl",
        message_id=0x100,
        signal_name=signal_name,
        unit="rpm",
        timestamps=array.array("d", [0.0, 1.0, 2.0]),
        values=array.array("d", [10.0, 20.0, 30.0]),
    )


def _curve_pen_style(panel, key: str) -> Qt.PenStyle:
    """The Qt pen style actually held by the rendered curve."""
    pen = panel._items[key].curve.opts["pen"]
    return pen.style()


@pytest.fixture()
def window(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", Mock())
    monkeypatch.setattr(QMessageBox, "critical", Mock())
    monkeypatch.setattr(QMessageBox, "information", Mock())

    w = MainWindow("Osvanta Bus Log Analyzer", "00.00.99")
    # Never spin up a real decode thread from a test.
    monkeypatch.setattr(w, "load_data", Mock())
    yield w
    w.close()
    qapp.processEvents()


# ── style table ────────────────────────────────────────────────────────────

def test_style_table_maps_every_key_to_a_qt_pen_style():
    assert DEFAULT_LINE_STYLE in LINE_STYLES
    assert pen_style("solid") == Qt.PenStyle.SolidLine
    assert pen_style("dash") == Qt.PenStyle.DashLine
    assert pen_style("dot") == Qt.PenStyle.DotLine
    assert pen_style("dashdot") == Qt.PenStyle.DashDotLine


def test_unknown_style_name_falls_back_to_the_default():
    # A configuration written by a newer version must not break this one.
    assert pen_style("zigzag") == pen_style(DEFAULT_LINE_STYLE)
    assert pen_style(None) == pen_style(DEFAULT_LINE_STYLE)


# ── plot panel ─────────────────────────────────────────────────────────────

def test_new_series_defaults_to_solid(panel):
    s = _series()
    panel.add_series(s.key, s)

    assert panel._items[s.key].line_style == "solid"
    assert _curve_pen_style(panel, s.key) == Qt.PenStyle.SolidLine


@pytest.mark.parametrize(
    "style,expected",
    [
        ("dash", Qt.PenStyle.DashLine),
        ("dot", Qt.PenStyle.DotLine),
        ("dashdot", Qt.PenStyle.DashDotLine),
        ("solid", Qt.PenStyle.SolidLine),
    ],
)
def test_set_series_line_style_reaches_the_curve_pen(panel, style, expected):
    s = _series()
    panel.add_series(s.key, s)

    panel.set_series_line_style(s.key, style)

    assert panel._items[s.key].line_style == style
    assert _curve_pen_style(panel, s.key) == expected


def test_set_series_line_style_rejects_unknown_names(panel):
    s = _series()
    panel.add_series(s.key, s)

    panel.set_series_line_style(s.key, "zigzag")

    assert panel._items[s.key].line_style == DEFAULT_LINE_STYLE
    assert _curve_pen_style(panel, s.key) == Qt.PenStyle.SolidLine


def test_set_series_line_style_ignores_unknown_key(panel):
    # Must not raise — configs can name signals this measurement does not have.
    panel.set_series_line_style("CAN1::Nope::Nope", "dash")


def test_line_style_change_emits_signal(panel):
    s = _series()
    panel.add_series(s.key, s)
    seen: list[tuple[str, str]] = []
    panel.signalLineStyleChanged.connect(lambda k, v: seen.append((k, v)))

    panel.set_series_line_style(s.key, "dot")

    assert seen == [(s.key, "dot")]


def test_setting_the_same_style_is_a_no_op(panel):
    s = _series()
    panel.add_series(s.key, s)
    seen: list[tuple[str, str]] = []
    panel.signalLineStyleChanged.connect(lambda k, v: seen.append((k, v)))
    depth_before = len(panel._undo_stack)

    panel.set_series_line_style(s.key, DEFAULT_LINE_STYLE)

    # No signal, and no undo entry burned on a change that did nothing.
    assert seen == []
    assert len(panel._undo_stack) == depth_before


def test_undo_restores_the_previous_line_style(panel):
    s = _series()
    panel.add_series(s.key, s)
    panel.set_series_line_style(s.key, "dash")
    panel.set_series_line_style(s.key, "dot")

    panel.undo()

    assert panel._items[s.key].line_style == "dash"
    assert _curve_pen_style(panel, s.key) == Qt.PenStyle.DashLine


def test_line_style_applies_in_points_mode(panel):
    # Points mode draws a thinner connecting line — it still honours the style.
    s = _series()
    panel.add_series(s.key, s)
    panel.set_series_line_style(s.key, "dash")

    panel.set_show_points(True)

    assert _curve_pen_style(panel, s.key) == Qt.PenStyle.DashLine


def test_pending_style_is_applied_when_the_series_arrives(panel):
    s = _series()
    panel.set_pending_line_styles({s.key: "dashdot"})

    panel.add_series(s.key, s)

    assert panel._items[s.key].line_style == "dashdot"
    assert _curve_pen_style(panel, s.key) == Qt.PenStyle.DashDotLine


def test_pending_style_is_consumed_once(panel):
    s = _series()
    panel.set_pending_line_styles({s.key: "dot"})
    panel.add_series(s.key, s)

    panel.remove_series(s.key)
    panel.add_series(s.key, s)

    # The queue entry was spent on the first add; the re-add starts clean.
    assert panel._pending_line_styles == {}
    assert panel._items[s.key].line_style == DEFAULT_LINE_STYLE


def test_pending_styles_are_normalised_when_queued(panel):
    s = _series()
    panel.set_pending_line_styles({s.key: "zigzag"})

    panel.add_series(s.key, s)

    assert panel._items[s.key].line_style == DEFAULT_LINE_STYLE


def test_series_line_styles_reports_every_plotted_signal(panel):
    a, b = _series("EngSpeed"), _series("Throttle")
    panel.add_series(a.key, a)
    panel.add_series(b.key, b)
    panel.set_series_line_style(b.key, "dash")

    assert panel.series_line_styles() == {a.key: "solid", b.key: "dash"}


# ── configuration round trip ───────────────────────────────────────────────

def test_save_configuration_records_line_style_per_signal(
    window, monkeypatch, tmp_path
):
    from PySide6.QtWidgets import QFileDialog

    a, b = _series("EngSpeed"), _series("Throttle")
    window.plot_panel.add_series(a.key, a)
    window.plot_panel.add_series(b.key, b)
    window.plot_panel.set_series_line_style(b.key, "dot")

    out = tmp_path / "config.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *_a, **_k: (str(out), "JSON Files (*.json)"),
    )
    window.save_configuration()

    saved = json.loads(out.read_text(encoding="utf-8"))
    styles = {s["key"]: s["line_style"] for s in saved["signals"]}
    assert styles == {a.key: "solid", b.key: "dot"}


def test_load_configuration_queues_line_styles(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    s = _series()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "version": "00.00.99",
        "measurement_path": str(tmp_path / "sample.blf"),
        "signals": [{"key": s.key, "line_style": "dash"}],
    }), encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        lambda *_a, **_k: (str(config), "JSON Files (*.json)"),
    )

    window.load_configuration()

    # Queued for the decode that follows; applied as the series is added.
    assert window.plot_panel._pending_line_styles == {s.key: "dash"}
    window.plot_panel.add_series(s.key, s)
    assert window.plot_panel._items[s.key].line_style == "dash"


def test_pre_v2_configuration_without_line_style_still_loads(
    window, monkeypatch, tmp_path
):
    from PySide6.QtWidgets import QFileDialog

    s = _series()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "version": "00.00.99",
        "measurement_path": str(tmp_path / "sample.blf"),
        "signals": [{"key": s.key, "visible": True, "group": ""}],
    }), encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        lambda *_a, **_k: (str(config), "JSON Files (*.json)"),
    )

    window.load_configuration()

    assert window.plot_panel._pending_line_styles == {}
    window.plot_panel.add_series(s.key, s)
    assert window.plot_panel._items[s.key].line_style == DEFAULT_LINE_STYLE


def test_temporary_plot_config_carries_line_style(window, tmp_path):
    s = _series()
    window.plot_panel.add_series(s.key, s)
    window.plot_panel.set_series_line_style(s.key, "dashdot")
    window._temporary_plot_config_path = tmp_path / "temp.json"

    config = window._capture_temporary_plot_configuration()

    assert config["version"] == 2
    assert config["signals"][0]["line_style"] == "dashdot"


def test_temporary_handoff_requeues_line_style(window, tmp_path):
    s = _series()
    window.plot_panel.add_series(s.key, s)
    window.plot_panel.set_series_line_style(s.key, "dot")
    window._temporary_plot_config_path = tmp_path / "temp.json"
    window._temporary_plot_handoff = window._capture_temporary_plot_configuration()
    window.plot_panel.clear_all()

    window._arm_temporary_plot_handoff()

    assert window.plot_panel._pending_line_styles == {s.key: "dot"}
