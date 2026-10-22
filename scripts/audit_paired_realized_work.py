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
from scripts.compare_native_policy_runs import records
from scripts.run_internal_comparison import load, reservation


def collect_arm(arm: Path) -> dict:
    clients = list(arm.glob("client_*/summary.json"))
    if len(clients) != 1:
        raise ValueError("paired work audit requires one completed client summary")
    summary = load(clients[0])
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
    unknown, duplicates, missing = Counter(), Counter(), Counter()
    seen = set()
    for event in records(arm / "server/runtime_events.sglang.jsonl"):
        kind = event.get("kind")
        if kind not in ("llm_submit", "llm_result"):
            continue
        attrs = event.get("attributes") or {}
        identity = kind, event.get("workflow_id"), attrs.get("request_id")
        if identity in seen:
            duplicates[kind] += 1
            continue
        seen.add(identity)
        row = workflows.get(event.get("workflow_id"))
        if row is None:
            unknown[kind] += 1
            continue
        field = "prompt_tokens" if kind == "llm_submit" else "output_tokens"
        value = attrs.get(field)
        if type(value) is not int or value < 0:
            missing[field] += 1
            continue
        row["native_submits" if kind == "llm_submit" else "native_results"] += 1
        row["input_tokens" if kind == "llm_submit" else "output_tokens"] += value
    indexed = {row["instance_id"]: row for row in workflows.values()}
    if len(indexed) != len(workflows):
        raise ValueError("duplicate task identity")
    return {
        "run": str(arm.resolve()), "workflows": indexed,
        "unknown_workflows": dict(unknown), "duplicate_events": dict(duplicates),
        "missing_usage": dict(missing),
        "work_evidence_complete": not (unknown or duplicates or missing) and all(
            row["native_submits"] == row["native_results"] and row["native_submits"] > 0
            for row in indexed.values()
        ),
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
        "arms": arms,
        "pairs": {f"{left}_vs_{right}": compare_pair(arms[left], arms[right])
                  for left, right in combinations(arms, 2)},
        "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
