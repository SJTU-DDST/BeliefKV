from copy import deepcopy
from dataclasses import asdict, replace
from functools import partial
import math
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.workflow_work_oracle import (
    COMBINATION_PATH_KIND, WorkflowWorkOracle, WorkflowWorkOracleArtifact,
)
from scripts.prepare_r5_path_comparison import configuration, nonranking_artifact, path_oracle_artifact
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_workflow_completion_path import path_model
from tests.test_workflow_shortest_frontier import fixture
from tests.test_workflow_size_admission import settings
from tests.test_workflow_work_oracle import add_serial_graphs, node, oracle_file, source


def learned():
    raw = deepcopy(path_model().raw)
    raw.update(
        strict_remaining_work_admission=True, shortest_work_first_admission=True,
        remaining_work_ranking_enabled=True, free_dispatch_coalescing_window_ms=2000,
        admission_cost_profile={
            "kind": "r5_frozen_batch_cost_proxy_capacity_v7", "identity": "path-fixture",
            "costs": asdict(fixture()[2]),
        },
    )
    return raw


def artifact(labels=None):
    return path_oracle_artifact(learned(), source() if labels is None else labels, "a" * 64)


def test_explicit_path_oracle_preserves_work_and_uses_the_shared_strict_selector():
    original, model = source(), learned()
    raw = path_oracle_artifact(model, original, "a" * 64)
    parsed = WorkflowWorkOracleArtifact(raw)
    assert raw["kind"] == COMBINATION_PATH_KIND
    assert parsed.path_combination and parsed.continuous_combination
    assert not parsed.model_work_only and parsed.combination_only
    assert parsed.strict_remaining_work
    for name in ("requests", "workflows", "workflow_graphs", "model_seconds_per_work_token"):
        assert raw[name] == original[name]
    assert raw["admission_cost_profile"]["costs"] == model["admission_cost_profile"]["costs"]
    assert raw["admission_cost_profile"]["oracle_reference_path_s"] == 100.
    assert parsed.rank_kind == "remaining_model_tool_critical_path_seconds"


@pytest.mark.parametrize("mutation", (
    lambda raw: raw.update(strict_remaining_work_admission=False),
    lambda raw: raw.update(ready_wait_priority_ms=30000),
    lambda raw: raw.update(completion_path_readout={}),
    lambda raw: raw.update(admission_order="global_ready_remaining_path"),
    lambda raw: raw["admission_cost_profile"].pop("oracle_reference_path_s"),
    lambda raw: raw["admission_cost_profile"].update(oracle_reference_path_s=math.nan),
))
def test_path_oracle_requires_explicit_seconds_and_unchanged_strict_policy(mutation):
    raw = artifact()
    mutation(raw)
    with pytest.raises(ValueError):
        WorkflowWorkOracleArtifact(raw)


def test_legacy_oracle_is_not_silently_reinterpreted_as_a_path_combination():
    raw = artifact()
    raw["kind"] = source()["kind"]
    with pytest.raises(ValueError):
        WorkflowWorkOracleArtifact(raw)
    model = learned()
    model["completion_path_readout"]["model_seconds_per_work_token"] *= 2
    with pytest.raises(ValueError, match="identical original"):
        path_oracle_artifact(model, source(), "a" * 64)


def test_elapsed_tools_update_path_order_without_changing_remaining_model_work(monkeypatch, tmp_path):
    clock = [100.]
    from beliefkv.runtime import workflow_work_oracle as module

    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    labels = source()
    labels["workflow_graphs"]["wf"] = [
        node("one", request_id="one"),
        node("tool", (0,), event_id="tool:end", kind=Kind.TOOL_END.value, delay=10.),
        node("two", (1,), request_id="two"),
        node("end", (2,), event_id="end:wf", kind=Kind.WORKFLOW_END.value),
    ]
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, artifact(labels)))
    try:
        assert oracle.prediction("wf").remaining_path_s == pytest.approx(10.94)
        oracle.submit("wf", "completed", "one", "child", "ctx", range(500), range(30))
        for now, expected in ((104., 6.24), (107., 3.24)):
            clock[0] = now
            prediction = oracle.prediction("wf")
            assert prediction.remaining_work == 12.
            assert prediction.remaining_path_s == pytest.approx(expected)
            assert prediction.effective_weight == pytest.approx(1. / (1. + expected / 100.))
        assert oracle.counts["path_cache_hits"]
        clock[0] = 110.
        oracle.submit("wf", "event", NS(
            event_id="tool:end", workflow_id="wf", kind=Kind.TOOL_END, ts_ms=110000.,
        ))
        assert oracle.prediction("wf").remaining_path_s == pytest.approx(.24)
        state = oracle.status()
        assert state["shared_R5_path_order"] and state["future_tool_durations_used"]
        assert "continuous_admission_utility" not in state
    finally:
        oracle.close()


def population(entries):
    raw = source()
    raw.update(requests={}, workflows={})
    for rid, invocation, wf, tokens, output in entries:
        raw["requests"][rid] = {
            "workflow_id": wf, "invocation_id": invocation, "context_id": invocation + "-context",
            "prompt_tokens": tokens, "output_tokens": output, "input_increment": tokens,
            "predecessor_request_id": None,
        }
        item = raw["workflows"].setdefault(wf, {"total_work_units": 0, "requests": 0})
        item["requests"] += 1
        item["total_work_units"] += tokens + 100 * output
    add_serial_graphs(raw)
    return raw


@pytest.mark.parametrize("capacity,expected", ((216, {"s1", "s2"}), (65, {"s1"})))
def test_exact_parallel_path_prioritizes_joint_siblings_with_the_same_capacity_policy(
    tmp_path, capacity, expected,
):
    labels = population((
        ("r1", "s1", "short", 60, 100), ("r2", "s2", "short", 60, 100),
        ("r3", "long", "long", 100, 150),
    ))
    labels["workflow_graphs"]["short"] = [
        node("r1", request_id="r1"), node("r2", request_id="r2"),
        node("end:short", (0, 1), event_id="end:short", kind=Kind.WORKFLOW_END.value),
    ]
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, artifact(labels)))
    try:
        short, long = oracle.prediction("short"), oracle.prediction("long")
        assert short.remaining_work > long.remaining_work
        assert short.remaining_path_s < long.remaining_path_s
        rows, resources, costs = fixture()
        siblings = [
            replace(
                rows[0], identity=owner, workflow="short", group="join",
                logical_tokens=60, context_tokens=60, category=category,
                weight=short.effective_weight, unfinished_members=2,
            ) for owner, category in (("s1", 0), ("s2", 2))
        ]
        other = replace(
            rows[1], identity="long", workflow="long", logical_tokens=100,
            context_tokens=100, weight=long.effective_weight,
        )
        result = choose_shortest_work_first(
            [other, *siblings], ("long",), replace(resources, logical_tokens=capacity), costs,
            strict_remaining_work=True, clock=lambda: 0,
        )
        assert set(result["selected"]) == expected
    finally:
        oracle.close()


def test_shared_path_oracle_reaches_actual_native_eligibility(monkeypatch, tmp_path):
    labels = population((
        ("a-request", "a", "a", 300, 1), ("b-request", "b", "b", 150, 100),
        ("c-request", "c", "c", 150, 100),
    ))
    path, digest = oracle_file(tmp_path, artifact(labels))
    environment = arm_environment({
        **settings(tmp_path), "WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": path, "WORKFLOW_WORK_MODEL_SHA256": digest,
        "WORKFLOW_COMBINATION_ADMISSION": "1", "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
        "AGENT_PRESSURE_PAUSE_POLICY": "workflow_tool_aware",
    }, "full")
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    from beliefkv.runtime import workflow_combination as combination
    from beliefkv.policy import workflow_shortest_frontier as policy

    monkeypatch.setattr(combination, "observe_static_full_mamba_headroom", lambda _cache: NS(
        observable=True, device_full_free_tokens=100, device_mamba_free_slots=0,
    ))
    monkeypatch.setattr(
        policy, "choose_shortest_work_first", partial(policy.choose_shortest_work_first, clock=lambda: 0),
    )
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "path-oracle.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=500),
            req_to_token_pool=NS(available_size=lambda: 24),
        ))
        admission = runtime._agent_admission
        admission.next_tick = float("inf")
        runtime._native_running_batch = NS(reqs=[])
        runtime._native_max_running = 24
        runtime._native_input_reserve = runtime._native_chunk_input_reserve = 2048
        for index, (owner, size) in enumerate((("a", 300), ("b", 150), ("c", 150))):
            runtime.on_events((
                replace(observed(index * 2, Kind.WORKFLOW_START), workflow_id=owner),
                replace(observed(
                    index * 2 + 1, Kind.INVOCATION_CREATE,
                    invocation_id=owner, context_id=owner + "-context",
                ), workflow_id=owner),
            ))
            req = request(owner + "-request", owner, tokens=size, workflow=owner)
            req.output_ids.clear()
            runtime.register_visible_request(req)
            runtime.context_sessions[owner + "-context"] = runtime.visible[req.rid]
        assert admission._conditional_distribution and not admission.rank_before_category
        assert admission._combination.strict_remaining_work
        assert admission._combination.completion_path_oracle
        assert not admission._work.model_work_only
        prediction = admission._work.prediction("a")
        assert admission._pressure_preference(
            admission.policy.programs["a"], prediction.updated_at,
        ) == pytest.approx(-prediction.effective_weight)
        keys = [admission.latest[name] for name in ("c", "b", "a")]
        assert admission._combination.order_tool_replacement(keys)[0] is admission.latest["a"]
        admission._combination.next_dispatch = 1.
        admission.next_tick = 0.
        admission.tick()
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"a"}
        requests = list(admission._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert [req.rid for req in selected.candidates] == ["a-request"]
        state = admission.status()
        assert state["workflow_distribution_changes_pressure_victims"]
        assert state["workflow_join_progress"] == "observed_ready_sibling_group_if_fits"
        validate_reference_admission(
            state, free_continuation_admission=True, free_dispatch_interval_ms=500,
            control_interval_ms=2000, workflow_size_resume=True,
            workflow_work_model_sha256=digest, workflow_work_source="oracle",
            workflow_combination_admission=True, pressure_pause_policy="workflow_tool_aware",
        )
    finally:
        runtime.close()


def test_nonranking_control_keeps_work_forecast_and_removes_path_inference():
    raw = nonranking_artifact(learned())
    assert raw["remaining_work_ranking_enabled"] is False
    assert raw["strict_remaining_work_admission"] is False
    assert raw["shortest_work_first_admission"] is False
    assert "completion_path_readout" not in raw
    assert raw["continuous_work_readout"] == learned()["continuous_work_readout"]


@pytest.mark.parametrize("provider", ("learned", "oracle"))
def test_preparation_preserves_non_model_settings_and_removes_stale_qualification(provider):
    old = {
        "common_environment": {"HICACHE_SIZE_GB": "200", "BELIEFKV_SUBAGENT_MATERIALIZATION": "off"},
        "comparison_contract": {
            "admission_utility": {"old": True},
            "matched_control_changes_only_materialization_policy": True,
        },
        "source_CPU_binding": {"old_source": True},
    }
    value = configuration(old, Path("/new-model.json"), "a" * 64, source=provider, ranking=True)
    assert all(value["common_environment"][name] == item for name, item in old["common_environment"].items())
    assert "source_CPU_binding" not in value
    assert "admission_utility" not in value["comparison_contract"]
    assert not value["comparison_contract"]["matched_control_changes_only_materialization_policy"]
    assert value["workflow_work_qualification"]["future_information_used"] is (provider == "oracle")
    assert not value["gpu_execution"]["registered"]


def test_preparation_uses_standard_serving_environment_and_actual_model_path(monkeypatch, tmp_path):
    from scripts import prepare_r5_path_comparison as module
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts.train_workflow_work import write

    traffic, cohort = tmp_path / "arrivals.json", tmp_path / "cohort.json"
    write(traffic, {"fixed": "arrivals"})
    write(cohort, {"fixed": "members"})
    model_path, labels_path, control_path = (
        tmp_path / name for name in ("learned.json", "labels.json", "control.json")
    )
    labels = source()
    labels["source_binding"] = {"source_arm": "/original-source"}
    write(model_path, learned())
    write(labels_path, labels)
    write(control_path, {
        "order": ["full"], "candidate_source_arm": "/original-source",
        "common_environment": {
            "WORKFLOW_COMBINATION_ADMISSION": "1", "MAX_RUNNING_REQUESTS": "24",
            "MODEL_PATH": "/actual-model", "TOOL_RESTORE_MODE": "speculative",
        },
        "comparison_contract": {},
        "workflow_arrivals": {"path": str(traffic), "sha256": sha256_file(traffic)},
        "private_analysis_cohort": {"path": str(cohort), "sha256": sha256_file(cohort)},
    })
    calls = []

    def standard_environment(reference, mode, checkout, engine, arm, port, plugin, **kwargs):
        assert mode == "full"
        assert "common_environment" not in reference["arms"]["full"]
        assert kwargs["max_running_requests"] == 24
        assert kwargs["sampling_mode"] == "position"
        assert engine == Path("/qualified-engine")
        return {"MODEL_PATH": "/actual-model", "BELIEFKV_ONLINE_TOOL_TIMING": "1"}, [], None

    def preflight(values, model_path):
        assert values["BELIEFKV_ONLINE_TOOL_TIMING"] == "1"
        assert model_path == "/actual-model"
        calls.append(1)
        return {"actual_runtime_constructor_accepted": True}

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(module, "environment", standard_environment)
    monkeypatch.setattr(module, "preflight_runtime", preflight)
    result = module.prepare(NS(
        control_configuration=control_path, model=model_path, oracle_labels=labels_path,
        engine=Path("/qualified-engine"), output_dir=tmp_path / "prepared",
    ))
    assert len(calls) == 3
    assert not result["GPU_registered"] and not result["performance_accepted"]
    assert set(result["configurations"]) == {"full", "oracle", "learned"}
