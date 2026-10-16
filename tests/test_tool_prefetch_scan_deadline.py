from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_policy import TransferStartWindow
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep
from beliefkv.runtime.sglang_v0520_runtime import SEMANTIC_FRAME_INTERVAL_MS
from tests.test_tool_predictive_transfers import event, tool_runtime
from tests.test_sglang_v0520_runtime import req
from beliefkv.core.events import RuntimeEventKind


@pytest.mark.parametrize("distribution", ("quantiles", "cdf", "history_switch"))
def test_waiting_for_the_earliest_possible_window_preserves_first_opportunity(distribution):
    runtime, initial = tool_runtime()
    issued = initial.issued_monotonic_ms
    hint = replace(
        initial, wait_p50_ms=5000., wait_p90_ms=9000.,
        expires_monotonic_ms=issued + 100_000.,
    )
    begin = 0
    if distribution == "cdf":
        hint = replace(hint, release_cdf=((100., .1), (3000., .8), (8000., .99)))
    elif distribution == "history_switch":
        hint = replace(
            hint, release_cdf=((100., .1), (30100., 1.)),
            residual_duration_mass=((1000., .90), (30000., .01), (45000., .09)),
            residual_support_detail="same_input_completed",
        )
        begin = 2000
    runtime.prefetch_lead_ms = 500.
    runtime.tool_wait_hints[hint.key.context_id] = hint
    opportunity = NS(
        step=PrefetchLoadStep(hint.key, 1, 2, 1, 2), fits_current_free_lists=True,
    )
    clock = [issued / 1000.]
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            side_effect=lambda: clock[0],
        ), patch.object(runtime, "_wait_prefetch_opportunity", return_value=opportunity) as inspect, \
             patch.object(runtime, "_h2d_start_window",
                          return_value=TransferStartWindow(200., 0., 500.)):
            first_expected = None
            for age in range(begin, 45_001, 10):
                clock[0] = (issued + age) / 1000.
                remaining = hint.remaining_quantile(.5, now_ms=issued + age)
                expected = remaining is not None and remaining <= 500.
                runtime._roll_tool_prefetch()
                if expected:
                    first_expected = age
                    assert inspect.call_count == 1
                    assert runtime._tool_ticket is not None
                    break
                assert not inspect.called
            assert first_expected is not None
            assert runtime.counts["tool_prefetch_scan_deadline_coalesced"] > 0
            assert runtime.counts["tool_prefetch_scans"] < first_expected / SEMANTIC_FRAME_INTERVAL_MS
    finally:
        runtime.close()


def test_a_new_forecast_wakes_a_scan_deferred_far_into_the_future():
    runtime, hint = tool_runtime()
    issued = hint.issued_monotonic_ms
    runtime.prefetch_lead_ms = 500.
    runtime._tool_timing_only = True
    runtime._model_worker = NS(disabled=False, close=lambda: None)
    runtime.tool_wait_hints[hint.key.context_id] = replace(
        hint, wait_p50_ms=30_000., expires_monotonic_ms=issued + 60_000.,
    )
    clock = [issued / 1000.]
    step = PrefetchLoadStep(hint.key, 1, 2, 1, 2)
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
            side_effect=lambda: clock[0],
        ), patch.object(runtime, "_wait_prefetch_opportunity",
                        return_value=NS(step=step, fits_current_free_lists=True)) as inspect, \
             patch.object(runtime, "_h2d_start_window",
                          return_value=TransferStartWindow(200., 0., 500.)):
            runtime._roll_tool_prefetch()
            assert runtime._tool_prefetch_next_ms == pytest.approx(issued + 29_500.)
            clock[0] += .01
            runtime._accept_tool_wait(hint)
            runtime._roll_tool_prefetch()
            inspect.assert_called_once()
            assert runtime._tool_ticket.key == hint.key
    finally:
        runtime.close()


def test_an_eligible_capacity_blocked_wait_keeps_the_existing_retry_cadence():
    runtime, near = tool_runtime()
    other = req("other")
    other.session_id, other.session_generation = "other-session", 1
    runtime.register_visible_request(other)
    runtime.on_events((
        event(3, RuntimeEventKind.INVOCATION_CREATE, invocation_id="other",
              context_id="ctx-other", agent_definition_id="child", agent_instance_id="other"),
        event(4, RuntimeEventKind.TOOL_START, invocation_id="other",
              attributes={"tool_family": "shell", "tool_run_id": "other-tool"}),
    ))
    runtime.tool_wait_hints["ctx-other"] = replace(
        near, key=runtime.context_sessions["ctx-other"], wait_p50_ms=30_000.,
        expires_monotonic_ms=near.issued_monotonic_ms + 60_000.,
        invocation_revision_ts_ms=4.,
    )
    now = near.issued_monotonic_ms
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now / 1000.,
        ), patch.object(runtime, "_wait_prefetch_opportunity", return_value=None):
            runtime._roll_tool_prefetch()
        assert runtime._tool_prefetch_next_ms - now <= SEMANTIC_FRAME_INTERVAL_MS
        assert not runtime.counts["tool_prefetch_scan_deadline_coalesced"]
    finally:
        runtime.close()
