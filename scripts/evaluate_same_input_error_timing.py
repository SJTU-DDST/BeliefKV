#!/usr/bin/env python3
"""Read-only paired timing of prior failed exact input vs frozen project history."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import median

import numpy as np

from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_structure_holdout import cold_calls, _paired_long_gain


def _baseline(row: dict) -> tuple[str, float] | None:
    for source, support, minimum in (
        ("project_shape_survivor_100ms_total_median_ms",
         "project_shape_survivor_100ms_support", 4),
        ("project_shape_survivor_500ms_total_median_ms",
         "project_shape_survivor_500ms_support", 4),
        ("project_class_duration_median_ms",
         "project_class_completed_support", 16),
    ):
        value = row.get(source)
        count = row.get(support)
        if (
            type(value) in (int, float) and math.isfinite(value) and value > 100
            and type(count) is int and count >= minimum
        ):
            return source, float(value)
    return None


def candidates(rows: list[dict]) -> list[dict]:
    matched = []
    for row in rows:
        previous = row.get("previous")
        if (
            row.get("is_child") is not True
            or row.get("status") not in ("success", "error")
            or not row.get("input_sha256")
            or not isinstance(previous, (tuple, list))
            or len(previous) != 3 or previous[2] != "error"
            or not all(type(value) in (int, float) and math.isfinite(value)
                       for value in (
                           previous[0], previous[1],
                           row.get("start_ts_ms"), row.get("duration_ms"),
                       ))
            or previous[0] <= 100
            or previous[1] >= row["start_ts_ms"]
            or row["duration_ms"] <= 100
        ):
            continue
        frozen = _baseline(row)
        if frozen is None:
            continue
        source, baseline = frozen
        matched.append({
            **row,
            "baseline_source": source,
            "baseline_ms": baseline,
            "exact_failed_input_ms": float(previous[0]),
        })
    return matched


def _score(rows: list[dict]) -> dict:
    if not rows:
        return {"count": 0, "workflow_count": 0}
    baseline = np.array([row["baseline_ms"] for row in rows])
    exact = np.array([row["exact_failed_input_ms"] for row in rows])
    baseline_errors = [
        abs(row["duration_ms"] - prediction)
        for row, prediction in zip(rows, baseline)
    ]
    exact_errors = [
        abs(row["duration_ms"] - prediction)
        for row, prediction in zip(rows, exact)
    ]
    workflow_baseline = defaultdict(list)
    workflow_exact = defaultdict(list)
    for row, b_error, e_error in zip(rows, baseline_errors, exact_errors):
        workflow_baseline[row["workflow"]].append(b_error)
        workflow_exact[row["workflow"]].append(e_error)
    return {
        "count": len(rows),
        "workflow_count": len(workflow_baseline),
        "distinct_inputs": len({
            (row["workflow"], row["invocation"], row["input_sha256"])
            for row in rows
        }),
        "successful_returns": sum(row["status"] == "success" for row in rows),
        "baseline_sources": dict(Counter(row["baseline_source"] for row in rows)),
        "baseline_error_p50_ms": _quantile(baseline_errors, .5),
        "exact_error_p50_ms": _quantile(exact_errors, .5),
        "baseline_error_p90_ms": _quantile(baseline_errors, .9),
        "exact_error_p90_ms": _quantile(exact_errors, .9),
        "baseline_within_500ms": sum(int(error <= 500) for error in baseline_errors),
        "exact_within_500ms": sum(int(error <= 500) for error in exact_errors),
        "baseline_workflow_median_p50_ms": median([
            median(errors) for errors in workflow_baseline.values()
        ]),
        "exact_workflow_median_p50_ms": median([
            median(errors) for errors in workflow_exact.values()
        ]),
        "paired_workflow_gain": _paired_long_gain(rows, baseline, exact),
    }


def evaluate(rows: list[dict]) -> dict:
    selected = candidates(rows)
    first_by_input: dict[tuple[str, str, str], dict] = {}
    for row in selected:
        key = row["workflow"], row["invocation"], row["input_sha256"]
        if (
            key not in first_by_input
            or row["start_ts_ms"] < first_by_input[key]["start_ts_ms"]
        ):
            first_by_input[key] = row
    unique = list(first_by_input.values())
    projects = sorted({row["project"] for row in rows})
    return {
        "status": "read_only_frozen_start_prior_not_action_eligible",
        "complete_calls_including_returned_failures": len(rows),
        "matched": _score(selected),
        "first_per_workflow_invocation_input": _score(unique),
        "by_project": {
            project: _score([
                row for row in selected if row["project"] == project
            ])
            for project in projects
        },
        "first_per_input_by_project": {
            project: _score([
                row for row in unique if row["project"] == project
            ])
            for project in projects
        },
    }


def validate_manifest(workflows: Path, manifest: Path) -> dict:
    payload = manifest.read_bytes()
    items = json.loads(payload)["workloads"]
    expected = [item["instance_id"] for item in items]
    if not expected or len(expected) != len(set(expected)):
        raise ValueError("workload manifest has no tasks or duplicate IDs")
    actual = {
        path.parent.name
        for path in workflows.glob("*/runtime_events.deepagents.jsonl")
    }
    if actual != set(expected):
        raise ValueError(
            f"workflow coverage differs from frozen manifest: "
            f"missing={sorted(set(expected) - actual)} "
            f"unexpected={sorted(actual - set(expected))}"
        )
    return {
        "expected_workflows": len(expected),
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        "projects": sorted({name.split("__", 1)[0] for name in expected}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--workload-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scope = validate_manifest(args.workflows, args.workload_manifest)
    rows, counts = cold_calls(
        args.workflows, include_returned_failures=True,
    )
    result = evaluate(rows)
    result["collector"] = counts
    result["frozen_scope"] = scope
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
