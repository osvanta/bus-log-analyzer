# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for core/dbc_decoder.py — decode pipeline and stats tracking."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.models import RawFrame


# ── Loading ────────────────────────────────────────────────────────────────

def test_load_sample_dbc(decoder):
    assert len(decoder.database.messages) == 3


def test_message_names(decoder):
    names = {m.name for m in decoder.database.messages}
    assert names == {"EngineControl", "GearStatus", "DiagRequest"}


def test_missing_dbc_raises(tmp_path):
    from core.dbc_decoder import DBCDecoder, DBCLoadError

    with pytest.raises(DBCLoadError):
        DBCDecoder(str(tmp_path / "nonexistent.dbc"))


def test_dbc_scaling_and_text_choices_load_natively_in_strict_mode(tmp_path):
    from core.dbc_decoder import load_database_file

    path = tmp_path / "mixed_scaling_and_choices.dbc"
    path.write_text(
        """\
VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 ErrorFrame: 8 ECU
 SG_ ErrorCode : 0|8@1+ (0.5,-10) [-10|117.5] "" ECU

VAL_ 256 ErrorCode 0 "NoError" 1 "MinorError" 255 "Unavailable" ;
""",
        encoding="cp1252",
    )

    db, messages = load_database_file(path)
    signal = db.get_message_by_name("ErrorFrame").signals[0]

    assert messages == ["Database loaded in strict mode."]
    assert signal.scale == pytest.approx(0.5)
    assert signal.offset == pytest.approx(-10)
    assert {key: str(value) for key, value in signal.choices.items()} == {
        0: "NoError",
        1: "MinorError",
        255: "Unavailable",
    }


def _mixed_linear_text_arxml(*, category: str = "LINEAR") -> str:
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0">
  <COMPU-METHOD>
    <SHORT-NAME>CM_ErrorCode</SHORT-NAME>
    <CATEGORY>{category}</CATEGORY>
    <COMPU-INTERNAL-TO-PHYS>
      <COMPU-SCALES>
        <COMPU-SCALE>
          <LOWER-LIMIT>0</LOWER-LIMIT>
          <UPPER-LIMIT>63</UPPER-LIMIT>
          <COMPU-RATIONAL-COEFFS>
            <COMPU-NUMERATOR><V>0</V><V>1</V></COMPU-NUMERATOR>
            <COMPU-DENOMINATOR><V>1</V></COMPU-DENOMINATOR>
          </COMPU-RATIONAL-COEFFS>
        </COMPU-SCALE>
        <COMPU-SCALE>
          <LOWER-LIMIT>0</LOWER-LIMIT>
          <UPPER-LIMIT>0</UPPER-LIMIT>
          <COMPU-CONST><VT>NoError</VT></COMPU-CONST>
        </COMPU-SCALE>
      </COMPU-SCALES>
    </COMPU-INTERNAL-TO-PHYS>
  </COMPU-METHOD>
</AUTOSAR>
"""


def test_arxml_mixed_linear_text_fallback_is_in_memory_and_guarded(
    tmp_path, monkeypatch
):
    import cantools
    from core.dbc_decoder import load_database_file

    path = tmp_path / "PowerTrain_FD.arxml"
    original = _mixed_linear_text_arxml()
    path.write_text(original, encoding="utf-8")
    expected_db = SimpleNamespace(messages=[])
    load_file_calls: list[bool] = []
    patched_inputs: list[str] = []

    def fail_load_file(_path, *, strict):
        load_file_calls.append(strict)
        raise ValueError(
            "Encountered a a non-unique child node of type COMPU-SCALE "
            "which ought to be unique"
        )

    def accept_load_string(text, *, database_format, strict):
        patched_inputs.append(text)
        assert database_format == "arxml"
        assert strict is False
        return expected_db

    monkeypatch.setattr(cantools.database, "load_file", fail_load_file)
    monkeypatch.setattr(cantools.database, "load_string", accept_load_string)

    db, messages = load_database_file(path)

    assert db is expected_db
    assert load_file_calls == [True, False]
    assert len(patched_inputs) == 1
    assert "SCALE_LINEAR_AND_TEXTTABLE" in patched_inputs[0]
    assert path.read_text(encoding="utf-8") == original
    assert any("CM_ErrorCode" in message for message in messages)
    assert any("not modified" in message for message in messages)


def test_arxml_fallback_ignores_unrelated_parse_errors(tmp_path, monkeypatch):
    import cantools
    from core.dbc_decoder import DBCLoadError, load_database_file

    path = tmp_path / "broken.arxml"
    path.write_text(_mixed_linear_text_arxml(), encoding="utf-8")
    load_string_called = False

    def fail_load_file(_path, *, strict):
        raise ValueError("Unrelated ARXML parse failure")

    def unexpected_load_string(*args, **kwargs):
        nonlocal load_string_called
        load_string_called = True

    monkeypatch.setattr(cantools.database, "load_file", fail_load_file)
    monkeypatch.setattr(cantools.database, "load_string", unexpected_load_string)

    with pytest.raises(DBCLoadError, match="Unrelated ARXML parse failure"):
        load_database_file(path)

    assert load_string_called is False


def test_arxml_fallback_rejects_non_mixed_scale_layout(
    tmp_path, monkeypatch
):
    import cantools
    from core.dbc_decoder import DBCLoadError, load_database_file

    path = tmp_path / "linear_only.arxml"
    xml = _mixed_linear_text_arxml().replace(
        "<COMPU-CONST><VT>NoError</VT></COMPU-CONST>",
        """<COMPU-RATIONAL-COEFFS>
            <COMPU-NUMERATOR><V>0</V><V>2</V></COMPU-NUMERATOR>
            <COMPU-DENOMINATOR><V>1</V></COMPU-DENOMINATOR>
          </COMPU-RATIONAL-COEFFS>""",
    )
    path.write_text(xml, encoding="utf-8")

    def fail_load_file(_path, *, strict):
        raise ValueError(
            "Encountered a a non-unique child node of type COMPU-SCALE "
            "which ought to be unique"
        )

    monkeypatch.setattr(cantools.database, "load_file", fail_load_file)
    monkeypatch.setattr(
        cantools.database,
        "load_string",
        lambda *args, **kwargs: pytest.fail("unsafe fallback was attempted"),
    )

    with pytest.raises(DBCLoadError, match="found no eligible"):
        load_database_file(path)


# ── Decode: EngineControl ──────────────────────────────────────────────────

def test_decode_engine_control_returns_two_signals(decoder, frame_engine):
    samples = decoder.decode_frame(frame_engine)
    assert len(samples) == 2


def test_decode_engine_speed_value(decoder, frame_engine):
    samples = decoder.decode_frame(frame_engine)
    eng_speed = next(s for s in samples if s.signal_name == "EngSpeed")
    assert eng_speed.numeric_value == pytest.approx(1200.0)


def test_decode_throttle_value(decoder, frame_engine):
    samples = decoder.decode_frame(frame_engine)
    throttle = next(s for s in samples if s.signal_name == "Throttle")
    assert throttle.numeric_value == pytest.approx(50.0)


def test_decode_sample_metadata(decoder, frame_engine):
    samples = decoder.decode_frame(frame_engine)
    s = samples[0]
    assert s.channel == 1
    assert s.message_name == "EngineControl"
    assert s.message_id == 0x100
    assert s.timestamp == pytest.approx(0.001)


# ── Decode: GearStatus (enum signal, decode_choices=False) ────────────────

def test_decode_gear_raw_value(decoder, frame_gear):
    samples = decoder.decode_frame(frame_gear)
    assert len(samples) == 1
    gear = samples[0]
    assert gear.signal_name == "Gear"
    assert gear.numeric_value == pytest.approx(4.0)


def test_decode_gear_display_label(decoder, frame_gear):
    samples = decoder.decode_frame(frame_gear)
    gear = samples[0]
    # choices cache should produce the string label
    assert gear.value == "Drive"


# ── Decode: DiagRequest (message with no signals) ─────────────────────────

def test_decode_diag_returns_empty(decoder, frame_diag):
    samples = decoder.decode_frame(frame_diag)
    assert samples == []


def test_decode_diag_increments_no_signals_stat(decoder, frame_diag):
    decoder.decode_frame(frame_diag)
    assert decoder.stats["decoded_no_signals"] == 1


def test_decode_diag_does_not_increment_fail_stat(decoder, frame_diag):
    decoder.decode_frame(frame_diag)
    assert decoder.stats["decode_fail"] == 0


# ── Decode: unknown arbitration ID ────────────────────────────────────────

def test_decode_unknown_id_returns_empty(decoder):
    frame = RawFrame(
        timestamp=0.0, channel=1, arbitration_id=0xDEAD,
        is_extended_id=False, is_fd=False, dlc=8,
        data=bytes(8), direction="Rx",
    )
    assert decoder.decode_frame(frame) == []


def test_decode_unknown_id_no_fail_stat(decoder):
    frame = RawFrame(
        timestamp=0.0, channel=1, arbitration_id=0xDEAD,
        is_extended_id=False, is_fd=False, dlc=8,
        data=bytes(8), direction="Rx",
    )
    decoder.decode_frame(frame)
    assert decoder.stats["decode_fail"] == 0


# ── Stats accumulation ─────────────────────────────────────────────────────

def test_stats_decode_success_count(decoder, frame_engine, frame_gear):
    decoder.decode_frame(frame_engine)
    decoder.decode_frame(frame_gear)
    assert decoder.stats["decode_success"] == 2


def test_stats_no_signals_accumulates(decoder, frame_diag):
    decoder.decode_frame(frame_diag)
    decoder.decode_frame(frame_diag)
    assert decoder.stats["decoded_no_signals"] == 2


# ── diagnostics_text ──────────────────────────────────────────────────────

def test_diagnostics_text_contains_dbc_label(decoder):
    text = decoder.diagnostics_text()
    assert "DBC file:" in text


def test_diagnostics_text_shows_message_count(decoder):
    text = decoder.diagnostics_text()
    assert "3" in text


def test_diagnostics_text_contains_no_signals_counter(decoder, frame_diag):
    decoder.decode_frame(frame_diag)
    text = decoder.diagnostics_text()
    assert "Matched, no signals:" in text
    assert "1" in text


# ── J1939 source-address matching (issue #13) ─────────────────────────────

_J1939_DBC = """\
VERSION ""

NS_ :

BS_:

BU_: MCU BMS ENG

BO_ 2566834927 DM1_239: 8 MCU
 SG_ DM1_239_DTC1 : 16|32@1+ (1,0) [0|4294967295] "" Vector__XXX

BO_ 2566834931 DM1_243: 8 BMS
 SG_ DM1_243_DTC1 : 16|32@1+ (1,0) [0|4294967295] "" Vector__XXX

BO_ 2566840320 VehicleDist: 8 ENG
 SG_ TotalDist : 0|32@1+ (0.125,0) [0|526385151.9] "km" Vector__XXX
"""


@pytest.fixture
def j1939_decoder(tmp_path):
    """DM1 (PGN 0xFECA) at SAs 0xEF and 0xF3; VD (PGN 0xFEE0) at SA 0x00 only."""
    from core.dbc_decoder import DBCDecoder

    path = tmp_path / "j1939.dbc"
    path.write_text(_J1939_DBC, encoding="utf-8")
    return DBCDecoder(str(path))


def _j1939_frame(arb_id: int, dtc: int = 0) -> RawFrame:
    data = bytes([0x00, 0xFF]) + dtc.to_bytes(4, "little") + bytes([0xFF, 0xFF])
    return RawFrame(
        timestamp=0.0, channel=1, arbitration_id=arb_id,
        is_extended_id=True, is_fd=False, dlc=8,
        data=data, direction="Rx",
    )


def _names(messages) -> list[str]:
    return [m.name for m in messages]


@pytest.mark.parametrize("arb_id", [0x18FECA13, 0x18FECAE6])
def test_j1939_undefined_sa_of_multi_sa_pgn_stays_undecoded(j1939_decoder, arb_id):
    assert j1939_decoder.candidates_for(arb_id, True) == []
    assert j1939_decoder.decode_frame(_j1939_frame(arb_id)) == []


@pytest.mark.parametrize("arb_id, name", [
    (0x18FECAEF, "DM1_239"),
    (0x18FECAF3, "DM1_243"),
])
def test_j1939_defined_sa_matches_only_its_own_message(j1939_decoder, arb_id, name):
    assert _names(j1939_decoder.candidates_for(arb_id, True)) == [name]


def test_j1939_sa_match_ignores_priority(j1939_decoder):
    # Priority 7 instead of the DBC's 6: same PGN and SA, still DM1_239.
    samples = j1939_decoder.decode_frame(_j1939_frame(0x1CFECAEF, dtc=61765))
    assert [(s.signal_name, s.value) for s in samples] == [("DM1_239_DTC1", 61765)]


def test_j1939_single_sa_pgn_keeps_placeholder_fallback(j1939_decoder):
    # VehicleDist is defined once, at SA 0x00; any sender's frame decodes into it.
    assert _names(j1939_decoder.candidates_for(0x18FEE0FE, True)) == ["VehicleDist"]


def test_vectorized_candidates_follow_decoder(j1939_decoder):
    from core.vectorized_decoder import VectorizedDBC

    vec = VectorizedDBC(j1939_decoder)
    assert vec.get_candidates(0x18FECA13, is_extended=True) == []
    assert _names(vec.get_candidates(0x18FECAEF, is_extended=True)) == ["DM1_239"]
    assert _names(vec.get_candidates(0x18FEE0FE, is_extended=True)) == ["VehicleDist"]


def test_j1939_dm1_trace_holds_only_its_own_node(j1939_decoder):
    # Four nodes send DM1 in the same millisecond; only SA 0xEF has a DTC.
    frames = [
        _j1939_frame(0x18FECAEF, dtc=61765),
        _j1939_frame(0x18FECA13),
        _j1939_frame(0x18FECAE6),
        _j1939_frame(0x18FECAF3),
    ]
    by_signal: dict[str, list] = {}
    for frame in frames:
        for s in j1939_decoder.decode_frame(frame):
            by_signal.setdefault(s.signal_name, []).append(s.value)
    assert by_signal == {"DM1_239_DTC1": [61765], "DM1_243_DTC1": [0]}
