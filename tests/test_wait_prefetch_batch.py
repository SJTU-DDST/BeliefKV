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


def batch_fixture(source, *, partial=False, node_count=4, completed_join=True):
    if source == "tool_wait":
        runtime, hint = tool_runtime()
        key = hint.key
    else:
        runtime = final_stage_runtime()
        if completed_join:
            runtime.on_events((event(7, RuntimeEventKind.RETURN, invocation_id="child"),))
        key = runtime.context_sessions["ctx-parent"]
    anchors = ContextSessionAnchors(
        key, tuple((component, ((node_count, node_count + 1),)) for component in (0, 2)),
        1., reusable_input_tokens=4 * node_count,
    )
    nodes = (prefetch_node(0, None, 1, key_tokens=0),) + tuple(
        prefetch_node(
            n, n - 1, n + 1, full_host=4, mamba_host=n == node_count, key_tokens=4,
        )
        for n in range(1, node_count + 1)
    )
    candidate = ActionLocalPrefetchCandidate(anchors, nodes, 4 * node_count, 1)
    steps = plan_prefetch_gpu_steps(candidate)
    opportunity = SessionH2DOpportunity(
        anchors, StaticPoolHeadroomObservation(
            True, device_full_free_tokens=max(100, 4 * node_count + 4),
            device_mamba_free_slots=4,
        ), steps[0], 4 * node_count, 1, True, steps=steps, candidate=candidate,
    )
    reserved = []

    def native_burst(**kwargs):
        outcomes = []
        for node, created, state, command in kwargs["nodes"]:
            full_tokens = 0 if nodes[node].full_device_tokens else 4
            op = NS(
                beliefkv_command_id=command, node_ids=[node],
                host_indices=tuple(range(full_tokens)),
                device_indices=tuple(range(full_tokens)),
                pool_transfers=[NS(
                    name="mamba", indices_from_pool=None,
                    host_indices=(10,), device_indices=(11,),
                )] if state else None,
            )
            assert kwargs["beliefkv_before_enqueue"](op)
            reserved.append(command)
            if partial and node == node_count:
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


def provisional_join_batch_fixture():
    runtime, key, opportunity, cache, reserved = batch_fixture(
        "join_ticket", completed_join=False,
    )
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1, attributes={"request_id": "child"},
    ),))
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.register_visible_request(child)
    runtime._semantic_worker = NS(close=lambda: None)
    runtime._semantic_keys["child"] = runtime.visible["child"]
    runtime._semantic_eos_proofs["child"] = True
    runtime._semantic_finished["child"] = (time.monotonic() * 1000., 3)
    runtime._h2d_samples.extend(((180, 10.),) * 3)
    return runtime, key, opportunity, cache, reserved


def test_provisional_join_dispatch_reuses_current_plan_and_rechecks_before_enqueue():
    runtime, key, opportunity, cache, _ = provisional_join_batch_fixture()
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          return_value=opportunity) as inspect:
            runtime.dispatch_join_prefetch()
        assert inspect.call_count == 2
        assert runtime._join_ticket.phase == "provisional"
        assert runtime._join_ticket.key == key
        assert runtime.physical_ledger.pending_count == 4
        cache.prefetch_gpu_session_nodes.assert_called_once()
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["context_advanced", "plan_changed", "eos_expired"])
def test_reused_join_plan_cannot_bypass_causal_physical_or_time_revalidation(change):
    runtime, _, opportunity, cache, _ = provisional_join_batch_fixture()

    def drain(**kwargs):
        if change == "context_advanced":
            runtime.on_events((event(
                8, RuntimeEventKind.CONTEXT_ADVANCE, invocation_id="parent",
                context_id="ctx-parent", context_epoch=1,
            ),))
        elif change == "eos_expired":
            runtime._semantic_finished["child"] = (time.monotonic() * 1000. - 251., 3)

    runtime.event_server = NS(drain=drain, close=lambda: None)
    changed = replace(opportunity, step=None, steps=()) if change == "plan_changed" else opportunity
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          side_effect=[opportunity, changed]):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_join_dispatch_records_capacity_limited_empty_prefix():
    runtime, _, opportunity, _, _ = batch_fixture("join_ticket")
    empty = replace(
        opportunity, step=None, steps=(), required_full_tokens=0,
        required_mamba_slots=0, no_step_reason="prefetch_capacity_budget",
    )
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          return_value=opportunity), \
            patch.object(runtime, "_reclaim_wait_prefetch_capacity", return_value=empty):
            runtime.dispatch_join_prefetch()
        plan = next(row for row in records if row["event"] == "wait_prefetch_plan")
        assert plan["reason"] == "no_restore_plan"
        assert plan["no_step_reason"] == "prefetch_capacity_budget"
        assert plan["planned_nodes"] == []
        assert plan["planned_full_tokens"] == 0
    finally:
        runtime.close()


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
    assert inspect.call_args.kwargs["max_steps"] == runtime.physical_ledger.max_pending


@pytest.mark.parametrize("source", ["join_ticket", "tool_wait"])
@pytest.mark.parametrize(
    "node_count,pending_limit,byte_limit,expected_count",
    [(40, 64, 4096, 40), (80, 64, 4096, 64),
     (40, 23, 4096, 23), (40, 64, 810, 20)],
)
def test_fragmented_wait_restores_beyond_sixteen_nodes_within_live_budgets(
    source, node_count, pending_limit, byte_limit, expected_count,
):
    runtime, key, opportunity, cache, _ = batch_fixture(source, node_count=node_count)
    runtime.physical_ledger.max_pending = pending_limit
    budget = PrefetchResidencyBudget(1, byte_limit, reserve_full_tokens=4)

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, opportunity.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs["fit_current_capacity"],
        )

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
            patch.object(runtime, "_current_residency_budget", return_value=budget), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(200., 0., 500.)), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  return_value=NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                  return_value=opportunity.headroom):
            if source == "tool_wait":
                runtime._roll_tool_prefetch()
            plan = runtime._wait_prefetch_opportunity(key)
            commands = runtime._issue_wait_prefetch_steps(key, plan.steps, source=source)
        assert [step.node_id for step in plan.steps] == list(range(1, expected_count + 1))
        assert len(commands) == runtime.physical_ledger.pending_count == expected_count
        assert plan.required_full_tokens == 4 * expected_count
        assert plan.required_mamba_slots == int(expected_count == node_count)
        assert sum(step.include_mamba for step in plan.steps) == int(expected_count == node_count)
        cache.prefetch_gpu_session_nodes.assert_called_once()
        for command, step in zip(commands, plan.steps):
            units = (("kv", 4),) + ((("mamba", 1),) if step.include_mamba else ())
            runtime.on_native_transfer_commit(NS(
                direction="h2d", status="completed", node_ids=(step.node_id,),
                num_tokens_by_pool=units,
                child_commits=(NS(
                    command_id=command, anchor_node_id=step.node_id,
                    published_node_ids=(step.node_id,), num_tokens_by_pool=units,
                    num_bytes=40 + 20 * step.include_mamba,
                ),),
            ))
        assert runtime.physical_ledger.pending_count == 0
        assert sum(action.num_bytes for action in runtime.completed_physical_actions) <= byte_limit
    finally:
        runtime.close()


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
def test_wait_restore_keeps_native_reserve_from_planning_through_enqueue(source):
    runtime, key, opportunity, cache, _ = batch_fixture(source)
    headroom = [
        replace(opportunity.headroom, device_full_free_tokens=20, device_mamba_free_slots=1)
    ]
    budget = PrefetchResidencyBudget(
        1, 1024, reserve_full_tokens=8, reserve_mamba_slots=1,
        evictable_full_tokens=16, evictable_mamba_slots=1,
    )

    def reclaim(**kwargs):
        headroom[0] = replace(
            headroom[0], device_full_free_tokens=kwargs["full_tokens"],
            device_mamba_free_slots=kwargs["mamba_slots"],
        )
        return {0: 4, 2: 1}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=lambda **kwargs:
                          replace(opportunity, headroom=headroom[0])), \
            patch.object(runtime, "_current_residency_budget", return_value=budget), \
            patch.object(runtime, "_publish_parent_pressure_candidates"), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(200., 0., 500.)):
            if source == "tool_wait":
                runtime._roll_tool_prefetch()
            else:
                planned = runtime._wait_prefetch_opportunity(key)
                assert planned.required_full_tokens == 16
                assert planned.required_mamba_slots == 1
                assert planned.fits_current_free_lists is False
            plan = runtime._reclaim_wait_prefetch_capacity(
                key, runtime._wait_prefetch_opportunity(key), source=source,
            )
            commands = runtime._issue_wait_prefetch_steps(key, plan.steps, source=source)
        cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
            full_tokens=24, mamba_slots=2, exclude_node_ids=(0, 1, 2, 3, 4),
        )
        assert len(commands) == 4
        assert runtime.physical_ledger.pending_count == 4
        assert runtime.counts["prefetch_burst_plan_changed"] == 0
        assert headroom[0].device_full_free_tokens - plan.required_full_tokens == 8
        assert headroom[0].device_mamba_free_slots - plan.required_mamba_slots == 1
        assert next(
            row for row in records
            if row["event"] == "wait_prefetch_plan" and row["reason"] == "submitted"
        )["planned_pool_bytes"] == {"kv": 160, "mamba": 20}
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ["join_ticket", "tool_wait"])
@pytest.mark.parametrize(
    "free_full,free_mamba,expected_nodes",
    [(10, 0, 2), (16, 0, 4), (20, 1, 4)],
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
            assert plan.required_mamba_slots == int(expected_nodes == 4 and free_mamba > 0)
            assert plan.deferred_mamba_slots == int(expected_nodes == 4 and free_mamba == 0)
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
            "kv": 40 * expected_nodes, "mamba": 20 * plan.required_mamba_slots,
        }
        assert submitted["deferred_mamba_slots"] == plan.deferred_mamba_slots
        assert [item[2] for item in cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"]] == [
            node == 4 and free_mamba > 0 for node in range(1, expected_nodes + 1)
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
            assert plan.fits_current_free_lists is None
            assert plan.steps == ()
            assert plan.no_step_reason == "prefetch_capacity_budget"
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


@pytest.mark.parametrize("source", ["join_ticket", "tool_wait"])
def test_wait_full_first_restore_leaves_only_checkpoint_state_for_a_later_burst(source):
    runtime, key, opportunity, cache, _ = batch_fixture(source)
    headroom = [replace(
        opportunity.headroom, device_full_free_tokens=16, device_mamba_free_slots=0,
    )]

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, opportunity.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs["fit_current_capacity"],
        )

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(1, 1024)), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(200., 0., 500.)), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  side_effect=lambda *args, **kwargs: NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                  side_effect=lambda *args: headroom[0]):
            if source == "tool_wait":
                runtime._roll_tool_prefetch()
            plan = runtime._wait_prefetch_opportunity(key, fit_current_capacity=True)
            assert plan.required_full_tokens == 16
            assert plan.required_mamba_slots == 0
            assert plan.deferred_mamba_slots == 1
            commands = runtime._issue_wait_prefetch_steps(key, plan.steps, source=source)
            assert len(commands) == 4
            for command, step in zip(commands, plan.steps):
                runtime.on_native_transfer_commit(NS(
                    direction="h2d", status="completed", node_ids=(step.node_id,),
                    num_tokens_by_pool=(("kv", 4),),
                    child_commits=(NS(
                        command_id=command, anchor_node_id=step.node_id,
                        published_node_ids=(step.node_id,),
                        num_tokens_by_pool=(("kv", 4),), num_bytes=40,
                    ),),
                ))
                opportunity.candidate.nodes[step.node_id].full_device_tokens = 4
            assert runtime.physical_ledger.pending_count == 0
            assert not opportunity.candidate.nodes[-1].mamba_device_present
            blocked = runtime._wait_prefetch_opportunity(key, fit_current_capacity=True)
            assert blocked.steps == ()
            headroom[0] = replace(
                headroom[0], device_full_free_tokens=0, device_mamba_free_slots=1,
            )
            state_plan = runtime._wait_prefetch_opportunity(key, fit_current_capacity=True)
            assert len(state_plan.steps) == 1
            assert state_plan.step.node_id == 4 and state_plan.step.include_mamba
            assert state_plan.required_full_tokens == 0
            assert state_plan.required_mamba_slots == 1
            assert state_plan.deferred_mamba_slots == 0
            state_commands = runtime._issue_wait_prefetch_steps(
                key, state_plan.steps, source=source,
            )
            assert len(state_commands) == 1
            runtime.on_native_transfer_commit(NS(
                direction="h2d", status="completed", node_ids=(4,),
                num_tokens_by_pool=(("kv", 0), ("mamba", 1)),
                child_commits=(NS(
                    command_id=state_commands[0], anchor_node_id=4, published_node_ids=(4,),
                    num_tokens_by_pool=(("kv", 0), ("mamba", 1)), num_bytes=20,
                ),),
            ))
        assert cache.prefetch_gpu_session_nodes.call_count == 2
        assert runtime.physical_ledger.pending_count == 0
        assert sum(action.num_bytes for action in runtime.completed_physical_actions) == 180
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "free_mamba,byte_limit", [(0, 1024), (4, 160)],
)
def test_wait_full_only_payload_does_not_expand_when_state_capacity_or_byte_budget_grows(
    free_mamba, byte_limit,
):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    headroom = [replace(opportunity.headroom, device_mamba_free_slots=free_mamba)]
    budget = [PrefetchResidencyBudget(1, byte_limit)]
    seen_payloads, records = [], []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, opportunity.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs["fit_current_capacity"],
        )

    def window(plan):
        seen_payloads.append((plan.required_full_tokens, plan.required_mamba_slots))
        return TransferStartWindow(200., 0., 500.)

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
            patch.object(runtime, "_current_residency_budget", side_effect=lambda **kwargs: budget[0]), \
            patch.object(runtime, "_h2d_start_window", side_effect=window), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  return_value=NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                  side_effect=lambda *args: headroom[0]):
            runtime._roll_tool_prefetch()
            plan = runtime._wait_prefetch_opportunity(key)
            assert len(plan.steps) == 4
            assert not any(step.include_mamba for step in plan.steps)
            assert plan.deferred_mamba_slots == 1
            headroom[0] = opportunity.headroom
            budget[0] = PrefetchResidencyBudget(1, 1024)
            seen_payloads.clear()
            commands = runtime._issue_wait_prefetch_steps(key, plan.steps, source="tool_wait")
        assert len(commands) == 4
        assert seen_payloads == [(16, 0)]
        assert not any(
            item[2] for item in cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"]
        )
        submitted = next(
            row for row in records
            if row["event"] == "wait_prefetch_plan" and row["reason"] == "submitted"
        )
        assert submitted["planned_pool_bytes"] == {"kv": 160, "mamba": 0}
        assert submitted["deferred_mamba_slots"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["state_capacity_lost", "full_extent_already_resident"])
def test_wait_burst_rejects_state_mask_changes_that_cannot_preserve_selected_payload(change):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    headroom = replace(opportunity.headroom, device_mamba_free_slots=(
        0 if change == "state_capacity_lost" else 4
    ))
    selected = opportunity.steps
    if change == "full_extent_already_resident":
        selected = tuple(replace(step, include_mamba=False) for step in selected)
        opportunity.candidate.nodes[-1].full_device_tokens = 4

    def inspect(**kwargs):
        return inspect_session_h2d_opportunity(
            cache, opportunity.anchors, max_steps=kwargs["max_steps"],
            fit_current_capacity=kwargs["fit_current_capacity"],
        )

    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=inspect), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(1, 1024)), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(200., 0., 500.)), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
                  return_value=NS(
                      observable=True, nodes=tuple(reversed(opportunity.candidate.nodes)),
                  )), \
            patch("beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
                  return_value=headroom):
            runtime._roll_tool_prefetch()
            assert not runtime._issue_wait_prefetch_steps(key, selected, source="tool_wait")
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime.physical_disabled
        assert runtime.counts["prefetch_burst_plan_changed"] == 1
    finally:
        runtime.close()


def test_tool_window_estimates_entire_prefix_payload():
    runtime, key, opportunity, _, _ = batch_fixture("tool_wait")
    with patch("beliefkv.runtime.sglang_v0520_runtime.estimate_native_service") as estimate:
        estimate.return_value = None
        assert runtime._h2d_start_window(opportunity) is None
    assert [call.args[1] for call in estimate.call_args_list] == [180, 180]


@pytest.mark.parametrize("requires_reclaim", [False, True])
def test_uncertain_tool_window_reaches_reclaim_and_revalidated_batch_submission(
    requires_reclaim,
):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    hint = runtime.tool_wait_hints[key.context_id]
    runtime._h2d_samples.extend(((180, 15.),) * 3)
    budget = PrefetchResidencyBudget(
        1, 1024, reserve_full_tokens=8, reserve_mamba_slots=1,
        evictable_full_tokens=16, evictable_mamba_slots=1,
    )
    headroom = [replace(
        opportunity.headroom,
        device_full_free_tokens=20 if requires_reclaim else 24,
        device_mamba_free_slots=1 if requires_reclaim else 2,
    )]
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)

    def reclaim(**kwargs):
        headroom[0] = replace(
            headroom[0], device_full_free_tokens=kwargs["full_tokens"],
            device_mamba_free_slots=kwargs["mamba_slots"],
        )
        return {0: 4, 2: 1}

    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=reclaim)
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=hint.issued_monotonic_ms / 1000.), \
            patch.object(runtime, "inspect_context_h2d_opportunity", side_effect=lambda **kwargs:
                          replace(opportunity, headroom=headroom[0])), \
            patch.object(runtime, "_current_residency_budget", return_value=budget), \
            patch.object(runtime, "_publish_parent_pressure_candidates"):
            runtime.dispatch_tool_prefetch()
        assert cache.reclaim_beliefkv_handoff_capacity.call_count == int(requires_reclaim)
        if requires_reclaim:
            cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
                full_tokens=24, mamba_slots=2, exclude_node_ids=(0, 1, 2, 3, 4),
            )
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert runtime.physical_ledger.pending_count == 4
        assert headroom[0].device_full_free_tokens - 16 == budget.reserve_full_tokens
        assert headroom[0].device_mamba_free_slots - 1 == budget.reserve_mamba_slots
        window = next(row for row in records if row["event"] == "tool_prefetch_latest_start")
        assert window["remaining_p50_ms"] == 300.
        assert window["prediction_early_ms"] == 200.
        assert window["start_window_ms"] == 315.
        assert runtime.graph.invocations[key.invocation_id].state.value == "wait_tool"
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["forecast_narrowed", "tool_finished"])
def test_tool_uncertainty_cannot_bypass_final_forecast_or_causal_revalidation(change):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    hint = runtime.tool_wait_hints[key.context_id]
    runtime._h2d_samples.extend(((180, 15.),) * 3)

    def drain(**kwargs):
        if change == "forecast_narrowed":
            runtime.tool_wait_hints[key.context_id] = replace(
                hint, wait_p10_ms=300., wait_p50_ms=300., wait_p90_ms=300.,
            )
        else:
            runtime.on_events((event(
                7, RuntimeEventKind.TOOL_END, invocation_id=key.invocation_id,
                attributes={"tool_run_id": "long"},
            ),))

    runtime.event_server = NS(drain=drain, close=lambda: None)
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=hint.issued_monotonic_ms / 1000.), \
            patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
            runtime.dispatch_tool_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        if change == "forecast_narrowed":
            assert runtime.counts["wait_prefetch_batch_timing_changed"] == 1
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_tool_window_uses_surviving_cdf_and_join_keeps_its_service_window():
    runtime, key, opportunity, _, _ = batch_fixture("tool_wait")
    hint = replace(
        runtime.tool_wait_hints[key.context_id],
        release_cdf=((100., .1), (400., .5), (800., .9), (1000., 1.)),
    )
    runtime.tool_wait_hints[key.context_id] = hint
    runtime._h2d_samples.extend(((180, 15.),) * 3)
    now_ms = hint.issued_monotonic_ms + 200.
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=now_ms / 1000.):
            window = runtime._h2d_start_window(opportunity)
            center = hint.remaining_quantile(.5, now_ms=now_ms)
            lower = hint.remaining_quantile(.1, now_ms=now_ms)
        assert window.prediction_early_ms == pytest.approx(center - lower)
        assert window.horizon_ms == pytest.approx(min(500., 115. + center - lower))
        assert window.prediction_early_ms != hint.wait_p50_ms - hint.wait_p10_ms
    finally:
        runtime.close()
    runtime, _, opportunity, _, _ = batch_fixture("join_ticket")
    runtime._h2d_samples.extend(((180, 15.),) * 3)
    try:
        window = runtime._h2d_start_window(opportunity)
        assert window.horizon_ms == 115.
        assert window.prediction_early_ms == 0.
    finally:
        runtime.close()


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
