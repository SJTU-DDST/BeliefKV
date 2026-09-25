#!/usr/bin/env python3
"""Read-only cross-workflow, same-project tool history causal replay."""

from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path
from statistics import median
from collections import defaultdict
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_repeated_tool_timing import _read_workflow, _summarize


def replay(workflows: Path) -> dict:
    calls = [
        row
        for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
        for row in _read_workflow(path)
    ]
    calls.sort(key=lambda row: (row["start_ts_ms"], row["workflow"]))
    if not calls:
        raise ValueError("no completed execute calls")
    projects = {row["project"] for row in calls}
    if len(projects) < 2:
        raise ValueError("project-isolated category baseline requires two projects")
    project_medians = {}
    for project in projects:
        remaining = [row for row in calls if row["project"] != project]
        if not remaining:
            raise ValueError("empty project-isolated baseline")
        overall = median(row["duration_ms"] for row in remaining)
        by_class = defaultdict(list)
        for row in remaining:
            by_class[row["class"]].append(row["duration_ms"])
        project_medians[project] = (
            overall,
            {name: median(values) for name, values in by_class.items()
             if len(values) >= 8},
        )

    pending: list[tuple[float, int, dict]] = []
    history: dict[tuple[str, str], dict] = {}
    novel = []
    counters = defaultdict(int)
    for sequence, row in enumerate(calls):
        now = row["start_ts_ms"]
        while pending and pending[0][0] < now:
            _, _, completed = heapq.heappop(pending)
            signature = completed["input_sha256"]
            if signature:
                history[(completed["project"], signature)] = completed
        heapq.heappush(pending, (row["terminal_ts_ms"], sequence, row))
        if row["is_child"] is not True:
            continue
        counters["all_completed_child_execute"] += 1
        if row["duration_ms"] >= 2_000:
            counters["all_long_child_execute"] += 1
        previous_invocation = row["previous"]
        if previous_invocation is not None and previous_invocation[2] == "success":
            counters["already_has_invocation_history"] += 1
            continue
        key = row["project"], row["input_sha256"]
        prior = history.get(key) if row["input_sha256"] else None
        if (
            prior is None or prior["status"] != "success"
            or prior["workflow"] == row["workflow"]
        ):
            continue
        if prior["terminal_ts_ms"] >= now:
            raise ValueError("future cross-workflow tool completion leaked")
        counters["new_successful_cross_workflow_prior"] += 1
        global_duration, classes = project_medians[row["project"]]
        baseline = classes.get(row["class"], global_duration)
        novel.append((row, prior, baseline))

    def scores(*, long_only: bool = False) -> dict:
        selected = [
            (row, prior, baseline) for row, prior, baseline in novel
            if not long_only or row["duration_ms"] >= 2_000
        ]
        def errors(use_prior: bool) -> list[dict]:
            return [{
                **row,
                "error_ms": abs(
                    row["duration_ms"] -
                    (prior["duration_ms"] if use_prior else baseline)
                ),
            } for row, prior, baseline in selected]
        return {
            "prior": _summarize(errors(True)),
            "held_out_category": _summarize(errors(False)),
            "predicted_long": sum(
                prior["duration_ms"] >= 2_000 for _, prior, _ in selected
            ),
            "predicted_long_false_positive": sum(
                prior["duration_ms"] >= 2_000 and row["duration_ms"] < 2_000
                for row, prior, _ in selected
            ),
        }
    return {
        "status": "offline_project_history_diagnostic_not_deployable",
        "counts": dict(counters),
        "new_cross_workflow_samples": scores(),
        "new_cross_workflow_long_child": scores(long_only=True),
        "note": (
            "Finished calls only. Cross-task image/version changes and "
            "current task workspace divergence are not controlled."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
