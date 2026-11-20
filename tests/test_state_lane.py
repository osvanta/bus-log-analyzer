# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""State lanes, apart from drawing: which signals become lanes, their
stretches, colours, and what is drawn at a zoom level."""
import numpy as np
import pytest

from core.signal_store import SignalSeries
from core.state_lane import (
    BIT, BIT_COLOR, FAULT_COLOR, LABELS, LANE, LINE, MAX_LANE_VALUES, PALETTE,
    build_lane, color_of, lane_kind, pieces, shown_as_lane,
)

GEAR = {0: 'P', 1: 'R', 2: 'N', 3: 'D'}


def _series(ts, vs, labels=None):
    series = SignalSeries(channel=1, message_name='Msg', message_id=0x100,
                          signal_name='S', unit='',
                          timestamps=np.asarray(ts, dtype=float),
                          values=np.asarray(vs, dtype=float))
    if labels is not None:
        series.raw_values = [labels.get(int(v), v) if np.isfinite(v) else '' for v in vs]
        series.has_labels = True
    return series


# ── Which signals become lanes ───────────────────────────────────────────


def test_a_signal_with_a_value_table_and_few_values_is_a_labelled_lane():
    assert lane_kind(_series([0, 1, 2], [0, 3, 1], GEAR)) == LABELS


def test_too_many_distinct_values_stay_a_line_even_with_a_value_table():
    many = list(range(MAX_LANE_VALUES + 1))
    labels = {v: f'S{v}' for v in many}
    assert lane_kind(_series(many, many, labels)) is None
    assert lane_kind(_series(many[:-1], many[:-1], labels)) == LABELS   # exactly the limit


def test_zeros_and_ones_without_a_value_table_are_a_bit_lane():
    assert lane_kind(_series([0, 1, 2, 3], [0, 1, 1, 0])) == BIT


@pytest.mark.parametrize('values', [
    [0, 0, 0],            # only 0: a still signal, not plainly a bit
    [1, 1, 1],            # only 1
    [0, 1, 2],            # a 2 is no bit
    [0.0, 0.5, 1.0],
    [10.0, 20.0, 30.0],
])
def test_other_numbers_stay_a_line(values):
    assert lane_kind(_series(range(len(values)), values)) is None


def test_missing_values_are_ignored_when_deciding():
    assert lane_kind(_series([0, 1, 2, 3], [0, np.nan, 1, 0])) == BIT
    assert lane_kind(_series([0, 1], [np.nan, np.nan])) is None
    assert lane_kind(_series([0, 1], [np.nan, np.nan], GEAR)) is None
    assert lane_kind(_series([], [])) is None


def test_a_long_signal_is_judged_on_all_of_it_not_its_first_part():
    from core import state_lane
    size = state_lane._CHUNK * 3
    bits = np.zeros(size)
    bits[-1] = 1.0                                       # its only 1, last
    assert lane_kind(_series(range(size), bits)) == BIT
    bits[-2] = 2.0                                       # and a 2 just before it
    assert lane_kind(_series(range(size), bits)) is None
    states = np.zeros(size)
    states[-MAX_LANE_VALUES:] = np.arange(MAX_LANE_VALUES) + 1   # a 17th value at the end
    labels = {v: f'S{v}' for v in range(MAX_LANE_VALUES + 1)}
    assert lane_kind(_series(range(size), states, labels)) is None
    states[-1] = 0.0
    assert lane_kind(_series(range(size), states, labels)) == LABELS


def test_the_users_choice_wins():
    gear = _series([0, 1], [0, 3], GEAR)
    speed = _series([0, 1], [10.0, 25.5])
    assert shown_as_lane(gear, '') == LABELS
    assert shown_as_lane(gear, LINE) is None
    assert shown_as_lane(speed, '') is None
    assert shown_as_lane(speed, LANE) == LABELS          # numbers shown as labels
    bit = _series([0, 1], [0, 1])
    assert shown_as_lane(bit, LANE) == BIT


# ── Stretches ────────────────────────────────────────────────────────────


def test_a_signal_becomes_one_stretch_per_change():
    lane = build_lane(_series([0, 1, 2, 3, 4, 5], [0, 0, 3, 3, 2, 3], GEAR), LABELS)
    assert lane.starts.tolist() == [0, 2, 4, 5]
    assert lane.ends.tolist() == [2, 4, 5, 5]            # the last ends at the last sample
    assert lane.values.tolist() == [0, 3, 2, 3]
    assert [lane.label(i) for i in range(lane.size)] == ['P', 'D', 'N', 'D']


def test_a_missing_value_ends_a_stretch_and_starts_none():
    lane = build_lane(_series([0, 1, 2, 3, 4], [3, np.nan, np.nan, 3, 3], GEAR), LABELS)
    assert list(zip(lane.starts.tolist(), lane.ends.tolist())) == [(0, 1), (3, 4)]
    assert lane.at(1.5) is None


def test_a_value_outside_the_table_is_written_as_its_number():
    lane = build_lane(_series([0, 1], [3, 7], GEAR), LABELS)
    assert lane.label(1) == '7'
    numbers = build_lane(_series([0, 1], [2.5, 4.0]), LABELS)
    assert [numbers.label(i) for i in range(2)] == ['2.5', '4']


def test_the_stretch_at_a_time_and_every_stretch_of_a_state():
    lane = build_lane(_series([0, 1, 2, 3, 4, 5], [0, 0, 3, 3, 2, 3], GEAR), LABELS)
    assert lane.at(-0.1) is None
    assert lane.at(0.0) == 0 and lane.at(1.99) == 0
    assert lane.at(2.0) == 1                             # a change starts the new stretch
    assert lane.at(5.0) == 3
    assert lane.at(5.01) is None
    assert lane.spans_of(3.0) == [(2.0, 4.0), (5.0, 5.0)]
    assert lane.spans_of(1.0) == []


def test_an_empty_signal_has_no_stretches():
    lane = build_lane(_series([], []), LABELS)
    assert lane.size == 0 and lane.at(0.0) is None


# ── Colours ──────────────────────────────────────────────────────────────


def test_a_value_has_the_same_colour_wherever_it_appears():
    assert color_of(3.0, 'D') == PALETTE[3]
    assert color_of(3.0, 'Drive') == PALETTE[3]
    assert color_of(11.0, 'X') == PALETTE[11 % len(PALETTE)]
    assert color_of(0.5, 'half') == color_of(0.5, 'half')
    assert color_of(0.5, 'half') in PALETTE


@pytest.mark.parametrize('label', ['Fault', 'SENSOR_ERROR', 'Failed', 'Abort', 'Derating',
                                   'CellOverTempCritical'])
def test_fault_like_states_are_red(label):
    assert color_of(2.0, label) == FAULT_COLOR


def test_a_bit_is_always_one_colour():
    assert color_of(1.0, '1', BIT) == BIT_COLOR
    lane = build_lane(_series([0, 1], [0, 1]), BIT)
    assert lane.color(1) == BIT_COLOR


# ── What is drawn at a zoom ──────────────────────────────────────────────


def test_wide_stretches_are_drawn_one_by_one():
    lane = build_lane(_series([0, 10, 20, 30], [0, 3, 1, 3], GEAR), LABELS)
    cut = pieces(lane, 0.0, 30.0, pixel=0.1)
    assert cut.index.tolist() == [0, 1, 2, 3]
    assert not cut.busy.any()


def test_only_stretches_in_view_are_drawn():
    lane = build_lane(_series([0, 10, 20, 30], [0, 3, 1, 3], GEAR), LABELS)
    assert pieces(lane, 12.0, 18.0, pixel=0.1).index.tolist() == [1]
    assert pieces(lane, 40.0, 50.0, pixel=0.1).index.size == 0
    assert pieces(lane, 5.0, 5.0, pixel=0.1).index.size == 0


def test_narrow_stretches_side_by_side_become_one_busy_block():
    # 0 to 10 s quiet, then a change every 0.01 s to 11 s, then quiet.
    ts = np.concatenate(([0.0], np.arange(10.0, 11.0, 0.01), [11.0, 20.0]))
    vs = np.concatenate(([0.0], np.arange(ts.size - 3) % 2 + 1, [3.0, 3.0]))
    lane = build_lane(_series(ts, vs, GEAR), LABELS)
    cut = pieces(lane, 0.0, 20.0, pixel=0.05)            # each fast change is < 2 px
    assert cut.busy.tolist() == [False, True, False]
    assert cut.starts[1] == pytest.approx(10.0)
    assert cut.ends[1] == pytest.approx(11.0)
    # Zoomed in, the same changes are drawn one by one.
    near = pieces(lane, 10.0, 10.1, pixel=0.0001)
    assert not near.busy.any() and near.index.size >= 10


def test_one_narrow_stretch_alone_stays_itself():
    lane = build_lane(_series([0, 10, 10.01, 20], [3, 2, 3, 3], GEAR), LABELS)
    cut = pieces(lane, 0.0, 20.0, pixel=0.05)
    assert cut.index.tolist() == [0, 1, 2] and not cut.busy.any()


def test_narrow_runs_apart_are_not_joined_across_a_wide_stretch():
    ts = [0, 1, 1.01, 1.02, 5, 9, 9.01, 9.02, 15]
    vs = [3, 1, 2, 1, 3, 1, 2, 1, 3]
    lane = build_lane(_series(ts, vs, GEAR), LABELS)
    cut = pieces(lane, 0.0, 15.0, pixel=0.05)
    assert cut.busy.tolist().count(True) == 2
    busy = list(zip(cut.starts[cut.busy].tolist(), cut.ends[cut.busy].tolist()))
    assert busy == [(1.0, 1.02), (9.0, 9.02)]
    # The wide stretches between them are drawn as themselves.
    assert lane.at(3.0) in cut.index.tolist() and lane.at(12.0) in cut.index.tolist()


def test_a_bit_draws_its_ones_and_leaves_its_zeros_empty():
    lane = build_lane(_series([0, 10, 12, 30, 31, 40], [0, 1, 0, 1, 0, 0]), BIT)
    cut = pieces(lane, 0.0, 40.0, pixel=0.01)
    assert [float(lane.values[i]) for i in cut.index.tolist()] == [1.0, 1.0]
    assert list(zip(cut.starts.tolist(), cut.ends.tolist())) == [(10, 12), (30, 31)]


def test_a_fast_toggling_bit_is_one_busy_block_and_narrow_zeros_alone_draw_nothing():
    ts = np.concatenate(([0.0], np.arange(10.0, 11.0, 0.01), [11.0, 20.0]))
    vs = np.concatenate(([0.0], np.arange(ts.size - 3) % 2, [0.0, 0.0]))
    lane = build_lane(_series(ts, vs), BIT)
    cut = pieces(lane, 0.0, 20.0, pixel=0.05)
    assert cut.busy.tolist() == [True]
    zeros = build_lane(_series([0, 1, 1.001, 1.002, 9], [1, 0, 0, 0, 1]), BIT)
    assert pieces(zeros, 0.0, 9.0, pixel=0.05).busy.sum() == 0
    # Two narrow 0s side by side, a missing sample between them: no 1, so
    # no busy block, and nothing drawn for them.
    split = build_lane(_series([0, 5, 5.001, 5.002, 5.003, 9, 12],
                               [1, 0, np.nan, 0, np.nan, 1, 1]), BIT)
    assert split.values.tolist() == [1, 0, 0, 1]
    cut = pieces(split, 0.0, 12.0, pixel=0.05)
    assert cut.busy.sum() == 0
    assert [float(split.values[i]) for i in cut.index.tolist()] == [1.0, 1.0]


def test_a_busy_block_never_spans_time_without_values():
    # Fast changes, then nothing for 4 s, then fast changes again.
    ts = [0, 5, 5.01, 5.02, 5.03, 9, 9.01, 9.02, 9.03, 15]
    vs = [3, 1, 2, np.nan, np.nan, 1, 2, 1, 3, 3]
    lane = build_lane(_series(ts, vs, GEAR), LABELS)
    cut = pieces(lane, 0.0, 15.0, pixel=0.05)
    busy = list(zip(cut.starts[cut.busy].tolist(), cut.ends[cut.busy].tolist()))
    assert busy == [(5.0, 5.02), (9.0, 9.03)]
