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


@pytest.mark.parametrize("source", ["join_ticket", "tool_wait"])
@pytest.mark.parametrize("running_count,free_full,expected_nodes", [
    (48, 100, 4), (47, 100, 0), (48, 64, 0),
])
def test_full_decode_batch_can_restore_one_waiting_context_without_a_request_row(
    source, running_count, free_full, expected_nodes,
):
    fixture = (
        provisional_join_batch_fixture()
        if source == "join_ticket" else batch_fixture(source)
    )
    runtime, _, opportunity, cache, _ = fixture
    cache.req_to_token_pool = NS(
        available_size=Mock(return_value=0), alloc=Mock(),
        mamba_pool=NS(size=8),
    )
    cache.host_pool_group = cache.cache_controller.mem_pool_host
    cache.token_to_kv_pool_allocator = NS(size=1024)
    cache.full_evictable_size = lambda: 0
    cache.mamba_evictable_size = lambda: 0
    runtime._observe_native_admission_capacity(
        running_batch=NS(reqs=[NS(rid=f"running-{n}") for n in range(running_count)]),
        adder=NS(
            max_running_requests=48, max_prefill_bs=4,
            page_size=1, rem_input_tokens=16,
        ),
    )
    headroom = replace(
        opportunity.headroom, device_full_free_tokens=free_full,
    )
    opportunity = replace(opportunity, headroom=headroom)
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=headroom,
        ), patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ), patch.object(
            runtime, "_h2d_start_window", return_value=TransferStartWindow(200., 0., 500.),
        ):
            if source == "tool_wait":
                runtime.dispatch_tool_prefetch()
            else:
                runtime.dispatch_join_prefetch()
        assert runtime.physical_ledger.pending_count == expected_nodes
        assert cache.prefetch_gpu_session_nodes.call_count == int(expected_nodes > 0)
        assert cache.req_to_token_pool.available_size() == 0
        cache.req_to_token_pool.alloc.assert_not_called()
        assert not runtime.physical_disabled
        if expected_nodes:
            assert runtime._residency_budget.request_slots == 1
            assert runtime._residency_budget.reserve_full_tokens == 65
            assert runtime._residency_budget.reserve_mamba_slots == 1
            assert runtime._residency_budget.byte_limit == 410
    finally:
        runtime.close()


def semantic_join_batch_fixture(*, center_tokens=48., lower_tokens=4., lead_ms=2000.):
    from beliefkv.runtime.semantic_report_worker import SemanticReportInput, SemanticReportReply

    runtime, key, opportunity, cache, reserved = provisional_join_batch_fixture()
    runtime._semantic_eos_proofs.clear()
    runtime._semantic_finished.clear()
    runtime._semantic_worker.error = ""
    runtime.semantic_work_statistic = "center"
    runtime.prefetch_lead_ms = lead_ms
    stage = runtime._final_stages["join"]
    stage.generated_tokens, stage.tokens_per_second = 120, 40.
    runtime._semantic_forecasts["child"] = SemanticReportReply(
        SemanticReportInput(
            runtime.visible["child"], time.monotonic() * 1000.,
            120, 256, "Evidence complete.", True, 500, 1, 2,
        ), .99, lower_tokens, center_tokens, max(center_tokens, 80.), 5.,
    )
    return runtime, key, opportunity, cache, reserved


@pytest.mark.parametrize("lead_ms,lower_tokens,expected_nodes", [
    (2000., 4., 4), (500., 4., 0), (2000., 48., 0),
])
def test_join_work_interval_reaches_final_batch_enqueue_with_residency_bound(
    lead_ms, lower_tokens, expected_nodes,
):
    runtime, _, opportunity, cache, _ = semantic_join_batch_fixture(
        lower_tokens=lower_tokens, lead_ms=lead_ms,
    )
    forecast = runtime._semantic_forecasts["child"]
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=forecast.observation.observed_ts_ms / 1000.), \
            patch.object(runtime, "inspect_context_h2d_opportunity",
                         return_value=opportunity) as inspect:
            runtime.dispatch_join_prefetch()
        assert runtime.physical_ledger.pending_count == expected_nodes
        assert cache.prefetch_gpu_session_nodes.call_count == int(expected_nodes > 0)
        assert not runtime.graph.joins["join"].satisfied
        if lead_ms < 1200.:
            inspect.assert_not_called()
            assert runtime.counts["semantic_h2d_too_early"] == 1
            assert not any(row["event"] == "wait_prefetch_plan" for row in records)
            return
        plan = next(row for row in records if row["event"] == "wait_prefetch_plan")
        assert plan["predicted_remaining_ms"] == 1200.
        assert plan["prediction_early_ms"] == (48. - lower_tokens) * 25.
        assert plan["start_window_ms"] == min(
            lead_ms, 110. + (48. - lower_tokens) * 25.,
        )
        if expected_nodes:
            assert runtime._join_ticket.work_endpoint_tokens == 168.
            latest = next(row for row in records if row["event"] == "final_stage_latest_start")
            assert latest["forecast_lower_tokens"] == lower_tokens
            assert latest["prediction_early_ms"] == 1100.
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["narrowed_interval", "tool_started", "descheduled"])
def test_join_work_uncertainty_is_rechecked_after_causal_drain(change):
    runtime, _, opportunity, cache, _ = semantic_join_batch_fixture()
    forecast = runtime._semantic_forecasts["child"]

    def drain(**kwargs):
        if change == "narrowed_interval":
            runtime._semantic_forecasts["child"] = replace(forecast, lower_tokens=48.)
        elif change == "tool_started":
            runtime.on_events((event(
                8, RuntimeEventKind.TOOL_START, invocation_id="child", context_id="ctx-child",
                attributes={"tool_run_id": "new"},
            ),))
        else:
            runtime._native_running_batch = NS(reqs=[NS(rid="other")])

    runtime.event_server = NS(drain=drain, close=lambda: None)
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=forecast.observation.observed_ts_ms / 1000.), \
            patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_join_enqueue_uses_new_forecast_after_decode_passes_selected_endpoint():
    runtime, _, opportunity, cache, _ = semantic_join_batch_fixture(center_tokens=8.)
    forecast = runtime._semantic_forecasts["child"]
    now = forecast.observation.observed_ts_ms / 1000.
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)

    def drain(**kwargs):
        runtime._semantic_progress["child"] = ((now * 1000., 129),)
        runtime._semantic_forecasts["child"] = replace(
            forecast,
            observation=replace(forecast.observation, observed_output_tokens=124),
            middle_tokens=16., lower_tokens=2.,
        )

    runtime.event_server = NS(drain=drain, close=lambda: None)
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now), \
            patch.object(runtime, "_semantic_rate", return_value=40.), \
            patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
            runtime.dispatch_join_prefetch()
        selected = next(row for row in records if row["event"] == "final_stage_latest_start")
        assert selected["generated_tokens"] + selected["projected_remaining_tokens"] == 128.
        assert runtime._join_ticket.work_endpoint_tokens == 140.
        assert runtime.counts["join_work_endpoint_refreshed"] == 1
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert runtime.physical_ledger.pending_count == 4
        assert not runtime.graph.joins["join"].satisfied
    finally:
        runtime.close()


@pytest.mark.parametrize("change", [
    "stale", "future", "low_score", "wrong_request", "wrong_epoch", "no_notice",
    "tool_eos", "work_exhausted", "later_completion", "narrowed_interval",
])
def test_selected_join_cannot_keep_old_endpoint_when_latest_forecast_invalidates_it(change):
    runtime, _, opportunity, cache, _ = semantic_join_batch_fixture(center_tokens=8.)
    forecast = runtime._semantic_forecasts["child"]
    now = forecast.observation.observed_ts_ms / 1000.

    def drain(**kwargs):
        observation = forecast.observation
        updates = {}
        if change == "stale":
            observation = replace(observation, observed_ts_ms=now * 1000. - 1501.)
        elif change == "future":
            observation = replace(observation, observed_ts_ms=now * 1000. + 1.)
        elif change == "low_score":
            updates["final_score"] = 0.
        elif change == "wrong_request":
            observation = replace(
                observation, key=replace(observation.key, request_id="other"),
            )
        elif change == "wrong_epoch":
            observation = replace(
                observation, key=replace(observation.key, context_epoch=2),
            )
        elif change == "no_notice":
            observation = replace(observation, notice_active=False)
        elif change == "tool_eos":
            runtime._semantic_eos_proofs["child"] = False
        elif change == "work_exhausted":
            updates.update(middle_tokens=0., upper_tokens=0.)
        elif change == "later_completion":
            updates.update(middle_tokens=200., upper_tokens=300.)
        else:
            updates.update(lower_tokens=40., middle_tokens=40.)
        runtime._semantic_forecasts["child"] = replace(
            forecast, observation=observation, **updates,
        )

    runtime.event_server = NS(drain=drain, close=lambda: None)
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now), \
            patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        assert runtime.counts["wait_prefetch_batch_timing_changed"] == 1
        assert runtime._join_ticket is None
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("remaining,upper,generated,endpoint", [
    (1., 2., 124, 125.), (4., 20., 129, 144.),
])
def test_join_latest_work_can_shorten_endpoint_or_use_live_upper_after_center_overrun(
    remaining, upper, generated, endpoint,
):
    runtime, key, opportunity, cache, _ = semantic_join_batch_fixture(center_tokens=8.)
    forecast = runtime._semantic_forecasts["child"]
    now = forecast.observation.observed_ts_ms / 1000.
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now), \
            patch.object(runtime, "_semantic_rate", return_value=40.), \
            patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
            runtime._roll_final_stage()
            assert runtime._join_ticket.work_endpoint_tokens == 128.
            runtime._semantic_progress["child"] = ((now * 1000., generated),)
            runtime._semantic_forecasts["child"] = replace(
                forecast, observation=replace(forecast.observation, observed_output_tokens=124),
                lower_tokens=0., middle_tokens=remaining, upper_tokens=upper,
            )
            assert runtime._wait_prefetch_timing_valid(key, opportunity, "join_ticket")
            assert runtime._join_ticket.work_endpoint_tokens == endpoint
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert runtime.physical_ledger.pending_count == 4
    finally:
        runtime.close()


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
            age = max(
                runtime.eos_protocol_window_ms, min(runtime.prefetch_lead_ms, 2000.),
            ) + 1.
            runtime._semantic_finished["child"] = (time.monotonic() * 1000. - age, 3)

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


@pytest.mark.parametrize("eos_age_ms,change,expected_nodes", [
    (300., None, 4), (750., None, 4), (1999., None, 4),
    (2001., None, 0), (750., "tool_started", 0),
    (750., "child_advanced", 0), (750., "parent_advanced", 0),
])
def test_observed_eos_survives_bounded_enqueue_delay_with_causal_revalidation(
    eos_age_ms, change, expected_nodes,
):
    runtime, _, opportunity, cache, _ = provisional_join_batch_fixture()
    runtime.prefetch_lead_ms = 2000.
    now = time.monotonic()
    runtime._semantic_finished["child"] = (now * 1000., 3)

    def drain(**kwargs):
        runtime._semantic_finished["child"] = (now * 1000. - eos_age_ms, 3)
        if change == "tool_started":
            runtime.on_events((event(
                8, RuntimeEventKind.TOOL_START, invocation_id="child",
                context_id="ctx-child", attributes={"tool_run_id": "new"},
            ),))
        elif change in ("child_advanced", "parent_advanced"):
            child = change == "child_advanced"
            runtime.on_events((event(
                8, RuntimeEventKind.CONTEXT_ADVANCE,
                invocation_id="child" if child else "parent",
                context_id="ctx-child" if child else "ctx-parent",
                context_epoch=2 if child else 1,
            ),))

    runtime.event_server = NS(drain=drain, close=lambda: None)
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now), \
            patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
            runtime.dispatch_join_prefetch()
        assert runtime.physical_ledger.pending_count == expected_nodes
        assert cache.prefetch_gpu_session_nodes.call_count == int(expected_nodes > 0)
        assert not runtime.graph.joins["join"].satisfied
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


def parallel_tool_batch_fixture(*, parallel=True, overlap=True, contexts=2):
    runtime, first_key, first, cache, _ = batch_fixture("tool_wait")
    runtime._parallel_wait_prefetch = parallel
    runtime._wait_prefetch_contexts = contexts
    cache.supports_beliefkv_overlap_prefetch = lambda: overlap
    other = req("other")
    other.session_id, other.session_generation = "other-session", 1
    runtime.register_visible_request(other)
    from tests.test_tool_predictive_transfers import event as tool_event
    runtime.on_events((
        tool_event(3, RuntimeEventKind.INVOCATION_CREATE, invocation_id="other",
                   context_id="ctx-other", agent_definition_id="child", agent_instance_id="other"),
        tool_event(4, RuntimeEventKind.TOOL_START, invocation_id="other",
                   attributes={"tool_family": "shell", "tool_run_id": "other-tool"}),
    ))
    key = runtime.context_sessions["ctx-other"]
    runtime.tool_wait_hints[key.context_id] = replace(
        runtime.tool_wait_hints[first_key.context_id], key=key,
        wait_p50_ms=350., invocation_revision_ts_ms=4.,
    )
    anchors = replace(
        first.anchors, key=key,
        component_leaves=tuple(
            (component, tuple((node + 20, created) for node, created in leaves))
            for component, leaves in first.anchors.component_leaves
        ),
    )
    candidate = replace(first.candidate, anchors=anchors, nodes=tuple(
        NS(**{
            **vars(node), "node_id": node.node_id + 20,
            "parent_id": node.parent_id + 20 if node.parent_id is not None else None,
        })
        for node in first.candidate.nodes
    ))
    steps = plan_prefetch_gpu_steps(candidate)
    second = replace(first, anchors=anchors, candidate=candidate, step=steps[0], steps=steps)
    plans = {first_key.context_id: first, key.context_id: second}
    issued = {}

    def native_burst(**kwargs):
        outcomes = []
        for node, created, state, command in kwargs["nodes"]:
            operation = NS(
                beliefkv_command_id=command, node_ids=[node],
                host_indices=(1, 2, 3, 4), device_indices=(5, 6, 7, 8),
                pool_transfers=[NS(
                    name="mamba", indices_from_pool=None,
                    host_indices=(10,), device_indices=(11,),
                )] if state else None,
            )
            if not kwargs["beliefkv_before_enqueue"](operation):
                break
            issued[command] = node, state
            outcomes.append(NS(issued=True, node_id=node, load_started=True))
        return tuple(outcomes)

    cache.prefetch_gpu_session_nodes = Mock(side_effect=native_burst)

    def acknowledge(commands):
        for command in commands:
            node, state = issued[command]
            units = (("kv", 4),) + ((("mamba", 1),) if state else ())
            runtime.on_native_transfer_commit(NS(
                direction="h2d", status="completed", node_ids=(node,),
                num_tokens_by_pool=units,
                child_commits=(NS(
                    command_id=command, anchor_node_id=node, published_node_ids=(node,),
                    num_tokens_by_pool=units, num_bytes=40 + 20 * state,
                ),),
            ))

    return runtime, first_key, key, plans, cache, acknowledge


@pytest.mark.parametrize("parallel,overlap,contexts,expected_contexts", (
    (False, True, 2, 1), (True, False, 2, 1),
    (True, True, 1, 1), (True, True, 2, 2),
))
def test_parallel_tool_restores_fill_available_context_slots_without_waiting_for_ack(
    parallel, overlap, contexts, expected_contexts,
):
    runtime, first, second, plans, cache, _ = parallel_tool_batch_fixture(
        parallel=parallel, overlap=overlap, contexts=contexts,
    )
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          side_effect=lambda **kwargs: plans[kwargs["context_id"]]), \
             patch.object(runtime, "_h2d_start_window",
                          return_value=TransferStartWindow(200., 0., 1000.)):
            runtime.dispatch_tool_prefetch()
            runtime.dispatch_tool_prefetch()
            runtime.dispatch_tool_prefetch()
        assert cache.prefetch_gpu_session_nodes.call_count == expected_contexts
        owners = set(runtime.physical_ledger.pending_h2d_contexts)
        assert owners == {
            (key.context_id, key.session_id, key.session_generation)
            for key in (first, second)[:expected_contexts]
        }
        assert runtime.physical_ledger.pending_count == 4 * expected_contexts
        assert runtime.counts["tool_prefetch_issued"] == 4 * expected_contexts
        assert runtime.counts["tool_prefetch_acked"] == 0
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("finish", ("out_of_order_ack", "expiry"))
def test_parallel_tool_tickets_keep_independent_ack_and_loss_accounting(finish):
    runtime, first, second, plans, _, acknowledge = parallel_tool_batch_fixture()
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity",
                          side_effect=lambda **kwargs: plans[kwargs["context_id"]]), \
             patch.object(runtime, "_h2d_start_window",
                          return_value=TransferStartWindow(200., 0., 1000.)):
            runtime.dispatch_tool_prefetch()
            runtime.dispatch_tool_prefetch()
        first_commands = tuple(
            command for command, (step, _, _) in runtime._prefetch_steps.items()
            if step.key == first
        )
        second_commands = tuple(
            command for command, (step, _, _) in runtime._prefetch_steps.items()
            if step.key == second
        )
        assert len(first_commands) == len(second_commands) == 4
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            if finish == "out_of_order_ack":
                acknowledge(second_commands)
                runtime.dispatch_tool_prefetch()
                assert runtime.counts["tool_prefetch_acked"] == 4
                assert runtime.physical_ledger.pending_count == 4
                assert first in runtime._tool_inflight_tickets
                acknowledge(first_commands)
            else:
                runtime.physical_ledger.max_age_s = 1e-9
                assert set(runtime.physical_ledger.expire()) == set(
                    first_commands + second_commands
                )
            runtime.dispatch_tool_prefetch()
            runtime.dispatch_tool_prefetch()
        assert not runtime._tool_inflight_tickets
        assert runtime.physical_ledger.pending_count == 0
        assert runtime.counts["tool_prefetch_acked"] == (
            8 if finish == "out_of_order_ack" else 0
        )
        assert runtime.counts["tool_prefetch_lost_ack"] == (0 if finish == "out_of_order_ack" else 2)
        assert not runtime.physical_disabled
    finally:
        runtime.close()


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


@pytest.mark.parametrize(
    "free_full,byte_limit,blocked",
    [(3, 1024, "prefetch_full_capacity_budget"),
     (100, 39, "prefetch_byte_capacity_budget")],
)
def test_zero_wait_plan_records_requested_payload_and_actual_capacity_reason(
    free_full, byte_limit, blocked,
):
    runtime, key, opportunity, _, _ = batch_fixture("join_ticket")
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    constrained = replace(
        opportunity, headroom=replace(
            opportunity.headroom, device_full_free_tokens=free_full,
        ), fits_current_free_lists=False,
    )
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=constrained), \
            patch.object(runtime, "_current_residency_budget",
                         return_value=PrefetchResidencyBudget(1, byte_limit)):
            plan = runtime._wait_prefetch_opportunity(key)
        runtime._record_wait_prefetch_plan("join_ticket", key, plan, reason="no_restore_plan")
        runtime._record_wait_prefetch_plan("join_ticket", key, plan, reason="no_restore_plan")
        assert len(records) == 1
        record = records[0]
        assert record["planned_full_tokens"] == 0
        assert record["no_step_reason"] == "prefetch_capacity_budget"
        assert record["blocked_detail"] == blocked
        assert record["capacity_budget"]["requested_full_tokens"] == 16
        assert record["capacity_budget"]["requested_mamba_slots"] == 1
        assert record["capacity_budget"]["full_limit_tokens"] == free_full
        assert record["capacity_budget"]["remaining_byte_limit"] == byte_limit
    finally:
        runtime.close()


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


def test_wait_plan_reports_remaining_full_and_state_without_implying_ready():
    runtime, key, opportunity, _, _ = batch_fixture("join_ticket")
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    partial = replace(
        opportunity, required_full_tokens=4, required_mamba_slots=0,
        host_backed_full_missing_device_tokens=16,
        host_backed_mamba_missing_device_nodes=1, unbacked_full_nodes=0,
        unbacked_mamba_leaves=0, steps=opportunity.steps[:1],
    )
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.):
            runtime._record_wait_prefetch_plan(
                "join_ticket", key, partial, reason="submitted",
            )
            runtime._record_wait_prefetch_plan(
                "join_ticket", key, partial, reason="submitted",
            )
            runtime._record_wait_prefetch_plan(
                "join_ticket", key,
                replace(partial, host_backed_full_missing_device_tokens=12),
                reason="submitted",
            )
            runtime._record_wait_prefetch_plan(
                "join_ticket", key,
                replace(partial, host_backed_full_missing_device_tokens=4),
                reason="submitted",
            )
            runtime._record_wait_prefetch_plan(
                "join_ticket", key,
                replace(partial, host_backed_full_missing_device_tokens=None),
                reason="submitted",
            )
        assert [row["unplanned_host_backed_full_tokens"] for row in records] == [12, 12, 8, 0, None]
        assert [row["restore_batch_scope"] for row in records] == [
            "partial_observed_host_backed_full", "partial_observed_host_backed_full",
            "partial_observed_host_backed_full",
            "all_observed_host_backed_full", "unobservable",
        ]
        assert all(row["host_backed_mamba_missing_device_nodes"] == 1 for row in records)
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_wait_batch_rechecks_causal_state_after_draining_events():
    runtime, key, opportunity, cache, _ = batch_fixture("join_ticket")
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    runtime.event_server = NS(drain=Mock(side_effect=lambda **kwargs: runtime.on_events((
        event(8, RuntimeEventKind.CONTEXT_ADVANCE, invocation_id="parent",
              context_id="ctx-parent", context_epoch=1),
    ))))
    assert not runtime._issue_wait_prefetch_steps(key, opportunity.steps, source="join_ticket")
    cache.prefetch_gpu_session_nodes.assert_not_called()
    assert runtime.physical_ledger.pending_count == 0
    runtime.event_server = None
    rejected = [
        row for row in records
        if row["event"] == "wait_prefetch_plan" and row["reason"] == "enqueue_causal_changed"
    ]
    assert len(rejected) == 1
    assert rejected[0]["join_id"] == "join"
    assert rejected[0]["context_epoch"] == key.context_epoch
    assert rejected[0]["planned_full_tokens"] is None
    runtime.close()


@pytest.mark.parametrize("source", ["join_ticket", "tool_wait"])
def test_wait_enqueue_rejection_reports_the_existing_fresh_capacity_plan(source):
    runtime, key, opportunity, cache, _ = batch_fixture(source)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    fresh = replace(
        opportunity, fits_current_free_lists=False,
        blocked_detail="prefetch_full_capacity_budget",
        capacity_budget={"full_limit_tokens": 8, "requested_full_tokens": 16},
    )
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity), \
            patch.object(runtime, "_h2d_start_window", return_value=TransferStartWindow(200., 0., 500.)):
            if source == "tool_wait":
                runtime._roll_tool_prefetch()
            with patch.object(runtime, "_wait_prefetch_opportunity", return_value=fresh) as capture:
                assert not runtime._issue_wait_prefetch_steps(key, opportunity.steps, source=source)
        capture.assert_called_once_with(key, max_steps=4, fit_current_capacity=True)
        rejected = [
            row for row in records
            if row["event"] == "wait_prefetch_plan" and row["reason"] == "enqueue_plan_changed"
        ]
        assert len(rejected) == 1
        assert rejected[0]["capacity_budget"] == fresh.capacity_budget
        assert rejected[0]["blocked_detail"] == "prefetch_full_capacity_budget"
        assert rejected[0]["join_id"] == ("join" if source == "join_ticket" else None)
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime.physical_disabled
    finally:
        runtime.close()


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
    runtime.enable_h2d_opportunity_sampling = True
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
            collect_diagnostics=True,
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
def test_wait_burst_keeps_missing_full_when_state_or_residency_changes(change):
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
            commands = runtime._issue_wait_prefetch_steps(key, selected, source="tool_wait")
        expected_nodes = (1, 2, 3, 4) if change == "state_capacity_lost" else (1, 2, 3)
        cache.prefetch_gpu_session_nodes.assert_called_once()
        issued = cache.prefetch_gpu_session_nodes.call_args.kwargs["nodes"]
        assert tuple(item[0] for item in issued) == expected_nodes
        assert not any(item[2] for item in issued)
        assert len(commands) == runtime.physical_ledger.pending_count == len(expected_nodes)
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
        assert runtime.counts["prefetch_burst_subset_revalidated"] == 1
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
    runtime.enable_h2d_opportunity_sampling = True
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
                collect_diagnostics=True,
            )
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert runtime.physical_ledger.pending_count == 4
        assert headroom[0].device_full_free_tokens - 16 == budget.reserve_full_tokens
        assert headroom[0].device_mamba_free_slots - 1 == budget.reserve_mamba_slots
        window = next(row for row in records if row["event"] == "tool_prefetch_latest_start")
        assert window["remaining_p50_ms"] == 300.
        assert window["prediction_early_ms"] == 200.
        assert window["start_window_ms"] == 565.
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
                hint, wait_p10_ms=600., wait_p50_ms=600., wait_p90_ms=600.,
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
        assert window.horizon_ms == pytest.approx(min(
            runtime.prefetch_lead_ms, 365. + center - lower,
        ))
        assert window.planning_slack_ms == 250.
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
    runtime, key, opportunity, _, _ = provisional_join_batch_fixture()
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
        runtime._roll_final_stage()
    assert runtime._join_ticket is not None
    runtime._semantic_eos_proofs.clear()
    runtime._semantic_finished.clear()
    runtime._semantic_worker = None
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
