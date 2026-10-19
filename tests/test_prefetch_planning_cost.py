from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalReceiptError,
    PrefetchCheckpointPlan, inspect_session_h2d_opportunity,
    plan_prefetch_gpu_steps, prefetch_expectation_from_native_op,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime, _ToolPrefetchTicket
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture
from tests.test_sglang_v0520_physical import (
    native_prefetch, prefetch_anchors, prefetch_node,
)
from tests.test_wait_prefetch_batch import batch_fixture


@pytest.mark.parametrize("max_steps", (1, 2, 3, 16))
@pytest.mark.parametrize("fit_capacity", (False, True))
@pytest.mark.parametrize("complete_cost_requested", (False, True))
def test_only_exhausted_or_explicit_complete_plans_are_reused(
    max_steps, fit_capacity, complete_cost_requested,
):
    anchors = replace(prefetch_anchors(), reusable_input_tokens=12)
    nodes = (
        prefetch_node(12, 11, 5, full_host=4, mamba_host=True, key_tokens=4),
        prefetch_node(11, 0, 4, full_host=8, key_tokens=8),
        prefetch_node(0, None, 1, key_tokens=0),
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=NS(observable=True, nodes=nodes),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=NS(observable=True, device_full_free_tokens=20, device_mamba_free_slots=1),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.plan_prefetch_gpu_steps",
        wraps=plan_prefetch_gpu_steps,
    ) as planner:
        observed = inspect_session_h2d_opportunity(
            object(), anchors, max_steps=max_steps, fit_current_capacity=fit_capacity,
            include_checkpoint_plan=complete_cost_requested,
        )
    planner.assert_called_once()
    expected_complete = complete_cost_requested or (max_steps > 2 and not fit_capacity)
    assert (observed.checkpoint_plan is not None) is expected_complete
    assert [step.node_id for step in observed.steps] == [11, 12][:max_steps]
    if expected_complete:
        assert [step.node_id for step in observed.checkpoint_plan.steps] == [11, 12]
        assert observed.checkpoint_plan.full_tokens == 12
        assert observed.checkpoint_plan.mamba_slots == 1


def test_implicit_complete_plan_is_recaptured_after_residency_and_pending_changes():
    runtime, key, cache, *_ = dispatch_fixture(
        NativeAdmissionRuntime, source="join_ticket", depth=4,
    )
    try:
        first = runtime._wait_prefetch_opportunity(key)
        assert first.checkpoint_plan is not None
        ancestor = cache.tree_core.node_by_id(1)
        ancestor.component_data[0].value = ancestor.component_data[0].host_value
        second = runtime._wait_prefetch_opportunity(key)
        assert second.checkpoint_plan.full_tokens == first.checkpoint_plan.full_tokens - 128
        assert 1 not in {step.node_id for step in second.checkpoint_plan.steps}
        ancestor.load_back_pending_id = 1
        blocked = runtime._wait_prefetch_opportunity(key)
        assert blocked.step is None
        assert blocked.checkpoint_plan is None
    finally:
        runtime.close()


@pytest.mark.parametrize("cached_plan", (False, True))
@pytest.mark.parametrize("all_sent", (False, True))
def test_tail_reclaim_uses_only_the_same_complete_input_plan(cached_plan, all_sent):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    hint = runtime.tool_wait_hints[key.context_id]
    runtime._tool_ticket = _ToolPrefetchTicket(key, hint.invocation_revision_ts_ms)
    sent = opportunity.steps if all_sent else opportunity.steps[:2]
    if cached_plan:
        opportunity = replace(opportunity, checkpoint_plan=PrefetchCheckpointPlan(
            opportunity.steps, opportunity.required_full_tokens, opportunity.required_mamba_slots,
        ))
    runtime.physical_ledger.register(PhysicalActionExpectation(
        "pending", "PREFETCH_GPU", key.context_id, key.context_epoch,
        (PhysicalChildExpectation(1, (1,), (("kv", 40),), 40),),
        (("kv", 10),), session_id=key.session_id, session_generation=key.session_generation,
    ))
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.plan_prefetch_gpu_steps",
            side_effect=(
                AssertionError("duplicate complete-plan traversal") if cached_plan else None
            ), wraps=plan_prefetch_gpu_steps,
        ) as planner, patch.object(
            runtime, "_current_residency_budget",
            return_value=PrefetchResidencyBudget(
                2, 10000, reserve_full_tokens=1, reserve_mamba_slots=1,
            ),
        ), patch.object(runtime, "_publish_parent_pressure_candidates"):
            runtime._reclaim_wait_prefetch_tail_capacity(
                key, opportunity, sent, source="tool_wait", commands=("pending",),
            )
        assert planner.call_count == int(not cached_plan and not all_sent)
        assert runtime.physical_ledger.pending_count == 1
        assert not runtime.completed_physical_actions
        if all_sent:
            cache.reclaim_beliefkv_handoff_capacity.assert_not_called()
        else:
            cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
                full_tokens=9, mamba_slots=1, exclude_node_ids=(0, 1, 2, 3, 4),
            )
    finally:
        runtime.close()


@pytest.mark.parametrize("transfers", (None, []))
@pytest.mark.parametrize("change", (
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"kv": 2, "mamba": 0}),
    lambda op, ctrl: setattr(ctrl, "_num_tokens_by_pool", lambda _: {"kv": True}),
    lambda op, ctrl: setattr(ctrl, "_transfer_num_bytes", lambda _: 19),
    lambda op, ctrl: setattr(ctrl, "_transfer_num_bytes", lambda _: True),
    lambda op, ctrl: setattr(op, "device_indices", ()),
    lambda op, ctrl: setattr(op, "host_indices", None),
    lambda op, ctrl: setattr(ctrl.mem_pool_host.entry_map["kv"].host_pool, "size_per_token", 0),
    lambda op, ctrl: setattr(ctrl.mem_pool_host.entry_map["kv"].host_pool, "size_per_token", True),
    lambda op, ctrl: ctrl.mem_pool_host.entry_map.update({object(): NS()}),
))
def test_full_only_accounting_keeps_native_validation(transfers, change):
    step, operation, controller = native_prefetch(mamba=0, sidecar=None)
    operation.pool_transfers = transfers
    change(operation, controller)
    with pytest.raises(PhysicalReceiptError):
        prefetch_expectation_from_native_op("prefetch-1", step, operation, controller)
