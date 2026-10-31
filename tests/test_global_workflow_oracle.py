from dataclasses import replace
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_sibling_coadmission import group
from tests.test_workflow_size_admission import settings
from tests.test_workflow_work_oracle import add_serial_graphs, oracle_file, source


def queued(monkeypatch, tmp_path, *, global_order, activate_first=True, window_ms=0):
    raw = source()
    raw["requests"], raw["workflows"] = {}, {}
    if global_order:
        raw["admission_order"] = "global_ready_remaining_path"
    if window_ms:
        raw["free_dispatch_coalescing_window_ms"] = window_ms
    for name, tokens, outputs in (("active", 400, 10000), ("long", 100, 1000), ("short", 300, 10)):
        workflow = name + "-work"
        raw["requests"][name + "-request"] = {
            "workflow_id": workflow, "invocation_id": name, "context_id": name + "-context",
            "prompt_tokens": tokens, "output_tokens": outputs, "input_increment": tokens,
            "predecessor_request_id": None,
        }
        raw["workflows"][workflow] = {"total_work_units": tokens + 100 * outputs, "requests": 1}
    add_serial_graphs(raw)
    path, digest = oracle_file(tmp_path, raw)
    environment = arm_environment({
        **settings(tmp_path), "WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": path, "WORKFLOW_WORK_MODEL_SHA256": digest,
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
    }, "full")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "global.sock"))
    runtime.attach_native_cache(NS(tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=500)))
    a = runtime._agent_admission
    a.next_tick = float("inf")
    for index, (name, tokens) in enumerate((("active", 400), ("long", 100), ("short", 300))):
        workflow = name + "-work"
        runtime.on_events((
            replace(observed(index * 2, Kind.WORKFLOW_START), workflow_id=workflow),
            replace(observed(index * 2 + 1, Kind.INVOCATION_CREATE, invocation_id=name,
                             context_id=name + "-context"), workflow_id=workflow),
        ))
        req = request(name + "-request", name, workflow=workflow, tokens=tokens)
        req.output_ids.clear()
        runtime.register_visible_request(req)
        if global_order and activate_first and index == 0:
            a.tick()
    a.policy.programs["long"].steps = 2
    return runtime, a


def test_global_oracle_coalesces_first_registrations_before_choosing_actual_service(monkeypatch, tmp_path):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        assert not any(item.active for item in a.policy.programs.values())
        a.tick()
        assert {item.identity for item in a.policy.programs.values() if item.active} == {"short"}
        assert a.status()["workflow_oracle_eager_first_grant"] is False
        assert a.status()["workflow_oracle_shared_free_dispatch"] is True
        requests = list(a._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert tuple(req.rid for req in selected.candidates) == ("short-request",)
    finally:
        runtime.close()


@pytest.mark.parametrize("boundary", ("periodic", "dispatch"))
@pytest.mark.parametrize("global_order", (False, True))
def test_explicit_global_oracle_changes_actual_service_selection_across_categories(
    monkeypatch, tmp_path, boundary, global_order,
):
    runtime, a = queued(monkeypatch, tmp_path, global_order=global_order)
    try:
        a.forget("active")
        if boundary == "periodic":
            a.next_tick = 0.
        a.tick(now=time.monotonic())
        selected = "short" if global_order else "long"
        assert {item.identity for item in a.policy.programs.values() if item.active} == {selected}
        requests = [a._live_requests[name + "-request"] for name in ("long", "short")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert tuple(req.rid for req in actual.candidates) == (selected + "-request",)
        assert a.status()["workflow_oracle_admission_order"] == (
            "global_ready_remaining_path" if global_order else "within_reference_category"
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("boundary", ("periodic", "dispatch"))
def test_global_oracle_preserves_ready_join_parent_priority(monkeypatch, tmp_path, boundary):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True)
    try:
        a.forget("active")
        runtime._join_priority_pending["long"] = "observed-join"
        monkeypatch.setattr(runtime, "_ready_join_parent", lambda req, key: key.invocation_id == "long")
        if boundary == "periodic":
            a.next_tick = 0.
        a.tick(now=time.monotonic())
        assert a.policy.programs["long"].active and not a.policy.programs["short"].active
    finally:
        runtime.close()


def test_global_oracle_keeps_active_continuity_and_cannot_admit_a_blocked_short_candidate(
    monkeypatch, tmp_path,
):
    runtime, a = queued(monkeypatch, tmp_path, global_order=True)
    try:
        a.tick()
        assert a.policy.programs["active"].active and not a.policy.programs["short"].active
        a.forget("active")
        runtime.graph.invocations["short"].active_tool_calls["t"] = "execute"
        a.next_tick = 0.
        a.tick()
        assert a.policy.programs["long"].active and not a.policy.programs["short"].active
    finally:
        runtime.close()


def test_global_sibling_notifications_share_free_budget_comparison_with_another_workflow(
    monkeypatch, tmp_path,
):
    raw = source()
    raw.update(admission_order="global_ready_remaining_path", requests={}, workflows={})
    tasks = (("child-0", "wf", 1000), ("child-1", "wf", 1000), ("peer", "other", 10))
    for name, workflow, outputs in tasks:
        raw["requests"][name + "-request"] = {
            "workflow_id": workflow, "invocation_id": name, "context_id": name + "-context",
            "prompt_tokens": 300, "output_tokens": outputs, "input_increment": 300,
            "predecessor_request_id": None,
        }
        item = raw["workflows"].setdefault(workflow, {"total_work_units": 0, "requests": 0})
        item["total_work_units"] += 300 + 100 * outputs
        item["requests"] += 1
    add_serial_graphs(raw)
    path, digest = oracle_file(tmp_path, raw)
    environment = arm_environment({
        **settings(tmp_path), "WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": path, "WORKFLOW_WORK_MODEL_SHA256": digest,
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500", "AGENT_COADMIT_JOIN_CHILDREN": "1",
    }, "full")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "global-siblings.sock"))
    runtime.attach_native_cache(NS(tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=900)))
    try:
        group(runtime)
        runtime.on_events((
            replace(observed(20, Kind.WORKFLOW_START), workflow_id="other"),
            replace(observed(21, Kind.INVOCATION_CREATE, invocation_id="peer", context_id="peer-context"),
                    workflow_id="other"),
        ))
        a = runtime._agent_admission
        a.next_tick = float("inf")
        for name, workflow, _outputs in tasks:
            req = request(name + "-request", name, workflow=workflow, tokens=300)
            req.output_ids.clear()
            runtime.register_visible_request(req)
        assert not any(item.active for item in a.policy.programs.values())
        assert not a.counts["sibling_free_programs_admitted"]
        a.tick()
        active = {item.identity for item in a.policy.programs.values() if item.active}
        assert "peer" in active and len(active & {"child-0", "child-1"}) == 1
        assert a.policy.remaining_capacity() >= 0
        requests = list(a._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert "peer-request" in {req.rid for req in actual.candidates}
    finally:
        runtime.close()
