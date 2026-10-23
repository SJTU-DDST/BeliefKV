from collections import OrderedDict
from copy import copy
from dataclasses import replace

import pytest

from beliefkv.runtime.subagent_retention import DormantSubagentRetention
from tests.test_subagent_retention import extent, remember


def decisions(manager):
    return (
        [(key, state.tier, state.probability, state.score, state.private_bytes)
         for key, state in manager.states.items()],
        dict(manager.counts), tuple(manager.work), manager.dirty,
    )


@pytest.mark.parametrize("policy", ("selective", "lru"))
@pytest.mark.parametrize("trained", (False, True))
@pytest.mark.parametrize("change", (
    "size", "tokens", "host", "device", "exclusive", "component", "generation",
    "node", "empty", "pending", "features", "budget",
))
def test_replanning_a_changed_snapshot_matches_fresh_metadata(
    monkeypatch, policy, trained, change,
):
    monkeypatch.setattr("time.monotonic", lambda: 100.)
    manager = DormantSubagentRetention(
        policy, host_budget_bytes=300, device_budget_bytes=150,
    )
    if trained:
        for value in (True, False) * 8:
            manager.estimator.observe({"unresolved": value}, value, predicted=.5)
    common = extent(1, exclusive=False)
    state = remember(
        manager, "a", returned_at=99.5, extents=(common, extent(2)),
        features={"unresolved": True, "reuse_count": 1},
    )
    remember(manager, "b", returned_at=99.75, extents=(common, extent(3)))
    manager._plan()
    manager.work.clear()
    changes = {
        "size": {"size_bytes": 700}, "tokens": {"units": 1024},
        "host": {"host": False}, "device": {"device": False},
        "exclusive": {"exclusive": False}, "component": {"component": 2},
        "generation": {"created": 20.}, "node": {"node_id": 20},
        "pending": {"pending": True},
    }
    if change in changes:
        state.extents = (common, replace(state.extents[1], **changes[change]))
    elif change == "empty":
        state.extents = ()
    elif change == "features":
        state.features["unresolved"] = False
    else:
        manager.host_budget_bytes = 150
    fresh = copy(manager)
    fresh.states = OrderedDict(
        (key, replace(item)) for key, item in manager.states.items()
    )
    fresh.work = manager.work.copy()
    fresh.counts = manager.counts.copy()
    manager._plan()
    fresh._plan()
    assert decisions(manager) == decisions(fresh)


def test_new_native_node_generation_stops_sharing_old_capacity():
    manager = DormantSubagentRetention("lru", host_budget_bytes=300)
    common = extent(1, exclusive=False)
    old = remember(manager, "old", extents=(common, extent(2)))
    new = remember(manager, "new", returned_at=2., extents=(common, extent(3)))
    manager._plan()
    assert old.tier == new.tier == "host"
    assert manager.counts["planned_unique_host_bytes"] == 300
    old.extents = (replace(common, created=2.), extent(2))
    manager._plan()
    assert new.tier == "host" and old.tier == "drop"
    assert manager.counts["planned_unique_host_bytes"] == 200


def test_physical_drop_releases_cached_snapshot_but_keeps_the_episode():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    manager = DormantSubagentRetention("selective", host_budget_bytes=100)
    for value in (True, False) * 8:
        manager.estimator.observe({}, value, predicted=.5)
    state = remember(manager)
    manager._plan()
    assert state.private_bytes == 100 and state._extent_geometry is not None
    runtime = NS(
        _opportunity_writer=None,
        _native_cache=NS(retire_beliefkv_child_session=Mock(return_value={
            "retry_anchors": (), "freed_device_units": {0: 4},
            "freed_host_units": {}, "discarded_nodes": ((1, 1.),), "reason": None,
        })),
    )
    manager._drop(runtime, state)
    assert state.extents == () and state.private_bytes == 0
    assert state._extent_geometry is None
    assert state.key.context_id in manager.states and state.discarded
