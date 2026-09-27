#!/usr/bin/env python3
"""Read-only causal adaptation of the early whole-JOIN notice clock."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median

import numpy as np

try:
    from scripts.evaluate_child_return_intent_timing import _metrics, load_episodes
    from scripts.evaluate_cold_tool_project_loo import require_complete_batch
    from scripts.evaluate_join_group_notice import collect
except ModuleNotFoundError:
    from evaluate_child_return_intent_timing import _metrics, load_episodes
    from evaluate_cold_tool_project_loo import require_complete_batch
    from evaluate_join_group_notice import collect


WINDOWS = ((4, 8), (4, 16), (8, 16), (8, 32))


def _balanced_prior(episodes: list[dict]) -> float:
    by_task = defaultdict(list)
    for episode in episodes:
        by_task[episode["task_id"]].append(episode["lead_ms"])
    if not by_task:
        raise ValueError("no training child notices")
    return median(median(leads) for leads in by_task.values())


def replay(
    groups: list[dict], *, prior_ms: float, min_history: int,
    history_limit: int,
) -> list[dict]:
    if not 0 < min_history <= history_limit:
        raise ValueError("invalid history window")
    completed: list[dict] = []
    predictions = []
    for row in sorted(groups, key=lambda item: item["trigger_ts_ms"]):
        now = row["trigger_ts_ms"]
        eligible = sorted(
            (
                item for item in completed
                if item["completion_ts_ms"] < now
                and item["project"] == row["project"]
                and item["task_id"] != row["task_id"]
            ),
            key=lambda item: item["completion_ts_ms"],
        )[-history_limit:]
        adapted = (
            median(item["lead_ms"] for item in eligible)
            if len(eligible) >= min_history else prior_ms
        )
        if row["label"] == "natural":
            predictions.append({
                "task_id": row["task_id"],
                "project": row["project"],
                "trigger_ts_ms": now,
                "actual_ms": row["lead_ms"],
                "static_ms": prior_ms,
                "adapted_ms": adapted,
                "causal_history": len(eligible),
            })
            completed.append({
                "task_id": row["task_id"],
                "project": row["project"],
                "lead_ms": row["lead_ms"],
                "completion_ts_ms": now + row["lead_ms"],
            })
    return predictions


def _metrics_by_task(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    actual = [row["actual_ms"] for row in rows]
    static = [row["static_ms"] for row in rows]
    adapted = [row["adapted_ms"] for row in rows]
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(
            abs(row["actual_ms"] - row["static_ms"])
            - abs(row["actual_ms"] - row["adapted_ms"])
        )
    means = np.asarray([
        np.mean(by_task[task]) for task in sorted(by_task)
    ])
    draws = np.random.default_rng(0).choice(
        means, size=(4000, len(means)), replace=True,
    ).mean(axis=1)
    return {
        "natural_candidates": len(rows),
        "workflows": len(by_task),
        "with_live_history": sum(row["causal_history"] >= 1 for row in rows),
        "using_adapted_prior": sum(
            row["adapted_ms"] != row["static_ms"] for row in rows
        ),
        "static": _metrics(actual, static),
        "online": _metrics(actual, adapted),
        "paired_mean_mae_improvement_ms": float(np.mean(means)),
        "task_bootstrap_95pct_ci_ms": [
            float(x) for x in np.percentile(draws, (2.5, 97.5))
        ],
    }


def evaluate(train_workflows: Path, heldout_workflows: Path) -> dict:
    train_ids, train_errors = require_complete_batch(train_workflows)
    heldout_ids, heldout_errors = require_complete_batch(heldout_workflows)
    train_groups, train_counts = collect(train_workflows, notice_source="shadow")
    heldout_groups, heldout_counts = collect(
        heldout_workflows, notice_source="shadow",
    )
    episodes, _ = load_episodes(train_workflows.parent)
    train_projects = {
        task.split("__", 1)[0] for task in train_ids
    }
    heldout_projects = {
        task.split("__", 1)[0] for task in heldout_ids
    }
    if not train_projects.isdisjoint(heldout_projects) or (
        set(train_ids) & set(heldout_ids)
    ):
        raise ValueError("train and held-out workflows must be project-disjoint")
    eligible_folds = sorted(
        project for project in train_projects
        if sum(
            row["label"] == "natural" and row["project"] == project
            for row in train_groups
        ) >= 5
    )
    if len(eligible_folds) < 2:
        raise ValueError("insufficient project-held-out training folds")
    folds = {}
    for project in eligible_folds:
        prior = _balanced_prior([
            item for item in episodes if item["project"] != project
        ])
        rows = [row for row in train_groups if row["project"] == project]
        folds[project] = {
            f"{minimum}:{limit}": _metrics_by_task(replay(
                rows, prior_ms=prior, min_history=minimum,
                history_limit=limit,
            ))
            for minimum, limit in WINDOWS
        }
    scores = {
        f"{minimum}:{limit}": float(np.mean([
            folds[project][f"{minimum}:{limit}"]["online"]["mae_ms"]
            for project in eligible_folds
        ]))
        for minimum, limit in WINDOWS
    }
    selected = min(WINDOWS, key=lambda cfg: (
        scores[f"{cfg[0]}:{cfg[1]}"], cfg,
    ))
    prior = _balanced_prior(episodes)
    heldout = replay(
        heldout_groups, prior_ms=prior,
        min_history=selected[0], history_limit=selected[1],
    )
    by_project = {
        project: _metrics_by_task([
            row for row in heldout if row["project"] == project
        ])
        for project in sorted(heldout_projects)
    }
    return {
        "status": "read_only_online_prior_development_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_frozen_workflows": len(train_ids),
        "heldout_frozen_workflows": len(heldout_ids),
        "train_runner_errors": train_errors,
        "heldout_runner_errors": heldout_errors,
        "train_group_counts": train_counts,
        "heldout_group_counts": heldout_counts,
        "training_folds": folds,
        "training_macro_mae_by_window_ms": scores,
        "selected_min_history": selected[0],
        "selected_history_limit": selected[1],
        "train_only_static_prior_ms": prior,
        "heldout": _metrics_by_task(heldout),
        "heldout_by_project": by_project,
        "scope": (
            "Only previously satisfied, natural complete JOIN groups from a "
            "different workflow in the same project update the online clock. "
            "The policy is chosen on training projects by leave-one-project-out "
            "macro MAE; the static fallback comes from training alone. "
            "Held-out outcomes never enter the offline prior. Causal test-time "
            "adaptation uses labels only after a JOIN has actually completed. "
            "Candidates with no natural outcome stay in the replay timeline "
            "but never update the clock. Not sealed validation or physical H2D."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate(args.train_workflows, args.heldout_workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
