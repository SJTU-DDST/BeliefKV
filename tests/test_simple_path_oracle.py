from copy import deepcopy
from dataclasses import replace
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.tool_boundary_preemption import ToolBoundaryPreemption
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle
from scripts.prepare_simple_path_oracle import prepare_artifact
from tests.test_full_only_native_lifecycle import full_native
from tests.test_global_workflow_oracle import queued
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_sibling_coadmission import group
from tests.test_reference_tool_replacement import run
from tests.test_tool_boundary_preemption import enabled
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event
from tests.test_workflow_size_admission import settings
from tests.test_workflow_work_oracle import add_serial_graphs, oracle_file, source


def legacy():
    raw = source()
    raw.update(
        admission_order="global_ready_remaining_path",
        free_dispatch_coalescing_window_ms=2000, remaining_work_ranking_enabled=True,
    )
    return raw


def prepared(raw=None, *, tool_boundary_preemption=True):
    original = legacy() if raw is None else deepcopy(raw)
    control = dict(original, remaining_work_ranking_enabled=False)
    return prepare_artifact(
        control, original, check_interval_ms=400,
        tool_boundary_preemption=tool_boundary_preemption,
    )


def runtime_for(monkeypatch, tmp_path, raw, *, capacity=1000):
    path, digest = oracle_file(tmp_path, raw)
    environment = arm_environment({
        **settings(tmp_path), "WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": path, "WORKFLOW_WORK_MODEL_SHA256": digest,
        "WORKFLOW_COMBINATION_ADMISSION": "0",
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500", "AGENT_COADMIT_JOIN_CHILDREN": "1",
    }, "full")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "simple.sock"))
    runtime.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=capacity),
    ))
    runtime._agent_admission.next_tick = float("inf")
    return runtime


def test_preparation_preserves_original_path_work_and_removes_scores():
    raw = prepared()
    old = legacy()
    for name in ("requests", "workflows", "workflow_graphs", "model_seconds_per_work_token"):
        assert raw[name] == old[name]
    assert raw["kind"] == old["kind"]
    assert raw["admission_order"] == "global_ready_remaining_path"
    assert raw["free_dispatch_coalescing_window_ms"] == 2000
    assert not any(name in raw for name in (
        "admission_cost_profile", "ready_wait_priority_ms", "ready_wait_priority_max_boost",
        "ready_capacity_accumulation_ms", "cost_aware_maximal_admission",
        "maximal_admission_objective",
    ))
    boundary = raw["tool_boundary_preemption"]
    assert boundary["ready_wait_protection_ms"] == 0
    assert boundary["min_work_ratio"] == 1
    assert boundary["cooldown_ms"] == boundary["returned_boundary_ttl_ms"] == 2000
    assert boundary["min_switch_interval_ms"] == boundary["margin_ms"] == 400


def test_actual_constructor_uses_original_selector_and_keeps_boundary_preemption(monkeypatch, tmp_path):
    runtime = runtime_for(monkeypatch, tmp_path, prepared())
    try:
        a = runtime._agent_admission
        assert a.rank_before_category and a._combination is None
        assert a._deferred_dispatch.window_ms == 2000
        assert a._tool_boundary_preemption.bounded and a._tool_boundary_preemption.critical_path
        status = a.status()
        assert status["workflow_shortest_work_first_admission"]
        assert status["workflow_public_timeout_force_resume"] is False
        assert status["workflow_oracle_selection"] == "remaining_model_tool_path_then_greedy_fit"
        assert "workflow_ready_wait_priority" not in status
        assert "workflow_ready_capacity_accumulation" not in status
    finally:
        runtime.close()


def test_disabling_boundary_preemption_changes_only_its_artifact_field():
    enabled_raw = prepared()
    disabled_raw = prepared(tool_boundary_preemption=False)
    assert enabled_raw.pop("tool_boundary_preemption")
    assert disabled_raw == enabled_raw
    assert prepared(disabled_raw, tool_boundary_preemption=False) == disabled_raw


def test_future_preparation_defaults_to_no_additional_boundary_preemption():
    original = legacy()
    control = dict(original, remaining_work_ranking_enabled=False)
    assert prepare_artifact(control, original, check_interval_ms=400) == prepared(
        tool_boundary_preemption=False,
    )


def test_disabled_boundary_cannot_be_inherited_from_a_prepared_input():
    assert prepared(prepared(), tool_boundary_preemption=False) == prepared(
        tool_boundary_preemption=False,
    )


def test_disabled_boundary_keeps_original_selector_and_sibling_support(monkeypatch, tmp_path):
    runtime = runtime_for(monkeypatch, tmp_path, prepared(tool_boundary_preemption=False))
    try:
        a = runtime._agent_admission
        assert a.rank_before_category and a._combination is None
        assert a._tool_boundary_preemption is None
        assert a._siblings is not None
        assert a._deferred_dispatch.window_ms == 2000
        assert a.status()["workflow_oracle_selection"] == "remaining_model_tool_path_then_greedy_fit"
        assert a.status()["workflow_size_new_preemption"] is False
    finally:
        runtime.close()


@pytest.mark.parametrize("value", (None, 0, 1, "false"))
def test_boundary_preparation_rejects_ambiguous_enablement(value):
    with pytest.raises(ValueError, match="explicitly enabled or disabled"):
        prepared(tool_boundary_preemption=value)


@pytest.mark.parametrize("forecast_allowed", (False, True))
def test_disabled_boundary_preserves_forecast_driven_native_replacement(
    demand_runtime, tmp_path, forecast_allowed,
):
    parts, _boundary, _req = enabled(demand_runtime, tmp_path, hint=True)
    runtime, a, controller, _adder = parts
    raw = deepcopy(a._work.artifact.raw)
    raw["kind"] = source()["kind"]
    raw["admission_order"] = "global_ready_remaining_path"
    for name in (
        "combination_work_readout", "combination_work_reference",
        "combination_reference_source_model_sha256",
    ):
        raw.pop(name, None)
    raw = prepared(raw, tool_boundary_preemption=False)
    a._work.close()
    a._work = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    a._tool_boundary_preemption = None
    if not forecast_allowed:
        hint = next(iter(runtime.tool_wait_hints.values()))
        now = time.monotonic() * 1000.
        runtime.tool_wait_hints[hint.key.context_id] = replace(
            hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
            issued_monotonic_ms=now, expires_monotonic_ms=now + 1000.,
            release_cdf=(), residual_duration_mass=(),
        )
    controller.rearm()
    assert run(parts) is forecast_allowed
    assert a.policy.programs["short"].active is forecast_allowed
    assert a.policy.programs["tool"].active is not forecast_allowed
    assert (demand_runtime[4].component_data[0].value is None) is forecast_allowed
    assert not controller.action_detail.get("tool_boundary_preemption")


@pytest.mark.parametrize("field,value", (
    ("ready_wait_priority_ms", 30_000), ("ready_wait_priority_max_boost", 1.25),
    ("cost_aware_maximal_admission", True), ("maximal_admission_objective", "benefit"),
    ("ready_capacity_accumulation_ms", 2000),
))
def test_original_selector_rejects_priority_noise(monkeypatch, tmp_path, field, value):
    raw = prepared()
    raw[field] = value
    with pytest.raises(ValueError, match="excludes aging"):
        runtime_for(monkeypatch, tmp_path, raw)


def test_long_ready_wait_cannot_bypass_short_priority_or_native_service(monkeypatch, tmp_path):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        now = time.monotonic()
        key, _arrived = a.requests["long-request"]
        a.requests["long-request"] = key, now - 1801.
        a.tick(now=now)
        assert {item.identity for item in a.policy.programs.values() if item.active} == {"short"}
        assert not a.counts["public_timeout_force_resumed"]
        requests = list(a._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        selection = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert tuple(req.rid for req in selection.candidates) == ("short-request",)
    finally:
        runtime.close()


def test_tool_time_changes_order_without_bins_or_aggregate_utility(monkeypatch, tmp_path):
    raw = source()
    raw.update(admission_order="global_ready_remaining_path", requests={}, workflows={})
    tasks = (("small-model-long-tool", 10), ("larger-model-short-path", 100))
    for name, outputs in tasks:
        raw["requests"][name] = {
            "workflow_id": name, "invocation_id": name, "context_id": name + "-context",
            "prompt_tokens": 300, "output_tokens": outputs, "input_increment": 300,
            "predecessor_request_id": None,
        }
        raw["workflows"][name] = {"total_work_units": 300 + 100 * outputs, "requests": 1}
    add_serial_graphs(raw)
    raw["workflow_graphs"][tasks[0][0]][-1]["delay_s"] = 100.
    runtime = runtime_for(monkeypatch, tmp_path, raw, capacity=500)
    try:
        for index, (name, _outputs) in enumerate(tasks):
            runtime.on_events((
                replace(observed(index * 2, Kind.WORKFLOW_START), workflow_id=name),
                replace(observed(index * 2 + 1, Kind.INVOCATION_CREATE, invocation_id=name,
                                 context_id=name + "-context"), workflow_id=name),
            ))
            runtime.register_visible_request(request(name, name, workflow=name, tokens=300))
        a = runtime._agent_admission
        a.tick()
        assert {item.identity for item in a.policy.programs.values() if item.active} == {
            "larger-model-short-path",
        }
        assert a._work.prediction(tasks[0][0]).remaining_work < a._work.prediction(tasks[1][0]).remaining_work
        assert a._work.prediction(tasks[0][0]).remaining_path_s > a._work.prediction(tasks[1][0]).remaining_path_s
    finally:
        runtime.close()


@pytest.mark.parametrize("boundary", ("periodic", "dispatch"))
@pytest.mark.parametrize("capacity,expected", ((700, {"child-0"}), (1000, {"child-0", "child-1"})))
def test_ready_siblings_get_actual_service_before_long_peer_when_they_fit(
    monkeypatch, tmp_path, capacity, expected, boundary,
):
    raw = source()
    raw.update(admission_order="global_ready_remaining_path", requests={}, workflows={})
    tasks = (("child-0", "wf", 10), ("peer", "other", 1000), ("child-1", "wf", 10))
    for name, workflow, outputs in tasks:
        raw["requests"][name] = {
            "workflow_id": workflow, "invocation_id": name, "context_id": name + "-context",
            "prompt_tokens": 300, "output_tokens": outputs, "input_increment": 300,
            "predecessor_request_id": None,
        }
        work = raw["workflows"].setdefault(workflow, {"total_work_units": 0, "requests": 0})
        work["total_work_units"] += 300 + 100 * outputs
        work["requests"] += 1
    add_serial_graphs(raw)
    runtime = runtime_for(monkeypatch, tmp_path, raw, capacity=capacity)
    try:
        group(runtime)
        runtime.on_events((
            replace(observed(20, Kind.WORKFLOW_START), workflow_id="other"),
            replace(observed(21, Kind.INVOCATION_CREATE, invocation_id="peer",
                             context_id="peer-context"), workflow_id="other"),
        ))
        for name, workflow, _outputs in tasks:
            runtime.register_visible_request(request(name, name, workflow=workflow, tokens=300))
        a = runtime._agent_admission
        if boundary == "periodic":
            a.next_tick = 0.
            a.tick()
        else:
            a._deferred_dispatch.dispatch(time.monotonic())
        assert {item.identity for item in a.policy.programs.values() if item.active} == expected
        requests = list(a._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        selection = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert {req.rid for req in selection.candidates} == expected
        assert a.policy.remaining_capacity() >= 0
    finally:
        runtime.close()


@pytest.mark.parametrize("tool_delay,allow", ((0., True), (100., False)))
def test_actual_native_tool_handover_uses_the_same_path_order(
    demand_runtime, tmp_path, tool_delay, allow,
):
    parts, old, req = enabled(demand_runtime, tmp_path)
    runtime, a, controller, _adder = parts
    raw = deepcopy(a._work.artifact.raw)
    raw["kind"] = source()["kind"]
    raw["admission_order"] = "global_ready_remaining_path"
    for name in (
        "combination_work_readout", "combination_work_reference",
        "combination_reference_source_model_sha256",
    ):
        raw.pop(name, None)
    raw = prepared(raw)
    raw["workflow_graphs"]["short-work"][-1]["delay_s"] = tool_delay
    a._work.close()
    a._work = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    boundary = a._tool_boundary_preemption = ToolBoundaryPreemption(a, raw)
    boundary.events((event(30, Kind.TOOL_START, invocation_id="tool"),))
    controller.rearm()
    assert run(parts) is allow
    assert a.policy.programs["short"].active is allow
    assert a.policy.programs["tool"].active is not allow
    assert boundary.status()["priority_basis"] == "remaining_model_tool_critical_path_seconds"
    assert not boundary.protected


def test_global_boundary_long_wait_does_not_create_an_age_protection(demand_runtime, tmp_path):
    parts, old, req = enabled(demand_runtime, tmp_path)
    runtime, a, controller, _adder = parts
    raw = deepcopy(a._work.artifact.raw)
    raw["kind"] = source()["kind"]
    raw["admission_order"] = "global_ready_remaining_path"
    for name in (
        "combination_work_readout", "combination_work_reference",
        "combination_reference_source_model_sha256",
    ):
        raw.pop(name, None)
    a._work.close()
    raw = prepared(raw)
    a._work = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    boundary = a._tool_boundary_preemption = ToolBoundaryPreemption(a, raw)
    key = a.latest["tool"]
    boundary.ready_since[key] = time.monotonic() * 1000. - 100_000.
    boundary.serviced(NS(reqs=[NS(rid=key.request_id)]))
    assert not boundary.protected
