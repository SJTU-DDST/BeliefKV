#!/usr/bin/env python3
"""Evaluate first child completion notices on a disjoint project."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median

try:
    from scripts.evaluate_child_return_intent_timing import _metrics, load_episodes
except ModuleNotFoundError:
    from evaluate_child_return_intent_timing import _metrics, load_episodes


def evaluate(train_root: Path, heldout_root: Path) -> dict:
    train, train_counts = load_episodes(train_root)
    heldout, heldout_counts = load_episodes(heldout_root)
    projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if not projects or not heldout_projects or projects & heldout_projects:
        raise ValueError("both splits need valid notices and disjoint projects")
    by_task: dict[str, list[float]] = defaultdict(list)
    for row in train:
        by_task[row["task_id"]].append(row["lead_ms"])
    if {row["task_id"] for row in heldout} & by_task.keys():
        raise ValueError("training and held-out task IDs overlap")
    prior_ms = median([median(values) for values in by_task.values()])
    actual = [row["lead_ms"] for row in heldout]
    join_actual = [row["lead_ms"] for row in heldout if row["join_last"]]
    return {
        "diagnostic_only": True,
        "train_projects": sorted(projects),
        "heldout_projects": sorted(heldout_projects),
        "train_unique_tasks": len(by_task),
        "train_counts": train_counts,
        "heldout_counts": heldout_counts,
        "frozen_task_balanced_notice_prior_ms": prior_ms,
        "heldout_notice_to_return_ms": _metrics(actual, [0.] * len(actual)),
        "heldout_notice_point_error_ms": _metrics(
            actual, [prior_ms] * len(actual),
        ),
        "heldout_join_last_notice_to_return_ms": _metrics(
            join_actual, [0.] * len(join_actual),
        ),
        "heldout_join_last_point_error_ms": _metrics(
            join_actual, [prior_ms] * len(join_actual),
        ),
        "scope": (
            "First notice only; revoked, blocked and censored children are "
            "excluded from point errors and counted separately. No physical "
            "H2D, sealed test, or task performance comparison."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-root", type=Path, required=True)
    parser.add_argument("--heldout-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(evaluate(args.train_root, args.heldout_root), indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
