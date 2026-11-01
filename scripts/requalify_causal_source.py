"""Requalify completed raw captures after the observed callback-clock correction."""

import argparse
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import _callback_observed_timestamp, _tool_key
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_source_capture as capture
from scripts.audit_replay_token_capture import audit as audit_tokens
from scripts.qualify_predicted_tool_offload_cpu import cases


KIND = "completed_native_capture_callback_clock_requalification"
ANALYSIS_SOURCES = (
    "scripts/audit_causal_replay.py",
    "beliefkv/experiments/causal_replay.py",
    "beliefkv/runtime/replay_token_capture.py",
)
BOUND_SOURCES = (
    *ANALYSIS_SOURCES, "scripts/audit_replay_token_capture.py", "scripts/requalify_causal_source.py",
    "tests/test_causal_callback_timing.py", "tests/test_source_requalification.py",
)


def verify_cpu(path):
    evidence = capture.load(path)
    if evidence.get("returncode") != 0 or evidence.get("failed_error_skipped_cases") != 0 or any(
        evidence.get(name) is not True for name in (
            "all_retained_literal_identities_present", "all_previous_source_paths_retained",
            "sources_unchanged_during_execution",
        )
    ):
        raise ValueError("requalification requires complete source-bound CPU evidence")
    report, source_path = (
        Path(evidence[name]["path"]) for name in ("report", "source_manifest")
    )
    if any(sha256_file(Path(evidence[name]["path"])) != evidence[name]["sha256"]
           for name in ("report", "source_manifest")):
        raise ValueError("requalification CPU evidence changed")
    identities, reported = cases(report)
    if len(identities) != evidence["unique_test_identities"] or (
        reported != evidence["junit_tests_including_subtests"]
        or sum(owner == "tests.test_causal_callback_timing" for owner, _name in identities) < 13
        or not any(owner == "tests.test_source_requalification" for owner, _name in identities)
    ):
        raise ValueError("CPU report omits the callback correction or requalification checks")
    sources = capture.load(source_path)
    if not set(BOUND_SOURCES) <= sources.keys():
        raise ValueError("CPU source binding omits the corrected qualification entry")
    capture.comparison.verify_cpu_snapshot(ROOT, sources)
    for name, digest in evidence["external_inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("bound external input changed: " + name)
    return sources, (report, source_path), len(identities)


def completed_execution(plan, state, *, alive=None, owned_runs=None):
    alive = capture.reservation.alive if alive is None else alive
    owned_runs = capture.reservation.owned_runs if owned_runs is None else owned_runs
    expected = int(plan["environment"]["ROOT_COUNT"])
    outcome = state.get("outcome") or {}
    if (
        plan.get("source_profile") != "nested_oneshot"
        or state.get("status") != "capture_failed" or state.get("original_exit_code") != 0
        or state.get("administratively_interrupted") is not False
        or outcome.get("status") != "completed"
        or outcome.get("completed_workflows") != expected
        or outcome.get("native_agent_jct_eligible_workflows") != expected
        or not state.get("finished_at")
        or any(not state.get(name) or alive(state[name]) for name in ("watcher", "driver"))
    ):
        raise ValueError("requalification requires the exact complete natural execution and actual exit")
    run = Path(plan["run_root"]).resolve()
    owned = owned_runs()
    if owned.get(run) or owned.get(run / "native_capture"):
        raise ValueError("completed native capture still has owned processes")
    drain_path = run / "native_capture/telemetry_drain.json"
    drain = capture.load(drain_path)
    status = drain["drained_status"]
    if (
        not drain["previous_zero_snapshot_ts_ms"] < status["snapshot_ts_ms"]
        or any(status.get(name) != 0 for name in (
            "writer_queue_depth", "pending_batch_count", "pending_request_count",
        ))
        or any(status.get(name) for name in ("writer_error", "failed_records", "dropped_records"))
    ):
        raise ValueError("requalification requires fresh healthy native telemetry drain")
    return expected, drain_path


def matching_audits(old_tokens, old_graph, corrected, arm, sources):
    if (
        old_tokens.get("token_evidence_complete") is not True
        or old_graph.get("causal_graph_compiled") is not False
        or old_graph.get("reason") != "invalid_causal_dependencies"
        or old_graph.get("error") != "tool duration and timestamps disagree"
        or old_graph.get("token_evidence_complete") is not True
        or corrected.get("causal_graph_compiled") is not True
        or corrected.get("token_evidence_complete") is not True
        or corrected.get("arm") != str(arm)
        or corrected.get("requests") != old_tokens.get("native_requests")
        or corrected.get("input_jsonl_prefixes") != old_graph.get("input_jsonl_prefixes")
        or corrected.get("token_input_jsonl_prefixes") != old_tokens.get("input_jsonl_prefixes")
    ):
        raise ValueError("only the observed callback-clock audit failure can be requalified")
    if any(corrected.get("analysis_source_sha256", {}).get(name) != sources[name]
           for name in ANALYSIS_SOURCES):
        raise ValueError("corrected audit was not produced by the bound analysis sources")
    prefixes = {
        **corrected["input_jsonl_prefixes"], **corrected["token_input_jsonl_prefixes"],
    }
    for name, prefix in prefixes.items():
        path = Path(name).resolve()
        if (
            not path.is_relative_to(arm) or path.stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(path) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed raw source changed or has an unsealed tail: " + name)
    return tuple(Path(name) for name in prefixes)


def publication_conflicts(graph):
    starts, conflicts = {}, []
    for node in graph.nodes:
        event = node.event
        if event is None:
            continue
        if event.kind is Kind.TOOL_START:
            starts[_tool_key(event)] = event
        elif event.kind is Kind.TOOL_END:
            start = starts.pop(_tool_key(event))
            declared = event.attributes.get("duration_ms")
            published = event.ts_ms - start.ts_ms
            if type(declared) not in (int, float) or not math.isfinite(declared):
                raise ValueError("native tool lacks its original measured duration")
            observed = _callback_observed_timestamp(event) - _callback_observed_timestamp(start)
            if abs(observed - declared) > 1.:
                raise ValueError("original tool duration lacks consistent observation evidence")
            if abs(published - declared) > 1.:
                conflicts.append({
                    "workflow_id": event.workflow_id, "tool_start_event_id": start.event_id,
                    "tool_end_event_id": event.event_id, "tool_duration_ms": declared,
                    "published_interval_ms": published, "observed_interval_ms": observed,
                })
    if starts or not conflicts:
        raise ValueError("requalification has no complete observed publication-clock conflict")
    return conflicts


def audit(registration, corrected_audit, cpu_evidence):
    registration, corrected_audit, cpu_evidence = (
        Path(path).resolve() for path in (registration, corrected_audit, cpu_evidence)
    )
    state_path = registration.with_suffix(".state.json")
    plan, state = capture.load(registration), capture.load(state_path)
    expected, drain_path = completed_execution(plan, state)
    capture.comparison.verify_sources(plan)
    run = Path(plan["run_root"]).resolve()
    arm = run / "native_capture"
    capture_path = run / "capture_plan.json"
    if capture.load(capture_path) != plan:
        raise ValueError("original registered and executed capture plans differ")
    sources, cpu_paths, cpu_cases = verify_cpu(cpu_evidence)
    token_path, graph_path = (run / name for name in ("token_evidence.json", "causal_evidence.json"))
    old_tokens, old_graph, corrected = (
        capture.load(path) for path in (token_path, graph_path, corrected_audit)
    )
    raw_paths = matching_audits(old_tokens, old_graph, corrected, arm, sources)
    verified_tokens = audit_tokens(arm)
    if (
        verified_tokens.get("token_evidence_complete") is not True
        or verified_tokens.get("original_generation_parameters_complete") is not True
        or verified_tokens.get("native_requests") != corrected["requests"]
        or verified_tokens.get("input_jsonl_prefixes") != old_tokens["input_jsonl_prefixes"]
    ):
        raise ValueError("fresh native token audit differs from the original complete capture")
    from scripts.run_causal_gpu_replay import source_plan

    graph, _expected, _source_evidence = source_plan(arm)
    summaries = list(arm.glob("client_*/summary.json"))
    if len(summaries) != 1:
        raise ValueError("require one exact completed native client")
    summary = capture.load(summaries[0])
    results = list(summaries[0].parent.glob("workflows/*/result.json"))
    if len(results) != expected or any(
        (result := capture.load(path)).get("natural_terminal") is not True
        or result.get("native_agent_jct_eligible") is not True or result.get("error")
        or result.get("semantic_gate_controlled_stop")
        or any(result.get("agent_control", {}).get(name, 0) for name in (
            "guard_intervened_completions", "guard_intervened_natural_language_returns",
            "forced_semantic_completions",
        ))
        for path in results
    ):
        raise ValueError("all original workflows must have complete unguarded natural returns")
    selection = capture.oneshot_source(
        summary, graph, expected_workflows=expected,
        expected_profile=plan["environment"]["FANOUT_PROFILE"],
    )
    if selection["accepted"] is not True or graph.summary()["requests"] != corrected["requests"] or (
        graph.summary()["workflows"] != expected
        or selection.get("minimum_completed_root_join_members", 0) < 2
    ):
        raise ValueError("corrected source does not satisfy its original collection contract")
    conflicts = publication_conflicts(graph)
    inputs = (
        registration, state_path, capture_path, token_path, graph_path, drain_path,
        corrected_audit, cpu_evidence, *cpu_paths, *raw_paths, summaries[0], *results,
        *(ROOT / name for name in BOUND_SOURCES),
    )
    return {
        "schema_version": 1, "kind": KIND, "original_registration": str(registration),
        "corrected_causal_audit": str(corrected_audit), "cpu_evidence": str(cpu_evidence),
        "original_capture_failed_state_preserved": True,
        "original_execution_and_actual_exit_verified": True,
        "fresh_native_drain_verified": True, "raw_tokens_events_and_tool_times_unchanged": True,
        "source_selection": selection, "native_requests": corrected["requests"],
        "workflows": expected,
        "token_source_sha256": sha256_file(arm / "server/request_tokens.jsonl"),
        "publication_clock_conflicts": conflicts, "passing_CPU_cases": cpu_cases,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "GPU_action": False, "gpu_replay_qualified": False, "performance_accepted": False,
        "scope": "Corrected CPU analysis of complete original execution; no new native capture or timing edits.",
    }


def verify(path, registration):
    path, registration = Path(path).resolve(), Path(registration).resolve()
    proof = capture.load(path)
    if proof.get("kind") != KIND or proof.get("original_registration") != str(registration):
        raise ValueError("source requalification belongs to another original execution")
    expected = audit(registration, proof["corrected_causal_audit"], proof["cpu_evidence"])
    if {key: value for key, value in proof.items() if key != "observed_at"} != expected:
        raise ValueError("source requalification differs from current exact raw evidence")
    return {**expected["inputs_sha256"], str(path): sha256_file(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "corrected-audit", "cpu-evidence", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or git_source_state(ROOT)["dirty"] or args.output.exists()
    ):
        raise ValueError("hide CUDA, use clean committed analysis sources, and preserve previous evidence")
    result = audit(args.registration, args.corrected_audit, args.cpu_evidence)
    capture.reservation.write_json(args.output, {
        "observed_at": datetime.now(timezone.utc).isoformat(), **result,
    })
    print({
        "workflows": result["workflows"], "requests": result["native_requests"],
        "corrected_publication_conflicts": len(result["publication_clock_conflicts"]),
        "original_failure_preserved": True, "GPU_action": False,
    })


if __name__ == "__main__":
    main()
