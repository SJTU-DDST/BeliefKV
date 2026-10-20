"""Capacity recovery needs the complete checkpoint, not a transfer subset."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation,
    PhysicalChildExpectation,
    PrefetchCheckpointPlan,
)
from tests.test_join_confirmed_reentry import continuing_join_fixture


@pytest.mark.parametrize("diagnostics", (False, True))
def test_checkpoint_only_keeps_the_whole_plan_without_transfer_budget_work(diagnostics):
    runtime, key, opportunity, cache, _ = continuing_join_fixture()
    whole = PrefetchCheckpointPlan(opportunity.steps, 9000, 3)
    opportunity = replace(opportunity, checkpoint_plan=whole)
    if diagnostics:
        runtime._opportunity_writer = NS(record=lambda _: None, close=lambda: None)
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ) as inspect, patch.object(
            runtime, "_current_residency_budget",
            side_effect=AssertionError("read-only checkpoint has no transfer budget"),
        ), patch.object(
            runtime, "_prefetch_byte_allowance",
            side_effect=AssertionError("no transfer subset is being selected"),
        ):
            result = runtime._wait_prefetch_opportunity(
                key, include_checkpoint_plan=True, apply_capacity_budget=False,
            )
        assert result is opportunity
        assert result.checkpoint_plan is whole
        assert (whole.full_tokens, whole.mamba_slots) == (9000, 3)
        inspect.assert_called_once_with(
            context_id=key.context_id, context_epoch=key.context_epoch,
            max_steps=runtime.physical_ledger.max_pending,
            fit_current_capacity=False, skip_empty_restore=True,
            include_checkpoint_plan=True,
        )
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert not runtime.physical_ledger.pending_count
        assert not runtime._critical_parent_demand
    finally:
        runtime.close()


@pytest.mark.parametrize("allowance", (0, 1024))
def test_h2d_planning_still_applies_live_capacity_and_byte_budgets(allowance):
    runtime, key, opportunity, cache, _ = continuing_join_fixture()
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ), patch.object(
            runtime, "_current_residency_budget", return_value=runtime._residency_budget,
        ) as budget, patch.object(
            runtime, "_prefetch_byte_allowance", return_value=allowance,
        ) as byte_budget:
            selected = runtime._wait_prefetch_opportunity(key, include_checkpoint_plan=True)
        budget.assert_called_once_with(include_evictable=True, headroom=opportunity.headroom)
        byte_budget.assert_called_once_with(runtime._residency_budget)
        assert selected is not opportunity
        assert bool(selected.steps) == bool(allowance)
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("state", ("missing", "no_step", "unobservable"))
def test_nonactionable_checkpoint_keeps_the_existing_no_budget_path(state):
    runtime, key, opportunity, cache, _ = continuing_join_fixture()
    if state == "missing":
        opportunity = None
    elif state == "no_step":
        opportunity = replace(opportunity, step=None, steps=())
    else:
        opportunity = replace(
            opportunity, headroom=replace(opportunity.headroom, observable=False),
        )
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ), patch.object(
            runtime, "_current_residency_budget",
            side_effect=AssertionError("nonactionable observation needs no budget"),
        ):
            assert runtime._wait_prefetch_opportunity(
                key, include_checkpoint_plan=True, apply_capacity_budget=False,
            ) is opportunity
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("apply_budget", (False, True))
def test_full_transaction_ledger_blocks_checkpoint_and_transfer_planning(apply_budget):
    runtime, key, _, cache, _ = continuing_join_fixture()
    runtime.physical_ledger.max_pending = 1
    runtime.register_physical_action(PhysicalActionExpectation(
        "pending", "PREFETCH_GPU", key.context_id, key.context_epoch,
        (PhysicalChildExpectation(90, (90,), (("kv", 10),), 100),),
        (("kv", 10),), session_id=key.session_id,
        session_generation=key.session_generation,
    ))
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity") as inspect:
            assert runtime._wait_prefetch_opportunity(
                key, include_checkpoint_plan=True, apply_capacity_budget=apply_budget,
            ) is None
        inspect.assert_not_called()
        assert runtime.physical_ledger.pending_count == 1
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()
