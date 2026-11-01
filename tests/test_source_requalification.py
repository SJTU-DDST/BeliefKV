from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from beliefkv.experiments.causal_replay import compile_causal_replay
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from scripts import requalify_causal_source as qual
from scripts import register_composite_source as registration
from scripts import run_causal_gpu_replay
from tests.test_causal_replay import mixed_trace


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def prefix(path):
    return {
        "complete_prefix_bytes": path.stat().st_size,
        "complete_prefix_sha256": sha256_file(path),
    }


@pytest.fixture
def completed(tmp_path):
    run = tmp_path / "run"
    drain = write(run / "native_capture/telemetry_drain.json", {
        "previous_zero_snapshot_ts_ms": 10.,
        "drained_status": {
            "snapshot_ts_ms": 20., "writer_queue_depth": 0,
            "pending_batch_count": 0, "pending_request_count": 0,
        },
    })
    plan = {
        "run_root": str(run), "source_profile": "nested_oneshot",
        "environment": {"ROOT_COUNT": "1", "FANOUT_PROFILE": "fixture"},
    }
    state = {
        "status": "capture_failed", "original_exit_code": 0,
        "administratively_interrupted": False, "finished_at": "2026-10-31",
        "outcome": {
            "status": "completed", "completed_workflows": 1,
            "native_agent_jct_eligible_workflows": 1,
        },
        "watcher": {"pid": 1}, "driver": {"pid": 2},
    }
    return plan, state, drain


def test_completed_execution_accepts_only_complete_exit_and_healthy_fresh_drain(completed):
    plan, state, drain = completed
    assert qual.completed_execution(
        plan, state, alive=lambda _: False, owned_runs=lambda: {},
    ) == (1, drain)


@pytest.mark.parametrize("change", (
    "interrupted", "partial", "nonzero_exit", "administrative", "unfinished",
    "live_watcher", "missing_driver", "owned_server", "stale_drain", "pending", "writer_error",
))
def test_analysis_correction_cannot_qualify_partial_live_or_unhealthy_execution(completed, change):
    plan, state, path = completed
    alive = lambda _: False
    owned = {}
    if change == "interrupted":
        state["status"] = "capture_interrupted"
    elif change == "partial":
        state["outcome"]["completed_workflows"] = 0
    elif change == "nonzero_exit":
        state["original_exit_code"] = 1
    elif change == "administrative":
        state["administratively_interrupted"] = True
    elif change == "unfinished":
        state["finished_at"] = None
    elif change == "live_watcher":
        alive = lambda row: row["pid"] == 1
    elif change == "missing_driver":
        state.pop("driver")
    elif change == "owned_server":
        owned[Path(plan["run_root"])] = [{"pid": 3}]
    else:
        drain = qual.capture.load(path)
        if change == "stale_drain":
            drain["previous_zero_snapshot_ts_ms"] = 20.
        elif change == "pending":
            drain["drained_status"]["pending_request_count"] = 1
        else:
            drain["drained_status"]["writer_error"] = "write failed"
        write(path, drain)
    with pytest.raises(ValueError):
        qual.completed_execution(plan, state, alive=alive, owned_runs=lambda: owned)


@pytest.fixture
def audits(tmp_path):
    arm = tmp_path / "native_capture"
    event = write(arm / "client/events.jsonl", {"fixture": "events"})
    tokens = write(arm / "server/request_tokens.jsonl", {"fixture": "tokens"})
    graph_prefix, token_prefix = {str(event): prefix(event)}, {str(tokens): prefix(tokens)}
    old_tokens = {
        "token_evidence_complete": True, "native_requests": 8,
        "input_jsonl_prefixes": token_prefix,
    }
    old_graph = {
        "causal_graph_compiled": False, "reason": "invalid_causal_dependencies",
        "error": "tool duration and timestamps disagree", "token_evidence_complete": True,
        "input_jsonl_prefixes": graph_prefix,
    }
    sources = {name: sha256_file(qual.ROOT / name) for name in qual.ANALYSIS_SOURCES}
    corrected = {
        "arm": str(arm), "causal_graph_compiled": True, "token_evidence_complete": True,
        "requests": 8, "input_jsonl_prefixes": deepcopy(graph_prefix),
        "token_input_jsonl_prefixes": deepcopy(token_prefix),
        "analysis_source_sha256": dict(sources),
    }
    return arm, old_tokens, old_graph, corrected, sources


def test_corrected_audit_retains_exact_original_raw_inputs(audits):
    arm, tokens, graph, corrected, sources = audits
    paths = qual.matching_audits(tokens, graph, corrected, arm, sources)
    assert len(paths) == 2 and all(path.is_relative_to(arm) for path in paths)


@pytest.mark.parametrize("change", (
    "tokens_incomplete", "other_failure", "graph_already_passed", "new_graph_failed",
    "requests_changed", "different_prefix", "wrong_analysis", "raw_changed", "foreign_raw",
))
def test_only_exact_callback_failure_and_unchanged_raw_inputs_are_accepted(audits, change, tmp_path):
    arm, tokens, graph, corrected, sources = audits
    if change == "tokens_incomplete":
        tokens["token_evidence_complete"] = False
    elif change == "other_failure":
        graph["error"] = "a child never returned"
    elif change == "graph_already_passed":
        graph["causal_graph_compiled"] = True
    elif change == "new_graph_failed":
        corrected["causal_graph_compiled"] = False
    elif change == "requests_changed":
        corrected["requests"] = 9
    elif change == "different_prefix":
        corrected["input_jsonl_prefixes"] = {}
    elif change == "wrong_analysis":
        corrected["analysis_source_sha256"][qual.ANALYSIS_SOURCES[0]] = "0" * 64
    elif change == "raw_changed":
        Path(next(iter(graph["input_jsonl_prefixes"]))).write_text("changed")
    else:
        foreign = write(tmp_path / "foreign.json", {})
        graph["input_jsonl_prefixes"] = {str(foreign): prefix(foreign)}
        corrected["input_jsonl_prefixes"] = deepcopy(graph["input_jsonl_prefixes"])
    with pytest.raises(ValueError):
        qual.matching_audits(tokens, graph, corrected, arm, sources)


def publication_graph():
    trace = mixed_trace()
    trace.events = [
        replace(event, join_id="root-join")
        if event.kind is Kind.INVOCATION_CREATE and event.invocation_id in ("a", "b")
        else event for event in trace.events
    ]
    original = trace.named["tool-end-parallel-one"]
    updated = replace(original, attributes={
        **original.attributes, "callback_observed_ts_ms": original.ts_ms - 3.5,
        "callback_publication_adjustment_ms": 3.5, "duration_ms": 51.5,
    })
    trace.events[trace.events.index(original)] = updated
    return compile_causal_replay(trace.ordered(), trace.native)


def test_actual_observation_evidence_explains_the_exact_material_conflict():
    graph = publication_graph()
    conflicts = qual.publication_conflicts(graph)
    assert len(conflicts) == 1
    assert conflicts[0]["tool_duration_ms"] == pytest.approx(51.5)
    assert conflicts[0]["published_interval_ms"] == pytest.approx(55.)
    with pytest.raises(ValueError, match="no complete observed"):
        trace = mixed_trace()
        qual.publication_conflicts(compile_causal_replay(trace.ordered(), trace.native))


@pytest.fixture
def cpu(tmp_path):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests="14", failures="0", errors="0", skipped="0")
    for index in range(13):
        ET.SubElement(suite, "testcase", classname="tests.test_causal_callback_timing", name=f"fixture{index}")
    ET.SubElement(suite, "testcase", classname="tests.test_source_requalification", name="fixture")
    report = tmp_path / "cpu.xml"
    ET.ElementTree(root).write(report)
    sources = write(tmp_path / "sources.json", {
        name: sha256_file(qual.ROOT / name) for name in qual.BOUND_SOURCES
    })
    evidence = {
        "returncode": 0, "failed_error_skipped_cases": 0,
        "all_retained_literal_identities_present": True,
        "all_previous_source_paths_retained": True, "sources_unchanged_during_execution": True,
        "unique_test_identities": 14, "junit_tests_including_subtests": 14,
        "report": {"path": str(report), "sha256": sha256_file(report)},
        "source_manifest": {"path": str(sources), "sha256": sha256_file(sources)},
        "external_inputs_sha256": {},
    }
    path = write(tmp_path / "cpu.json", evidence)
    return path, evidence, report, sources


def test_cpu_binding_checks_the_actual_fixed_sources_and_required_cases(cpu):
    path, _evidence, report, sources = cpu
    manifest, paths, count = qual.verify_cpu(path)
    assert set(manifest) == set(qual.BOUND_SOURCES)
    assert paths == (report, sources) and count == 14


@pytest.mark.parametrize("change", (
    "failed", "changed", "omitted", "report_sha", "source_sha", "missing_binding",
    "wrong_source", "external_changed", "callback_cases_missing",
))
def test_requalification_requires_matching_successful_source_bound_cpu_evidence(cpu, change):
    path, evidence, report, sources = cpu
    if change == "failed":
        evidence["returncode"] = 1
    elif change == "changed":
        evidence["sources_unchanged_during_execution"] = False
    elif change == "omitted":
        evidence["all_retained_literal_identities_present"] = False
    elif change == "report_sha":
        evidence["report"]["sha256"] = "0" * 64
    elif change == "source_sha":
        evidence["source_manifest"]["sha256"] = "0" * 64
    elif change in ("missing_binding", "wrong_source"):
        data = qual.capture.load(sources)
        if change == "missing_binding":
            data.pop("scripts/requalify_causal_source.py")
        else:
            data["beliefkv/experiments/causal_replay.py"] = "0" * 64
        write(sources, data)
        evidence["source_manifest"]["sha256"] = sha256_file(sources)
    elif change == "external_changed":
        evidence["external_inputs_sha256"] = {str(report): "0" * 64}
    else:
        tree = ET.parse(report)
        for case in tree.iter("testcase"):
            case.set("classname", "unrelated")
        tree.write(report)
        evidence["report"]["sha256"] = sha256_file(report)
    write(path, evidence)
    with pytest.raises(ValueError):
        qual.verify_cpu(path)


@pytest.fixture
def sidecar_source(completed, audits, monkeypatch, tmp_path):
    plan, state, _drain = completed
    _other_arm, tokens, graph, corrected, sources = audits
    arm = Path(plan["run_root"]) / "native_capture"
    for key in ("input_jsonl_prefixes", "token_input_jsonl_prefixes"):
        replacement = {}
        for name in corrected[key]:
            path = write(arm / Path(name).relative_to(_other_arm), {"fixture": name})
            replacement[str(path)] = prefix(path)
        corrected[key] = replacement
    tokens["input_jsonl_prefixes"] = deepcopy(corrected["token_input_jsonl_prefixes"])
    graph["input_jsonl_prefixes"] = deepcopy(corrected["input_jsonl_prefixes"])
    corrected["arm"] = str(arm)
    plan_path = write(tmp_path / "source.registration.json", plan)
    state_path = write(plan_path.with_suffix(".state.json"), state)
    write(Path(plan["run_root"]) / "capture_plan.json", plan)
    write(Path(plan["run_root"]) / "token_evidence.json", tokens)
    write(Path(plan["run_root"]) / "causal_evidence.json", graph)
    client = arm / "client_1"
    write(client / "summary.json", {
        "workflows": [{"workflow_id": "w", "native_agent_jct_eligible": True}],
        "completed_workflows": 1, "subagent_fanout_profile": "fixture",
        "delegation_contract": {
            "root_minimum_delegations_prompt": 2, "minimum_completed_root_join_members": 2,
        },
    })
    write(client / "workflows/task/result.json", {
        "natural_terminal": True, "native_agent_jct_eligible": True, "error": None,
    })
    corrected_path = write(tmp_path / "corrected.json", corrected)
    cpu_path = write(tmp_path / "cpu-evidence.json", {})
    cpu_report, cpu_sources = write(tmp_path / "cpu-report.json", {}), write(tmp_path / "cpu-sources.json", {})
    monkeypatch.setattr(qual, "verify_cpu", lambda _: (sources, (cpu_report, cpu_sources), 14))
    monkeypatch.setattr(qual.capture.comparison, "verify_sources", lambda _: None)
    monkeypatch.setattr(qual.capture.reservation, "alive", lambda _: False)
    monkeypatch.setattr(qual.capture.reservation, "owned_runs", lambda: {})
    monkeypatch.setattr(qual, "audit_tokens", lambda _: {
        **tokens, "original_generation_parameters_complete": True,
    })
    monkeypatch.setattr(run_causal_gpu_replay, "source_plan", lambda _: (publication_graph(), {}, {}))
    return plan_path, state_path, corrected_path, cpu_path


def test_complete_analysis_sidecar_keeps_original_failure_and_raw_files_unchanged(sidecar_source, tmp_path):
    plan, state, corrected, cpu_path = sidecar_source
    before = state.read_bytes()
    proof = qual.audit(plan, corrected, cpu_path)
    path = write(tmp_path / "requalification.json", {"observed_at": "now", **proof})
    completion = qual.verify(path, plan)
    assert proof["workflows"] == 1 and proof["native_requests"] == 8
    assert proof["source_selection"]["observed_completed_root_join_max_members"] == {"w": 2}
    assert proof["original_capture_failed_state_preserved"]
    assert state.read_bytes() == before and qual.capture.load(state)["status"] == "capture_failed"
    assert completion[str(path)] == sha256_file(path)
    for name, digest in proof["inputs_sha256"].items():
        assert sha256_file(Path(name)) == digest
    selected, again = registration.origin_completion(plan, {plan: path})
    assert selected["run_root"] and again == completion
    with pytest.raises(ValueError, match="did not complete"):
        registration.origin_completion(plan, {})
    tampered = qual.capture.load(path)
    tampered["raw_tokens_events_and_tool_times_unchanged"] = False
    write(path, tampered)
    with pytest.raises(ValueError, match="differs"):
        qual.verify(path, plan)


def test_sidecars_are_explicitly_bound_to_one_original_and_reject_duplicates(sidecar_source, tmp_path):
    plan, _state, corrected, cpu_path = sidecar_source
    path = write(tmp_path / "qualification.json", qual.audit(plan, corrected, cpu_path))
    assert registration.qualification_paths([path]) == {plan: path}
    with pytest.raises(ValueError, match="duplicate"):
        registration.qualification_paths([path, path])
    with pytest.raises(ValueError, match="another"):
        qual.verify(path, tmp_path / "another.registration.json")


@pytest.mark.parametrize("change", ("incomplete_tokens", "generation_missing", "different_requests"))
def test_raw_tokens_are_freshly_checked_before_requalification(sidecar_source, monkeypatch, change):
    plan, _state, corrected, cpu_path = sidecar_source
    observed = qual.audit_tokens(Path(plan.parent))
    if change == "incomplete_tokens":
        observed["token_evidence_complete"] = False
    elif change == "generation_missing":
        observed["original_generation_parameters_complete"] = False
    else:
        observed["native_requests"] = 9
    monkeypatch.setattr(qual, "audit_tokens", lambda _: observed)
    with pytest.raises(ValueError, match="fresh native token"):
        qual.audit(plan, corrected, cpu_path)
