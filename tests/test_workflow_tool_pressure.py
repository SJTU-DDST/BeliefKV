from dataclasses import replace
import json
import time
from types import SimpleNamespace as NS
from pathlib import Path

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.policy.paper_baselines import ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet, WorkflowToolState
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.workflow_tool_pressure import WorkflowToolPressure
from scripts.run_causal_comparison import preflight_runtime
from scripts.prepare_tool_aware_pressure import prepare
from tests.test_global_workflow_oracle import queued
from tests.test_tool_predictive_transfers import event
from tests.test_workflow_size_admission import settings


def policy(states, ranks=None, *, capacity=100, buffer=0):
    ranks = ranks or {}
    live = WorkflowSizeWorkingSet(
        capacity, buffer_tokens=buffer, rank=lambda identity, now: ranks.get(identity, 1.),
        sibling_group=lambda identity: None, observe_ready=lambda item: not states[item.identity].blocked,
    )
    live.tool_pressure_state = lambda item, now: states[item.identity]
    live.pressure_preference = lambda item, now: ranks.get(item.identity)
    return live


def test_short_tool_weight_does_not_manufacture_capacity_for_a_child():
    states = {
        "tool": WorkflowToolState(True, True, source="short_tool_grace"),
        "child": WorkflowToolState(),
    }
    live = policy(states)
    live.programs = {
        "tool": ThunderProgram("tool", 90, steps=2, acting=True, active=True),
        "child": ThunderProgram("child", 20, steps=1, pending=True),
    }
    live.acting_since["tool"] = 0.
    assert live.tick(now=100.) == ()
    assert live.programs["tool"].active and not live.programs["child"].active
    assert live.remaining_capacity_with_decay(now=100.) == 10
    assert not live.last_pressure


@pytest.mark.parametrize("pending", (False, True))
def test_long_blocked_owner_yields_before_smaller_executable_child_even_if_its_work_is_short(pending):
    states = {
        "tool": WorkflowToolState(True, True, .01, source="live_conditional_tool_return"),
        "child": WorkflowToolState(),
    }
    live = policy(states, {"tool": 1., "child": 1000.})
    live.programs = {
        "tool": ThunderProgram("tool", 90, steps=2, active=True, acting=not pending, pending=pending),
        "child": ThunderProgram("child", 20, steps=2, pending=True),
    }
    assert live.tick(now=10.) == ("child",)
    assert not live.programs["tool"].active and live.programs["child"].active
    assert live.programs["tool"].pending is pending
    assert not live.programs["child"].marked_for_pause
    assert live.remaining_capacity() == 80
    decision = live.last_pressure[0]
    assert decision["selected"] == "tool" and not decision["marked_for_pause"]
    assert decision["tool_retention_weight"] == .01
    assert decision["physical_capacity_credit"] is False


@pytest.mark.parametrize("known", (False, True))
def test_work_ranking_compares_blocked_owners_within_a_common_retention_band(known):
    states = {
        name: WorkflowToolState(True, True, weight)
        for name, weight in (("short", .02), ("long", .1))
    }
    live = policy(states, {"short": 1., "long": 100.} if known else {})
    live.programs = {
        "short": ThunderProgram("short", 20, steps=2, acting=True, active=True),
        "long": ThunderProgram("long", 90, steps=2, acting=True, active=True),
    }
    live.tick(now=10.)
    selected = "long" if known else "short"
    assert not live.programs[selected].active
    assert live.last_pressure[0]["prediction_used"] is known


def test_retention_takes_precedence_over_work_when_a_tool_will_return_soon():
    states = {
        "soon": WorkflowToolState(True, True, 1.),
        "long-wait": WorkflowToolState(True, True, 0.),
    }
    live = policy(states, {"soon": 1000., "long-wait": 1.})
    live.programs = {
        name: ThunderProgram(name, size, steps=2, acting=True, active=True)
        for name, size in (("soon", 20), ("long-wait", 90))
    }
    live.tick(now=10.)
    assert live.programs["soon"].active and not live.programs["long-wait"].active


def test_a_paused_blocked_owner_is_not_reactivated_until_a_real_successor_is_ready():
    states = {
        "tool": WorkflowToolState(True, True, 0.),
        "ready": WorkflowToolState(),
    }
    live = policy(states)
    live.programs = {
        "tool": ThunderProgram("tool", 20, steps=2, acting=True),
        "ready": ThunderProgram("ready", 20, steps=1, pending=True),
    }
    assert live.tick(now=10.) == ("ready",)
    states["tool"] = WorkflowToolState()
    live.programs["tool"].pending, live.programs["tool"].acting = True, False
    assert live.tick(now=11.) == ("tool",)


@pytest.mark.parametrize("state", (
    WorkflowToolState(True, False, 0.),
    WorkflowToolState(True, True, 0., protected=True),
))
def test_an_unsafe_or_protected_owner_is_never_discounted_or_immediately_paused(state):
    states = {"tool": state, "ready": WorkflowToolState()}
    live = policy(states)
    live.programs = {
        "tool": ThunderProgram("tool", 90, active=True, acting=True, steps=2),
        "ready": ThunderProgram("ready", 20, pending=True, steps=2),
    }
    assert live.remaining_capacity_with_decay(now=10.) == 10
    assert live.tick(now=10.) == ()
    assert live.programs["tool"].active and not live.last_pressure


def test_mutable_compute_only_gets_boundary_marks_and_no_free_capacity_credit():
    states = {name: WorkflowToolState() for name in ("short", "long")}
    live = policy(states, {"short": 1., "long": 100.})
    live.programs = {
        name: ThunderProgram(name, size, steps=2, pending=True, active=True)
        for name, size in (("short", 20), ("long", 90))
    }
    live.tick(now=10.)
    assert live.remaining_capacity() == -10
    assert live.programs["long"].marked_for_pause and not live.programs["short"].marked_for_pause
    assert all(item.active for item in live.programs.values())
    live.tick(now=11.)
    assert not live.programs["short"].marked_for_pause
    live.completed("long", 90, now=12.)
    assert not live.programs["long"].active


def test_buffers_are_not_discounted_and_state_is_observed_once_per_control():
    live = policy({"tool": WorkflowToolState(True, True, 0.)}, buffer=10)
    live.programs["tool"] = ThunderProgram("tool", 90, acting=True, active=True)
    calls = []
    live.tool_pressure_state = lambda item, now: (
        calls.append((item.identity, now)) or WorkflowToolState(True, True, 0.)
    )
    assert live.remaining_capacity_with_decay(now=10.) == 90
    live.tick(now=10.)
    assert calls == [("tool", 10.)]
    live.tick(now=12.)
    assert calls == [("tool", 10.), ("tool", 12.)]


def observer(runtime, admission, *, identity="long", age=10.):
    helper = WorkflowToolPressure(admission)
    admission._tool_pressure = helper
    admission.workflow_pressure_pause = True
    admission.pressure_pause_policy = "workflow_tool_aware"
    admission.policy.tool_pressure_state = helper.observe
    admission.policy.pressure_preference = admission._pressure_preference
    for name, previous in tuple(admission.latest.items()):
        current = replace(previous, session_id="pressure-" + name, session_generation=1)
        admission.latest[name] = current
        admission.requests[current.request_id] = current, admission.requests[current.request_id][1]
        runtime.visible[current.request_id] = current
        runtime.context_sessions[current.context_id] = current
        request = admission._live_requests[current.request_id]
        request.session_id, request.session_generation = current.session_id, current.session_generation
    item = admission.policy.programs[identity]
    key = admission.latest[identity]
    invocation = runtime.graph.invocations[identity]
    invocation.state = InvocationState.WAIT_TOOL
    invocation.active_tool_calls["tool-run"] = "execute"
    request = admission._live_requests[key.request_id]
    request.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    request.output_ids.clear()
    request.finished = lambda: False
    request.is_retracted = request.retracted_stain = False
    now = time.monotonic()
    helper.starts[identity] = {"tool-run": now - age}
    return helper, item, key, request, now


def test_real_control_preserves_a_ready_child_when_the_registered_tool_successor_is_still_blocked(
    monkeypatch, tmp_path,
):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        helper, item, key, request, now = observer(runtime, a)
        item.active = True
        a.policy.capacity_tokens, a.policy.buffer_tokens = 350, 0
        a.next_tick = 0.
        a.tick(now=now)
        assert a.policy.programs["short"].active
        assert not item.active and item.pending
        assert not a.policy.programs["short"].marked_for_pause
        assert a.policy.remaining_capacity() == 50
        assert a.counts["workflow_pressure_decisions"] == 1
        assert request.kv.kv_allocated_len == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("guard", (
    None, "running", "allocated", "committed", "row", "output", "retracted", "finished", "stale",
    "stale_attempt",
))
def test_pending_before_tool_end_is_blocked_but_only_an_unstarted_request_can_pause(
    monkeypatch, tmp_path, guard,
):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        helper, item, key, request, now = observer(runtime, a)
        if guard == "running":
            runtime._native_running_batch = NS(reqs=[request])
        elif guard == "allocated":
            request.kv.kv_allocated_len = 1
        elif guard == "committed":
            request.kv.kv_committed_len = 1
        elif guard == "row":
            request.kv.req_pool_idx = 0
        elif guard == "output":
            request.output_ids.append(1)
        elif guard == "retracted":
            request.is_retracted = True
        elif guard == "finished":
            request.finished = lambda: True
        elif guard == "stale":
            runtime.context_sessions.pop(key.context_id)
        elif guard == "stale_attempt":
            request.cache_request_handle.attempt_id += 1
        state = helper.observe(item, now)
        assert state.pause_safe is (guard is None)
        if guard != "stale":
            assert state.blocked
        assert item.pending and not item.acting
        assert state.weight == (2. ** -8 if guard is None else 1.)
        assert request.kv.kv_allocated_len == (1 if guard == "allocated" else 0)
    finally:
        runtime.close()


def test_cpu_candidate_preserves_tool_times_and_retains_event_replacement():
    root = Path(__file__).resolve().parents[1]
    base = root / (
        "experiments/reports/remaining_original_projects_20261030/"
        "event_tool_replacement_window2s_cpu_r1/full.json"
    )
    arrivals = root / (
        "experiments/reports/remaining_original_projects_20261030/"
        "sustained56_initial12_mean70_r1/full.json"
    )
    before = base.read_bytes()
    common = json.loads(before)["common_environment"]
    candidate = prepare(
        base, arrivals, ranking=True,
        oracle_path=Path(common["WORKFLOW_WORK_MODEL"]),
        oracle_sha256=common["WORKFLOW_WORK_MODEL_SHA256"],
    )
    assert base.read_bytes() == before
    assert "AGENT_TOOL_REPLACEMENT" not in candidate["common_environment"]
    assert candidate["common_environment"]["AGENT_PRESSURE_PAUSE_POLICY"] == "workflow_tool_aware"
    assert candidate["comparison_contract"]["free_dispatch_coalescing_window_ms"] == 2000
    assert candidate["comparison_contract"]["tool_prefetch_disabled_by_user"]
    assert not candidate["comparison_contract"]["layer_handoff"]
    assert candidate["comparison_contract"]["predicted_tool_replacement_bypasses_coalescing_window"]
    assert not candidate["gpu_execution"]["registered"]


def test_a_new_tool_after_a_long_model_gap_resets_short_wait_protection(monkeypatch, tmp_path):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        helper, item, key, request, now = observer(runtime, a)
        a.policy.acting_since[item.identity] = now - 100.
        assert helper.observe(item, now).weight < 1.
        helper.events((
            event(30, Kind.TOOL_END, invocation_id=item.identity, attributes={"tool_run_id": "tool-run"}),
            event(31, Kind.TOOL_START, invocation_id=item.identity, attributes={"tool_run_id": "new-run"}),
        ))
        runtime.graph.invocations[item.identity].active_tool_calls = {"new-run": "execute"}
        state = helper.observe(item, time.monotonic())
        assert state.weight == 1. and state.source == "short_tool_grace"
    finally:
        runtime.close()


@pytest.mark.parametrize("ranking", (False, True))
def test_matched_control_changes_actual_selection_without_changing_window_or_bookkeeping(
    monkeypatch, tmp_path, ranking,
):
    runtime, a = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False,
        window_ms=2000, ranking=ranking,
    )
    try:
        deadline = a._deferred_dispatch.next_dispatch
        a.next_tick = 0.
        a.tick(now=deadline + .01)
        selected = "short" if ranking else "long"
        assert {item.identity for item in a.policy.programs.values() if item.active} == {selected}
        state = a.status()
        assert state["workflow_oracle_coalescing_window_ms"] == 2000
        assert state["workflow_oracle_remaining_work_ranking_enabled"] is ranking
        assert state["workflow_oracle_future_work_used_for_decisions"] is ranking
        assert state["workflow_work_predictor"]["future_trace_used_online"] is True
        assert a._pressure_preference(a.policy.programs["long"], deadline) == (
            a._rank("long", deadline) if ranking else None
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("probability,weight", ((.02, .08), (.8, 1.), (None, 2. ** -8)))
def test_live_age_conditioned_forecast_or_elapsed_fallback_drives_tool_weight(
    monkeypatch, tmp_path, probability, weight,
):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        helper, item, key, request, now = observer(runtime, a)
        calls = []
        runtime.tool_offload_min_survival = .75
        monkeypatch.setattr(runtime, "_live_tool_hint", lambda *_args, **_kwargs: NS(
            predictor_sha256=runtime.predictor_sha256,
            release_probability_within=lambda horizon, now_ms: (
                calls.append((horizon, now_ms)) or probability
            ),
        ))
        state = helper.observe(item, now)
        assert state.weight == pytest.approx(weight)
        assert calls == [(2000., now * 1000.)]
        assert state.return_probability == probability
    finally:
        runtime.close()


def test_large_roundtrip_cost_extends_the_short_return_horizon_without_scanning_kv(
    monkeypatch, tmp_path,
):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        helper, item, key, request, now = observer(runtime, a)
        runtime._residency_pool_bytes = (1_000_000_000, 0)
        state = helper.observe(item, now)
        assert state.horizon_ms == 25000.
        assert not runtime.counts["agent_blocked_backing_probe_nodes"]
    finally:
        runtime.close()


@pytest.mark.parametrize("protection", ("selected_restore", "join", "lease"))
def test_selected_restoration_and_ready_join_rights_survive_tool_aware_pressure(
    monkeypatch, tmp_path, protection,
):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        helper, item, key, request, now = observer(runtime, a)
        if protection == "selected_restore":
            a._residency_pause = NS(compared_requests={key}, close=lambda: None)
        elif protection == "join":
            runtime._join_priority_pending[item.identity] = "join"
            monkeypatch.setattr(runtime, "_ready_join_parent", lambda *_args: True)
        else:
            runtime._prefetch_service_leases["test"] = NS(key=key, expires_at=now + 2.)
        assert helper.observe(item, now).protected
        assert helper.observe(item, now).weight == 1.
    finally:
        runtime._prefetch_service_leases.clear()
        runtime.close()


@pytest.mark.parametrize("mode", MODES)
def test_new_pressure_policy_is_explicit_and_isolated_from_controls(tmp_path, mode):
    configured = arm_environment({
        **settings(tmp_path), "AGENT_PRESSURE_PAUSE_POLICY": "workflow_tool_aware",
    }, mode)
    assert configured["BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY"] == (
        "workflow_tool_aware" if mode == "full" else "reference"
    )


def test_actual_entry_binds_tool_aware_policy_without_an_extra_replacement_switch(monkeypatch, tmp_path):
    configured = arm_environment({
        **settings(tmp_path), "AGENT_PRESSURE_PAUSE_POLICY": "workflow_tool_aware",
    }, "full")
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "tools.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        a = runtime._agent_admission
        assert a.policy.tool_pressure_state is not None
        assert not hasattr(runtime, "agent_tool_replacement")
        validate_reference_admission(
            a.status(), control_interval_ms=2000, free_continuation_admission=True,
            workflow_size_resume=True, workflow_work_model_sha256=runtime.workflow_work_model_sha256,
            pressure_pause_policy="workflow_tool_aware",
        )
        verified = preflight_runtime(configured, str(tmp_path))["verified_configuration"]
        assert verified["agent_pressure_pause_policy"] == "workflow_tool_aware"
    finally:
        runtime.close()
