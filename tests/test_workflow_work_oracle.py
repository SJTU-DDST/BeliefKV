from copy import deepcopy
import hashlib
import json
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.workflow_work_oracle import KIND, WorkflowWorkOracle, WorkflowWorkOracleArtifact
from scripts import run_causal_comparison as comparison
from tests.test_paper_baseline_runtime import request
from tests.test_reference_sibling_coadmission import group
from tests.test_workflow_size_admission import settings


def source():
    raw = {
        "kind": KIND, "future_information_used": True, "deployable": False,
        "prefill_token_weight": .01, "work_thresholds": [100., 1000.],
        "workflows": {"wf": {"total_work_units": 4700, "requests": 2}},
        "requests": {
            "one": {
                "workflow_id": "wf", "invocation_id": "child", "context_id": "ctx",
                "prompt_tokens": 500, "output_tokens": 30, "input_increment": 500,
                "predecessor_request_id": None,
            },
            "two": {
                "workflow_id": "wf", "invocation_id": "child", "context_id": "ctx",
                "prompt_tokens": 730, "output_tokens": 10, "input_increment": 200,
                "predecessor_request_id": "one",
            },
        },
    }
    add_serial_graphs(raw)
    return raw


def node(identity, dependencies=(), *, request_id=None, event_id=None, kind="native_request", delay=0.):
    return {
        "node_id": identity, "dependencies": list(dependencies), "delay_s": delay,
        "request_id": request_id, "event_id": event_id, "kind": kind,
    }


def add_serial_graphs(raw):
    raw["workflow_graphs"] = {}
    raw["model_seconds_per_work_token"] = .02
    for workflow in raw["workflows"]:
        graph = raw["workflow_graphs"].setdefault(workflow, [])
        for request, item in raw["requests"].items():
            if item["workflow_id"] == workflow:
                graph.append(node(
                    "request:" + request, (len(graph) - 1,) if graph else (),
                    request_id=request,
                ))
        graph.append(node(
            "event:end:" + workflow, (len(graph) - 1,), event_id="end:" + workflow,
            kind=Kind.WORKFLOW_END.value,
        ))


def oracle_file(tmp_path, raw=None):
    path = tmp_path / "oracle.json"
    path.write_text(json.dumps(source() if raw is None else raw))
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def configured(tmp_path):
    path, digest = oracle_file(tmp_path)
    return {
        **settings(tmp_path), "WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": path, "WORKFLOW_WORK_MODEL_SHA256": digest,
    }


def test_native_progress_exact_remaining_work_and_completion_conservation(tmp_path):
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path))
    assert oracle.prediction("wf").remaining_work == 47.
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.submit("wf", "generated", "one", 17)
    assert oracle.prediction("wf").remaining_work == 30.
    oracle.submit("wf", "completed", "one", "child", "ctx", range(500), range(30))
    assert oracle.prediction("wf").remaining_work == 12.
    oracle.submit("wf", "completed", "one", "child", "ctx", range(500), range(30))
    oracle.submit("wf", "started", "two", "child", "ctx", range(730))
    oracle.submit("wf", "completed", "two", "child", "ctx", range(730), range(10))
    assert oracle.prediction("wf").remaining_work == 0.
    oracle.submit("wf", "event", NS(kind=Kind.WORKFLOW_END, workflow_id="wf"))
    assert oracle.prediction("wf") is None
    status = oracle.status()
    assert status["counts"]["completed_requests"] == 2
    assert status["counts"]["observed_output_tokens"] == 40
    assert status["counts"]["observed_input_tokens"] == 700
    assert status["future_trace_used_online"] is True
    assert status["deployable"] is False and status["queue_depth"] == 0


@pytest.mark.parametrize("action,args", (
    ("started", ("missing", "child", "ctx", range(500))),
    ("started", ("one", "other", "ctx", range(500))),
    ("started", ("one", "child", "other", range(500))),
    ("started", ("one", "child", "ctx", range(499))),
    ("started", ("two", "child", "ctx", range(730))),
    ("generated", ("one", 10)),
    ("completed", ("one", "child", "ctx", range(500), range(29))),
    ("event", (NS(kind=Kind.WORKFLOW_END, workflow_id="wf"),)),
))
def test_unknown_wrong_identity_early_successor_or_truncated_work_is_rejected(tmp_path, action, args):
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path))
    with pytest.raises(ValueError):
        oracle.submit("wf", action, *args)
    assert oracle.status()["error"] and oracle.prediction("wf") is None


@pytest.mark.parametrize("output", (-1, 31, 9))
def test_output_progress_cannot_exceed_source_or_move_backwards(tmp_path, output):
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path))
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.submit("wf", "generated", "one", 10)
    with pytest.raises(ValueError):
        oracle.submit("wf", "generated", "one", output)


@pytest.mark.parametrize("mutation", (
    lambda raw: raw.update(future_information_used=False),
    lambda raw: raw.update(deployable=True),
    lambda raw: raw["workflows"]["wf"].update(total_work_units=4701),
    lambda raw: raw["requests"]["two"].update(predecessor_request_id=None),
    lambda raw: raw["requests"]["one"].update(predecessor_request_id="two"),
    lambda raw: raw["requests"]["two"].update(input_increment=731),
))
def test_oracle_artifact_rejects_undeclared_future_work_and_broken_serial_population(mutation):
    raw = deepcopy(source())
    mutation(raw)
    with pytest.raises(ValueError):
        WorkflowWorkOracleArtifact(raw)


@pytest.mark.parametrize("mode", MODES)
def test_oracle_provider_is_full_only_and_control_paths_are_isolated(tmp_path, mode):
    value = arm_environment(configured(tmp_path), mode)
    assert value["BELIEFKV_WORKFLOW_WORK_SOURCE"] == ("oracle" if mode == "full" else "learned")
    assert bool(value["BELIEFKV_WORKFLOW_WORK_MODEL"]) == (mode == "full")


def test_real_runtime_and_constructor_preflight_require_explicit_oracle(monkeypatch, tmp_path):
    value = arm_environment(configured(tmp_path), "full")
    result = comparison.preflight_runtime(value, str(tmp_path))
    assert result["verified_configuration"]["workflow_work_source"] == "oracle"
    for name, item in value.items():
        monkeypatch.setenv(name, item)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "oracle.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        state = runtime._agent_admission.status()
        assert state["future_information_used"] is True
        validate_reference_admission(
            state, free_continuation_admission=True, control_interval_ms=2000,
            workflow_size_resume=True, workflow_work_source="oracle",
            workflow_work_model_sha256=value["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"],
        )
        with pytest.raises(ValueError):
            validate_reference_admission(
                state, free_continuation_admission=True, control_interval_ms=2000,
                workflow_size_resume=True,
                workflow_work_model_sha256=value["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"],
            )
        assert not runtime.layer_handoff_enabled and not runtime.enable_tool_prefetch
    finally:
        runtime.close()


def test_oracle_does_not_silently_load_as_a_learned_predictor(tmp_path):
    value = arm_environment(configured(tmp_path), "full")
    value["BELIEFKV_WORKFLOW_WORK_SOURCE"] = "learned"
    with pytest.raises((ValueError, KeyError)):
        comparison.preflight_runtime(value, str(tmp_path))


@pytest.mark.parametrize("long_tool", (False, True))
def test_actual_continuous_resume_and_sibling_grouping_use_oracle_work(monkeypatch, tmp_path, long_tool):
    raw = source()
    raw["requests"] = {}
    raw["workflows"] = {}
    for rid, invocation, context, workflow, tokens, outputs in (
        ("a1", "child-0", "child-0-context", "wf", 300, 20),
        ("a2", "child-1", "child-1-context", "wf", 300, 20),
        ("b", "peer", "peer-context", "other", 200, 1000),
    ):
        raw["requests"][rid] = {
            "workflow_id": workflow, "invocation_id": invocation, "context_id": context,
            "prompt_tokens": tokens, "output_tokens": outputs, "input_increment": tokens,
            "predecessor_request_id": None,
        }
        item = raw["workflows"].setdefault(workflow, {"total_work_units": 0, "requests": 0})
        item["total_work_units"] += tokens + 100 * outputs
        item["requests"] += 1
    add_serial_graphs(raw)
    if long_tool:
        graph = raw["workflow_graphs"]["wf"]
        graph.insert(0, node(
            "event:long-tool", event_id="long-tool", kind=Kind.TOOL_END.value, delay=100.,
        ))
        for item in graph[1:]:
            item["dependencies"] = [dep + 1 for dep in item["dependencies"]]
        graph[1]["dependencies"] = [0]
    path, digest = oracle_file(tmp_path, raw)
    value = arm_environment({
        **settings(tmp_path), "WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": path, "WORKFLOW_WORK_MODEL_SHA256": digest,
        "AGENT_COADMIT_JOIN_CHILDREN": "1",
    }, "full")
    for name, item in value.items():
        monkeypatch.setenv(name, item)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "rank.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        group(runtime)
        for rid, invocation, workflow, size in (
            ("a1", "child-0", "wf", 300), ("b", "peer", "other", 200),
            ("a2", "child-1", "wf", 300),
        ):
            runtime.register_visible_request(request(rid, invocation, workflow=workflow, tokens=size))
        admission = runtime._agent_admission
        for item in admission.policy.programs.values():
            item.active = False
        admission.next_tick = 0.
        admission.tick(now=time.monotonic())
        assert {p.identity for p in admission.policy.programs.values() if p.active} == (
            {"peer", "child-1"} if long_tool else {"child-0", "child-1"}
        )
        assert admission._resume_groups == {"child-0": "join", "child-1": "join"}
        assert sorted({row[2] for row in admission.policy.last_selection["ordered"]}) == pytest.approx(
            sorted(((100. if long_tool else 0.) + 46. * .02, 1002. * .02)),
        )
        assert admission.status()["physical_capacity_credit"] is False
    finally:
        runtime.close()


def test_parallel_tools_use_the_longest_branch_and_observed_elapsed_time(monkeypatch, tmp_path):
    raw = source()
    raw["workflow_graphs"]["wf"] = [
        node("event:start-a", event_id="start-a", kind=Kind.TOOL_START.value),
        node("event:end-a", (0,), event_id="end-a", kind=Kind.TOOL_END.value, delay=30.),
        node("event:start-b", event_id="start-b", kind=Kind.TOOL_START.value),
        node("event:end-b", (2,), event_id="end-b", kind=Kind.TOOL_END.value, delay=20.),
        node("request:one", (1, 3), request_id="one"),
        node("request:two", (4,), request_id="two"),
        node("event:end:wf", (5,), event_id="end:wf", kind=Kind.WORKFLOW_END.value),
    ]
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    monkeypatch.setattr(time, "monotonic", lambda: 100.)
    assert oracle.prediction("wf").remaining_path_s == pytest.approx(30.94)
    oracle.submit("wf", "event", NS(
        kind=Kind.TOOL_START, workflow_id="wf", event_id="start-a", ts_ms=90000.,
    ))
    oracle.submit("wf", "event", NS(
        kind=Kind.TOOL_START, workflow_id="wf", event_id="start-b", ts_ms=95000.,
    ))
    prediction = oracle.prediction("wf")
    assert prediction.remaining_path_s == pytest.approx(20.94)
    assert prediction.model_only_path_s == pytest.approx(.94)
    assert prediction.tool_and_fixed_delay_path_s == 20.
    monkeypatch.setattr(time, "monotonic", lambda: 140.)
    assert oracle.prediction("wf").remaining_path_s == pytest.approx(.94)


def test_longer_tool_path_changes_oracle_priority_with_equal_model_work(tmp_path):
    raw = source()
    graph = raw["workflow_graphs"]["wf"]
    graph.insert(0, node(
        "event:tool", event_id="tool", kind=Kind.TOOL_END.value, delay=100.,
    ))
    for item in graph[1:]:
        item["dependencies"] = [dep + 1 for dep in item["dependencies"]]
    graph[1]["dependencies"] = [0]
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    assert oracle.prediction("wf").remaining_work == 47.
    assert oracle.prediction("wf").remaining_path_s == pytest.approx(100.94)
