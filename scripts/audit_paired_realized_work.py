#!/usr/bin/env python3
"""Audit complete per-workflow work without selecting a favorable JCT subset."""

from __future__ import annotations

import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.metrics.workload_compatibility import WORK_METRICS, compare_workload
from beliefkv.experiments.source_provenance import sha256_file
from scripts.compare_native_policy_runs import records
from scripts.run_internal_comparison import load, reservation


REQUEST_KINDS = ("llm_submit", "llm_result")


def client_requests(client: Path, row: dict) -> dict:
    path = client / "workflows" / row["instance_id"] / "runtime_events.deepagents.jsonl"
    identities = {kind: {} for kind in REQUEST_KINDS}
    errors = Counter()
    for event in records(path):
        kind = event.get("kind")
        if kind not in identities:
            continue
        attrs = event.get("attributes") or {}
        request = attrs.get("request_id")
        if event.get("workflow_id") != row["workflow_id"]:
            errors["wrong_workflow"] += 1
            continue
        if type(request) is not str or not request:
            errors["missing_request_id"] += 1
            continue
        internal = attrs.get("runtime_internal")
        if type(internal) is not bool:
            errors["missing_internal_classification"] += 1
        if request in identities[kind]:
            errors["duplicate_" + kind] += 1
            continue
        identities[kind][request] = internal
    return {
        "source": str(path), "source_exists": path.is_file(),
        "identities": identities, "errors": dict(errors),
        "summary_counts": {
            kind: row["trace"].get("event_counts", {}).get(kind) for kind in REQUEST_KINDS
        },
    }


def request_alignment(client: dict, native: dict, expected_requests: int) -> dict:
    submitted, returned = (client["identities"][kind] for kind in REQUEST_KINDS)
    external = {
        kind: sum(internal is False for internal in client["identities"][kind].values())
        for kind in REQUEST_KINDS
    }
    differences = {
        "client_submit_without_result": sorted(submitted.keys() - returned.keys()),
        "client_result_without_submit": sorted(returned.keys() - submitted.keys()),
        "client_internal_classification_disagrees": sorted(
            request for request in submitted.keys() & returned.keys()
            if submitted[request] is not returned[request]
        ),
        **{
            f"{kind}_{side}": sorted(
                client["identities"][kind].keys() - native[kind]
                if side == "missing_native" else native[kind] - client["identities"][kind].keys()
            )
            for kind in REQUEST_KINDS for side in ("missing_native", "missing_client")
        },
    }
    matches_summary = (
        type(expected_requests) is int and expected_requests > 0
        and all(value == expected_requests for value in external.values())
    )
    all_counts_match = all(
        type(client["summary_counts"][kind]) is int
        and client["summary_counts"][kind] == len(client["identities"][kind])
        for kind in REQUEST_KINDS
    )
    return {
        "client_source": client["source"], "client_source_exists": client["source_exists"],
        "client_errors": client["errors"],
        "client_external_counts": external,
        "client_internal_request_ids": sorted(
            request for request, internal in submitted.items() if internal is True
        ),
        "client_external_counts_match_summary": matches_summary,
        "client_summary_counts": client["summary_counts"],
        "client_total_counts_match_summary": all_counts_match,
        **differences,
        "complete": (
            client["source_exists"] and not client["errors"]
            and matches_summary and all_counts_match
            and not any(differences.values())
        ),
    }


def collect_arm(arm: Path) -> dict:
    summaries = list(arm.glob("client_*/summary.json"))
    if len(summaries) != 1:
        raise ValueError("paired work audit requires one completed client summary")
    client = summaries[0].parent
    summary = load(summaries[0])
    workflows = {}
    for row in summary["workflows"]:
        workflow = row["workflow_id"]
        if workflow in workflows:
            raise ValueError("duplicate workflow identity")
        workflows[workflow] = {
            "instance_id": row["instance_id"], "jct_seconds": row["duration_seconds"],
            "native_agent_jct_eligible": row.get("native_agent_jct_eligible"),
            "outcome": row["outcome"],
            "llm_requests": row["trace"]["llm_request_count"],
            "tool_calls": row["trace"]["tool_call_count"],
            "input_tokens": 0, "output_tokens": 0, "native_submits": 0, "native_results": 0,
        }
    clients = {
        row["workflow_id"]: client_requests(client, row)
        for row in summary["workflows"]
    }
    native = {
        workflow: {kind: set() for kind in REQUEST_KINDS} for workflow in workflows
    }
    unknown, duplicates, missing, invalid_identity = Counter(), Counter(), Counter(), Counter()
    seen = set()
    for event in records(arm / "server/runtime_events.sglang.jsonl"):
        kind = event.get("kind")
        if kind not in REQUEST_KINDS:
            continue
        attrs = event.get("attributes") or {}
        request = attrs.get("request_id")
        if type(request) is not str or not request:
            invalid_identity[kind] += 1
            continue
        identity = kind, event.get("workflow_id"), request
        if identity in seen:
            duplicates[kind] += 1
            continue
        seen.add(identity)
        row = workflows.get(event.get("workflow_id"))
        if row is None:
            unknown[kind] += 1
            continue
        native[event["workflow_id"]][kind].add(request)
        field = "prompt_tokens" if kind == "llm_submit" else "output_tokens"
        value = attrs.get(field)
        if type(value) is not int or value < 0:
            missing[field] += 1
            continue
        row["native_submits" if kind == "llm_submit" else "native_results"] += 1
        row["input_tokens" if kind == "llm_submit" else "output_tokens"] += value
    for workflow, row in workflows.items():
        row["request_alignment"] = request_alignment(
            clients[workflow], native[workflow], row["llm_requests"],
        )
    indexed = {row["instance_id"]: row for row in workflows.values()}
    if len(indexed) != len(workflows):
        raise ValueError("duplicate task identity")
    population_complete = (
        bool(indexed) and summary.get("workflow_count") == len(indexed)
        and summary.get("completed_workflows") == len(indexed)
        and all(row["outcome"] == "completed" for row in indexed.values())
    )
    inputs = [
        summaries[0], arm / "server/runtime_events.sglang.jsonl",
        *(Path(row["source"]) for row in clients.values()),
    ]
    return {
        "run": str(arm.resolve()), "workflows": indexed,
        "unknown_workflows": dict(unknown), "duplicate_events": dict(duplicates),
        "missing_usage": dict(missing), "invalid_native_request_identity": dict(invalid_identity),
        "complete_task_population": population_complete,
        "work_evidence_complete": population_complete and not (
            unknown or duplicates or missing or invalid_identity
        ) and all(
            row["native_submits"] == row["native_results"] and row["native_submits"] > 0
            and row["request_alignment"]["complete"]
            for row in indexed.values()
        ),
        "token_scope": (
            "All valid native request usage, including internal context compression. "
            "Summary LLM request counts exclude internal requests; client request IDs "
            "classify them because native events do not carry that classification."
        ),
        "input_sha256": {
            str(path.resolve()): sha256_file(path) if path.is_file() else None for path in inputs
        },
    }


def compare_pair(left: dict, right: dict) -> dict:
    a, b = left["workflows"], right["workflows"]
    common = sorted(a.keys() & b.keys())
    rows = [{
        "instance_id": instance, "left_work": a[instance], "right_work": b[instance],
        "workload_compatibility": compare_workload(a[instance], b[instance]),
        "jct_delta_seconds": a[instance]["jct_seconds"] - b[instance]["jct_seconds"],
    } for instance in common]
    return {
        "same_task_population": a.keys() == b.keys(), "paired_workflows": len(rows),
        "work_evidence_complete": left["work_evidence_complete"] and right["work_evidence_complete"],
        "median_symmetric_work_gaps": {
            field: median(
                row["workload_compatibility"]["metrics"][field]["symmetric_relative_gap"]
                for row in rows
            ) if rows else None for field in WORK_METRICS
        },
        "joint_close_counts": {
            str(tolerance): sum(
                compare_workload(a[instance], b[instance], tolerance=tolerance)[
                    "necessary_workload_condition_passed"
                ] for instance in common
            ) for tolerance in (.05, .10, .20)
        },
        "rows": rows, "performance_accepted": False,
        "scope": (
            "All shared tasks retained. Counts are diagnostics, not a selected JCT cohort. "
            "No JCT/token normalization or causal claim from aggregate or subset proximity."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    arms = {name: collect_arm(Path(path)) for name, path in
            (value.split("=", 1) for value in args.arm)}
    reservation.write_json(args.output, {
        "schema_version": 2, "created_at": reservation.now().isoformat(),
        "analysis_source_sha256": {
            path: sha256_file(ROOT / path) for path in (
                "scripts/audit_paired_realized_work.py",
                "scripts/compare_native_policy_runs.py",
                "beliefkv/metrics/workload_compatibility.py",
            )
        },
        "arms": arms,
        "pairs": {f"{left}_vs_{right}": compare_pair(arms[left], arms[right])
                  for left, right in combinations(arms, 2)},
        "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
