"""EOS capacity work precedes H2D and resumes only for its actual beneficiary."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_physical import ContextSessionAnchors, SessionH2DOpportunity
from tests.test_critical_remaining_capacity import capacity_fixture
from tests.test_join_confirmed_reentry import (
    client_submit, complete_join, continuing_join_fixture, parallel_join_fixture,
)
from tests.test_wait_prefetch_batch import provisional_join_batch_fixture


@pytest.mark.parametrize("snapshot", ("current", "foreign", "changed_capacity"))
def test_capacity_preflight_reuses_only_this_keys_current_free_list_observation(snapshot):
    with capacity_fixture() as state:
        anchors = ContextSessionAnchors(state.key, (), 1.)
        headroom = state.headroom
        if snapshot == "foreign":
            anchors = replace(anchors, key=replace(state.key, request_id="foreign"))
        elif snapshot == "changed_capacity":
            headroom = replace(headroom, device_full_free_tokens=0)
        planned = SessionH2DOpportunity(
            anchors, headroom, None, 0, 0, None,
            candidate=state.opportunity.candidate,
            checkpoint_plan=state.opportunity.checkpoint_plan,
        )
        state.runtime._reclaim_critical_parent_headroom(_planned_opportunity=planned)
        if snapshot == "current":
            state.planner.assert_not_called()
        else:
            state.planner.assert_called_once_with(
                state.key, include_checkpoint_plan=True, apply_capacity_budget=False,
            )
        state.current_budget.assert_called_once_with(
            include_evictable=True, headroom=state.headroom,
        )
        state.cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
            full_tokens=300, mamba_slots=2, exclude_node_ids=(1, 2),
            collect_diagnostics=True, collect_cold_full_candidates=True,
        )


@pytest.mark.parametrize("freed", (False, True))
def test_eos_preflights_before_selecting_h2d_and_replans_only_after_capacity_progress(freed):
    runtime, key, opportunity, cache, _ = provisional_join_batch_fixture()
    runtime._borrowable_parent_headroom = True
    runtime.enable_prepare_host = True
    ended = runtime._semantic_finished["child"][0] / 1000.
    try:
        with patch("time.monotonic", new=lambda: ended), patch.object(
            runtime, "_wait_prefetch_opportunity", return_value=opportunity,
        ) as inspect, patch.object(
            runtime, "_current_residency_budget",
            return_value=PrefetchResidencyBudget(1, 1_000_000, 4104, 2),
        ), patch.object(
            runtime, "_reclaim_critical_parent_headroom", return_value=freed,
        ) as reclaim:
            assert runtime._roll_final_stage() is opportunity
        reclaim.assert_called_once_with(_planned_opportunity=opportunity)
        assert inspect.call_count == 1 + int(freed)
        assert all(call.args == (key,) for call in inspect.call_args_list)
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("bound", (False, True))
@pytest.mark.parametrize("payload", ("full", "mamba", "capacity_only"))
def test_confirmed_partial_restore_keeps_its_ticket_and_capacity_demand_when_tail_is_blocked(
    bound, payload,
):
    runtime, _, opportunity, cache, _ = continuing_join_fixture()
    complete_join(runtime)
    if bound:
        runtime.on_events((client_submit(),))
    ticket = runtime._confirmed_join_tickets["join"]
    ticket.issued_nodes = 1
    runtime._critical_parent_demand = (ticket.key, 5000, 1, ticket.expires_at)
    blocked = replace(
        opportunity, step=None, steps=(), required_full_tokens=0,
        required_mamba_slots=0, fits_current_free_lists=None,
        no_step_reason="prefetch_capacity_budget",
        host_backed_full_missing_device_tokens=8 if payload == "full" else 0,
        host_backed_mamba_missing_device_nodes=1 if payload == "mamba" else 0,
    )
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=blocked):
            runtime.dispatch_join_prefetch()
        assert runtime._confirmed_join_tickets["join"] is ticket
        assert not ticket.restoration_complete
        assert runtime._live_critical_parent_demand() is not None
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("bound", (False, True))
def test_known_empty_tail_keeps_the_existing_retirement_and_bound_consumer_behavior(bound):
    runtime, _, opportunity, _, _ = continuing_join_fixture()
    complete_join(runtime)
    if bound:
        runtime.on_events((client_submit(),))
    ticket = runtime._confirmed_join_tickets["join"]
    ticket.issued_nodes = 1
    empty = replace(
        opportunity, step=None, steps=(), required_full_tokens=0, required_mamba_slots=0,
        fits_current_free_lists=None, no_step_reason="no_host_backed_missing_copy",
        host_backed_full_missing_device_tokens=0, host_backed_mamba_missing_device_nodes=0,
    )
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=empty):
            runtime.dispatch_join_prefetch()
        assert ("join" in runtime._confirmed_join_tickets) is bound
        if bound:
            assert ticket.restoration_complete
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("beneficiary", ("matching", "bound", "other", "expired", "generation"))
def test_merged_h2d_ack_rearms_only_one_live_matching_beneficiary(beneficiary):
    with patch("time.monotonic", new=lambda: 1000.):
        runtime, first, other, plans, cache, _ = parallel_join_fixture(provisional=True)
        runtime.enable_prepare_host = True
        runtime._continuing_joins.add("join")
        runtime._context_tokens[first.context_id] = (first.context_epoch, 17, 0, False)
        try:
            with patch.object(
                runtime, "inspect_context_h2d_opportunity",
                side_effect=lambda **kwargs: plans[kwargs["context_id"]],
            ):
                runtime.dispatch_join_prefetch()
            commands = runtime._join_ticket.command_ids
            assert len(commands) == 4
            target = other if beneficiary == "other" else (
                replace(first, session_generation=first.session_generation + 1)
                if beneficiary == "generation" else first
            )
            runtime._critical_parent_demand = (
                target, 5000, 1, 999. if beneficiary == "expired" else 1002.,
            )
            if beneficiary == "bound":
                complete_join(runtime)
                runtime.on_events((client_submit(),))
                def anchors(session, generation, **kwargs):
                    return (
                        plans[first.context_id].anchors.component_leaves
                        if (session, generation) == (first.session_id, first.session_generation)
                        else None
                    )
                cache.session_refs = NS(
                    snapshot_latest_session_leaf_anchors=anchors,
                    snapshot_session_leaf_anchors=anchors,
                )
            runtime._critical_reclaim_next_ms = 1_000_500.
            commits = tuple(
                NS(
                    command_id=command, anchor_node_id=index, published_node_ids=(index,),
                    num_tokens_by_pool=(("kv", 4),) + ((("mamba", 1),) if index == 4 else ()),
                    num_bytes=40 + (20 if index == 4 else 0),
                )
                for index, command in enumerate(commands, 1)
            )
            runtime.on_native_transfer_commit(NS(
                direction="h2d", status="completed", node_ids=(1, 2, 3, 4),
                num_tokens_by_pool=(("kv", 16), ("mamba", 1)), child_commits=commits,
            ))
            expected = beneficiary in ("matching", "bound")
            assert runtime._critical_reclaim_next_ms == (0. if expected else 1_000_500.)
            assert runtime.counts["critical_parent_capacity_rearmed_after_h2d"] == int(expected)
            assert runtime.counts["native_physical_completed"] == 4
            assert not runtime.physical_ledger.pending_count
            assert not runtime.physical_disabled
        finally:
            runtime.close()
