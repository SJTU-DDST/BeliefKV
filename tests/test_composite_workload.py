from copy import deepcopy
from dataclasses import replace
import json

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import compile_causal_replay
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.composite_workload import load_composed_source, merge_plans
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.replay_token_capture import capture_record
from scripts import prepare_composite_workload as prep
from scripts.run_causal_gpu_replay import source_plan
from tests.test_causal_replay import Trace


def trace(workflow, offset=0.):
    value = Trace(workflow)
    value.add("start", Kind.WORKFLOW_START, offset)
    value.create(workflow + "-root", offset + 1.)
    for index in range(2):
        value.model(
            workflow + f"-r{index}", workflow + "-root", offset + 2. + index * 20.,
            offset + 10. + index * 20., offset + 12. + index * 20.,
        )
        if not index:
            value.tool("actual-tool", workflow + "-root", offset + 13., 5.)
    value.returned(workflow + "-root", offset + 34.)
    value.add("end", Kind.WORKFLOW_END, offset + 35.)
    for row in value.native:
        row["attributes"]["monotonic_clock_domain"] = "source-clock:" + workflow
    return value


def test_independent_clock_sources_compose_after_compilation_with_exact_tools_and_work():
    left, right = trace("left"), trace("right", 10_000_000.)
    with pytest.raises(ValueError, match="clock"):
        compile_causal_replay(left.ordered() + right.ordered(), left.native + right.native)
    a = compile_causal_replay(left.ordered(), left.native)
    b = compile_causal_replay(right.ordered(), right.native)
    result = merge_plans((a, b))
    assert result.summary()["requests"] == 4
    assert result.summary()["workflows"] == 2
    assert result.request_usage == {**a.request_usage, **b.request_usage}
    assert result.nodes == (*a.nodes, *b.nodes)
    assert {node.delay_ms for node in result.nodes if node.event and node.event.kind is Kind.TOOL_END} == {5.}


@pytest.mark.parametrize("failure", ("same_workflow", "same_request", "foreign_dependency", "incomplete"))
def test_composition_rejects_identity_collisions_and_incomplete_workflows(failure):
    left = compile_causal_replay(trace("left").ordered(), trace("left").native)
    right = compile_causal_replay(trace("right").ordered(), trace("right").native)
    if failure == "same_workflow":
        right = left
    elif failure == "same_request":
        right = replace(right, request_usage=left.request_usage)
    elif failure == "foreign_dependency":
        right = replace(right, nodes=(
            replace(right.nodes[0], dependencies=(left.nodes[0].node_id,)), *right.nodes[1:],
        ))
    else:
        right = replace(right, nodes=tuple(
            node for node in right.nodes if not node.event or node.event.kind is not Kind.WORKFLOW_END
        ))
    with pytest.raises(ValueError):
        merge_plans((left, right))


def make_capture(directory, workflow, offset=0.):
    source = directory / workflow
    task = "task-" + workflow
    client = source / "client_1"
    event_path = client / "workflows" / task / "runtime_events.deepagents.jsonl"
    event_path.parent.mkdir(parents=True)
    (source / "server").mkdir()
    value = trace(workflow, offset)
    event_path.write_text("".join(json.dumps(event.to_dict()) + "\n" for event in value.ordered()))
    native = source / "server/runtime_events.sglang.jsonl"
    native.write_text("".join(json.dumps(row) + "\n" for row in value.native))
    tokens = source / "server/request_tokens.jsonl"
    records = []
    for index in range(2):
        raw = next(row for row in value.native if row["attributes"]["request_id"] == workflow + f"-r{index}")
        raw = {
            **raw, "ts_ms": offset + 2. + index * 20.,
            "request_id": workflow + f"-r{index}", "session_id": workflow + "-session",
            "session_generation": 1,
            "_replay_request_parameters": {
                "max_new_tokens": 32, "min_new_tokens": 0, "ignore_eos": False,
                "stop": [], "stop_regex": [], "stop_token_ids": [9],
            },
            "_replay_finish_reason": {"type": "stop", "matched": 9},
        }
        records.append(capture_record(
            raw, [3] * 100_000, event="request_prompt",
            base_request_id=workflow + "-r0" if index else None,
            common_prefix_tokens=100_000 if index else 0,
        ))
        records.append(capture_record(raw, [4, 5, 6, 9], event="request_output"))
    tokens.write_text("".join(json.dumps(row) + "\n" for row in records))
    (client / "manifest.json").write_text("{}")
    (client / "summary.json").write_text(json.dumps({"workflow_count": 1, "completed_workflows": 1}))
    return {
        "capture": str(source), "workflows": [{
            "project": "locobench/" + workflow, "task_id": task, "workflow_id": workflow,
            "eligible": True, "source_capture": str(source), "event_sha256": sha256_file(event_path),
            "token_source_sha256": sha256_file(tokens), "protocol_sha256": "protocol-" + workflow,
            "model_work_units": 10., "generated_tokens": 8, "necessary_input_tokens": 100_000,
            "requests": 2, "initial_prompt_tokens": 100_000, "max_context_tokens": 100_000,
            "tool_total_s": .005, "critical_model_tool_proxy_s": 10., "joins": 0, "nested_joins": 0,
        }],
    }


def test_materialized_composition_preserves_raw_token_bytes_and_reloads_into_actual_replay(tmp_path, monkeypatch):
    inventories = [make_capture(tmp_path, "left"), make_capture(tmp_path, "right", 10_000_000.)]
    # Original dataset prompt verification is separately tested against the real archive.
    monkeypatch.setattr(prep, "original_source_proof", lambda inventory: {"fixture": inventory["capture"]})
    monkeypatch.setattr(prep, "serving_artifacts", lambda *_args: ({}, {}))
    destination = tmp_path / "composed"
    prep.prepare(inventories, destination)
    graph, expected, evidence = source_plan(destination)
    assert graph.summary()["workflows"] == 2
    assert evidence["independent_clock_domains_compiled_separately"]
    assert not evidence["matched_natural_native_baseline_available"]
    assert (destination / "server/request_tokens.jsonl").read_bytes() == b"".join(
        (tmp_path / workflow / "server/request_tokens.jsonl").read_bytes()
        for workflow in ("left", "right")
    )
    with CapturedTokenCorpus(destination / "server/request_tokens.jsonl", expected) as corpus:
        for request in ("right-r1", "left-r1", "left-r0", "right-r0"):
            item = corpus.get(request)
            assert list(item.prompt_tokens) == [3] * 100_000
            assert list(item.output_tokens) == [4, 5, 6, 9]
            assert item.native_finish_reason == {"type": "stop", "matched": 9}
    original = tmp_path / "right/server/request_tokens.jsonl"
    with original.open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="original.*changed"):
        load_composed_source(destination)


def test_representative_selection_never_picks_a_lucky_shorter_rerun():
    first = {"project": "a", "task_id": "old", "source_capture": "first", "eligible": True, "work": 100}
    rerun = {**first, "task_id": "new", "source_capture": "second", "work": 1}
    selected, unused = prep.project_representatives([{"workflows": [first]}, {"workflows": [rerun]}])
    assert selected == [first]
    assert unused[0]["reason"] == "keep_first_declared_project_representative"
    invalid = deepcopy(first)
    invalid["eligible"] = False
    with pytest.raises(ValueError, match="naturally eligible"):
        prep.project_representatives([{"workflows": [invalid]}])


def test_composed_source_uses_explicit_registration_without_fabricating_capture_state(tmp_path, monkeypatch):
    from beliefkv.experiments.composite_workload import (
        KIND, composed_quality, registered_source_arm, verify_composed_registration,
    )
    from scripts import run_position_replay_calibration as calibration
    from scripts import run_causal_comparison as comparison

    inventory = make_capture(tmp_path, "workflow")
    monkeypatch.setattr(prep, "original_source_proof", lambda _inventory: {})
    manifest = {"workloads": [{"instance_id": "task-workflow", "repo": "locobench/workflow"}]}
    monkeypatch.setattr(prep, "serving_artifacts", lambda *_args: ({
        "workload_manifest.json": manifest, "serving_source.json": {"engine_tree": "tree"},
    }, {}))
    arm = tmp_path / "composite"
    prep.prepare([inventory], arm)
    source = {
        "source_kind": KIND, "source_arm": str(arm), "engine_tree": "tree",
        "composition_sha256": sha256_file(arm / "composite_source.json"),
        "matched_natural_native_baseline_available": False,
        "environment": {
            "ROOT_COUNT": "1", "MAX_RUNNING_REQUESTS": "24",
            "WORKLOAD_MANIFEST": str(arm / "workload_manifest.json"),
        },
    }
    registration = tmp_path / "composed.registration.json"
    registration.write_text(json.dumps(source))
    assert not registration.with_suffix(".state.json").exists()
    assert registered_source_arm(source) == arm
    graph, _, _ = verify_composed_registration(source)
    assert calibration.source_ready({"source_registration": str(registration)})
    assert not calibration.complete_source_metrics(registration)["matched_natural_native_baseline_available"]
    assert comparison.complete_source_population(composed_quality(arm), graph) == 1
    assert comparison.source_timing_metrics(composed_quality(arm), None) == {
        "mean_jct_ms": None, "replay_duration_ms": None, "timing_comparable_to_replay": False,
    }
    with pytest.raises(ValueError, match="no matched natural"):
        comparison.source_timing_metrics(composed_quality(arm), {"mean_jct_ms": 1.})
    qualified = []
    monkeypatch.setattr(comparison, "project_disjoint_workload", lambda *a, **kw: qualified.append((a, kw)))
    result = comparison.experiment_source_evidence(
        {"common_environment": {
            "ROOT_COUNT": "1", "MAX_RUNNING_REQUESTS": "24",
            "WORKLOAD_MANIFEST": str(arm / "workload_manifest.json"),
        }}, composed_quality(arm), {
            "source_registration": str(registration),
            "external_inputs": [{"path": str(registration), "sha256": sha256_file(registration)}],
        }, arm, 1, 24,
    )
    assert result["basis"] == "independently_compiled_complete_original_workflows"
    assert qualified[0][1] == {"expected_workflows": 1}
    broken = {**source, "matched_natural_native_baseline_available": True}
    with pytest.raises(ValueError, match="unavailable native"):
        verify_composed_registration(broken)


def test_diversity_selection_retains_whole_short_long_projects_and_excludes_only_middle_work():
    rows = [{
        "project": f"project-{index}", "task_id": f"task-{index}", "model_work_units": work,
        "source_jct": 999999. - index, "predictive_gain": -999999. + index,
    } for index, work in enumerate([10.] * 10 + [14.] * 4 + [22.] * 24 + [40.] * 10)]
    before = deepcopy(rows)
    selected, excluded = prep.select_diverse_projects(rows, minimum_ratio=3., minimum_projects=36)
    assert rows == before
    assert len(selected) + len(excluded) == 48
    assert 36 <= len(selected) < 48
    assert {row["model_work_units"] for row in excluded} == {22.}
    assert sum(row["model_work_units"] == 10. for row in selected) == 10
    assert sum(row["model_work_units"] == 40. for row in selected) == 10
    from beliefkv.experiments.workload_mix import summary
    assert summary([row["model_work_units"] for row in selected])["p80_p20"] >= 3.
    for row in rows:
        row["source_jct"], row["predictive_gain"] = 0., 0.
    again, _ = prep.select_diverse_projects(rows, minimum_ratio=3., minimum_projects=36)
    assert [row["project"] for row in selected] == [row["project"] for row in again]


@pytest.mark.parametrize("matched", (None, {"mean_jct_ms": 250.}))
def test_natural_capture_timing_remains_explicit_for_original_source_controls(matched):
    from scripts.run_causal_comparison import source_timing_metrics

    result = source_timing_metrics({"duration_seconds": 1.5}, matched)
    assert result["replay_duration_ms"] == 1500.
    assert result["mean_jct_ms"] == (None if matched is None else 250.)
    assert result["timing_comparable_to_replay"] is (matched is not None)
