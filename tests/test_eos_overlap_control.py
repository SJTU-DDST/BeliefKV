"""EOS restore eligibility and advisory work placement on the native pipeline."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_physical import expected
from tests.test_sglang_v0520_runtime import event
from tests.test_tool_predictive_transfers import tool_runtime
from tests.test_wait_prefetch_batch import provisional_join_batch_fixture


@pytest.mark.parametrize("service_history", ("empty", "unrelated", "unsupported_size"))
def test_live_eos_can_submit_without_a_transfer_duration_forecast(service_history):
    runtime, _, opportunity, cache, _ = provisional_join_batch_fixture()
    runtime._h2d_samples.clear()
    if service_history != "empty":
        runtime._h2d_samples.extend(((1, 10000.),) * 3)
    if service_history == "unrelated":
        runtime._native_service_samples.extend(NS(direction="d2h") for _ in range(8))
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ), patch.object(
            runtime, "_estimate_h2d_service",
            side_effect=AssertionError("observed EOS needs no service forecast"),
        ), patch.object(
            runtime, "_h2d_start_window",
            side_effect=AssertionError("no predicted start window after EOS"),
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert runtime.physical_ledger.pending_count == 4
        assert runtime._join_ticket.phase == "provisional"
        assert not runtime.graph.joins["join"].satisfied
        selected = next(row for row in records if row["event"] == "final_stage_latest_start")
        assert selected["service_support"] == "not_required_observed_eos"
        assert selected["h2d_ms"] is None
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("no_capacity", "no_payload", "expired", "tool"))
def test_eos_without_a_service_forecast_keeps_capacity_payload_and_causal_guards(change):
    runtime, _, opportunity, cache, _ = provisional_join_batch_fixture()
    runtime._h2d_samples.clear()
    if change == "no_capacity":
        opportunity = replace(
            opportunity, fits_current_free_lists=False,
            headroom=replace(
                opportunity.headroom, device_full_free_tokens=0, device_mamba_free_slots=0,
            ),
        )
    elif change == "no_payload":
        opportunity = replace(
            opportunity, step=None, steps=(),
            required_full_tokens=0, required_mamba_slots=0,
        )
    elif change == "expired":
        ended_ms, output_tokens = runtime._semantic_finished["child"]
        runtime._semantic_finished["child"] = (
            ended_ms - runtime.eos_prefetch_evidence_window_ms - 1., output_tokens,
        )
    else:
        runtime.on_events((event(
            8, RuntimeEventKind.TOOL_START, invocation_id="child",
            attributes={"tool_run_id": "new"},
        ),))
    try:
        with patch.object(
            runtime, "inspect_context_h2d_opportunity", return_value=opportunity,
        ):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert not runtime.physical_ledger.pending_count
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("deferred", (False, True))
def test_advisory_worker_and_wait_queries_run_once_on_the_selected_side_of_forward(deferred):
    runtime, hint = tool_runtime()
    runtime.enable_tool_prefetch = False
    runtime.tool_wait_hints.clear()
    calls = []
    runtime._model_worker = NS(
        poll=lambda: calls.append("poll") or (hint,),
        disabled=False, close=lambda: None,
    )
    runtime._tool_timing_only = True
    runtime._submit_tool_wait = lambda context: calls.append(("query", context))
    runtime.event_server = NS(
        drain=lambda **kwargs: calls.append("events"), close=lambda: None,
    )
    try:
        runtime.scheduler_step(defer_observations=True, defer_wait_predictions=deferred)
        assert calls == (["events"] if deferred else ["events", "poll", ("query", "ctx-tool")])
        assert bool(runtime.tool_wait_hints) is not deferred
        runtime.dispatch_after_forward()
        assert calls == ["events", "poll", ("query", "ctx-tool")]
        assert runtime.tool_wait_hints["ctx-tool"] is hint
        runtime.dispatch_after_forward()
        assert calls == ["events", "poll", ("query", "ctx-tool")]
        assert not runtime._wait_prediction_maintenance_deferred
    finally:
        runtime.close()


def test_deferred_wait_forecast_does_not_delay_tool_return_or_accept_its_stale_reply():
    runtime, hint = tool_runtime()
    runtime.enable_tool_prefetch = False
    runtime.tool_wait_hints.clear()
    runtime._tool_timing_only = True
    polls = Mock(return_value=(hint,))
    runtime._model_worker = NS(poll=polls, disabled=False, close=lambda: None)
    runtime.event_server = NS(
        drain=lambda **kwargs: runtime.on_events((event(
            8, RuntimeEventKind.TOOL_END, invocation_id="tool",
            attributes={"tool_run_id": "long"},
        ),)),
        close=lambda: None,
    )
    try:
        runtime.scheduler_step(defer_observations=True, defer_wait_predictions=True)
        assert runtime.graph.invocations["tool"].state is InvocationState.READY
        polls.assert_not_called()
        runtime.dispatch_after_forward()
        polls.assert_called_once()
        assert not runtime.tool_wait_hints
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_eos_dispatch_precedes_deferred_tool_forecasting():
    runtime = NativeAdmissionRuntime()
    runtime.enable_final_stage_prefetch = True
    calls = []
    runtime._eos_prefetch_keys["ready"] = object()
    runtime._wait_prediction_maintenance_deferred = True
    runtime.dispatch_join_prefetch = lambda: calls.append("eos")
    runtime._maintain_wait_predictions = lambda now_ms: calls.append("predictions")
    try:
        runtime.dispatch_after_forward()
        assert calls == ["eos", "predictions"]
        assert not runtime._wait_prediction_maintenance_deferred
    finally:
        runtime._eos_prefetch_keys.clear()
        runtime.close()


def test_expired_physical_transaction_is_retired_before_deferred_forecasting():
    runtime = NativeAdmissionRuntime()
    try:
        with patch("beliefkv.runtime.sglang_v0520_physical.monotonic", return_value=100.):
            runtime.physical_ledger.register(expected("expired"))
        with patch(
            "beliefkv.runtime.sglang_v0520_physical.monotonic",
            return_value=100. + runtime.physical_ledger.max_age_s + 1.,
        ):
            runtime.scheduler_step(defer_observations=True, defer_wait_predictions=True)
        assert not runtime.physical_ledger.is_pending("expired")
        assert runtime.counts["physical_expired"] == 1
        assert runtime._wait_prediction_maintenance_deferred
    finally:
        runtime.close()
