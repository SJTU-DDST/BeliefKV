"""EOS-first JOIN restoration with a bounded, optional progress forecast."""

from collections import OrderedDict
from dataclasses import replace
import json
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.native_transfer_policy import (
    SelectiveJoinPrefetchPolicy, TransferStartWindow,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_online_workload_timing import report_fixture
from tests.test_report_prefetch_planning_ceiling import install_cost_evidence
from tests.test_sglang_v0520_runtime import event, req
from tests.test_wait_prefetch_batch import batch_fixture


def selective_fixture(monkeypatch):
    runtime, child, stage = report_fixture(monkeypatch)
    runtime.join_prefetch_trigger = "selective_report_or_eos"
    child.session_id, child.session_generation = "child-session", 1
    runtime.on_requests_requeued((child,), is_retracted=False)
    install_cost_evidence(runtime, stage)
    plan = NS(
        step=NS(key=stage.key, leaf_node_id=7, leaf_creation_time=8),
        required_full_tokens=65536, required_mamba_slots=1,
        fits_current_free_lists=True,
    )
    return runtime, child, stage, plan


def test_large_missing_checkpoint_with_an_eos_deficit_can_start_before_eos(monkeypatch):
    runtime, _, stage, plan = selective_fixture(monkeypatch)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(400., 10., 510.)) as estimate:
            assert runtime._roll_final_stage() is plan
            assert estimate.call_count == 1
        ticket = runtime._join_ticket
        assert ticket.report_start_window_ms == 560.
        assert runtime._join_ticket_has_report_service(ticket)
        assert not runtime._join_ticket_has_native_eos(ticket)
        assert runtime._critical_parent_demand[0] == stage.key
        with patch.object(runtime, "_h2d_start_window",
                          side_effect=AssertionError("enqueue reuses the bounded scalar forecast")):
            assert runtime._wait_prefetch_timing_valid(stage.key, plan, "join_ticket")
        selected = next(row for row in records if row["event"] == "final_stage_latest_start")
        assert selected["selective_report"] is True
        assert selected["missing_checkpoint_full_bytes"] == 65536 * 20480
        assert selected["estimated_recovery_deficit_ms"] == 360.
        assert "not exposed GPU wait" in selected["service_semantics"]
    finally:
        runtime.close()


@pytest.mark.parametrize("case", ("small_parent", "unknown_parent", "distant", "not_last_child", "not_running"))
def test_ineligible_parent_or_child_needs_no_native_path_capture(monkeypatch, case):
    runtime, _, stage, _ = selective_fixture(monkeypatch)
    try:
        if case == "small_parent":
            runtime._context_tokens[stage.key.context_id] = (stage.key.context_epoch, 100, 0, False)
        elif case == "unknown_parent":
            runtime._context_tokens.clear()
        elif case == "distant":
            stage.expected_tokens = 4096
        elif case == "not_last_child":
            runtime.graph.joins["join"].member_invocation_ids.add("other")
        else:
            runtime._native_running_batch = NS(reqs=())
        with patch.object(runtime, "_wait_prefetch_opportunity",
                          side_effect=AssertionError("cheap filter must reject before native capture")):
            assert runtime._roll_final_stage() is None
        assert runtime._critical_parent_demand is None
        assert runtime._join_ticket is None
    finally:
        runtime.close()


@pytest.mark.parametrize("case", (
    "small_missing_full", "mamba_only", "no_host_copy", "eos_window_sufficient",
    "unsupported_timing", "no_admission_slot",
))
def test_optional_prediction_does_not_reclaim_or_transfer_without_value(monkeypatch, case):
    runtime, _, _, plan = selective_fixture(monkeypatch)
    if case in ("small_missing_full", "mamba_only"):
        plan.required_full_tokens = 100 if case == "small_missing_full" else 0
    if case == "no_host_copy":
        plan.step = None
    window = None if case == "unsupported_timing" else TransferStartWindow(400., 0., 500.)
    if case == "eos_window_sufficient":
        runtime._join_eos_arrival_window_ms = 500.
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_h2d_start_window", return_value=window), \
            patch.object(runtime, "_prefetch_slot_available", return_value=case != "no_admission_slot"):
            assert runtime._roll_final_stage() is None
        assert runtime._critical_parent_demand is None
        assert runtime._join_ticket is None
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_capacity_preparation_precedes_forecast_transfer_without_using_busy_pages(monkeypatch):
    runtime, _, stage, plan = selective_fixture(monkeypatch)
    plan.fits_current_free_lists = False
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(400., 0., 500.)):
            assert runtime._roll_final_stage() is None
        assert runtime._critical_parent_demand[:3] == (stage.key, 65536, 1)
        assert runtime._join_ticket is None
        assert runtime.counts["report_service_waiting_for_cold_capacity"] == 1
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_selective_advance_is_capped_even_with_a_slow_controller_estimate(monkeypatch):
    runtime, _, _, plan = selective_fixture(monkeypatch)
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_report_remaining_ms", return_value=1001.), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(5000., 100., 5100.)):
            assert runtime._roll_final_stage() is None
        assert runtime._join_ticket is None
    finally:
        runtime.close()


def test_selective_checks_are_periodic_bounded_and_rotate_targets(monkeypatch):
    runtime, _, stage, _ = selective_fixture(monkeypatch)
    runtime._final_stages = OrderedDict(
        (str(index), replace(stage, join_id=str(index))) for index in range(10)
    )
    runtime._selective_join_policy = replace(runtime._selective_join_policy, max_candidates=2)
    seen = []
    try:
        with patch.object(runtime, "_report_parent_full_upper_bytes", return_value=2 ** 30), \
            patch.object(runtime, "_report_remaining_ms",
                         side_effect=lambda item: seen.append(item.join_id)), \
            patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=1.):
            for _ in range(100):
                assert runtime._roll_final_stage() is None
        assert seen == ["0", "1"]
        with patch.object(runtime, "_report_parent_full_upper_bytes", return_value=2 ** 30), \
            patch.object(runtime, "_report_remaining_ms",
                         side_effect=lambda item: seen.append(item.join_id)), \
            patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=1.201):
            assert runtime._roll_final_stage() is None
        assert seen == ["0", "1", "2", "3"]
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "payload_grew", "different_checkpoint", "tool", "stale_epoch", "remaining_grew",
))
def test_forecast_scalar_never_authorizes_a_changed_target(monkeypatch, change):
    runtime, _, stage, plan = selective_fixture(monkeypatch)
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(400., 0., 500.)):
            assert runtime._roll_final_stage() is plan
        if change == "payload_grew":
            plan = NS(**{**vars(plan), "required_full_tokens": 65537})
        elif change == "different_checkpoint":
            plan = NS(**{**vars(plan), "step": NS(
                key=stage.key, leaf_node_id=9, leaf_creation_time=8,
            )})
        elif change == "tool":
            runtime._decoded_tool_requests.add("child")
        elif change == "remaining_grew":
            stage.expected_tokens = 4096
        else:
            runtime.graph.contexts["ctx-child"].epoch += 1
        assert not runtime._wait_prefetch_timing_valid(stage.key, plan, "join_ticket")
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_tool_boundary_discards_unconsumed_forecast_capacity_intent(monkeypatch):
    runtime, _, _, plan = selective_fixture(monkeypatch)
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(400., 0., 500.)):
            assert runtime._roll_final_stage() is plan
        assert runtime._critical_parent_demand is not None
        runtime.on_events((event(
            8, RuntimeEventKind.TOOL_START, invocation_id="child",
            attributes={"tool_run_id": "new-call"},
        ),))
        assert runtime._critical_parent_demand is None
        assert runtime._join_ticket is None
        assert not runtime._final_stages
    finally:
        runtime.close()


def assert_eos_skips_forecast_gates(monkeypatch, trigger):
    runtime, child, stage, plan = selective_fixture(monkeypatch)
    runtime.join_prefetch_trigger = trigger
    plan.required_full_tokens = 1
    runtime._report_candidate_next_ms = time.monotonic() * 1000. + 60_000.
    child_key = runtime.visible[child.rid]
    runtime._semantic_keys[child.rid] = child_key
    runtime._semantic_eos_proofs[child.rid] = True
    runtime._semantic_finished[child.rid] = (time.monotonic() * 1000., 128)
    runtime._ensure_observed_final_stage(child_key)
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_report_remaining_ms",
                         side_effect=AssertionError("EOS does not need remaining work")), \
            patch.object(runtime, "_h2d_start_window",
                         side_effect=AssertionError("EOS does not need a duration estimate")):
            assert runtime._roll_final_stage() is plan
            assert runtime._wait_prefetch_timing_valid(stage.key, plan, "join_ticket")
        assert runtime._join_ticket.report_start_window_ms is None
    finally:
        runtime.close()


def test_eos_immediately_takes_over_without_forecast_size_or_cadence_gates(monkeypatch):
    assert_eos_skips_forecast_gates(monkeypatch, "selective_report_or_eos")


def test_eos_only_issues_without_forecast_size_or_cadence_gates(monkeypatch):
    assert_eos_skips_forecast_gates(monkeypatch, "observed_eos")


def test_eos_only_skips_report_planning_and_decode_progress_sampling(monkeypatch):
    runtime, child, _, _ = selective_fixture(monkeypatch)
    runtime.join_prefetch_trigger = "observed_eos"
    progress = tuple(runtime._semantic_progress[child.rid])
    try:
        with patch.object(runtime, "_roll_report_service_prefetch",
                          side_effect=AssertionError("report selection is disabled")), \
            patch.object(runtime, "_observe_report_service",
                         side_effect=AssertionError("report progress is disabled")), \
            patch.object(runtime, "_wait_prefetch_opportunity",
                         side_effect=AssertionError("no native capture before EOS")):
            assert runtime._roll_final_stage() is None
            assert runtime.on_batch_completed(NS(reqs=(child,))) == ()
        assert tuple(runtime._semantic_progress[child.rid]) == progress
        assert runtime._join_ticket is None
        assert runtime._critical_parent_demand is None
    finally:
        runtime.close()


def test_eos_takes_over_an_existing_forecast_ticket_without_reestimating(monkeypatch):
    runtime, child, stage, plan = selective_fixture(monkeypatch)
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(400., 0., 500.)):
            assert runtime._roll_final_stage() is plan
        ticket = runtime._join_ticket
        observe_eos(runtime, child, eos_ms=time.monotonic() * 1000.)
        with patch.object(runtime, "_report_remaining_ms",
                          side_effect=AssertionError("observed EOS replaces the forecast")), \
            patch.object(runtime, "_h2d_start_window",
                         side_effect=AssertionError("no controller estimate after EOS")):
            assert runtime._roll_final_stage() is None
            assert runtime._wait_prefetch_timing_valid(stage.key, plan, "join_ticket")
        assert runtime._join_ticket is ticket
    finally:
        runtime.close()


def test_eos_yields_an_unissued_forecast_for_a_different_parent(monkeypatch):
    runtime, child, stage, plan = selective_fixture(monkeypatch)
    try:
        other = req("other")
        other.session_id, other.session_generation = "other-session", 1
        runtime.register_visible_request(other)
        runtime.on_events((
            event(8, RuntimeEventKind.INVOCATION_CREATE, invocation_id="second-child",
                  context_id="ctx-second-child"),
            event(9, RuntimeEventKind.JOIN_CREATE, join_id="second-join",
                  member_invocation_ids=("second-child",)),
            event(10, RuntimeEventKind.JOIN_WAIT, invocation_id="other", join_id="second-join"),
        ))
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(400., 0., 500.)):
            assert runtime._roll_final_stage() is plan
        other_key = runtime.visible["other"]
        runtime._final_stages["second-join"] = replace(
            stage, key=other_key, join_id="second-join", child_id="second-child",
            child_epoch=0, request_id=None,
        )
        runtime._join_ticket = replace(
            runtime._join_ticket, key=other_key, join_id="second-join",
            member_ids=("second-child",),
        )
        assert runtime._live_join_ticket()
        observe_eos(runtime, child, eos_ms=time.monotonic() * 1000.)
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan):
            assert runtime._roll_final_stage() is plan
        assert runtime._join_ticket.join_id == "join"
        assert runtime._join_ticket.report_start_window_ms is None
        assert runtime.counts["selective_join_yielded_to_eos"] == 1
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_partial_transfer_of_a_selected_large_checkpoint_does_not_reapply_the_size_gate(monkeypatch):
    runtime, _, stage, plan = selective_fixture(monkeypatch)
    try:
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan), \
            patch.object(runtime, "_h2d_start_window",
                         return_value=TransferStartWindow(400., 0., 500.)):
            assert runtime._roll_final_stage() is plan
        partial = NS(**{**vars(plan), "required_full_tokens": 10, "required_mamba_slots": 0})
        assert runtime._wait_prefetch_timing_valid(stage.key, partial, "join_ticket")
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


def test_selected_forecast_can_enqueue_a_real_native_cpu_burst_and_record_its_trigger(monkeypatch):
    runtime, key, opportunity, cache, _ = batch_fixture("join_ticket", completed_join=False)
    runtime.join_prefetch_trigger = "selective_report_or_eos"
    runtime._selective_join_policy = SelectiveJoinPrefetchPolicy(min_full_bytes=100)
    runtime._context_tokens[key.context_id] = (key.context_epoch, 33, 0, False)
    runtime._h2d_samples.extend(((180, 400.),) * 3)
    runtime._report_remaining_ms = Mock(return_value=60.)
    stage = runtime._final_stages["join"]
    stage.request_id = "child"
    runtime._final_request_stages["child"] = stage
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity):
            runtime.dispatch_join_prefetch()
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert runtime.physical_ledger.pending_count == 4
        issues = [row for row in records if row["event"] == "prefetch_native_issued"]
        assert len(issues) == 4
        assert {row["trigger_kind"] for row in issues} == {"selective_report_service"}
    finally:
        runtime.close()


def observe_eos(runtime, child, *, eos_ms):
    key = runtime.visible[child.rid]
    runtime._semantic_keys[child.rid] = key
    runtime._semantic_eos_proofs[child.rid] = True
    runtime._semantic_finished[child.rid] = (eos_ms, 128)
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=eos_ms / 1000.):
        runtime._ensure_observed_final_stage(key)


def test_protocol_window_learns_only_causal_native_arrival_and_excludes_service_queue(monkeypatch):
    runtime, child, stage, _ = selective_fixture(monkeypatch)
    try:
        observe_eos(runtime, child, eos_ms=1000.)
        assert stage.key.context_id in runtime._join_eos_arrival_pending
        runtime.on_events((event(8, RuntimeEventKind.RETURN, invocation_id="child"),))
        assert stage.key.context_id in runtime._join_eos_arrival_pending
        runtime._join_eos_arrival_samples.extend((200., 300., 400.))
        parent = req("parent")
        parent.rid = "parent-next"
        parent.beliefkv_metadata["context_epoch"] = 1
        parent.session_id, parent.session_generation = "s", 1
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=1.5):
            assert runtime.register_visible_request(parent)
        assert list(runtime._join_eos_arrival_samples) == [200., 300., 400., 500.]
        assert runtime._join_eos_arrival_window_ms == 230.
        assert not runtime._join_eos_arrival_pending
        runtime._observe_join_eos_arrival(runtime.visible[parent.rid], 6000.)
        assert len(runtime._join_eos_arrival_samples) == 4
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "session", "generation", "epoch", "not_satisfied", "child_cancelled", "tool",
    "compact", "workflow_end",
))
def test_protocol_window_never_learns_from_invalidated_or_different_reentry(monkeypatch, change):
    runtime, child, stage, _ = selective_fixture(monkeypatch)
    try:
        observe_eos(runtime, child, eos_ms=1000.)
        runtime.graph.joins["join"].satisfied = change != "not_satisfied"
        runtime.graph.joins["join"].completed_member_ids.add("child")
        runtime.graph.invocations["child"].state = (
            InvocationState.CANCELLED if change == "child_cancelled" else InvocationState.DONE
        )
        key = replace(stage.key, request_id="parent-next", context_epoch=1)
        if change == "session":
            key = replace(key, session_id="different")
        elif change == "generation":
            key = replace(key, session_generation=2)
        elif change == "epoch":
            key = replace(key, context_epoch=2)
        elif change in ("tool", "compact", "workflow_end"):
            kind = {
                "tool": RuntimeEventKind.TOOL_START,
                "compact": RuntimeEventKind.CONTEXT_COMPACT,
                "workflow_end": RuntimeEventKind.WORKFLOW_END,
            }[change]
            runtime._invalidate_join_eos_arrival(event(
                8, kind, invocation_id="child", context_id="ctx-child",
            ))
        runtime._observe_join_eos_arrival(key, 1200.)
        assert not runtime._join_eos_arrival_samples
    finally:
        runtime.close()


def test_selected_policy_survives_plan_resume_and_runtime_configuration(tmp_path, monkeypatch):
    from scripts.resume_semantic_h2d_ab import resume_environment
    from scripts.summarize_semantic_h2d_ab import main

    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps({"workloads": [{"instance_id": "task"}]}))
    seed = tmp_path / "seed.json"
    seed.write_text("{}")
    monkeypatch.setattr(sys, "argv", [
        "summarize", "--run-root", str(tmp_path), "--initialize", "--root-count", "1",
        "--workload-manifest", str(manifest), "--arm-order", "predictive_h2d",
        "--h2d-seed", str(seed),
        "--join-prefetch-trigger", "selective_report_or_eos",
        "--join-prediction-min-full-bytes", "1073741824",
        "--join-prediction-interval-ms", "250",
        "--join-prediction-max-candidates", "2",
    ])
    main()
    plan = json.loads((tmp_path / "ab_plan.json").read_text())
    environment = resume_environment(plan, tmp_path, python="/python", port=18454)
    for name in (
        "JOIN_PREFETCH_TRIGGER", "JOIN_PREDICTION_MIN_FULL_BYTES",
        "JOIN_PREDICTION_INTERVAL_MS", "JOIN_PREDICTION_MAX_CANDIDATES",
    ):
        monkeypatch.setenv(f"BELIEFKV_{name}", environment[name])
    runtime = NativeAdmissionRuntime()
    try:
        assert runtime.join_prefetch_trigger == "selective_report_or_eos"
        assert vars(runtime._selective_join_policy) == plan["selective_join_prefetch"] == {
            "min_full_bytes": 1073741824, "interval_ms": 250., "max_candidates": 2,
        }
        assert not plan["policy_configuration"]["predictive_h2d"]["semantic_text_stream"]
        assert "EOS default" in plan["join_prediction_semantics"]
    finally:
        runtime.close()
