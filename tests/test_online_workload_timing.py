from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import time

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.predictor.online_workload_timing import (
    OnlineReportWork, OnlineWorkloadToolTiming,
)
from beliefkv.runtime.context_lifecycle import (
    CompletionBudgetMiddleware, ContextLifecycleMiddleware, ContextLifecyclePolicy,
)
from beliefkv.runtime.sglang_v0520_observer import StaticPoolHeadroomObservation
from tests.test_sglang_v0520_runtime import event, final_stage_runtime, req


def tool_event(kind, *, start=0., call="one", invocation="agent", **attrs):
    return NS(
        kind=kind, workflow_id="workflow", invocation_id=invocation,
        ts_ms=start, attributes={
            "tool_run_id": call, "tool_name": "execute",
            "backend_class": "sandbox", "observed_command_shape": "pytest",
            **attrs,
        },
    )


def test_online_tools_learn_only_completed_causal_observations():
    history = OnlineWorkloadToolTiming()
    for index, duration in enumerate((1000., 2000., 3000., 4000.)):
        history.observe(tool_event(RuntimeEventKind.TOOL_START, call=str(index)))
        history.observe(tool_event(
            RuntimeEventKind.TOOL_END, call=str(index), start=duration,
            duration_ms=duration,
        ))
    history.observe(tool_event(RuntimeEventKind.TOOL_START, call="active", start=10_000.))
    estimate = history.estimate("workflow", "agent", now_ms=10_500.)
    assert estimate.scope == "workflow" and estimate.samples == 4
    assert estimate.p50_ms == 1500.
    from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
    from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
    key = PrefillCandidateKey("request", "workflow", "agent", "ctx", 0, 0)
    hint = NativeToolWaitHint(
        key, estimate.p10_ms, estimate.p50_ms, estimate.p90_ms,
        10_500., 12_500., "a" * 64, release_cdf=estimate.release_cdf,
    )
    assert hint.remaining_quantile(.5, now_ms=10_500.) == pytest.approx(1500.)
    assert hint.release_probability_within(1000., now_ms=10_500.) is not None
    assert all(tau > 0 for tau, _ in estimate.release_cdf)
    assert history.estimate("workflow", "agent", now_ms=15_000.) is None
    history.observe(tool_event(RuntimeEventKind.TOOL_START, call="parallel", start=10_000.))
    assert history.estimate("workflow", "agent", now_ms=10_500.) is None


def test_timeout_does_not_become_an_observed_completion():
    history = OnlineWorkloadToolTiming()
    for index in range(8):
        history.observe(tool_event(RuntimeEventKind.TOOL_START, call=str(index)))
        history.observe(tool_event(
            RuntimeEventKind.TOOL_END, call=str(index), start=180_000.,
            duration_ms=180_000., tool_error_class="timeout",
        ))
    history.observe(tool_event(RuntimeEventKind.TOOL_START, call="active"))
    assert history.estimate("workflow", "agent", now_ms=1000.) is None


def test_active_tool_index_preserves_duplicate_and_terminal_event_semantics():
    history = OnlineWorkloadToolTiming()

    def observe(item):
        history.observe(item)
        flattened = {
            identity: value for group in history._open_by_invocation.values()
            for identity, value in group.items()
        }
        assert flattened == history.open
        assert all(history._open_by_invocation.values())

    observe(tool_event(RuntimeEventKind.TOOL_START, call="one", invocation="a"))
    observe(tool_event(RuntimeEventKind.TOOL_START, call="one", invocation="a", start=500.))
    assert history.open[("workflow", "a", "one")][1] == 0.
    observe(tool_event(RuntimeEventKind.TOOL_START, call="two", invocation="b"))
    other = tool_event(RuntimeEventKind.TOOL_START, call="three", invocation="a")
    other.workflow_id = "other-workflow"
    observe(other)
    observe(tool_event(
        RuntimeEventKind.TOOL_END, call="two", invocation="b", duration_ms=float("nan"),
    ))
    observe(tool_event(RuntimeEventKind.TOOL_END, call="two", invocation="b"))
    observe(tool_event(RuntimeEventKind.RETURN, invocation="a"))
    assert set(history.open) == {("other-workflow", "a", "three")}
    observe(tool_event(RuntimeEventKind.TOOL_START, invocation="cancelled"))
    observe(tool_event(RuntimeEventKind.INVOCATION_CANCEL, invocation="cancelled"))
    observe(tool_event(RuntimeEventKind.TOOL_START, invocation="root"))
    observe(tool_event(RuntimeEventKind.TOOL_START, invocation="child"))
    observe(tool_event(RuntimeEventKind.WORKFLOW_END, invocation="root"))
    assert set(history._open_by_invocation) == {("other-workflow", "a")}


def test_report_work_adapts_without_using_current_future_length():
    work = OnlineReportWork()
    assert work.remaining(expected=100, generated=30) == 70.
    assert work.remaining(expected=100, generated=110) is None
    for actual in (200, 200, 300, 300):
        work.complete(expected=100, generated=actual)
    assert work.remaining(expected=100, generated=210) == 90.


def report_fixture(monkeypatch):
    monkeypatch.setenv("BELIEFKV_JOIN_PREFETCH_TRIGGER", "report_service_or_eos")
    monkeypatch.setenv("BELIEFKV_BORROWABLE_PARENT_HEADROOM", "1")
    runtime = final_stage_runtime()
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1, attributes={"request_id": "child"},
    ),))
    child = req("child")
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.register_visible_request(child)
    runtime._native_running_batch = NS(reqs=(child,))
    now_ms = time.monotonic() * 1000.
    runtime._semantic_progress["child"] = deque(((now_ms - 200., 96), (now_ms, 120)))
    stage = runtime._final_stages["join"]
    stage.generated_tokens = 120
    stage.last_service_at = now_ms / 1000.
    return runtime, child, stage


def test_report_service_can_trigger_before_eos_and_invalidates_on_tool(monkeypatch):
    runtime, child, stage = report_fixture(monkeypatch)
    try:
        plan = NS(
            step=NS(key=stage.key), required_full_tokens=100_000,
            required_mamba_slots=1,
        )
        window = NS(horizon_ms=500., service_ms=400.)
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), patch.object(
            runtime, "_h2d_start_window", return_value=window,
        ):
            assert runtime._roll_final_stage() is plan
        ticket = runtime._join_ticket
        assert ticket is not None and runtime._join_ticket_has_report_service(ticket)
        assert not runtime._join_ticket_has_native_eos(ticket)
        runtime._decoded_tool_requests.add("child")
        assert not runtime._join_ticket_has_report_service(ticket)
        assert runtime._report_remaining_ms(stage) is None
    finally:
        runtime.close()


def test_report_does_not_predict_while_waiting_for_service(monkeypatch):
    runtime, child, stage = report_fixture(monkeypatch)
    try:
        runtime._native_running_batch = NS(reqs=())
        assert runtime._report_remaining_ms(stage) is None
        assert runtime._roll_final_stage() is None
    finally:
        runtime.close()


def test_borrowed_capacity_includes_restore_and_native_service_reserve(monkeypatch):
    runtime, _, stage = report_fixture(monkeypatch)
    try:
        plan = NS(
            step=NS(key=stage.key), required_full_tokens=100_000,
            required_mamba_slots=1,
        )
        with patch.object(
            runtime, "_wait_prefetch_opportunity", return_value=plan,
        ), patch.object(
            runtime, "_h2d_start_window", return_value=NS(horizon_ms=500., service_ms=400.),
        ), patch.object(
            runtime, "_current_residency_budget",
            return_value=NS(reserve_full_tokens=4105, reserve_mamba_slots=2),
        ):
            assert runtime._roll_final_stage() is plan
        key, full, mamba, _ = runtime._critical_parent_demand
        assert key == stage.key and full == 104105 and mamba == 3
    finally:
        runtime.close()


def test_soft_headroom_has_no_reclaim_when_capacity_already_suffices(monkeypatch):
    runtime, _, stage = report_fixture(monkeypatch)
    try:
        runtime._critical_parent_demand = (stage.key, 100_000, 1, time.monotonic() + 1.)
        native = NS(reclaim_beliefkv_handoff_capacity=Mock())
        runtime._native_cache = native
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=StaticPoolHeadroomObservation(
                True, device_full_free_tokens=100_000, device_mamba_free_slots=1,
            ),
        ):
            runtime._reclaim_critical_parent_headroom()
        native.reclaim_beliefkv_handoff_capacity.assert_not_called()
    finally:
        runtime.close()


def test_ratio_compaction_counts_total_prompt_including_frozen_source(monkeypatch):
    policy = ContextLifecyclePolicy(
        window_tokens=262144, compression_trigger_ratio=.95,
    )
    monkeypatch.setattr(
        "beliefkv.runtime.context_lifecycle.context_token_counter",
        lambda _: lambda messages, **kwargs: sum(len(message.content) for message in messages),
    )
    from tests.test_context_lifecycle import _FakeModel
    from langchain_core.messages import HumanMessage, SystemMessage

    middleware = ContextLifecycleMiddleware(
        _FakeModel(), backend=NS(), policy=policy, compaction_sink=NS(),
    )
    assert policy.trigger_tokens == 249036
    assert middleware._count_tokens(
        [HumanMessage(content="x" * 100_000)],
        SystemMessage(content="s" * 150_000), None,
    ) >= policy.trigger_tokens


def test_95_percent_input_gets_a_completion_that_fits(monkeypatch):
    monkeypatch.setattr(
        "beliefkv.runtime.context_lifecycle.context_token_counter",
        lambda _: lambda *args, **kwargs: 249000,
    )
    middleware = CompletionBudgetMiddleware(
        intermediate_tokens=16384, final_tokens=16384,
        model_context_tokens=262144, adaptive_context_budget=True,
    )
    request = NS(
        model_settings={}, state={}, system_message=None, messages=[], tools=[],
        override=lambda **kwargs: kwargs,
    )
    assert middleware._request(request)["model_settings"]["max_tokens"] == 12888


def test_capacity_price_retains_safe_restore_when_free_list_plan_is_empty():
    from dataclasses import replace
    from tests.test_wait_prefetch_batch import batch_fixture
    runtime, key, opportunity, cache, _ = batch_fixture("join_ticket")
    try:
        blocked = replace(
            opportunity, step=None, steps=(),
            required_full_tokens=0, required_mamba_slots=0,
        )
        priced = runtime._report_restore_cost_plan(blocked)
        assert priced.required_full_tokens == opportunity.required_full_tokens
        assert priced.required_mamba_slots == opportunity.required_mamba_slots
        assert blocked.step is None
        cache.prefetch_gpu_session_nodes.assert_not_called()
    finally:
        runtime.close()


def test_critical_headroom_can_prepare_an_unhinted_tool_wait_but_not_active_llm():
    from beliefkv.control.causal_graph import InvocationState
    from tests.test_tool_predictive_transfers import tool_runtime
    runtime, hint = tool_runtime()
    runtime.tool_wait_hints.clear()
    runtime._native_cache = NS()
    demand = (NS(), 100_000, 1, time.monotonic() + 1.)
    captured = NS()
    try:
        with patch.object(runtime, "_live_critical_parent_demand", return_value=demand), patch.object(
            runtime, "capture_shadow_candidate", return_value=captured,
        ) as capture:
            assert runtime._refreshed_shadow_backup_candidate(
                context_id=hint.key.context_id,
            ) is captured
            runtime.graph.invocations[hint.key.invocation_id].state = InvocationState.RUNNING_LLM
            assert runtime._refreshed_shadow_backup_candidate(
                context_id=hint.key.context_id,
            ) is None
            assert capture.call_count == 1
    finally:
        runtime.close()
