#!/usr/bin/env python3
"""Training-only causal project-history check for JOIN and parent reentry."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import _paired_long_gain
from scripts.evaluate_join_rolling_stage import (
    OBSERVED_THRESHOLDS, THRESHOLDS, collect,
)


def causal_project_predictions(
    rows: list[dict], target: str, *, minimum_support: int = 4,
    minimum_workflows: int = 3, window: int = 64,
) -> list[tuple[dict, float]]:
    if target not in ("join_lead_ms", "parent_lead_ms"):
        raise ValueError("unsupported JOIN target")
    if minimum_support < 2 or minimum_workflows < 2 or window < minimum_support:
        raise ValueError("invalid history support")
    history = deque(maxlen=window)
    completions = sorted(
        rows, key=lambda row: row["signal_ts_ms"] + row[target],
    )
    selected = []
    completed = 0
    for row in sorted(rows, key=lambda item: item["signal_ts_ms"]):
        while (
            completed < len(completions)
            and completions[completed]["signal_ts_ms"]
            + completions[completed][target] < row["signal_ts_ms"]
        ):
            history.append(completions[completed])
            completed += 1
        if (
            len(history) >= minimum_support
            and len({old["task_id"] for old in history}) >= minimum_workflows
        ):
            selected.append((row, median(old[target] for old in history)))
    return selected


def project_leave_one_out(
    by_threshold: dict[int, list[dict]], frozen_ids: list[str],
    *, thresholds: tuple[int, ...] = THRESHOLDS,
) -> dict:
    projects = sorted({task.split("__", 1)[0] for task in frozen_ids})
    if len(projects) < 3:
        raise ValueError("JOIN audit needs three training projects")
    result = {
        "status": "train_only_join_project_history_not_action_eligible",
        "frozen_workflow_count": len(frozen_ids),
        "projects": projects,
        "thresholds": {},
        "scope": (
            "All task IDs are training projects. Each project is held out "
            "when fitting the stage prior; the online arm only uses that "
            "project's completed JOIN or parent reentry strictly before "
            "each stage. Natural labels score results after the fact. "
            "This is causal adaptation, not zero-shot project generalization "
            "or physical H2D eligibility."
        ),
    }
    for threshold in thresholds:
        rows = by_threshold[threshold]
        if {row["task_id"] for row in rows} - set(frozen_ids):
            raise ValueError("JOIN candidate not in frozen training manifest")
        targets = {}
        for target in ("join_lead_ms", "parent_lead_ms"):
            natural = [
                row for row in rows
                if row["group_label"] == "natural" and row["label"] == "true"
                and row[target] is not None
            ]
            by_project = {}
            for project in projects:
                other_by_task = defaultdict(list)
                for row in natural:
                    if row["project"] != project:
                        other_by_task[row["task_id"]].append(row[target])
                frozen_prior = (
                    median(median(values) for values in other_by_task.values())
                    if other_by_task else None
                )
                own = [row for row in natural if row["project"] == project]
                selected = causal_project_predictions(own, target)
                selected_rows = [item for item, _ in selected]
                actual = [row[target] for row in selected_rows]
                predicted = [value for _, value in selected]
                baseline = (
                    [frozen_prior] * len(actual)
                    if frozen_prior is not None else []
                )
                by_project[project] = {
                    "frozen_tasks": sum(
                        task.split("__", 1)[0] == project for task in frozen_ids
                    ),
                    "natural_candidates": len(own),
                    "online_supported": len(selected),
                    "online_supported_workflows": len({
                        row["task_id"] for row in selected_rows
                    }),
                    "actual_lead_at_least_500ms": sum(
                        value >= 500 for value in actual
                    ),
                    "frozen_prior_ms": frozen_prior,
                    "frozen_p50_error_ms": (
                        _quantile([abs(a-b) for a, b in zip(actual, baseline)], .5)
                        if frozen_prior is not None else None
                    ),
                    "online_p50_error_ms": _quantile(
                        [abs(a-p) for a, p in zip(actual, predicted)], .5
                    ),
                    "online_p90_error_ms": _quantile(
                        [abs(a-p) for a, p in zip(actual, predicted)], .9
                    ),
                    "online_within_500ms": sum(
                        abs(a-p) <= 500 for a, p in zip(actual, predicted)
                    ),
                    "paired_gain": (
                        _paired_long_gain(
                            [
                                {"duration_ms": a, "workflow": row["task_id"]}
                                for row, a in zip(selected_rows, actual)
                            ],
                            baseline, predicted,
                        )
                        if frozen_prior is not None else None
                    ),
                }
            targets[target] = by_project
        result["thresholds"][str(threshold)] = targets
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-late-stages", action="store_true",
        help="Train-only coverage audit of observed 2400-7000 char stages.",
    )
    args = parser.parse_args()
    frozen_ids, runner_errors = require_complete_batch(args.workflows)
    thresholds = (
        OBSERVED_THRESHOLDS if args.include_late_stages else THRESHOLDS
    )
    report = project_leave_one_out({
        threshold: collect(args.workflows, threshold)[0]
        for threshold in thresholds
    }, frozen_ids, thresholds=thresholds)
    report["runner_error_workflows"] = runner_errors
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "frozen_workflow_count": len(frozen_ids),
        "projects": report["projects"],
    }, indent=2))


if __name__ == "__main__":
    main()
