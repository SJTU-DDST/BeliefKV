"""Critical headroom follows remaining checkpoint data and current growth reserve."""

from contextlib import ExitStack, contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_observer import StaticPoolHeadroomObservation
from beliefkv.runtime.sglang_v0520_physical import PrefetchCheckpointPlan
from tests.test_idle_full_prepare_pipeline import fixture


@contextmanager
def capacity_fixture(*, remaining=(100, 1), reserve=(200, 1), free=(250, 1),
                     source="native_next_prefill", checkpoint=True):
    runtime, cache, key = fixture()
    clock = [1000.]
    runtime._critical_parent_demand = (key, 5000, 10, 1002.)
    cache.beliefkv_handoff_last_capacity = {"full_target_satisfied": True}
    headroom = StaticPoolHeadroomObservation(
        True, device_full_free_tokens=free[0], device_mamba_free_slots=free[1],
        host_full_free_tokens=10000, host_mamba_free_slots=100,
    )
    opportunity = NS(
        candidate=NS(
            nodes=(NS(node_id=1), NS(node_id=2)),
            missing_full_device_tokens=99999,
        ),
        checkpoint_plan=PrefetchCheckpointPlan((), *remaining) if checkpoint else None,
        # The currently selectable subset/probe is not the complete checkpoint.
        required_full_tokens=1, required_mamba_slots=0, headroom=headroom,
    )
    budget = PrefetchResidencyBudget(
        1, 1024**2, reserve_full_tokens=reserve[0],
        reserve_mamba_slots=reserve[1], source=source,
    )
    try:
        with ExitStack() as stack:
            stack.enter_context(patch("time.monotonic", new=lambda: clock[0]))
            stack.enter_context(patch(
                "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
                return_value=headroom,
            ))
            planner = stack.enter_context(patch.object(
                runtime, "_wait_prefetch_opportunity", return_value=opportunity,
            ))
            current_budget = stack.enter_context(patch.object(
                runtime, "_current_residency_budget", return_value=budget,
            ))
            publication = stack.enter_context(patch.object(
                runtime, "_publish_parent_pressure_candidates",
            ))
            yield NS(
                runtime=runtime, cache=cache, key=key, clock=clock,
                planner=planner, current_budget=current_budget,
                publication=publication, budget=budget, headroom=headroom,
                opportunity=opportunity,
            )
    finally:
        runtime.close()


@pytest.mark.parametrize("remaining,reserve,expected", (
    ((100, 1), (200, 1), (300, 2)),
    ((6000, 1), (200, 1), (6200, 2)),
    ((100, 12), (200, 2), (300, 14)),
    ((0, 1), (200, 1), (200, 2)),
    ((100, 0), (200, 1), (300, 1)),
    ((0, 0), (300, 2), (300, 2)),
    ((4000, 9), (1000, 1), (5000, 10)),
))
def test_reclaim_uses_whole_safe_checkpoint_and_current_independent_reserves(
    remaining, reserve, expected,
):
    with capacity_fixture(remaining=remaining, reserve=reserve, free=(0, 0)) as state:
        state.runtime._reclaim_critical_parent_headroom()
        state.cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
            full_tokens=expected[0], mamba_slots=expected[1], exclude_node_ids=(1, 2),
            collect_diagnostics=True, collect_cold_full_candidates=True,
        )
        assert state.runtime._critical_parent_demand == (state.key, *expected, 1002.)
        state.planner.assert_called_once_with(
            state.key, include_checkpoint_plan=True, apply_capacity_budget=False,
        )
        assert state.runtime.counts["critical_parent_capacity_target_refreshed"] == int(
            expected != (5000, 10),
        )
        assert not state.runtime.physical_ledger.pending_count
        assert not state.runtime.completed_physical_actions


@pytest.mark.parametrize("free", ((300, 2), (1000, 20)))
def test_remaining_restore_already_fits_without_any_reclaim_or_emergency_copy(free):
    with capacity_fixture(free=free) as state:
        assert state.runtime._reclaim_critical_parent_headroom()
        assert state.runtime._critical_parent_demand == (state.key, 300, 2, 1002.)
        state.cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        state.cache.prepare_beliefkv_cold_full_capacity.assert_not_called()
        state.publication.assert_not_called()
        assert state.runtime.counts["critical_parent_capacity_refresh_satisfied"] == 1


@pytest.mark.parametrize("source,checkpoint", (
    ("legacy_capacity_unavailable", True),
    ("native_next_prefill", False),
))
def test_unobservable_reserve_or_checkpoint_keeps_the_previous_explicit_demand(source, checkpoint):
    with capacity_fixture(source=source, checkpoint=checkpoint) as state:
        original = state.runtime._critical_parent_demand
        state.runtime._reclaim_critical_parent_headroom()
        state.cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
            full_tokens=5000, mamba_slots=10, exclude_node_ids=(1, 2),
            collect_diagnostics=True, collect_cold_full_candidates=True,
        )
        assert state.runtime._critical_parent_demand == original
        assert not state.runtime.counts["critical_parent_capacity_target_refreshed"]
        if not checkpoint:
            state.current_budget.assert_not_called()


@pytest.mark.parametrize("change", ("expire", "different_parent"))
def test_a_changed_beneficiary_during_planning_is_not_overwritten_or_reclaimed(change):
    with capacity_fixture() as state:
        other = replace(
            state.key, request_id="other", invocation_id="other",
            context_id="ctx-other", session_id="other-session",
        )
        invocation = state.runtime.graph.invocations["other"]
        invocation.state = InvocationState.WAIT_JOIN
        state.runtime.context_sessions[other.context_id] = other
        replacement = (other, 7000, 4, 1002.)

        def budget(**kwargs):
            if change == "expire":
                state.clock[0] = 1003.
            else:
                state.runtime._critical_parent_demand = replacement
            return state.budget

        state.current_budget.side_effect = budget
        assert not state.runtime._reclaim_critical_parent_headroom()
        assert state.runtime._critical_parent_demand == (
            None if change == "expire" else replacement
        )
        state.cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        state.cache.prepare_beliefkv_cold_full_capacity.assert_not_called()
        assert not state.runtime.counts["critical_parent_capacity_target_refreshed"]


def test_already_satisfied_saved_target_keeps_the_existing_no_planning_fast_path():
    with capacity_fixture(free=(5000, 10)) as state:
        assert state.runtime._reclaim_critical_parent_headroom()
        state.planner.assert_not_called()
        state.current_budget.assert_not_called()
        state.cache.reclaim_beliefkv_handoff_capacity.assert_not_called()


def test_unobservable_headroom_does_not_start_checkpoint_or_budget_planning():
    with capacity_fixture() as state, patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(False),
    ):
        assert not state.runtime._reclaim_critical_parent_headroom()
        state.planner.assert_not_called()
        state.current_budget.assert_not_called()
        state.cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
