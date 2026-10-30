from copy import deepcopy
import json
from pathlib import Path

import pytest

from beliefkv.runtime.replay_token_capture import native_request_expectations
from experiments.reports.workflow_mix_20261029 import register_parallel_pilot as registration
from experiments.reports.workflow_mix_20261029.finish_parallel_pilot import validate_population
from scripts.audit_parallel_workstreams import shape
from scripts.prepare_parallel_workstream_pilot import pilot_manifest
from tests.test_causal_replay import mixed_trace


ROOT = Path(__file__).resolve().parents[1]


def test_pilot_changes_actual_deliverables_but_preserves_projects_and_environments():
    source = json.loads((ROOT / "configs/workloads/locobench_mixed_scope18_20261029.json").read_bytes())
    declaration = json.loads((ROOT / "experiments/reports/workflow_mix_20261029/scope18_declaration.json").read_bytes())
    original = deepcopy(source)
    manifest, contract = pilot_manifest(source, declaration)
    assert source == original
    assert len(manifest["workloads"]) == len({row["repo"] for row in manifest["workloads"]}) == 6
    assert contract["scope_counts"] == {"investigation": 2, "implementation": 2, "multi_stage": 2}
    assert contract["shared_execution_protocol"]["child_warning_target_calls"] == [4, 8]
    assert contract["shared_execution_protocol"]["hard_model_call_or_output_limit_added"] is False
    assert contract["shape_review"]["automatic_expansion_to_eighteen"] is False
    by_id = {row["instance_id"]: row for row in source["workloads"]}
    for task, scope in zip(manifest["workloads"], contract["assignments"], strict=True):
        old = by_id[scope["original_instance_id"]]
        for field in set(old) - {"problem_statement", "instance_id", "dataset_provenance"}:
            assert task[field] == old[field]
        assert task["instance_id"] != old["instance_id"]
        assert task["problem_statement"].startswith("Requested deliverable for this derived task:\n")
        assert scope["requested_deliverable"] in task["problem_statement"][:4096]
        assert task["dataset_provenance"]["derived_task"]["original_full_task_success_claimed"] == (
            scope["scope_hypothesis"] == "multi_stage"
        )
        assert "size_tier" not in task and "scope_hypothesis" not in task
        if scope["scope_hypothesis"] == "multi_stage":
            assert old["problem_statement"] in task["problem_statement"]
        elif scope["scope_hypothesis"] == "investigation":
            assert "No patch or complete project repair" in task["problem_statement"]
    for band in (1, 4):
        assert {row["scope_hypothesis"] for row in contract["assignments"] if row["context_band"] == band} == {
            "investigation", "implementation", "multi_stage",
        }


def test_pilot_rejects_missing_or_duplicated_project_population():
    source = json.loads((ROOT / "configs/workloads/locobench_mixed_scope18_20261029.json").read_bytes())
    declaration = json.loads((ROOT / "experiments/reports/workflow_mix_20261029/scope18_declaration.json").read_bytes())
    declaration["assignments"] = declaration["assignments"][:-1]
    # Remove an actual selected band as well, regardless of source order.
    declaration["assignments"] = [
        row for row in declaration["assignments"]
        if row["project"] != "locobench/python_web_dashboard_medium_039"
    ]
    with pytest.raises(ValueError):
        pilot_manifest(source, declaration)


def native_sample(trace, *, zero=False, duplicate_owner=False):
    expected = native_request_expectations(trace.native)
    samples = []
    for rid in ("a-first", "a-first" if duplicate_owner else "b-last"):
        identity = expected[rid]["llm_result"][0]
        samples.append({
            **dict(zip(("workflow_id", "invocation_id", "context_id", "context_epoch"), identity)),
            "request_id": rid, "phase": "decode", "token_delta": 0 if zero else 1,
        })
    return {"event": "gpu_service_sample", "phase": "decode",
            "launch_monotonic_ms": 20., "request_samples": samples}


def test_shape_separates_offered_overlap_positive_native_decode_and_internal_calls():
    trace = mixed_trace()
    row = shape(trace.ordered(), native_request_expectations(trace.native), [native_sample(trace)])
    assert row["summary"]["children"] == 3
    assert row["summary"]["joins"] == 2
    assert row["summary"]["nested_joins"] == 1
    assert row["summary"]["child_model_calls"]["p50"] == 1
    assert sum(child["model_calls"] for child in row["children"]) == 4
    assert row["summary"]["joins_with_multiple_outstanding_model_requests"] == 1
    assert row["summary"]["joins_with_positive_shared_decode"] == 1
    root = next(group for group in row["joins"] if group["join_id"] == "root-join")
    assert root["parent_invocation_id"] == "root"
    assert root["multiple_outstanding_model_requests_s"] == .0095


@pytest.mark.parametrize("variant", ("zero", "duplicate_owner", "prefill", "outside_wait", "none"))
def test_shape_does_not_promote_zero_progress_or_serial_service(variant):
    trace = mixed_trace()
    sample = native_sample(trace, zero=variant == "zero", duplicate_owner=variant == "duplicate_owner")
    if variant == "prefill":
        sample["phase"] = "prefill"
    if variant == "outside_wait":
        sample["launch_monotonic_ms"] = 80.
    row = shape(
        trace.ordered(), native_request_expectations(trace.native),
        [] if variant == "none" else [sample],
    )
    assert row["summary"]["joins_with_multiple_outstanding_model_requests"] == 1
    assert row["summary"]["joins_with_positive_shared_decode"] == 0
    assert row["summary"]["native_service_evidence_available"] is (variant != "none")


@pytest.mark.parametrize("variant", ("missing_result", "wrong_native_owner", "wrong_service_owner", "missing_return"))
def test_shape_rejects_incomplete_or_mismatched_ownership(variant):
    trace = mixed_trace()
    expected = native_request_expectations(trace.native)
    sample = native_sample(trace)
    if variant == "missing_result":
        expected.pop("a-first")
    elif variant == "wrong_native_owner":
        expected["a-first"]["llm_result"] = (("w", "wrong", "ctx-a", 0), 4)
    elif variant == "wrong_service_owner":
        sample["request_samples"][0]["invocation_id"] = "wrong"
    else:
        trace.events.remove(trace.named["return-a"])
    with pytest.raises(ValueError):
        shape(trace.ordered(), expected, [sample])


def test_finished_pilot_matches_original_project_ids_without_adding_another_prefix():
    declaration = {"assignments": [
        {"instance_id": f"task-{i}", "project": f"locobench/project-{i}"} for i in range(6)
    ]}
    rows = [{"task_id": row["instance_id"], "project": row["project"]} for row in declaration["assignments"]]
    validate_population(rows, declaration)
    with pytest.raises(ValueError):
        validate_population(rows[:-1], declaration)
    rows[-1]["project"] = rows[0]["project"]
    with pytest.raises(ValueError):
        validate_population(rows, declaration)


@pytest.mark.parametrize("variant", ("valid", "incomplete", "different", "live_reviewer", "wrong_review"))
def test_new_collection_waits_complete_equal_work_and_reviewer_exit(monkeypatch, variant):
    control, oracle = Path("/test/control.json"), Path("/test/oracle.json")
    work = {"tokens": [1, 2, 3]}
    def complete(path):
        if variant == "incomplete":
            return None
        return {} if path == oracle and variant == "different" else work
    review = {
        "complete_equal_work_fresh_drain_and_actual_exit": True,
        "inputs_sha256": {str(control): "sha", str(oracle): "wrong" if variant == "wrong_review" else "sha"},
    }
    monkeypatch.setattr(registration, "complete_work", complete)
    monkeypatch.setattr(registration, "sha256_file", lambda _path: "sha")
    monkeypatch.setattr(registration.capture, "load", lambda p: review if p.name == "review" else {"process": {"pid": 1}})
    monkeypatch.setattr(registration.capture.reservation, "alive", lambda _owner: variant == "live_reviewer")
    if variant == "valid":
        registration.predecessor_complete(control, oracle, Path("/test/review"), Path("/test/worker"))
    else:
        with pytest.raises(ValueError):
            registration.predecessor_complete(control, oracle, Path("/test/review"), Path("/test/worker"))


@pytest.mark.parametrize("failure", (False, True))
def test_cpu_pilot_reviewer_rewrites_heartbeat_and_persists_terminal_state(tmp_path, monkeypatch, failure):
    from experiments.reports.workflow_mix_20261029 import finish_parallel_pilot as finish
    import sys

    source, declaration, cost, output = (tmp_path / name for name in ("source.json", "decl.json", "cost.json", "out"))
    rows = [{"task_id": f"task-{i}", "project": f"locobench/project-{i}", "eligible": True} for i in range(6)]
    source.write_text(json.dumps({"run_root": str(tmp_path / "run")}))
    source.with_suffix(".state.json").write_text("{}")
    declaration.write_text(json.dumps({
        "assignments": [{"instance_id": row["task_id"], "project": row["project"]} for row in rows],
        "shape_review": {},
    }))
    cost.write_text(json.dumps({"model_cost_calibration": {"model_seconds_per_work_token": .02}}))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(sys, "argv", [
        "finish", "--registration", str(source), "--declaration", str(declaration),
        "--model-cost-evidence", str(cost), "--output-dir", str(output),
    ])
    observations = []
    def ready(*_args, **_kwargs):
        observations.append(json.loads((output / "state.json").read_bytes())["status"])
        return len(observations) >= 3
    def complete(*_args):
        if failure:
            raise ValueError("preserved failure fixture")
        return {"complete": True}
    monkeypatch.setattr(finish, "source_ready", ready)
    monkeypatch.setattr(finish, "audit_finished_source", complete)
    monkeypatch.setattr(finish.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(finish, "inventory_capture", lambda *_a, **_k: {"workflows": rows})
    monkeypatch.setattr(finish, "population_summary", lambda values: {"count": len(values)})
    monkeypatch.setattr(finish, "audit", lambda _arm: {"summary": {"children": 12}})
    finish.main()
    state = json.loads((output / "state.json").read_bytes())
    assert observations == ["waiting_complete_native_pilot"] * 3
    assert state["status"] == (
        "stopped_preserve_pilot_evidence" if failure else "complete_pilot_requires_shape_review"
    )
    assert state["finished_at"] and state["GPU_followup_registered"] is False
    if failure:
        assert "preserved failure fixture" in state["error"]
    else:
        assert json.loads((output / "review.json").read_bytes())["all_workflows_naturally_eligible"] is True
