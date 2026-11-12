"""Bound advisory refresh work without delaying real events or native checks."""

from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.predicted_tool_offload import DemandAwareToolOffload, PredictedToolOffload
from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_tool_replacement import pair, run
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event, tool_runtime


@pytest.mark.parametrize("controller_type", (PredictedToolOffload, DemandAwareToolOffload))
def test_same_wait_refresh_keeps_deadline_and_replaces_forecast(controller_type, monkeypatch):
    runtime, hint = tool_runtime()
    try:
        runtime.agent_residency_pause = True
        runtime._online_tool_timing = NS()
        controller = runtime._predicted_tool_offload = controller_type(runtime)
        clock = hint.issued_monotonic_ms + 10.
        monkeypatch.setattr("time.monotonic", lambda: clock / 1000.)
        controller.next_ms = clock + 150.
        replacement = getattr(controller, "replacement", None)
        if replacement is not None:
            replacement.next_ms = clock + 175.
        refreshed = replace(hint, issued_monotonic_ms=clock, wait_p10_ms=5000.)
        runtime._accept_tool_wait(refreshed)
        assert runtime.tool_wait_hints[hint.key.context_id] is refreshed
        assert controller.next_ms == clock + 150.
        if replacement is not None:
            assert replacement.next_ms == clock + 175.
        assert runtime.counts["tool_offload_hint_refreshes_coalesced"] == 1
        assert not runtime.counts["tool_offload_hint_rearms"]
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "new_wait", "request_identity", "invocation_revision", "predictor", "scope",
    "expired_previous", "short_new_lifetime",
))
def test_new_or_uncovered_wait_forecast_wakes_immediately(change):
    runtime, hint = tool_runtime()
    try:
        controller = DemandAwareToolOffload(runtime)
        now = hint.issued_monotonic_ms + 10.
        previous, refreshed = hint, replace(hint, issued_monotonic_ms=now)
        if change == "new_wait":
            previous = None
        elif change == "request_identity":
            previous = replace(hint, key=replace(hint.key, session_generation=2))
        elif change == "invocation_revision":
            previous = replace(hint, invocation_revision_ts_ms=1.)
        elif change == "predictor":
            previous = replace(hint, predictor_sha256="b" * 64)
        elif change == "scope":
            refreshed = replace(refreshed, residual_support_detail="online_run_command")
        elif change == "expired_previous":
            previous = replace(hint, expires_monotonic_ms=now)
        else:
            refreshed = replace(refreshed, expires_monotonic_ms=now + 50.)
        controller.next_ms = controller.replacement.next_ms = now + 150.
        controller.hint_updated(previous, refreshed, now_ms=now)
        assert controller.next_ms == controller.replacement.next_ms == 0.
        assert runtime.counts["tool_offload_hint_rearms"] == 1
    finally:
        runtime.close()


def test_repeated_refresh_cannot_extend_or_leave_an_unbounded_deadline():
    runtime, hint = tool_runtime()
    try:
        controller = DemandAwareToolOffload(runtime)
        now = hint.issued_monotonic_ms
        controller.next_ms = controller.replacement.next_ms = now + 10_000.
        previous = hint
        for age in range(0, 200, 10):
            refreshed = replace(hint, issued_monotonic_ms=now + age)
            controller.hint_updated(previous, refreshed, now_ms=now + age)
            previous = refreshed
            assert controller.next_ms == controller.replacement.next_ms == now + 200.
        controller.next_ms = controller.replacement.next_ms = 0.
        controller.hint_updated(previous, refreshed, now_ms=now + 199.)
        assert controller.next_ms == controller.replacement.next_ms == 0.
    finally:
        runtime.close()


def test_stale_hint_cannot_change_a_valid_wait_or_its_deadline(demand_runtime):
    runtime, _admission, controller, _adder = pair(demand_runtime)
    runtime._online_tool_timing = NS()
    hint = next(iter(runtime.tool_wait_hints.values()))
    controller.next_ms = controller.replacement.next_ms = hint.issued_monotonic_ms + 150.
    stale = replace(hint, invocation_revision_ts_ms=-1.)
    runtime._accept_tool_wait(stale)
    assert runtime.tool_wait_hints[hint.key.context_id] is hint
    assert controller.next_ms == controller.replacement.next_ms == hint.issued_monotonic_ms + 150.
    assert runtime.counts["tool_wait_result_stale"] == 1


def test_forecast_that_becomes_eligible_runs_by_the_existing_200_ms_deadline(
    demand_runtime, monkeypatch,
):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    hint = next(iter(runtime.tool_wait_hints.values()))
    clock = [max(time.monotonic() * 1000., hint.issued_monotonic_ms) + 10.]
    monkeypatch.setattr("time.monotonic", lambda: clock[0] / 1000.)
    runtime._online_tool_timing = NS(prefetch_supported=lambda *args, **kwargs: True)
    # Keep issuance before processing despite floating point seconds/ms rounding.
    short = replace(
        hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
        issued_monotonic_ms=clock[0] - 1., expires_monotonic_ms=clock[0] + 5000.,
        residual_support_detail="online_run_command",
    )
    runtime._accept_tool_wait(short)
    assert not run(parts)
    deadline = controller.replacement.next_ms
    assert deadline == pytest.approx(clock[0] + controller.interval_ms, rel=0., abs=1e-6)
    clock[0] += 50.
    supported = replace(
        hint, issued_monotonic_ms=clock[0] - 1., expires_monotonic_ms=clock[0] + 5000.,
        residual_support_detail="online_run_command",
    )
    runtime._accept_tool_wait(supported)
    assert runtime.tool_wait_hints[hint.key.context_id] is supported
    assert not runtime.counts["tool_wait_result_stale"]
    assert controller.replacement.next_ms == deadline
    assert not run(parts)
    assert demand_runtime[4].component_data[0].value is not None
    clock[0] = deadline + 1.
    assert run(parts), {"runtime": dict(runtime.counts), "admission": dict(admission.counts)}
    assert admission.policy.programs["consumer"].active
    assert demand_runtime[4].component_data[0].value is None


@pytest.mark.parametrize("interval_ms", (300, 400, 500))
def test_changed_retry_deadline_still_serves_a_newly_eligible_forecast(
    demand_runtime, monkeypatch, interval_ms,
):
    monkeypatch.setenv("BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS", str(interval_ms))
    controller = DemandAwareToolOffload(demand_runtime[0])
    assert controller.interval_ms == interval_ms
    test_forecast_that_becomes_eligible_runs_by_the_existing_200_ms_deadline(
        (demand_runtime[0], controller, *demand_runtime[2:]), monkeypatch,
    )


@pytest.mark.parametrize("interval_ms", (300, 400, 500))
def test_retry_interval_does_not_change_offload_profitability(
    demand_runtime, monkeypatch, interval_ms,
):
    parts = pair(demand_runtime)
    runtime, _admission, controller, _adder = parts
    worth = controller._worth_offloading
    checked = []

    def compare(*args, **kwargs):
        controller.interval_ms = 200.
        original_result = worth(*args, **kwargs)
        original_threshold = runtime.counts["tool_offload_threshold_us_last"]
        controller.interval_ms = interval_ms
        result = worth(*args, **kwargs)
        assert result == original_result
        assert runtime.counts["tool_offload_threshold_us_last"] == original_threshold > 0
        checked.append(original_threshold)
        return result

    monkeypatch.setattr(controller, "_worth_offloading", compare)
    assert run(parts)
    assert checked
    assert controller.control_cost_margin_ms == 400.


@pytest.mark.parametrize("event_path", ("tool_return", "backup_ack", "new_request"))
def test_longer_backoff_keeps_actual_event_wakeups(demand_runtime, monkeypatch, event_path):
    monkeypatch.setenv("BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS", "400")
    if event_path == "backup_ack":
        from tests.test_reference_tool_replacement import (
            test_backup_ack_retries_immediately_and_revalidates_both_sides as check,
        )
        check(demand_runtime, None)
    elif event_path == "tool_return":
        from tests.test_reference_tool_replacement import (
            test_new_tool_return_event_rearms_without_advancing_the_control_clock as check,
        )
        check(demand_runtime)
    else:
        parts = pair(demand_runtime)
        runtime, admission, controller, _adder = parts
        controller.next_ms = controller.replacement.next_ms = float("inf")
        runtime.on_events((event(
            10, Kind.LLM_SUBMIT, invocation_id="consumer", context_id="ctx-consumer",
            attributes={"request_id": "consumer"},
        ),))
        assert controller.next_ms == controller.replacement.next_ms == 0.
        assert run(parts)
        assert admission.policy.programs["consumer"].active


@pytest.mark.parametrize("value", ("0", "-1", "nan", "inf", "-inf", "bad"))
def test_invalid_retry_interval_is_rejected(demand_runtime, monkeypatch, value):
    monkeypatch.setenv("BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS", value)
    with pytest.raises(ValueError):
        DemandAwareToolOffload(demand_runtime[0])


@pytest.mark.parametrize("guard", (
    "unsupported_prediction", "foreign_prediction", "pending_donor", "restore_ticket",
    "wrong_session", "unobserved_block",
))
def test_ineligible_donor_window_skips_native_consumer_inspection(
    demand_runtime, monkeypatch, guard,
):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    hint = next(iter(runtime.tool_wait_hints.values()))
    if guard == "unsupported_prediction":
        monkeypatch.setattr(runtime, "_tool_offload_remaining", lambda *args, **kwargs: None)
    elif guard == "foreign_prediction":
        runtime.tool_wait_hints[hint.key.context_id] = replace(hint, predictor_sha256="b" * 64)
    elif guard == "pending_donor":
        admission.policy.programs["tool"].pending = True
    elif guard == "restore_ticket":
        runtime._tool_ticket = NS(key=hint.key)
    elif guard == "wrong_session":
        runtime.context_sessions[hint.key.context_id] = replace(hint.key, session_generation=2)
    else:
        monkeypatch.setattr(runtime, "_agent_observed_blocked_budget_key", lambda key: False)
    inspect = Mock(side_effect=AssertionError("no eligible donor must not inspect a consumer prefix"))
    monkeypatch.setattr(runtime, "_read_request_reentry", inspect)
    assert not run(parts)
    inspect.assert_not_called()
    assert admission.counts["tool_event_replacement_no_eligible_donor"] == 1
    assert admission.counts["tool_event_replacement_donor_prefilter_checks"] == 1
    assert controller.cursor == controller.max_contexts
    assert demand_runtime[4].component_data[0].value is not None
    assert not admission.policy.programs["consumer"].active
    assert not runtime.physical_ledger.pending_count


def test_donor_prefilter_does_not_keep_a_failed_forecast_across_attempts(demand_runtime, monkeypatch):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    with monkeypatch.context() as scoped:
        scoped.setattr(runtime, "_tool_offload_remaining", lambda *args, **kwargs: None)
        assert not run(parts)
    controller.rearm()
    assert run(parts)
    assert admission.policy.programs["consumer"].active
    assert admission.counts["tool_event_replacement_no_eligible_donor"] == 1


def test_donor_prefilter_keeps_the_same_bounded_rotating_window(demand_runtime, monkeypatch):
    runtime, admission, controller, _adder = pair(demand_runtime)
    original = admission.latest["tool"]
    donors = tuple(replace(original, invocation_id=f"donor-{index}") for index in range(9))
    for donor in donors:
        admission.policy.programs[donor.invocation_id] = NS()
    inspected = []

    def eligible(_controller, key):
        inspected.append(key)
        return key == donors[-1]

    monkeypatch.setattr(PredictedToolOffload, "_eligible", eligible)
    monkeypatch.setattr(runtime, "_agent_observed_blocked_budget_key", lambda key: True)
    assert controller.replacement._first_candidate_donor(donors) is None
    assert inspected == list(donors[:8])
    inspected.clear()
    controller.cursor = 1
    assert controller.replacement._first_candidate_donor(donors) == donors[-1]
    assert inspected == list(donors[1:])


@pytest.mark.parametrize("race", ("tool_return", "pending_donor"))
def test_prechecked_donor_still_revalidates_after_consumer_inspection(
    demand_runtime, monkeypatch, race,
):
    parts = pair(demand_runtime)
    runtime, admission, _controller, _adder = parts
    inspect = runtime._read_request_reentry

    def changed_during_inspection(*args, **kwargs):
        result = inspect(*args, **kwargs)
        if race == "tool_return":
            runtime.on_events((event(
                10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
            ),))
        else:
            admission.policy.programs["tool"].pending = True
        return result

    monkeypatch.setattr(runtime, "_read_request_reentry", changed_during_inspection)
    assert not run(parts)
    assert demand_runtime[4].component_data[0].value is not None
    assert not admission.policy.programs["consumer"].active
    assert not runtime.physical_ledger.pending_count


def test_settled_private_plan_avoids_shadow_capture_and_preserves_cursor(demand_runtime, monkeypatch):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    inspect = Mock(side_effect=AssertionError("a complete settled release plan needs no shadow rebuild"))
    monkeypatch.setattr(runtime, "_refreshed_shadow_backup_selection", inspect)
    before = controller.node_cursor
    assert run(parts)
    inspect.assert_not_called()
    assert controller.node_cursor == before + 2 * controller.max_nodes
    assert admission.counts["tool_event_replacement_shadow_refresh_skips"] == 1
    assert not admission.counts["tool_event_replacement_shadow_refresh_fallbacks"]
    assert admission.policy.programs["consumer"].active
    assert demand_runtime[4].component_data[0].value is None


@pytest.mark.parametrize("index_state", ("missing", "stale_identity", "insufficient"))
def test_incomplete_backed_index_refreshes_and_serves_in_the_same_attempt(
    demand_runtime, monkeypatch, index_state,
):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    donor, cache, key = demand_runtime[4], demand_runtime[2], admission.latest["tool"]
    if index_state == "insufficient":
        from array import array
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", range(1, 17)))))
    runtime._parent_pressure_candidates.clear()
    if index_state == "stale_identity":
        runtime._remember_parent_pressure_node(donor.id, key, donor.creation_time - 1.)
    elif index_state == "insufficient":
        runtime._remember_parent_pressure_node(donor.id, key, donor.creation_time)
        assert len(donor.component_data[0].value) == 16
    inspect = Mock(wraps=runtime._refreshed_shadow_backup_selection)
    plan = Mock(wraps=controller.replacement._release_plan)
    monkeypatch.setattr(runtime, "_refreshed_shadow_backup_selection", inspect)
    monkeypatch.setattr(controller.replacement, "_release_plan", plan)
    before = controller.node_cursor
    assert run(parts)
    inspect.assert_called_once()
    assert plan.call_count == 2
    assert admission.counts["tool_event_replacement_shadow_refresh_fallbacks"] == 1
    assert controller.node_cursor == before + 2 * controller.max_nodes
    assert admission.policy.programs["consumer"].active
    assert runtime.counts["predicted_tool_offload_freed_component_0"] == 32


@pytest.mark.parametrize("blocked", ("locked", "unbacked"))
def test_readonly_shadow_refresh_reuses_only_its_same_call_plan(
    demand_runtime, monkeypatch, blocked,
):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    donor = demand_runtime[4]
    if blocked == "locked":
        donor.component_data[0].lock_ref = 1
    else:
        donor.component_data[0].host_value = None
        demand_runtime[2].tree_core._update_duplicate_tracking(donor)
        monkeypatch.setattr(runtime, "issue_shadow_backup_steps", Mock(return_value=()))
    plan = Mock(wraps=controller.replacement._release_plan)
    monkeypatch.setattr(controller.replacement, "_release_plan", plan)
    assert not run(parts)
    plan.assert_called_once()
    assert admission.counts["tool_event_replacement_release_plan_reuses"] == 1
    assert controller.node_cursor == controller.max_nodes
    assert donor.component_data[0].value is not None
    assert not admission.policy.programs["consumer"].active
    assert not runtime.physical_ledger.pending_count


@pytest.mark.parametrize("race", ("tool_return", "consumer_disappears", "new_lock", "native_refusal"))
def test_fast_backed_plan_does_not_replace_final_native_authority(
    demand_runtime, monkeypatch, race,
):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    evict = controller._evict_backed

    def changed_before_release(key, **kwargs):
        if race == "tool_return":
            runtime.on_events((event(
                10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
            ),))
        elif race == "consumer_disappears":
            runtime._capacity_waiting_queue.clear()
        elif race == "new_lock":
            demand_runtime[4].component_data[0].lock_ref += 1
        else:
            monkeypatch.setattr(demand_runtime[2], "_can_beliefkv_full_demote", lambda *a, **k: False)
        return evict(key, **kwargs)

    monkeypatch.setattr(controller, "_evict_backed", changed_before_release)
    assert not run(parts)
    assert demand_runtime[4].component_data[0].value is not None
    assert not admission.policy.programs["consumer"].active
    assert not runtime.counts["predicted_tool_offload_freed_component_0"]
    assert not runtime.physical_ledger.pending_count
