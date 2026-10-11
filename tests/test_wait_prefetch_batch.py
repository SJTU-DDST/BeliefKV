from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget, TransferStartWindow
from beliefkv.runtime.sglang_v0520_runtime import _ExecutionHandoffTicket
from beliefkv.runtime.sglang_v0520_physical import (
    ActionLocalPrefetchCandidate, ContextSessionAnchors, PrefetchLoadStep,
    SessionH2DOpportunity, inspect_session_h2d_opportunity, plan_prefetch_gpu_steps,
)
from beliefkv.runtime.sglang_v0520_observer import StaticPoolHeadroomObservation
from tests.test_sglang_v0520_physical import prefetch_node
from tests.test_sglang_v0520_runtime import event, final_stage_runtime, req
from tests.test_tool_predictive_transfers import tool_runtime


def batch_fixture(source, *, partial=False):
    if source == "tool_wait":
        runtime, hint = tool_runtime()
        key = hint.key
    else:
        runtime = final_stage_runtime()
        runtime.on_events((event(7, RuntimeEventKind.RETURN, invocation_id="child"),))
        key = runtime.context_sessions["ctx-parent"]
    anchors = ContextSessionAnchors(
        key, ((0, ((4, 5),)), (2, ((4, 5),))), 1., reusable_input_tokens=16,
    )
    nodes = (prefetch_node(0, None, 1, key_tokens=0),) + tuple(
        prefetch_node(n, n - 1, n + 1, full_host=4, mamba_host=n == 4, key_tokens=4)
        for n in range(1, 5)
    )
    candidate = ActionLocalPrefetchCandidate(anchors, nodes, 16, 1)
    steps = plan_prefetch_gpu_steps(candidate, max_steps=16)
    opportunity = SessionH2DOpportunity(
        anchors, StaticPoolHeadroomObservation(
            True, device_full_free_tokens=100, device_mamba_free_slots=4,
        ), steps[0], 16, 1, True, steps=steps, candidate=candidate,
    )
    reserved = []

    def native_burst(**kwargs):
        outcomes = []
        for node, created, state, command in kwargs["nodes"]:
            op = NS(
                beliefkv_command_id=command, node_ids=[node],
                host_indices=tuple(range(4)), device_indices=tuple(range(4)),
                pool_transfers=[NS(
                    name="mamba", indices_from_pool=None,
                    host_indices=(10,), device_indices=(11,),
                )] if state else None,
            )
            assert kwargs["beliefkv_before_enqueue"](op)
            reserved.append(command)
            if partial and node == 4:
                break
            outcomes.append(NS(issued=True, node_id=node, load_started=True))
        return tuple(outcomes)

    entries = {
        "kv": NS(host_pool=NS(size_per_token=10)),
        "mamba": NS(host_pool=NS(size_per_token=20)),
    }
    cache = NS(
        prefetch_gpu_session_nodes=Mock(side_effect=native_burst),
        cache_controller=NS(
            mem_pool_host=NS(entry_map=entries),
            _num_tokens_by_pool=lambda op: {
                "kv": len(op.host_indices), **({"mamba": 1} if op.pool_transfers else {}),
            },
            _transfer_num_bytes=lambda op: len(op.host_indices) * 10 + (
                20 if op.pool_transfers else 0
            ),
        ),
    )
    runtime.attach_native_cache(cache)
    return runtime, key, opportunity, cache, reserved


@pytest.mark.parametrize("source", ["join_ticket", "tool_wait"])
@pytest.mark.parametrize("partial", [False, True])
def test_wait_burst_restores_checkpoint_once_and_cancels_only_unsubmitted_tail(source, partial):
    runtime, key, opportunity, cache, reserved = batch_fixture(source, partial=partial)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity), \
        patch.object(runtime, "_h2d_start_window", return_value=TransferStartWindow(200., 0., 500.)):
        if source == "tool_wait":
            runtime._roll_tool_prefetch()
        commands = runtime._issue_wait_prefetch_steps(key, opportunity.steps, source=source)
    assert len(commands) == (3 if partial else 4)
    assert runtime.physical_ledger.pending_count == len(commands)
    assert not runtime.completed_physical_actions
    assert runtime.physical_ledger.pending_count > 2
    cache.prefetch_gpu_session_nodes.assert_called_once()
    assert [item[2] for item in cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"]] == [
        False, False, False, True,
    ]
    if partial:
        assert not runtime.physical_ledger.is_pending(reserved[-1])
        assert reserved[-1] not in runtime._prefetch_steps
        assert reserved[-1] not in runtime._prefetch_issue_budgets
    plan = next(row for row in records if row["event"] == "wait_prefetch_plan"
                and row["reason"].startswith("submitted"))
    assert plan["planned_pool_bytes"] == {"kv": 160, "mamba": 20}
    for index, command in enumerate(commands, 1):
        state = index == 4
        pool_units = (("kv", 4),) + ((("mamba", 1),) if state else ())
        runtime.on_native_transfer_commit(NS(
            direction="h2d", status="completed", node_ids=(index,),
            num_tokens_by_pool=pool_units,
            child_commits=(NS(
                command_id=command, anchor_node_id=index, published_node_ids=(index,),
                num_tokens_by_pool=pool_units,
                num_bytes=40 + 20 * state,
            ),),
        ))
    assert runtime.physical_ledger.pending_count == 0
    assert sum(action.num_bytes for action in runtime.completed_physical_actions) == (
        120 if partial else 180
    )


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("slots", [0, 1])
def test_handoff_burst_keeps_one_issue_allowance_and_cleans_unsubmitted_tail(partial, slots):
    runtime, key, opportunity, cache, reserved = batch_fixture("join_ticket", partial=partial)
    runtime.enable_execution_handoff = True
    request = req(key.request_id)
    request.session_id, request.session_generation = key.session_id, key.session_generation
    ticket = _ExecutionHandoffTicket(key, request, time.monotonic() + 2.)
    budget = PrefetchResidencyBudget(slots, 1024, source="native_next_prefill")
    with patch.object(runtime, "_current_residency_budget", return_value=budget):
        commands = runtime._issue_handoff_prefetch_steps(ticket, opportunity.steps)
    assert len(commands) == (3 if partial else 4)
    assert runtime.physical_ledger.pending_count == len(commands)
    assert runtime._prefetch_issue_budgets == dict.fromkeys(commands, budget)
    cache.prefetch_gpu_session_nodes.assert_called_once()
    if partial:
        assert not runtime.physical_ledger.is_pending(reserved[-1])
        assert reserved[-1] not in runtime._prefetch_steps
        assert reserved[-1] not in runtime._prefetch_issue_budgets
    runtime._discard_prefetch_tracking("test_completed")
    assert not runtime._prefetch_issue_budgets
    runtime.close()


def test_wait_plan_crops_by_total_capacity_and_bytes_while_preserving_prefill_reserve():
    runtime, key, opportunity, cache, _ = batch_fixture("join_ticket")
    constrained = replace(
        opportunity, headroom=replace(
            opportunity.headroom, device_full_free_tokens=4,
        ), fits_current_free_lists=False,
    )
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=constrained) as inspect, \
        patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
            1, 125, reserve_full_tokens=4, evictable_full_tokens=16,
        )):
        plan = runtime._wait_prefetch_opportunity(key)
    assert len(plan.steps) == 3
    assert plan.required_full_tokens == 12
    assert plan.required_mamba_slots == 0
    assert plan.fits_current_free_lists is False
    assert inspect.call_args.kwargs["max_steps"] == 16


def test_wait_plan_records_changed_capture_failure_inside_sampling_interval():
    runtime, key, opportunity, _, _ = batch_fixture("join_ticket")
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    failed = replace(
        opportunity, step=None, steps=(), candidate=None,
        blocked_detail="node ancestry exceeds bound",
    )
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.):
            runtime._record_wait_prefetch_plan("join_ticket", key, failed, reason="closure_unobservable")
            runtime._record_wait_prefetch_plan("join_ticket", key, failed, reason="closure_unobservable")
            runtime._record_wait_prefetch_plan(
                "join_ticket", key, replace(failed, blocked_detail="pending_load_transfer"),
                reason="closure_unobservable",
            )
        assert [row["blocked_detail"] for row in records] == [
            "node ancestry exceeds bound", "pending_load_transfer",
        ]
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_wait_batch_rechecks_causal_state_after_draining_events():
    runtime, key, opportunity, cache, _ = batch_fixture("join_ticket")
    runtime.event_server = NS(drain=Mock(side_effect=lambda **kwargs: runtime.on_events((
        event(8, RuntimeEventKind.CONTEXT_ADVANCE, invocation_id="parent",
              context_id="ctx-parent", context_epoch=1),
    ))))
    assert not runtime._issue_wait_prefetch_steps(key, opportunity.steps, source="join_ticket")
    cache.prefetch_gpu_session_nodes.assert_not_called()
    assert runtime.physical_ledger.pending_count == 0
    runtime.event_server = None


def test_cold_reclaim_excludes_every_node_in_the_target_prefix():
    runtime, key, opportunity, cache, _ = batch_fixture("join_ticket")
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={0: 16})
    with patch.object(runtime, "_publish_parent_pressure_candidates") as publish, \
        patch.object(runtime, "_wait_prefetch_opportunity", return_value=opportunity):
        refreshed = runtime._reclaim_wait_prefetch_capacity(
            key, replace(opportunity, fits_current_free_lists=False), source="join_ticket",
        )
    assert refreshed is opportunity
    publish.assert_called_once_with(exclude_node_ids=(0, 1, 2, 3, 4))
    cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
        full_tokens=16, mamba_slots=1, exclude_node_ids=(0, 1, 2, 3, 4),
    )


@pytest.mark.parametrize("source", ["join_ticket", "tool_wait"])
@pytest.mark.parametrize(
    "free_full,free_mamba,expected_nodes",
    [(10, 0, 2), (16, 0, 3), (20, 1, 4)],
)
def test_wait_reclaim_submits_only_the_prefix_that_fits_current_capacity(
    source, free_full, free_mamba, expected_nodes,
):
    runtime, key, opportunity, cache, reserved = batch_fixture(source)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})
    headroom = replace(
        opportunity.headroom, device_full_free_tokens=free_full,
        device_mamba_free_slots=free_mamba,
    )

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, opportunity.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs["fit_current_capacity"],
        )

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
            patch.object(runtime, "_publish_parent_pressure_candidates"), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(
                             1, 1024, evictable_full_tokens=16, evictable_mamba_slots=2,
                         )), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(200., 0., 500.)), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  return_value=NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )) as closure, \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                  return_value=headroom):
            if source == "tool_wait":
                runtime._roll_tool_prefetch()
            plan = runtime._reclaim_wait_prefetch_capacity(
                key, replace(opportunity, fits_current_free_lists=False), source=source,
            )
            assert plan.fits_current_free_lists is True
            assert len(plan.steps) == expected_nodes
            assert plan.required_full_tokens == 4 * expected_nodes
            assert plan.required_mamba_slots == int(expected_nodes == 4)
            before_enqueue = closure.call_count
            commands = runtime._issue_wait_prefetch_steps(key, plan.steps, source=source)
            assert closure.call_count == before_enqueue + 1
        assert len(commands) == expected_nodes
        assert runtime.physical_ledger.pending_count == expected_nodes
        assert reserved == list(commands)
        submitted = next(
            row for row in records
            if row["event"] == "wait_prefetch_plan" and row["reason"] == "submitted"
        )
        assert submitted["planned_pool_bytes"] == {
            "kv": 40 * expected_nodes, "mamba": 20 * int(expected_nodes == 4),
        }
        assert [item[2] for item in cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"]] == [
            node == 4 for node in range(1, expected_nodes + 1)
        ]
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ["join_ticket", "tool_wait"])
def test_wait_partial_prefetch_cannot_skip_an_ancestor_that_does_not_fit(source):
    runtime, key, opportunity, cache, _ = batch_fixture(source)
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})
    headroom = replace(
        opportunity.headroom, device_full_free_tokens=3, device_mamba_free_slots=1,
    )

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, opportunity.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs["fit_current_capacity"],
        )

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
            patch.object(runtime, "_publish_parent_pressure_candidates"), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(
                             1, 1024, evictable_full_tokens=16,
                         )), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  return_value=NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                  return_value=headroom):
            plan = runtime._reclaim_wait_prefetch_capacity(
                key, replace(opportunity, fits_current_free_lists=False), source=source,
            )
            assert plan.fits_current_free_lists is False
            assert plan.steps == opportunity.steps[:1]
            assert not runtime._issue_wait_prefetch_steps(key, plan.steps, source=source)
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_wait_partial_prefetch_revalidates_only_the_selected_payload_when_capacity_grows():
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    seen_payloads = []

    def window(plan):
        seen_payloads.append((plan.required_full_tokens, plan.required_mamba_slots))
        return TransferStartWindow(200., 0., 500.)

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, opportunity.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs["fit_current_capacity"],
        )

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(1, 1024)), \
            patch.object(runtime, "_h2d_start_window", side_effect=window), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  return_value=NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                  return_value=opportunity.headroom):
            runtime._roll_tool_prefetch()
            seen_payloads.clear()
            commands = runtime._issue_wait_prefetch_steps(
                key, opportunity.steps[:2], source="tool_wait",
            )
        assert len(commands) == 2
        assert seen_payloads == [(8, 0)]
        submitted = next(
            row for row in records
            if row["event"] == "wait_prefetch_plan" and row["reason"] == "submitted"
        )
        assert submitted["planned_pool_bytes"] == {"kv": 80, "mamba": 0}
        assert len(cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"]) == 2
    finally:
        runtime.close()


def test_tool_window_estimates_entire_prefix_payload():
    runtime, key, opportunity, _, _ = batch_fixture("tool_wait")
    with patch("beliefkv.runtime.sglang_v0520_runtime.estimate_native_service") as estimate:
        estimate.return_value = None
        assert runtime._h2d_start_window(opportunity) is None
    assert [call.args[1] for call in estimate.call_args_list] == [180, 180]


def test_join_batch_window_rechecks_live_work_progress_and_service_availability():
    runtime, key, opportunity, _, _ = batch_fixture("join_ticket")
    runtime._join_ticket.phase = "provisional"
    runtime._join_ticket.stage_bound = True
    runtime._join_ticket.work_endpoint_tokens = 128.
    runtime._final_stages["join"] = NS(
        key=key, request_id="child", generated_tokens=96., tokens_per_second=40.,
    )
    with patch.object(runtime, "_h2d_start_window",
                      return_value=TransferStartWindow(200., 0., 500.)), \
        patch.object(runtime, "_final_stage_service_available", return_value=True) as service, \
        patch.object(runtime, "_semantic_rate", return_value=40.):
        assert not runtime._wait_prefetch_timing_valid(key, opportunity, "join_ticket")
        runtime._semantic_progress["child"] = ((time.monotonic() * 1000., 124),)
        assert runtime._wait_prefetch_timing_valid(key, opportunity, "join_ticket")
        service.return_value = False
        assert not runtime._wait_prefetch_timing_valid(key, opportunity, "join_ticket")
        service.return_value = True
        runtime._semantic_progress["child"] = ((time.monotonic() * 1000., 129),)
        assert not runtime._wait_prefetch_timing_valid(key, opportunity, "join_ticket")
        runtime._semantic_eos_proofs["child"] = True
        runtime._semantic_finished["child"] = (time.monotonic() * 1000., 129)
        assert runtime._wait_prefetch_timing_valid(key, opportunity, "join_ticket")
