"""The same critical parent's later reclaim keeps scoped queued FULL donors."""

from array import array
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_physical import (
    PrefetchLoadStep, SessionH2DOpportunity,
)
from tests.test_queued_full_donor import queued_fixture
from tests.test_waiting_full_tail_backup import native_cache


@pytest.mark.parametrize("path", ("initial", "tail"))
@pytest.mark.parametrize("guard", (
    "eligible", "device_lock", "frontier", "no_demand", "other_beneficiary",
    "already_running", "no_host",
))
def test_later_critical_reclaim_uses_native_queue_guards(native_cache, path, guard):
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
    runtime._native_page_size = 1
    budget = PrefetchResidencyBudget(1, 1024**3, source="native_next_prefill")
    runtime._residency_budget = budget
    original = cache.beliefkv_cold_full_owner_validator
    before = allocator.available_size()
    mamba_free, mamba_value = pool.mamba_allocator.available_size(), state.value
    references = (full.session_ref, state.session_ref)
    first = PrefetchLoadStep(
        parent, leaf_node_id=10002, leaf_creation_time=2.,
        node_id=10001, creation_time=1., include_mamba=False,
    )
    second = PrefetchLoadStep(
        parent, leaf_node_id=10002, leaf_creation_time=2.,
        node_id=10002, creation_time=2., include_mamba=False,
    )
    opportunity = SessionH2DOpportunity(
        anchors=NS(key=parent), headroom=NS(observable=False), step=first,
        required_full_tokens=before + 4, required_mamba_slots=0,
        fits_current_free_lists=False, steps=(first,),
        candidate=NS(nodes=(
            NS(node_id=10001, full_host_tokens=4, full_device_tokens=0),
            NS(node_id=10002, full_host_tokens=before + 4, full_device_tokens=0),
        ), missing_full_device_tokens=before + 8),
        checkpoint_plan=NS(steps=(first, second)),
    )
    if guard == "device_lock":
        full.lock_ref = 1
    elif guard == "frontier":
        runtime._capacity_waiting_queue = [request]
    elif guard == "no_demand":
        runtime._critical_parent_demand = None
    elif guard == "other_beneficiary":
        runtime._critical_parent_demand = (
            donor, *runtime._critical_parent_demand[1:],
        )
    elif guard == "already_running":
        runtime._native_running_batch = NS(reqs=[request])
    elif guard == "no_host":
        full.host_value = None
        cache.tree_core._update_duplicate_tracking(node)
    try:
        with patch.object(runtime, "_current_residency_budget", return_value=budget), patch.object(
            runtime, "_wait_prefetch_opportunity", return_value=opportunity,
        ), patch.object(runtime, "_live_wait_prefetch", return_value=True), patch.object(
            runtime.physical_ledger, "is_pending", return_value=True,
        ):
            if path == "initial":
                runtime._reclaim_wait_prefetch_capacity(parent, opportunity, source="join_ticket")
            else:
                runtime._reclaim_wait_prefetch_tail_capacity(
                    parent, opportunity, (first,), source="join_ticket", commands=("pending",),
                )
        assert allocator.available_size() == before + (4 if guard == "eligible" else 0)
        assert (full.value is None) == (guard == "eligible")
        assert cache.beliefkv_cold_full_owner_validator == original
        assert state.value is mamba_value
        assert pool.mamba_allocator.available_size() == mamba_free
        assert (full.session_ref, state.session_ref) == references
        assert not runtime.physical_disabled
    finally:
        full.lock_ref = 0
        runtime.close()
