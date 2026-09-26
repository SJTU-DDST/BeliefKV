#!/usr/bin/env python3
"""Require complete training and causal long-call support before project holdout."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import cold_calls
from scripts.pilot_cold_child_tool_long import train_shape_screen


LONG_MS = 2_000


def assess(workflows: Path, *, expected_roots: int = 128) -> dict:
    ids, runner_errors = require_complete_batch(workflows)
    calls, censor = cold_calls(workflows)
    long = [row for row in calls if row["duration_ms"] >= LONG_MS]
    projects = {row["project"] for row in calls}
    long_projects = {row["project"] for row in long}
    checks = {
        "expected_frozen_workflows": len(ids) == expected_roots,
        "three_training_projects": len(projects) >= 3,
        "long_calls_from_two_projects": len(long_projects) >= 2,
        "twenty_long_calls": len(long) >= 20,
        "twenty_short_calls": len(calls) - len(long) >= 20,
        "five_long_call_workflows": len({
            row["workflow"] for row in long
        }) >= 5,
        "every_project_cv_fit_supported": all(
            sum(
                row["duration_ms"] >= LONG_MS
                for row in calls if row["project"] != project
            ) >= 10 and sum(
                row["duration_ms"] < LONG_MS
                for row in calls if row["project"] != project
            ) >= 20 for project in projects
        ),
    }
    report = {
        "status": "train_only_holdout_readiness_not_predictive_action_eligible",
        "expected_roots": expected_roots,
        "frozen_workflows": len(ids),
        "runner_errors": runner_errors,
        "train_projects": sorted(projects),
        "successful_cold_child_execute": len(calls),
        "long_calls": len(long),
        "long_by_project": dict(sorted(Counter(
            row["project"] for row in long
        ).items())),
        "long_workflows": len({row["workflow"] for row in long}),
        "censor": censor,
        "checks": checks,
        "note": (
            "This gate reads only the final frozen training batch; it does "
            "not open the heldout manifest, fit an ETA, establish JOIN "
            "accuracy, or authorize a physical predictive action."
        ),
    }
    if not all(checks.values()):
        report["ready_for_project_holdout"] = False
        return report
    screen = train_shape_screen(
        calls, include_live_peers=True, include_long_history=True,
    )
    report["train_project_cv_screen"] = screen
    checks["train_cv_long_screen_qualified"] = (
        screen["threshold_chosen_on_train_cv"] is not None
    )
    report["ready_for_project_holdout"] = all(checks.values())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-roots", type=int, default=128)
    args = parser.parse_args()
    if args.expected_roots <= 0:
        parser.error("--expected-roots must be positive")
    report = assess(args.train_workflows, expected_roots=args.expected_roots)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ready_for_project_holdout": report["ready_for_project_holdout"],
        "checks": report["checks"],
    }, indent=2))
    if not report["ready_for_project_holdout"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
