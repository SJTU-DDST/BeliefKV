"""A bounded reclaim must cover the checkpoint across native radix segments."""

import pytest
from types import SimpleNamespace as NS
from unittest.mock import patch

from tests.test_full_only_native_lifecycle import full_native
from tests.test_frontier_cold_reclaim import cold_frontier_fixture


@pytest.mark.parametrize("full_native", (128,), indirect=True)
@pytest.mark.parametrize("node_limit,segments", ((16, 16), (64, 26)))
def test_native_fragment_budget_can_release_pages_without_fitting_the_consumer(
    full_native, node_limit, segments,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _commits = full_native
    nodes = []
    for length in range(2, 61, 2):
        node, generation = insert_session(cache, list(range(1, length + 1)), "donor")
        full = node.component_data[0]
        assert len(full.value) == 2
        full.host_value = full.value.clone()
        cache.tree_core._update_duplicate_tracking(node)
        nodes.append(node)
    active, _ = insert_session(cache, list(range(1000, 1067)), "active")
    lock = cache.inc_lock_ref(active.id)
    cache.beliefkv_cold_full_owner_validator = (
        lambda owners: owners == (("donor", generation),)
    )
    anchors = cache.session_refs.snapshot_latest_session_leaf_anchors(
        "donor", generation,
    )
    try:
        assert allocator.available_size() == 1
        freed = cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=52, mamba_slots=0, max_nodes=node_limit,
            collect_diagnostics=True,
        )
        assert freed == {0: 2 * segments}
        assert allocator.available_size() == 1 + 2 * segments
        assert len(cache.beliefkv_handoff_last_victims) == segments
        assert cache.beliefkv_handoff_last_capacity["full_target_satisfied"] is (
            node_limit == 64
        )
        assert sum(node.component_data[0].value is None for node in nodes) == segments
        assert all(node.component_data[0].host_value is not None for node in nodes)
        assert active.component_data[0].value is not None
        assert active.component_data[0].lock_ref == 1
        assert cache.session_refs.snapshot_latest_session_leaf_anchors(
            "donor", generation,
        ) == anchors
        assert not writes and not loads
    finally:
        cache.dec_lock_ref(active.id, lock.to_dec_params())


@pytest.mark.parametrize("policy,node_limit", (("waiting", 16), ("selected_queue", 64)))
def test_actual_dispatch_scopes_the_larger_extent_budget_to_selected_queue(
    policy, node_limit,
):
    runtime, _key, request, cache, _reads, actions, _records, _reset, _free = (
        cold_frontier_fixture(reclaimed_tokens=512, reclaim_node_limit=node_limit)
    )
    runtime.frontier_cold_reclaim_policy = policy
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
        assert cache.reclaim_beliefkv_handoff_capacity.call_args.kwargs["max_nodes"] == node_limit
        assert actions and runtime._frontier_restore_consumer is not None
        assert runtime._prefetch_priority_normal_admissions == 4
        assert runtime._execution_handoff is None
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_larger_selected_extent_budget_preserves_global_probe_backoff():
    runtime, _key, request, cache, _reads, actions, _records, _reset, _free = (
        cold_frontier_fixture(reclaimed_tokens=0, reclaim_node_limit=64)
    )
    runtime.frontier_cold_reclaim_policy = "selected_queue"
    try:
        for now in (10., 10.051, 10.1, 10.151, 10.199):
            with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now):
                runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
        assert runtime._frontier_reclaim_after_ms == 10200.
        assert not actions and runtime._frontier_restore_consumer is None
        assert runtime._execution_handoff is None
        assert not runtime.physical_disabled
    finally:
        runtime.close()
