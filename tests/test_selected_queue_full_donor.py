"""Selected ordinary recovery may borrow only backed, unstarted queue tails."""

from array import array
from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_runtime import _ExecutionHandoffTicket
from tests.test_frontier_cold_reclaim import cold_frontier_fixture
from tests.test_queued_full_donor import queued_fixture
from tests.test_sglang_v0520_runtime import req
from tests.test_waiting_full_tail_backup import native_cache


def selected_fixture():
    runtime, key, donor, request, cache = queued_fixture()
    runtime._critical_parent_demand = None
    runtime._join_ticket = None
    runtime._confirmed_join_tickets.clear()
    runtime._eos_prefetch_keys.clear()
    runtime.enable_frontier_restore = runtime.enable_frontier_cold_reclaim = True
    runtime.enable_execution_handoff = False
    runtime.frontier_cold_reclaim_policy = "selected_queue"
    invocation = runtime.graph.invocations[key.invocation_id]
    invocation.state = InvocationState.RUNNING_LLM
    invocation.blocking_child_ids.clear()
    invocation.active_tool_calls.clear()
    selected = req(key.request_id)
    selected.beliefkv_metadata.update(
        root_workflow_id=key.root_workflow_id, invocation_id=key.invocation_id,
        context_id=key.context_id, context_epoch=key.context_epoch,
    )
    selected.session_id, selected.session_generation = key.session_id, key.session_generation
    selected.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    selected.origin_input_ids, selected.output_ids = [1, 2, 3, 4, 5], []
    selected.is_retracted = selected.retracted_stain = False
    runtime.visible[key.request_id] = key
    runtime._capacity_waiting_queue = [selected, request]
    runtime._execution_handoff = _ExecutionHandoffTicket(
        key, selected, time.monotonic() + 1.5,
    )
    return runtime, key, donor, selected, request, cache


def test_selected_authority_is_explicit_scoped_and_cannot_evict_its_consumer():
    runtime, key, donor, selected, request, cache = selected_fixture()
    original = cache.beliefkv_cold_full_owner_validator
    owners = ((donor.session_id, donor.session_generation),)
    try:
        assert not original(owners)
        runtime.frontier_cold_reclaim_policy = "waiting"
        with runtime._queued_full_donor_scope(key) as count:
            assert count == 0
            assert not cache.beliefkv_cold_full_owner_validator(owners)
        runtime.frontier_cold_reclaim_policy = "selected_queue"
        with runtime._queued_full_donor_scope(key) as count:
            assert count == 1
            assert cache.beliefkv_cold_full_owner_validator(owners)
            assert not cache.beliefkv_cold_full_owner_validator((
                *owners, (key.session_id, key.session_generation),
            ))
        assert cache.beliefkv_cold_full_owner_validator is original
        assert runtime.counts["frontier_restore_queued_donor_transactions"] == 1
        assert not runtime.physical_ledger.pending_count
        assert selected.kv.req_pool_idx is request.kv.req_pool_idx is None
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "selected_row", "selected_generated", "selected_removed", "selected_expired",
    "ticket_replaced", "selected_epoch", "eos", "join_ticket", "critical_demand",
    "donor_row", "donor_allocated", "donor_generated", "donor_removed",
    "donor_running", "donor_epoch", "no_slots",
))
def test_selected_capacity_authority_revalidates_consumer_and_donors(change):
    runtime, key, donor, selected, request, cache = selected_fixture()
    original = cache.beliefkv_cold_full_owner_validator
    owners = ((donor.session_id, donor.session_generation),)
    try:
        with runtime._queued_full_donor_scope(key) as count:
            assert count == 1
            if change == "selected_row":
                selected.kv.req_pool_idx = 0
            elif change == "selected_generated":
                selected.output_ids.append(42)
            elif change == "selected_removed":
                runtime._capacity_waiting_queue.remove(selected)
            elif change == "selected_expired":
                runtime._execution_handoff.expires_at = time.monotonic() - 1.
            elif change == "ticket_replaced":
                runtime._execution_handoff = _ExecutionHandoffTicket(key, selected, time.monotonic() + 1.)
            elif change == "selected_epoch":
                runtime.graph.contexts[key.context_id].epoch += 1
            elif change == "eos":
                runtime._eos_prefetch_keys["new-child"] = key
            elif change == "join_ticket":
                runtime._join_ticket = NS(key=key)
            elif change == "critical_demand":
                runtime._critical_parent_demand = (key, 1, 0, time.monotonic() + 1.)
            elif change == "donor_row":
                request.kv.req_pool_idx = 0
            elif change == "donor_allocated":
                request.kv.kv_allocated_len = 1
            elif change == "donor_generated":
                request.output_ids.append(42)
            elif change == "donor_removed":
                runtime._capacity_waiting_queue.remove(request)
            elif change == "donor_running":
                runtime._native_running_batch = NS(reqs=[request])
            elif change == "donor_epoch":
                runtime.graph.contexts[donor.context_id].epoch += 1
            else:
                runtime._residency_budget = replace(runtime._residency_budget, request_slots=0)
            assert not cache.beliefkv_cold_full_owner_validator(owners)
    finally:
        runtime._join_ticket = None
        runtime.close()


def test_selected_scope_respects_a_custom_native_validator_and_restores_after_failure():
    runtime, key, donor, _selected, _request, cache = selected_fixture()
    owners = ((donor.session_id, donor.session_generation),)
    original = cache.beliefkv_cold_full_owner_validator
    try:
        custom = Mock(return_value=False)
        cache.beliefkv_cold_full_owner_validator = custom
        with runtime._queued_full_donor_scope(key) as count:
            assert count == 0
            assert cache.beliefkv_cold_full_owner_validator is custom
        cache.beliefkv_cold_full_owner_validator = original
        with pytest.raises(RuntimeError, match="native failure"):
            with runtime._queued_full_donor_scope(key):
                assert cache.beliefkv_cold_full_owner_validator(owners)
                raise RuntimeError("native failure")
        assert cache.beliefkv_cold_full_owner_validator is original
    finally:
        runtime.close()


def test_selected_scope_does_not_inspect_beyond_sixteen_queue_tails():
    runtime, key, donor, selected, request, cache = selected_fixture()
    try:
        runtime._capacity_waiting_queue = [selected, *[req(f"plain-{i}") for i in range(16)], request]
        with runtime._queued_full_donor_scope(key) as count:
            assert count == 0
            assert not cache.beliefkv_cold_full_owner_validator((
                (donor.session_id, donor.session_generation),
            ))
    finally:
        runtime.close()


def test_selected_scope_preserves_raw_head_and_reordered_selected_head():
    runtime, key, donor, selected, request, cache = selected_fixture()
    owners = ((donor.session_id, donor.session_generation),)
    try:
        runtime._capacity_waiting_queue = [request, selected]
        with runtime._queued_full_donor_scope(key) as count:
            assert count == 0
            assert not cache.beliefkv_cold_full_owner_validator(owners)
    finally:
        runtime.close()


@pytest.mark.parametrize("guard", (
    "none", "full_lock", "mamba_lock", "pending_write", "pending_load",
))
def test_actual_native_selected_reclaim_keeps_host_state_and_native_guards(native_cache, guard):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool = native_cache
    runtime, key, donor, _selected, request, _cache = selected_fixture()
    request.origin_input_ids = array("q", (10, 11, 12, 13, 14))
    node, generation = insert_session(cache, [10, 11, 12, 13], donor.session_id)
    donor = replace(donor, session_generation=generation)
    request.session_generation = generation
    runtime.visible[request.rid] = runtime.context_sessions[donor.context_id] = donor
    runtime._context_session_changed(donor.context_id)
    full, state = node.component_data[0], node.component_data[2]
    full.host_value = full.value.clone()
    cache.tree_core._update_duplicate_tracking(node)
    cache.tree_core.set_hicache_enabled()
    cache.is_write_back = True
    cache.host_memory_mode = "cache"
    cache.beliefkv_join_pressure_candidates = ()
    cache.beliefkv_join_pressure_candidates_by_component = {0: (), 2: ()}
    runtime.attach_native_cache(cache)
    original = cache.beliefkv_cold_full_owner_validator
    before = allocator.available_size()
    state_value, state_free = state.value, pool.mamba_allocator.available_size()
    anchors = cache.session_refs.snapshot_latest_session_leaf_anchors(donor.session_id, generation)
    try:
        assert not cache.reclaim_beliefkv_handoff_capacity(full_tokens=before + 4, mamba_slots=0)
        if guard == "full_lock":
            full.lock_ref = 1
        elif guard == "mamba_lock":
            state.lock_ref = 1
        elif guard == "pending_write":
            node.write_through_pending_id = 99
        elif guard == "pending_load":
            node.load_back_pending_id = 99
        with runtime._queued_full_donor_scope(key) as count:
            assert count == 1
            freed = cache.reclaim_beliefkv_handoff_capacity(
                full_tokens=before + 4, mamba_slots=0, max_nodes=16,
            )
        assert freed.get(0, 0) == (4 if guard == "none" else 0)
        assert allocator.available_size() == before + (4 if guard == "none" else 0)
        assert full.host_value is not None
        assert state.value is state_value
        assert pool.mamba_allocator.available_size() == state_free
        assert cache.session_refs.snapshot_latest_session_leaf_anchors(
            donor.session_id, generation,
        ) == anchors
        assert cache.beliefkv_cold_full_owner_validator is original
        if guard == "none":
            assert cache.inspect_beliefkv_reentry(request)["missing_full_tokens"] == 4
    finally:
        full.lock_ref = state.lock_ref = 0
        node.write_through_pending_id = node.load_back_pending_id = None
        runtime.close()


@pytest.mark.parametrize("freed", (0, 128, 512))
def test_fitting_fragment_cannot_mask_incomplete_input_capacity(freed):
    runtime, _key, request, cache, _reads, actions, _records, _reset, _free = (
        cold_frontier_fixture(
            free_tokens=65, reclaimed_tokens=freed, reclaim_node_limit=64,
        )
    )
    runtime.frontier_cold_reclaim_policy = "selected_queue"
    try:
        runtime.dispatch_frontier_restore([request], running_batch=NS(reqs=[]))
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once()
        assert cache.reclaim_beliefkv_handoff_capacity.call_args.kwargs["full_tokens"] >= 513
        assert bool(actions) is (freed == 512)
        assert bool(runtime._frontier_restore_consumer) is (freed == 512)
        cache.split_beliefkv_prefetch_full_prefix.assert_not_called()
        assert not runtime.physical_disabled
        assert runtime._execution_handoff is None
    finally:
        runtime.close()


@pytest.mark.parametrize("setting", ("", "all", "true"))
def test_selected_reclaim_policy_rejects_unknown_values(monkeypatch, setting):
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime

    monkeypatch.setenv("BELIEFKV_FRONTIER_COLD_RECLAIM_POLICY", setting)
    with pytest.raises(ValueError, match="frontier cold reclaim policy"):
        NativeAdmissionRuntime()
