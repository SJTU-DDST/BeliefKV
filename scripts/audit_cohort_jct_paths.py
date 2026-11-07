#!/usr/bin/env python3
"""Decompose complete measured workflows while preserving censored background."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_causal_jct_paths import account, first_launches, request_cohorts, unique
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_gpu_replay import source_plan


def measured_plan(plan, workflows):
    workflows = tuple(workflows)
    if (
        not workflows or any(type(value) is not str or not value for value in workflows)
        or len(set(workflows)) != len(workflows)
    ):
        raise ValueError("require distinct fixed measured workflow identities")
    members = set(workflows)
    nodes = tuple(node for node in plan.nodes if node.workflow_id in members)
    indexed = {node.node_id: node for node in nodes}
    if len(indexed) != len(nodes):
        raise ValueError("duplicate measured graph node")
    for kind in (Kind.WORKFLOW_START, Kind.WORKFLOW_END):
        counts = Counter(
            node.workflow_id for node in nodes if node.event and node.event.kind is kind
        )
        if counts != Counter({workflow: 1 for workflow in workflows}):
            raise ValueError("every measured workflow requires its whole source graph")
    for node in nodes:
        if any(
            previous not in indexed or indexed[previous].workflow_id != node.workflow_id
            for previous in node.dependencies
        ):
            raise ValueError("cannot drop or cross a measured causal dependency")
    requests = [node.request_id for node in nodes if node.request_id]
    if len(requests) != len(set(requests)) or any(
        request not in plan.request_usage for request in requests
    ):
        raise ValueError("invalid measured source request population")
    return replace(
        plan, nodes=nodes,
        request_usage={request: plan.request_usage[request] for request in requests},
    )


def request_details(events, requests, launches, cohorts):
    starts = {
        row["workflow_id"]: row["ts_ms"] for row in events
        if row["kind"] == Kind.WORKFLOW_START.value
    }
    details = {}
    for request, row in requests.items():
        workflow, invocation, context, epoch = row["native_receipt"]["identity"]
        start = starts[workflow]
        submitted = row["submitted_monotonic_ms"]
        launch = launches[request]
        complete = row["native_complete_monotonic_ms"]
        details[request] = {
            "workflow_id": workflow, "invocation_id": invocation,
            "context_id": context, "context_epoch": epoch,
            "cohort": cohorts.get(request, "other"),
            "submitted_offset_s": (submitted - start) / 1000.,
            "first_launch_offset_s": (launch - start) / 1000.,
            "complete_offset_s": (complete - start) / 1000.,
            "submit_to_first_launch_s": (launch - submitted) / 1000.,
            "first_launch_to_complete_s": (complete - launch) / 1000.,
            "prompt_tokens": row["prompt_tokens"], "output_tokens": row["output_tokens"],
        }
    return details


def audit_arm(plan, arm, report_path):
    report = json.loads(report_path.read_bytes())
    workflows = report["cohort"]["measured_workflow_ids"]
    if (
        report.get("all_measured_completed") is not True
        or report.get("normal_tails_removed") is not False
        or set(report["measured_workflow_JCT_ms"]) != set(workflows)
    ):
        raise ValueError("require complete fixed-cohort JCT with all normal tails")
    selected = measured_plan(plan, workflows)
    members, expected_requests = set(workflows), set(selected.request_usage)
    reader = SealedRecords()
    events = [
        row for row in reader(arm / "replay/runtime_events.replay.jsonl")
        if row["workflow_id"] in members
    ]
    requests = unique((
        row for row in reader(arm / "replay/native_requests.jsonl")
        if row["request_id"] in expected_requests
        or row["native_receipt"]["identity"][0] in members
    ), "request_id")
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if (
        status.get("writer_error") is not None
        or status.get("dropped_records") != 0 or status.get("failed_records") != 0
        or status.get("writer_queue_depth") != 0 or status.get("pending_batch_count") != 0
    ):
        raise ValueError("require complete and drained native telemetry")
    samples = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"][
        "records_seen"
    ]
    launches = first_launches(
        reader(arm / "server/runtime_audit.jsonl"), requests, expected_records=samples,
    )
    cohorts = request_cohorts(events)
    result = account(
        selected, events, requests.values(), launches,
        expected_jct_ms=report["measured_workflow_JCT_ms"], cohorts=cohorts,
    )
    details = request_details(events, requests, launches, cohorts)
    for workflow, row in result["workflows"].items():
        own = [value for value in details.values() if value["workflow_id"] == workflow]
        row["workflow_to_first_submit_s"] = min(value["submitted_offset_s"] for value in own)
        row["workflow_to_first_native_launch_s"] = min(
            value["first_launch_offset_s"] for value in own
        )
        row["request_count"] = len(own)
    return {
        **result, "requests": details, "arm": str(arm.resolve()),
        "cohort": report["cohort"], "native_launch_samples": samples,
        "measured_receipts": len(requests),
        "measured_outputs": sum(row["output_tokens"] for row in requests.values()),
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (report_path, status_path)
        },
    }


def compare_paths(left, right):
    if left["cohort"] != right["cohort"] or left["workflows"].keys() != right["workflows"].keys():
        raise ValueError("comparison must preserve the exact arrival-selected cohort")
    if left["requests"].keys() != right["requests"].keys():
        raise ValueError("comparison changed measured request population")
    for request, before in left["requests"].items():
        after = right["requests"][request]
        if any(before[key] != after[key] for key in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
            "prompt_tokens", "output_tokens",
        )):
            raise ValueError("comparison changed measured work or identity")
    workflows = []
    for workflow, before in left["workflows"].items():
        after = right["workflows"][workflow]
        components = set(before["components_ms"]) | set(after["components_ms"])
        differences = {
            name: (after["components_ms"].get(name, 0.) - before["components_ms"].get(name, 0.))
            / 1000. for name in sorted(components)
        }
        delta = (after["jct_ms"] - before["jct_ms"]) / 1000.
        if not math.isclose(sum(differences.values()), delta, rel_tol=1e-10, abs_tol=1e-7):
            raise ValueError("path component differences do not reproduce complete JCT")
        request_deltas = []
        for request, old in left["requests"].items():
            if old["workflow_id"] != workflow:
                continue
            new = right["requests"][request]
            request_deltas.append({
                "request_id": request, "invocation_id": old["invocation_id"],
                "cohort_left": old["cohort"], "cohort_right": new["cohort"],
                "submit_to_first_launch_delta_s": (
                    new["submit_to_first_launch_s"] - old["submit_to_first_launch_s"]
                ),
                "first_launch_to_complete_delta_s": (
                    new["first_launch_to_complete_s"] - old["first_launch_to_complete_s"]
                ),
                "on_left_observed_path": request in before["critical_path_requests"],
                "on_right_observed_path": request in after["critical_path_requests"],
                "left": old, "right": new,
            })
        workflows.append({
            "workflow_id": workflow, "left_jct_s": before["jct_ms"] / 1000.,
            "right_jct_s": after["jct_ms"] / 1000., "right_minus_left_jct_s": delta,
            "right_minus_left_components_s": differences,
            "first_native_launch_delta_s": (
                after["workflow_to_first_native_launch_s"]
                - before["workflow_to_first_native_launch_s"]
            ),
            "observed_path_membership_changed": (
                set(before["critical_path_requests"]) != set(after["critical_path_requests"])
            ),
            "largest_request_wait_increases": sorted(
                request_deltas, key=lambda row: row["submit_to_first_launch_delta_s"],
                reverse=True,
            )[:10],
        })
    return {
        "right_minus_left_mean_jct_s": right["mean_jct_s"] - left["mean_jct_s"],
        "right_minus_left_mean_components_s": {
            key: statistics.mean(row["right_minus_left_components_s"].get(key, 0.) for row in workflows)
            for key in sorted({
                key for row in workflows for key in row["right_minus_left_components_s"]
            })
        },
        "workflows_by_descending_jct_delta": sorted(
            workflows, key=lambda row: row["right_minus_left_jct_s"], reverse=True,
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-arm", type=Path, required=True)
    parser.add_argument("--arm", action="append", required=True, help="NAME=ARM_PATH")
    parser.add_argument("--cohort-report", action="append", required=True, help="NAME=REPORT_PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    arms = dict(value.split("=", 1) for value in args.arm)
    reports = dict(value.split("=", 1) for value in args.cohort_report)
    if (
        len(arms) != len(args.arm) or len(reports) != len(args.cohort_report)
        or arms.keys() != reports.keys() or len(arms) < 2 or args.output.exists()
    ):
        raise ValueError("require matching distinct arms/reports and a new output")
    plan, _, source = source_plan(args.source_arm)
    results = {
        name: audit_arm(plan, Path(path), Path(reports[name])) for name, path in arms.items()
    }
    names = list(arms)
    comparisons = {
        f"{left}_to_{right}": compare_paths(results[left], results[right])
        for index, left in enumerate(names) for right in names[index + 1:]
    }
    paths = (
        Path(__file__), ROOT / "scripts/audit_causal_jct_paths.py",
        ROOT / "scripts/audit_join_reentry_service.py", ROOT / "scripts/audit_tool_reentry_service.py",
        ROOT / "scripts/audit_sealed_wait_prefetch.py",
    )
    output = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_evidence": source, "arms": results, "comparisons": comparisons,
        "inputs_sha256": {str(path): sha256_file(path) for path in paths},
        "scope": {
            "whole_fixed_measured_workflows_only": True,
            "background_retained_in_actual_execution_but_excluded_from_path_population": True,
            "last_completing_dependency_path_reconstructs_each_complete_JCT": True,
            "submit_to_launch_includes_all_local_waiting": True,
            "first_launch_to_complete_includes_interleaving_and_is_not_pure_GPU_time": True,
            "first_launch_is_not_streamed_TTFT": True,
            "request_wait_deltas_are_not_additive_or_causal_removable_JCT": True,
            "observed_paths_can_differ_between_methods": True,
            "no_counterfactual_execution_or_prediction_error_attribution": True,
        },
        "GPU_action": False, "performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
    print(json.dumps({
        "measured_workflows": len(results[names[0]]["workflows"]),
        "mean_jct_s": {name: result["mean_jct_s"] for name, result in results.items()},
        "mean_differences_s": {
            name: value["right_minus_left_mean_jct_s"] for name, value in comparisons.items()
        },
    }))


if __name__ == "__main__":
    main()
