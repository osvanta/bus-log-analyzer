# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The bulk BLF/ASC load: frame grouping, channel counts, database parses."""
from __future__ import annotations

import numpy as np
import pytest

from core.bus_types import BusType
from core.raw_frame_store import FLAG_LIN
from gui.load_worker import _bulk_compute_store_stats, _group_frames


def _expected_groups(keys):
    order = np.argsort(keys, kind="stable")
    ordered = keys[order]
    bounds = np.concatenate(([0], np.flatnonzero(np.diff(ordered)) + 1, [len(keys)]))
    return order, bounds


@pytest.mark.parametrize("distinct", [1, 7, 700, 70_000])
def test_grouping_matches_a_stable_argsort(distinct):
    rng = np.random.default_rng(distinct)
    values = rng.choice(
        np.uint64(1 << 40) + rng.integers(0, 1 << 33, distinct, dtype=np.uint64),
        size=300_000,
    )
    # A few keys that appear once each, late in the log: the sample used to
    # find the distinct keys misses them.
    values[-5:] = np.arange(5, dtype=np.uint64) + np.uint64(3)

    order, bounds = _group_frames(values)
    expected_order, expected_bounds = _expected_groups(values)
    assert np.array_equal(order, expected_order)
    assert np.array_equal(bounds, expected_bounds)


def test_channel_counts_split_by_bus_and_count_unknown_channels():
    class Store:
        def __init__(self):
            self.channels = set()
            self.channel_frame_counts = {}
            self.first_frame_ids = []

    class Frames:
        channels = np.array([1, 1, 1, 2, 255, 255, 1], dtype=np.uint8)
        arb_ids = np.array([0x100, 0x20, 0x100, 0x200, 0x7, 0x8, 0x21], dtype=np.uint32)
        dlcs = np.full(7, 8, dtype=np.uint8)
        directions = np.zeros(7, dtype=np.uint8)
        flags = np.array([0, FLAG_LIN, 0, 1, 0, FLAG_LIN, FLAG_LIN], dtype=np.uint8)

        def __len__(self):
            return 7

    store = Store()
    _bulk_compute_store_stats(store, Frames())

    assert store.channel_frame_counts == {
        (BusType.CAN, 1): 2,
        (BusType.LIN, 1): 2,
        (BusType.CAN, 2): 1,
        None: 2,
    }
    assert store.channels == {(BusType.CAN, 1), (BusType.LIN, 1), (BusType.CAN, 2), None}
    assert store.total_frames == 7
    assert len(store.first_frame_ids) == 7


def test_a_blf_load_parses_its_database_once(blf_path, sample_dbc_path, monkeypatch):
    """The reader's parsed database is the one the channel map decodes with."""
    from core.channel_config import ChannelConfig
    from core.dbc_decoder import DBCDecoder
    from gui.load_worker import LoadWorker

    parses = []
    original = DBCDecoder.__init__

    def counting_init(self, *args, **kwargs):
        parses.append(args)
        original(self, *args, **kwargs)

    monkeypatch.setattr(DBCDecoder, "__init__", counting_init)
    worker = LoadWorker(blf_path, ChannelConfig.from_single_dbc(str(sample_dbc_path)))
    stores, failures = [], []
    worker.finished.connect(stores.append)
    worker.failed.connect(failures.append)
    worker.run()
    try:
        assert not failures
        assert len(parses) == 1
        assert stores[0].decoded_frames > 0
    finally:
        stores[0].raw_frame_store.close()
