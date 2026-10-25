"""Selected ordinary restore through real control paths and scalar DMA stand-ins."""

from copy import copy
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture
from tests.test_execution_frontier_restore import fixture


def cold_frontier_fixture(
    *, free_tokens=1, reclaimed_tokens=128, runtime_class=NativeAdmissionRuntime,
    reclaim_node_limit=16,
):
    runtime, key, cache, reads, actions, records, reset, _dispatch, _now = dispatch_fixture(
        runtime_class, source="join_ticket", depth=4, native_budget=True, running=0,
    )
    request = NS(
        rid=key.request_id, session_id=key.session_id, session_generation=key.session_generation,
        cache_request_handle=NS(attempt_id=key.attempt_id),
        beliefkv_metadata={
            "root_workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
            "context_id": key.context_id, "context_epoch": key.context_epoch,
        },
        origin_input_ids=tuple(range(513)), output_ids=(),
    )
    runtime.register_visible_request(request)
    invocation = runtime.graph.invocations[key.invocation_id]
    invocation.state = InvocationState.RUNNING_LLM
    invocation.blocking_child_ids.clear()
    runtime.enable_frontier_restore = runtime.enable_frontier_cold_reclaim = True
    runtime.enable_execution_handoff = runtime.enable_tool_prefetch = False
    runtime._join_ticket = None
    runtime._native_max_running = 24
    runtime._native_input_reserve = 0
    runtime._native_prefill_slots = 1
    runtime._prefetch_priority_normal_admissions = 4
    runtime.prefill_scheduling_policy = "join_priority"
    nodes = {index: cache.tree_core.node_by_id(index) for index in range(5)}
    cache.tree_core.node_by_id = nodes.__getitem__
    cache.tree_core.full_host_duplicates = {99: NS(id=99)}
    free = [free_tokens]
    cache.token_to_kv_pool_allocator.available_size = lambda: free[0]
    cache.req_to_token_pool.available_size = lambda: 1
    cache.req_to_token_pool.mamba_allocator.available_size = lambda: 1
    cache.inspect_beliefkv_reentry = Mock(side_effect=lambda _request: {
        "component_leaves": ((0, ((4, 5.),)), (2, ((4, 5.),))),
        "reusable_input_tokens": 512, "checkpoint_tokens": 512,
        "device_checkpoint_tokens": 0, "missing_full_tokens": 512,
        "missing_mamba_slots": 1,
    })

    def reclaim(**kwargs):
        assert kwargs["max_nodes"] == reclaim_node_limit
        assert set(kwargs["exclude_node_ids"]) == set(range(5))
        assert not kwargs.get("collect_cold_full_candidates")
        free[0] += reclaimed_tokens
        return {0: reclaimed_tokens} if reclaimed_tokens else {}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)

    def split(**kwargs):
        node = nodes[kwargs["node_id"]]
        count = kwargs["prefix_tokens"]
        assert 0 < count < len(node.key)
        fragment = copy(node)
        fragment.id = 100
        fragment.component_data = {
            component: copy(value) for component, value in node.component_data.items()
        }
        fragment.key = node.key[:count]
        fragment.component_data[0].host_value = node.component_data[0].host_value[:count]
        fragment.component_data[2].value = fragment.component_data[2].host_value = None
        node.key = node.key[count:]
        node.component_data[0].host_value = node.component_data[0].host_value[count:]
        node.parent = fragment
        nodes[fragment.id] = fragment
        return fragment.id, fragment.creation_time

    cache.split_beliefkv_prefetch_full_prefix = Mock(side_effect=split)
    return runtime, key, request, cache, reads, actions, records, reset, free


@pytest.mark.parametrize("reclaimed", (0, 64, 128))
def test_zero_budget_reclaim_refreshes_running_consumer_and_preserves_native_reserve(reclaimed):
    runtime, key, request, cache, _reads, actions, _records, _reset, free = (
        cold_frontier_fixture(reclaimed_tokens=reclaimed)
    )
    try:
        assert runtime._current_residency_budget().byte_limit == 0
        with patch.object(
            runtime, "_wait_prefetch_opportunity", wraps=runtime._wait_prefetch_opportunity,
        ) as refresh:
            runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        if not reclaimed:
            refresh.assert_not_called()
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
        assert runtime.counts["frontier_restore_cold_capacity_checks"] == 1
        assert runtime.physical_ledger.pending_count == int(reclaimed > 0)
        assert len(actions) == int(reclaimed > 0)
        assert not runtime.physical_disabled
        assert runtime._execution_handoff is None
        if reclaimed:
            assert runtime._frontier_restore_consumer.key == key
            assert runtime.physical_ledger.pending_action_bytes("PREFETCH_GPU") == (
                reclaimed * cache.host_pool_group.entry_map["kv"].host_pool.size_per_token
            )
            assert free[0] - reclaimed == runtime._current_residency_budget().reserve_full_tokens
            assert cache.split_beliefkv_prefetch_full_prefix.call_count == int(reclaimed < 128)
        else:
            assert runtime._frontier_restore_consumer is None
            cache.split_beliefkv_prefetch_full_prefix.assert_not_called()
    finally:
        runtime.close()


def test_fitting_partial_prefix_is_used_without_any_cold_reclaim():
    runtime, _key, request, cache, _reads, actions, _records, _reset, free = (
        cold_frontier_fixture(free_tokens=65)
    )
    cache.reclaim_beliefkv_handoff_capacity.side_effect = AssertionError("free prefix first")
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        cache.split_beliefkv_prefetch_full_prefix.assert_called_once()
        assert cache.split_beliefkv_prefetch_full_prefix.call_args.kwargs["prefix_tokens"] == 64
        assert runtime.physical_ledger.pending_count == len(actions) == 1
        assert free[0] - 64 == runtime._current_residency_budget().reserve_full_tokens
    finally:
        runtime.close()


def test_free_partial_reuses_the_same_call_plan_and_recaptures_only_after_native_split():
    runtime, _key, request, cache, _reads, actions, _records, _reset, _free = (
        cold_frontier_fixture(free_tokens=65)
    )
    original_split = cache.split_beliefkv_prefetch_full_prefix.side_effect
    order = []

    def split(**kwargs):
        order.append("native_split")
        return original_split(**kwargs)

    original_refresh = runtime._wait_prefetch_opportunity

    def refresh(*args, **kwargs):
        order.append("refresh")
        return original_refresh(*args, **kwargs)

    cache.split_beliefkv_prefetch_full_prefix.side_effect = split
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", side_effect=refresh):
            runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        assert order == ["native_split", "refresh"]
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        assert runtime.physical_ledger.pending_count == len(actions) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("eos", "epoch", "session", "split_declined"))
def test_reusing_the_free_plan_still_rechecks_native_split_and_enqueue_authority(change):
    runtime, key, request, cache, _reads, actions, _records, _reset, _free = (
        cold_frontier_fixture(free_tokens=65)
    )
    original_split = cache.split_beliefkv_prefetch_full_prefix.side_effect

    def split(**kwargs):
        if change == "split_declined":
            return None
        fragment = original_split(**kwargs)
        if change == "eos":
            runtime._eos_prefetch_keys["new-eos"] = key
        elif change == "epoch":
            runtime.graph.contexts[key.context_id].epoch += 1
        else:
            request.session_generation += 1
        return fragment

    cache.split_beliefkv_prefetch_full_prefix.side_effect = split
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        assert not actions and not runtime.physical_ledger.pending_count
        assert runtime._execution_handoff is runtime._frontier_restore_consumer is None
        assert not runtime.physical_disabled
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("sampling", (False, True))
def test_ordinary_reclaim_builds_native_diagnostics_only_when_explicitly_requested(sampling):
    runtime, _key, request, cache, _reads, _actions, _records, _reset, _free = (
        cold_frontier_fixture(reclaimed_tokens=0)
    )
    runtime.enable_h2d_opportunity_sampling = sampling
    runtime._borrowable_parent_headroom = True
    runtime._opportunity_writer = NS(record=lambda _record: None, close=lambda: None)
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
        assert cache.reclaim_beliefkv_handoff_capacity.call_args.kwargs.get(
            "collect_diagnostics", False,
        ) is sampling
    finally:
        runtime.close()


def test_failed_cold_probe_is_global_bounded_and_does_not_hold_native_admission():
    runtime, _key, request, cache, _reads, _actions, _records, _reset, _free = (
        cold_frontier_fixture(reclaimed_tokens=0)
    )
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.):
            runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        assert runtime._frontier_reclaim_after_ms == 10200.
        for now in (10.051, 10.1, 10.151, 10.199):
            runtime._execution_handoff_next_ms = 0.
            with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now):
                runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        assert cache.inspect_beliefkv_reentry.call_count == 1
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
        assert runtime._execution_handoff is runtime._frontier_restore_consumer is None
        assert not runtime.defer_prefill_for_prefetch(request)
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.25):
            runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        assert cache.reclaim_beliefkv_handoff_capacity.call_count == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("reason", ("disabled", "no_index", "empty_index", "unknown_index", "no_reclaim"))
def test_zero_capacity_without_an_eligible_probe_does_no_queue_or_native_inspection(monkeypatch, reason):
    runtime, _requests, cache = fixture(monkeypatch)
    runtime.enable_frontier_cold_reclaim = reason != "disabled"
    runtime._current_residency_budget.return_value = NS(request_slots=1, byte_limit=0)
    cache.tree_core = NS(full_host_duplicates={99: NS(id=99)})
    cache.reclaim_beliefkv_handoff_capacity = Mock()
    if reason == "no_index":
        del cache.tree_core.full_host_duplicates
    elif reason == "empty_index":
        cache.tree_core.full_host_duplicates = {}
    elif reason == "unknown_index":
        cache.tree_core.full_host_duplicates = Mock()
    elif reason == "no_reclaim":
        del cache.reclaim_beliefkv_handoff_capacity

    class UnreadQueue:
        def __len__(self):
            return 1

        def __iter__(self):
            raise AssertionError("empty capacity must retain the cheap negative path")

    try:
        runtime.dispatch_frontier_restore(UnreadQueue(), running_batch=None)
        runtime.plan_native_prefill.assert_not_called()
        cache.inspect_beliefkv_reentry.assert_not_called()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("head", ("resident", "backed_off", "untagged"))
def test_cold_candidate_does_not_restore_a_tail_instead_of_the_actual_selected_head(monkeypatch, head):
    from beliefkv.runtime.sglang_v0520_admission import NativePrefillPlan

    runtime, requests, cache = fixture(monkeypatch)
    runtime.enable_frontier_cold_reclaim = True
    runtime._current_residency_budget.return_value = NS(request_slots=3, byte_limit=1024)
    keys = tuple(runtime.visible[request.rid] for request in requests)
    runtime.plan_native_prefill.return_value = NativePrefillPlan(runtime.semantic_revision, keys)
    runtime._issue_handoff_prefetch_steps = Mock()
    if head == "resident":
        inspect = cache.inspect_beliefkv_reentry.side_effect
        cache.inspect_beliefkv_reentry.side_effect = lambda request: (
            {"missing_full_tokens": 0, "missing_mamba_slots": 0}
            if request is requests[0] else inspect(request)
        )
    elif head == "backed_off":
        runtime._frontier_probe_after_ms[keys[0]] = float("inf")
    else:
        requests = [NS(rid="plain", beliefkv_metadata=None), *requests]
    try:
        runtime.dispatch_frontier_restore(requests, running_batch=None)
        assert cache.inspect_beliefkv_reentry.call_count == int(head == "resident")
        runtime._issue_handoff_prefetch_steps.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("reason", ("eos", "critical_parent", "full_batch"))
def test_cold_probe_cannot_start_ahead_of_join_or_a_native_capacity_guard(reason):
    runtime, key, request, cache, _reads, actions, _records, _reset, _free = cold_frontier_fixture()
    if reason == "eos":
        runtime._eos_prefetch_keys["child-eos"] = key
    elif reason == "critical_parent":
        runtime._critical_parent_demand = ("critical", 1, 1, 1.)
    else:
        runtime._native_prefill_slots = 0
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        assert not actions and not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_eos_drained_after_cold_reclaim_prevents_ordinary_enqueue():
    runtime, key, request, cache, _reads, actions, _records, _reset, _free = cold_frontier_fixture()
    runtime.event_server = NS(
        drain=lambda **_kwargs: runtime._eos_prefetch_keys.update({"new-eos": key}),
        close=lambda: None,
    )
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
        assert not actions and not runtime.physical_ledger.pending_count
        assert runtime._execution_handoff is runtime._frontier_restore_consumer is None
    finally:
        runtime.close()


@pytest.mark.parametrize("value", ("true", "-1", "2"))
def test_frontier_cold_reclaim_rejects_unknown_settings(monkeypatch, value):
    monkeypatch.setenv("BELIEFKV_FRONTIER_COLD_RECLAIM", value)
    with pytest.raises(ValueError, match="frontier cold reclaim setting"):
        NativeAdmissionRuntime()
