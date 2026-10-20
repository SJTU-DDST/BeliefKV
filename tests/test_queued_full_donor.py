"""Critical FULL capacity may use unstarted queue tails under native guards."""

from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from tests.test_causal_full_donor import fixture
from tests.test_sglang_v0520_runtime import req
from tests.test_waiting_full_tail_backup import native_cache


def queued_fixture():
    runtime, parent, donor = fixture()
    request = req(donor.request_id)
    request.beliefkv_metadata.update(
        root_workflow_id=donor.root_workflow_id, invocation_id=donor.invocation_id,
        context_id=donor.context_id, context_epoch=donor.context_epoch,
    )
    request.session_id, request.session_generation = donor.session_id, donor.session_generation
    request.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    request.origin_input_ids, request.output_ids = [10, 11, 12, 13, 14], []
    request.is_retracted = request.retracted_stain = False
    runtime.visible[donor.request_id] = donor
    runtime.graph.invocations[donor.invocation_id].state = InvocationState.RUNNING_LLM
    runtime._capacity_waiting_queue = [req("frontier"), request]
    runtime._residency_budget = PrefetchResidencyBudget(
        1, 1024**3, source="native_next_prefill",
    )
    runtime._set_critical_parent_demand(parent, 4000, 1, time.monotonic() + 10.)
    cache = NS(beliefkv_cold_full_owner_validator=runtime._live_cold_full_owners)
    runtime._native_cache = cache
    return runtime, parent, donor, request, cache


def test_queue_permission_exists_only_inside_a_critical_full_transaction():
    runtime, parent, donor, request, cache = queued_fixture()
    owners = ((donor.session_id, donor.session_generation),)
    original = cache.beliefkv_cold_full_owner_validator
    try:
        assert not original(owners)
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == 1
            assert cache.beliefkv_cold_full_owner_validator(owners)
            assert not runtime._live_parent_pressure_key(donor)
            assert not cache.beliefkv_cold_full_owner_validator((
                *owners, (parent.session_id, parent.session_generation),
            ))
        assert cache.beliefkv_cold_full_owner_validator is original
        assert not original(owners)
        assert request.kv.req_pool_idx is None
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()
        assert not runtime._capacity_waiting_queue


@pytest.mark.parametrize("change", (
    "row", "allocated", "committed", "retracted", "retracted_stain",
    "generated", "finished", "metadata_epoch", "native_generation",
    "visible_key", "context_epoch", "workflow_end", "ready", "active_tool",
    "active_child", "running_batch", "removed", "frontier", "missing_kv",
    "missing_output", "missing_row", "unknown_budget", "no_slots",
))
def test_queue_donor_revalidates_native_request_liveness_at_each_owner_check(change):
    runtime, parent, donor, request, cache = queued_fixture()
    owners = ((donor.session_id, donor.session_generation),)
    try:
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == 1
            if change == "row":
                request.kv.req_pool_idx = 0
            elif change == "allocated":
                request.kv.kv_allocated_len = 1
            elif change == "committed":
                request.kv.kv_committed_len = 1
            elif change == "retracted":
                request.is_retracted = True
            elif change == "retracted_stain":
                request.retracted_stain = True
            elif change == "generated":
                request.output_ids.append(42)
            elif change == "finished":
                request.finished = lambda: True
            elif change == "metadata_epoch":
                request.beliefkv_metadata["context_epoch"] += 1
            elif change == "native_generation":
                request.session_generation += 1
            elif change == "visible_key":
                runtime.visible.pop(request.rid)
            elif change == "context_epoch":
                runtime.graph.contexts[donor.context_id].epoch += 1
            elif change == "workflow_end":
                runtime.graph.workflows[donor.root_workflow_id].end_ts_ms = 500.
            elif change == "ready":
                runtime.graph.invocations[donor.invocation_id].state = InvocationState.READY
            elif change == "active_tool":
                runtime.graph.invocations[donor.invocation_id].active_tool_calls = {"shell"}
            elif change == "active_child":
                runtime.graph.invocations[donor.invocation_id].blocking_child_ids = {"child"}
            elif change == "running_batch":
                runtime._native_running_batch = NS(reqs=[request])
            elif change == "removed":
                runtime._capacity_waiting_queue = []
            elif change == "frontier":
                runtime._capacity_waiting_queue = [request]
            elif change == "missing_kv":
                request.kv = None
            elif change == "missing_output":
                del request.output_ids
            elif change == "missing_row":
                del request.kv.req_pool_idx
            elif change == "unknown_budget":
                runtime._residency_budget = replace(
                    runtime._residency_budget, source="legacy_capacity_unavailable",
                )
            else:
                runtime._residency_budget = replace(runtime._residency_budget, request_slots=0)
            assert not cache.beliefkv_cold_full_owner_validator(owners)
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("no_demand", "custom_validator", "protected_restore"))
def test_no_queue_override_without_the_runtime_authority_or_with_a_restore_lease(change):
    runtime, parent, donor, _, cache = queued_fixture()
    try:
        if change == "no_demand":
            runtime._critical_parent_demand = None
        elif change == "custom_validator":
            cache.beliefkv_cold_full_owner_validator = Mock(return_value=False)
        else:
            runtime._prefetch_service_leases["restore"] = NS(key=donor)
        original = cache.beliefkv_cold_full_owner_validator
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == 0
            assert cache.beliefkv_cold_full_owner_validator is original
    finally:
        runtime._prefetch_service_leases.clear()
        runtime.close()


@pytest.mark.parametrize("invalidate", ("expire", "beneficiary_changed", "exception"))
def test_scoped_queue_authority_expires_and_restores_the_original_validator(invalidate):
    runtime, parent, donor, _, cache = queued_fixture()
    original = cache.beliefkv_cold_full_owner_validator
    owners = ((donor.session_id, donor.session_generation),)
    try:
        if invalidate == "exception":
            with pytest.raises(RuntimeError, match="native failure"):
                with runtime._queued_full_donor_scope(parent):
                    raise RuntimeError("native failure")
        else:
            with runtime._queued_full_donor_scope(parent):
                if invalidate == "expire":
                    with patch("time.monotonic", return_value=time.monotonic() + 20.):
                        assert not cache.beliefkv_cold_full_owner_validator(owners)
                else:
                    runtime._critical_parent_demand = None
                    assert not cache.beliefkv_cold_full_owner_validator(owners)
        assert cache.beliefkv_cold_full_owner_validator is original
    finally:
        runtime.close()


@pytest.mark.parametrize("guard", ("none", "full_lock", "mamba_lock", "pending_write", "pending_load"))
def test_actual_native_full_reclaim_keeps_mamba_and_obeys_physical_guards(native_cache, guard):
    from array import array
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, pool = native_cache
    runtime, parent, donor, request, _ = queued_fixture()
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
    mamba_free, mamba_value = pool.mamba_allocator.available_size(), state.value
    references = (full.session_ref, state.session_ref)
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
        with runtime._queued_full_donor_scope(parent) as candidates:
            assert candidates == 1
            freed = cache.reclaim_beliefkv_handoff_capacity(full_tokens=before + 4, mamba_slots=0)
        assert cache.beliefkv_cold_full_owner_validator == original
        assert allocator.available_size() == before + (4 if guard == "none" else 0)
        assert (full.value is None) == (guard == "none")
        assert freed.get(0, 0) == (4 if guard == "none" else 0)
        assert state.value is mamba_value
        assert pool.mamba_allocator.available_size() == mamba_free
        assert (full.session_ref, state.session_ref) == references
        assert cache.session_refs.snapshot_latest_session_leaf_anchors(
            donor.session_id, generation,
        ) == anchors
        if guard == "none":
            observation = cache.inspect_beliefkv_reentry(request)
            assert observation["checkpoint_tokens"] == observation["missing_full_tokens"] == 4
            assert observation["missing_mamba_slots"] == 0
            match = cache.match_prefix(MatchPrefixParams(
                key=RadixKey(array("q", (10, 11, 12, 13))),
            ))
            assert match.host_hit_length == 4
            assert not len(match.device_indices)
    finally:
        full.lock_ref = state.lock_ref = 0
        node.write_through_pending_id = node.load_back_pending_id = None
        runtime.close()
