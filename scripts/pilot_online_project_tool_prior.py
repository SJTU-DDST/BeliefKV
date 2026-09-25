#!/usr/bin/env python3
"""Causal, read-only project/class online adaptation on completed tool calls."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import heapq
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _quantile, _read_workflow


def pilot(
    workflows: Path, *, minimum_support: int = 16,
    allow_legacy_origin: bool = False,
) -> dict:
    if minimum_support < 2:
        raise ValueError("minimum support must be at least two")
    calls = sorted(
        (
            row
            for path in workflows.glob("*/runtime_events.deepagents.jsonl")
            for row in _read_workflow(
                path, allow_legacy_origin=allow_legacy_origin
            )
        ),
        key=lambda row: (row["start_ts_ms"], row["workflow"]),
    )
    if not calls:
        raise ValueError("no tool calls")
    projects = {row["project"] for row in calls}
    child_projects = {row["project"] for row in calls if row["is_child"] is True}
    if len(child_projects) < 2:
        raise ValueError("project-isolated baseline requires two child projects")
    baseline = {}
    for project in projects:
        train = [row for row in calls if row["project"] != project
                 and row["is_child"] is True]
        class_durations = defaultdict(list)
        for row in train:
            class_durations[row["class"]].append(row["duration_ms"])
        baseline[project] = (
            median(row["duration_ms"] for row in train),
            {name: median(values) for name, values in class_durations.items()
             if len(values) >= 8},
        )
    pending = []
    observed: dict[tuple[str, str], deque[float]] = defaultdict(
        lambda: deque(maxlen=64)
    )
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    all_cold: list[dict] = []
    total_long_child = 0
    eligible_long_child = 0
    for sequence, row in enumerate(calls):
        now = row["start_ts_ms"]
        while pending and pending[0][0] < now:
            _, _, done = heapq.heappop(pending)
            if done["status"] == "success" and done["is_child"] is True:
                observed[(done["project"], done["class"])].append(
                    done["duration_ms"]
                )
        heapq.heappush(pending, (row["terminal_ts_ms"], sequence, row))
        if row["is_child"] is not True or (
            row["previous"] is not None and row["previous"][2] == "success"
        ):
            continue
        long_call = row["duration_ms"] >= 2_000
        total_long_child += long_call
        history = observed[(row["project"], row["class"])]
        general, medians = baseline[row["project"]]
        reference = medians.get(row["class"], general)
        supported = len(history) >= minimum_support
        eligible_long_child += long_call and supported
        prior = median(history) if supported else reference
        long_rate = (
            sum(value >= 2_000 for value in history) / len(history)
            if supported else 0.0
        )
        item = {
            "project": row["project"], "workflow": row["workflow"],
            "long": long_call, "duration_ms": row["duration_ms"],
            "prior_error_ms": abs(row["duration_ms"] - prior),
            "baseline_error_ms": abs(row["duration_ms"] - reference),
            "prior_ms": prior, "historical_long_rate": long_rate,
            "supported": supported,
        }
        all_cold.append(item)
        if supported:
            groups[(row["project"], minimum_support)].append(item)
    all_rows = [row for group in groups.values() for row in group]
    selected = [
        row for row in all_rows
        if row["historical_long_rate"] >= .8 and row["prior_ms"] >= 2_000
    ]
    selection_sweep = {}
    for cut in (.5, .6, .7, .8, .9):
        chosen = [
            row for row in all_rows
            if row["historical_long_rate"] >= cut and row["prior_ms"] >= 2_000
        ]
        true_long = sum(row["long"] for row in chosen)
        selection_sweep[str(cut)] = {
            "selected": len(chosen),
            "true_long": true_long,
            "false_long": len(chosen) - true_long,
            "precision": true_long / len(chosen) if chosen else None,
            "recall_of_all_cold_long": (
                true_long / total_long_child if total_long_child else None
            ),
            "workflow_count": len({row["workflow"] for row in chosen}),
        }
    def metrics(rows: list[dict]) -> dict:
        workflows = defaultdict(list)
        for row in rows:
            workflows[row["workflow"]].append(row)
        return {
            "count": len(rows),
            "long_count": sum(row["long"] for row in rows),
            "workflow_count": len(workflows),
            "supported_count": sum(row["supported"] for row in rows),
            "prior_p50_error_ms": _quantile([
                row["prior_error_ms"] for row in rows
            ], .5),
            "prior_p90_error_ms": _quantile([
                row["prior_error_ms"] for row in rows
            ], .9),
            "prior_p95_error_ms": _quantile([
                row["prior_error_ms"] for row in rows
            ], .95),
            "baseline_p50_error_ms": _quantile([
                row["baseline_error_ms"] for row in rows
            ], .5),
            "baseline_p90_error_ms": _quantile([
                row["baseline_error_ms"] for row in rows
            ], .9),
            "prior_within_500ms": (
                sum(row["prior_error_ms"] <= 500 for row in rows) / len(rows)
                if rows else None
            ),
            "baseline_within_500ms": (
                sum(row["baseline_error_ms"] <= 500 for row in rows) / len(rows)
                if rows else None
            ),
            "prior_workflow_weighted_p50_ms": _quantile([
                _quantile([row["prior_error_ms"] for row in values], .5)
                for values in workflows.values()
            ], .5),
            "baseline_workflow_weighted_p50_ms": _quantile([
                _quantile([row["baseline_error_ms"] for row in values], .5)
                for values in workflows.values()
            ], .5),
        }
    return {
        "status": "offline_causal_online_adaptation_pilot_not_deployable",
        "legacy_origin_inferred": allow_legacy_origin,
        "minimum_completed_project_class_samples": minimum_support,
        "total_cold_child_long": total_long_child,
        "long_after_support": eligible_long_child,
        "all_cold_child": metrics(all_cold),
        "all_cold_child_long": metrics([row for row in all_cold if row["long"]]),
        "predicted_long": {
            "count": sum(row["prior_ms"] >= 2_000 for row in all_cold),
            "true_long": sum(
                row["long"] for row in all_cold if row["prior_ms"] >= 2_000
            ),
            "false_long": sum(
                not row["long"] for row in all_cold if row["prior_ms"] >= 2_000
            ),
            "long_recall": (
                sum(row["long"] for row in all_cold if row["prior_ms"] >= 2_000)
                / total_long_child if total_long_child else None
            ),
        },
        "supported": metrics(all_rows),
        "supported_long": metrics([row for row in all_rows if row["long"]]),
        "high_confidence_long_selection": {
            **metrics(selected),
            "precision": (
                sum(row["long"] for row in selected) / len(selected)
                if selected else None
            ),
            "recall_of_all_cold_long": (
                sum(row["long"] for row in selected) / total_long_child
                if total_long_child else None
            ),
        },
        "long_selection_sweep": selection_sweep,
        "by_project": {
            project: {
                "supported": metrics([
                    row for row in all_rows if row["project"] == project
                ]),
                "selected": metrics([
                    row for row in selected if row["project"] == project
                ]),
            }
            for project in sorted(projects)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--minimum-support", type=int, default=16)
    parser.add_argument("--allow-legacy-origin", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = pilot(
        args.workflows, minimum_support=args.minimum_support,
        allow_legacy_origin=args.allow_legacy_origin,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
