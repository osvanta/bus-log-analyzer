# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for core/channel_config.py — save/load round-trip and query helpers."""
from __future__ import annotations

import json

import pytest

from core.bus_types import BusType, all_channels_key
from core.channel_config import ChannelConfig

CAN1 = (BusType.CAN, 1)
CAN2 = (BusType.CAN, 2)
LIN1 = (BusType.LIN, 1)
ALL_CAN = all_channels_key(BusType.CAN)
ALL_LIN = all_channels_key(BusType.LIN)


# ── Loading ────────────────────────────────────────────────────────────────

def test_load_legacy_v1(legacy_v1_path):
    cfg = ChannelConfig.load(legacy_v1_path)
    assert cfg.name == "Legacy Config"
    assert CAN1 in cfg.channels


def test_load_legacy_v1_channel_path(legacy_v1_path):
    cfg = ChannelConfig.load(legacy_v1_path)
    assert cfg.channels[CAN1] == "C:/fake/engine.dbc"


def test_legacy_canscope_type_still_loads(legacy_v1_path):
    """Channel configs written before the Osvanta rename must keep opening."""
    raw = json.loads(legacy_v1_path.read_text(encoding="utf-8"))
    assert raw["type"] == ChannelConfig.LEGACY_TYPE
    cfg = ChannelConfig.load(legacy_v1_path)
    assert cfg.name == "Legacy Config"


def test_legacy_file_round_trips_to_new_names(tmp_path, legacy_v1_path):
    """Re-saving a legacy config writes the new extension and type."""
    cfg = ChannelConfig.load(legacy_v1_path)
    out = tmp_path / f"resaved{ChannelConfig.FILE_EXTENSION}"
    cfg.save(out)
    assert out.suffix == ".osvanta_ch"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["type"] == "osvanta_channel_config"
    assert ChannelConfig.load(out).channels == cfg.channels


def test_load_wrong_type_raises(tmp_path):
    bad = tmp_path / "bad.osvanta_ch"
    bad.write_text(json.dumps({"type": "something_else", "channels": {}}))
    with pytest.raises(ValueError, match="Not a channel config file"):
        ChannelConfig.load(bad)


# ── Save / round-trip ──────────────────────────────────────────────────────

def test_save_round_trip(tmp_path, sample_dbc_path):
    out = tmp_path / "test.osvanta_ch"
    cfg = ChannelConfig(name="Test", channels={CAN1: str(sample_dbc_path)})
    cfg.save(out)

    loaded = ChannelConfig.load(out)
    assert loaded.name == "Test"
    assert loaded.channels[CAN1] == str(sample_dbc_path)


def test_save_writes_version_3(tmp_path):
    out = tmp_path / "test.osvanta_ch"
    ChannelConfig(name="X", channels={}).save(out)
    data = json.loads(out.read_text())
    assert data["version"] == 3


def test_save_writes_type_field(tmp_path):
    out = tmp_path / "test.osvanta_ch"
    ChannelConfig(name="X", channels={}).save(out)
    data = json.loads(out.read_text())
    assert data["type"] == "osvanta_channel_config"


# ── Factory ───────────────────────────────────────────────────────────────

def test_from_single_dbc_uses_all_channels_key(sample_dbc_path):
    cfg = ChannelConfig.from_single_dbc(str(sample_dbc_path))
    assert ALL_CAN in cfg.channels


def test_from_single_dbc_name_is_stem(sample_dbc_path):
    cfg = ChannelConfig.from_single_dbc(str(sample_dbc_path))
    assert cfg.name == sample_dbc_path.stem


# ── Query helpers ──────────────────────────────────────────────────────────

def test_dbc_path_for_specific_channel(sample_dbc_path):
    cfg = ChannelConfig(channels={CAN1: str(sample_dbc_path)})
    assert cfg.dbc_path_for(BusType.CAN, 1) == str(sample_dbc_path)


def test_dbc_path_for_falls_back_to_all_channels(sample_dbc_path):
    cfg = ChannelConfig(channels={ALL_CAN: str(sample_dbc_path)})
    assert cfg.dbc_path_for(BusType.CAN, 99) == str(sample_dbc_path)


def test_dbc_path_for_returns_none_when_unassigned():
    cfg = ChannelConfig(channels={})
    assert cfg.dbc_path_for(BusType.CAN, 1) is None


def test_is_empty_on_fresh():
    assert ChannelConfig().is_empty()


def test_is_empty_false_when_assigned(sample_dbc_path):
    cfg = ChannelConfig(channels={CAN1: str(sample_dbc_path)})
    assert not cfg.is_empty()


def test_all_dbc_paths_deduplicates(sample_dbc_path):
    p = str(sample_dbc_path)
    cfg = ChannelConfig(channels={CAN1: p, CAN2: p})
    assert cfg.all_dbc_paths() == [p]


def test_assigned_channels_excludes_all_channels_key(sample_dbc_path):
    p = str(sample_dbc_path)
    cfg = ChannelConfig(channels={ALL_CAN: p, CAN1: p})
    assert ALL_CAN not in cfg.assigned_channels()
    assert CAN1 in cfg.assigned_channels()


def test_summary_contains_name(sample_dbc_path):
    cfg = ChannelConfig(name="My Config", channels={CAN1: str(sample_dbc_path)})
    assert "My Config" in cfg.summary()


# ── Bus dimension: migration, collision, and the CAN-only view ─────────────

def test_v2_integer_keys_migrate_to_can(tmp_path, sample_dbc_path):
    """A pre-LIN config has bare integer keys, and every one of them is CAN.

    Version 3 added the bus prefix. Anything older predates LIN support
    entirely, so reading those keys as CAN is exact rather than a guess.
    """
    legacy = tmp_path / "v2.osvanta_ch"
    legacy.write_text(json.dumps({
        "type": "osvanta_channel_config",
        "version": 2,
        "name": "Old Truck",
        "channels": {"0": str(sample_dbc_path), "1": str(sample_dbc_path)},
    }), encoding="utf-8")

    cfg = ChannelConfig.load(legacy)

    assert set(cfg.channels) == {ALL_CAN, CAN1}
    assert not cfg.has_bus(BusType.LIN)
    assert cfg.dbc_path_for(BusType.CAN, 1) == str(sample_dbc_path)


def test_v2_config_resaves_with_bus_prefixed_keys(tmp_path, sample_dbc_path):
    legacy = tmp_path / "v2.osvanta_ch"
    legacy.write_text(json.dumps({
        "type": "osvanta_channel_config",
        "version": 2,
        "name": "Old Truck",
        "channels": {"1": str(sample_dbc_path)},
    }), encoding="utf-8")

    out = tmp_path / "resaved.osvanta_ch"
    ChannelConfig.load(legacy).save(out)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 3
    assert list(data["channels"]) == ["CAN:1"]
    assert ChannelConfig.load(out).channels == {CAN1: str(sample_dbc_path)}


def test_can_and_lin_channel_one_resolve_to_different_databases(tmp_path):
    """The collision the bus dimension exists to prevent.

    Under the old integer keying these two assignments were the same key and
    the second silently replaced the first.
    """
    can_db = str(tmp_path / "powertrain.dbc")
    lin_db = str(tmp_path / "doors.ldf")
    cfg = ChannelConfig(channels={CAN1: can_db, LIN1: lin_db})

    assert cfg.dbc_path_for(BusType.CAN, 1) == can_db
    assert cfg.dbc_path_for(BusType.LIN, 1) == lin_db
    assert sorted(cfg.buses()) == [BusType.CAN, BusType.LIN]


def test_all_channels_fallback_does_not_cross_buses(tmp_path):
    """An All-CAN database must never be applied to a LIN channel."""
    can_db = str(tmp_path / "fallback.dbc")
    cfg = ChannelConfig(channels={ALL_CAN: can_db})

    assert cfg.dbc_path_for(BusType.CAN, 7) == can_db
    assert cfg.dbc_path_for(BusType.LIN, 7) is None


def test_databases_for_bus_only_returns_that_bus(tmp_path):
    can_db = str(tmp_path / "powertrain.dbc")
    lin_db = str(tmp_path / "doors.ldf")
    cfg = ChannelConfig(channels={CAN1: can_db, LIN1: lin_db, ALL_LIN: lin_db})

    assert cfg.databases_for_bus(BusType.CAN) == [(can_db, 1)]
    assert sorted(cfg.databases_for_bus(BusType.LIN)) == sorted(
        [(lin_db, 1), (lin_db, 0)]
    )


def test_can_decoder_map_is_integer_keyed_and_skips_lin(sample_dbc_path):
    """Regression guard for the raw-CAN decode loops.

    BLF/ASC match decoders against RawFrame.channel, a bare integer. If this
    view ever returned bus-tagged keys, every lookup in those loops would miss
    and decoding would produce no signals at all — with no exception raised.
    A test is the only thing that catches that, because nothing crashes.
    """
    path = str(sample_dbc_path)
    cfg = ChannelConfig(channels={
        ALL_CAN: path,
        CAN1: path,
        LIN1: path,          # must not leak into the CAN-only view
    })
    cfg.build_all_decoders()

    decoder_map = cfg.can_decoder_map()

    assert set(decoder_map) == {None, 1}, "keys must be plain ints, None = All CAN"
    assert all(not isinstance(key, tuple) for key in decoder_map)
    assert decoder_map[1] is not None
    assert decoder_map[None] is not None


def test_summary_labels_each_bus(tmp_path, sample_dbc_path):
    cfg = ChannelConfig(name="Mixed", channels={
        CAN1: str(sample_dbc_path),
        LIN1: str(tmp_path / "doors.ldf"),
    })
    summary = cfg.summary()
    assert "CAN 1" in summary
    assert "LIN 1" in summary
