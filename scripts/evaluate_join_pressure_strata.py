#!/usr/bin/env python3
"""Read-only, project-held-out JOIN clock with causally sampled queue pressure."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_join_group_notice import collect


MAX_METRIC_AGE_MS = 2000.
MIN_WORKFLOWS = 5
MIN_PROJECTS = 2


def pressure_bin(running: float, queued: float) -> str:
    if queued < 1:
        return "idle" if running < 9 else "busy_no_queue"
    return "light_queue" if queued < 9 else "heavy_queue"


def attach_asof(
    groups: list[dict], metrics: list[dict], *, batch: str,
) -> tuple[list[dict], dict]:
    times = [item.get("monotonic_ts_ms") for item in metrics]
    if any(
        type(t) not in (int, float) or not math.isfinite(t)
        for t in times
    ) or times != sorted(times):
        raise ValueError("metric timestamps must be finite and ordered")
    rows = []
    skipped = Counter()
    for group in groups:
        if group["label"] != "natural":
            skipped[f"excluded_{group['label']}"] += 1
            continue
        when = group["trigger_ts_ms"]
        index = bisect_left(times, when) - 1
        if index < 0 or when - times[index] > MAX_METRIC_AGE_MS:
            skipped["missing_recent_prior_metric"] += 1
            continue
        sample = metrics[index]
        running, queued = (
            sample.get("num_running_reqs"),
            sample.get("num_queue_reqs"),
        )
        if any(
            type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 for value in (running, queued)
        ):
            skipped["invalid_prior_metric"] += 1
            continue
        rows.append({
            "task_id": group["task_id"],
            "project": group["project"],
            "join_id": group["join_id"],
            "batch": batch,
            "lead_ms": group["lead_ms"],
            "pressure_bin": pressure_bin(running, queued),
            "running": running,
            "queued": queued,
            "metric_age_ms": when - times[index],
        })
    return rows, dict(skipped)


def load(workflows: Path) -> tuple[list[dict], dict]:
    ids, errors = require_complete_batch(workflows)
    groups, counts = collect(workflows, notice_source="shadow")
    if {row["task_id"] for row in groups} - set(ids):
        raise ValueError("JOIN trace outside frozen batch manifest")
    metrics_path = workflows.parent / "sglang_metrics.jsonl"
    with metrics_path.open(encoding="utf-8") as stream:
        metrics = [json.loads(line) for line in stream if line.strip()]
    rows, skipped = attach_asof(groups, metrics, batch=str(workflows.parent))
    return rows, {
        "frozen_workflows": len(ids),
        "frozen_projects": sorted({
            task.split("__", 1)[0] for task in ids
        }),
        "runner_errors": errors,
        "group_counts": counts,
        "asof_rows": len(rows),
        "excluded": skipped,
    }


def task_balanced_prior(rows: list[dict]) -> float:
    if not rows:
        raise ValueError("no completed natural JOIN labels")
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(row["lead_ms"])
    return median(median(leads) for leads in by_task.values())


def _supported(rows: list[dict]) -> bool:
    return (
        len({row["task_id"] for row in rows}) >= MIN_WORKFLOWS
        and len({row["project"] for row in rows}) >= MIN_PROJECTS
    )


def forecast(train: list[dict], test: list[dict]) -> list[dict]:
    projects = {row["project"] for row in train}
    if not projects or projects & {row["project"] for row in test}:
        raise ValueError("forecast must exclude held-out projects")
    global_prior = task_balanced_prior(train)
    by_bin = defaultdict(list)
    by_family = defaultdict(list)
    for row in train:
        by_bin[row["pressure_bin"]].append(row)
        family = (
            "no_queue"
            if row["pressure_bin"] in ("idle", "busy_no_queue") else "queued"
        )
        by_family[family].append(row)
    out = []
    for row in test:
        bucket = by_bin[row["pressure_bin"]]
        family = (
            "no_queue"
            if row["pressure_bin"] in ("idle", "busy_no_queue") else "queued"
        )
        if _supported(bucket):
            source = row["pressure_bin"]
        elif _supported(by_family[family]):
            source = family
            bucket = by_family[family]
        else:
            source = "global"
            bucket = train
        out.append({
            **row,
            "global_eta_ms": global_prior,
            "pressure_eta_ms": task_balanced_prior(bucket),
            "prior_source": source,
            "prior_training_workflows": len({
                item["task_id"] for item in bucket
            }),
        })
    return out


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"events": 0}
    by_task = defaultdict(list)
    for row in rows:
        base = abs(row["lead_ms"] - row["global_eta_ms"])
        candidate = abs(row["lead_ms"] - row["pressure_eta_ms"])
        by_task[row["task_id"]].append(base - candidate)
    means = np.asarray([
        np.mean(values) for _, values in sorted(by_task.items())
    ])
    rng = np.random.default_rng(42)
    draws = rng.choice(means, size=(4000, len(means)), replace=True).mean(axis=1)
    return {
        "events": len(rows),
        "independent_workflows": len(means),
        "prior_sources": dict(Counter(row["prior_source"] for row in rows)),
        "real_lead_p50_ms": _quantile([row["lead_ms"] for row in rows], .5),
        "global_p50_absolute_error_ms": _quantile([
            abs(row["lead_ms"] - row["global_eta_ms"]) for row in rows
        ], .5),
        "pressure_p50_absolute_error_ms": _quantile([
            abs(row["lead_ms"] - row["pressure_eta_ms"]) for row in rows
        ], .5),
        "global_within_500ms": sum(
            abs(row["lead_ms"] - row["global_eta_ms"]) <= 500 for row in rows
        ),
        "pressure_within_500ms": sum(
            abs(row["lead_ms"] - row["pressure_eta_ms"]) <= 500 for row in rows
        ),
        "workflow_mean_mae_gain_ms": float(np.mean(means)),
        "workflow_bootstrap_95pct_ci_ms": [
            float(x) for x in np.percentile(draws, [2.5, 97.5])
        ],
    }


def evaluate(train_workflows: list[Path], heldout_workflows: Path | None) -> dict:
    if not train_workflows or len({
        path.resolve() for path in train_workflows
    }) != len(train_workflows):
        raise ValueError("training batches must be nonempty and distinct")
    train, train_sources = [], {}
    for workflows in train_workflows:
        rows, source = load(workflows)
        train.extend(rows)
        train_sources[str(workflows)] = source
    projects = sorted({row["project"] for row in train})
    if len(projects) < 3:
        raise ValueError("need three training projects for project LOO")
    fold_rows = []
    folds = {}
    for project in projects:
        test = [row for row in train if row["project"] == project]
        predictions = forecast(
            [row for row in train if row["project"] != project], test,
        )
        fold_rows.extend(predictions)
        folds[project] = {
            "all": summarize(predictions),
            "by_pressure": {
                pressure: summarize([
                    row for row in predictions
                    if row["pressure_bin"] == pressure
                ])
                for pressure in (
                    "idle", "busy_no_queue", "light_queue", "heavy_queue",
                )
            },
        }
    report = {
        "status": "train_only_pressure_join_clock_not_action_eligible",
        "training_projects": projects,
        "train_sources": train_sources,
        "train_loo": summarize(fold_rows),
        "train_loo_by_pressure": {
            pressure: summarize([
                row for row in fold_rows if row["pressure_bin"] == pressure
            ])
            for pressure in ("idle", "busy_no_queue", "light_queue", "heavy_queue")
        },
        "train_loo_folds": folds,
        "scope": (
            "The earliest all-member early notice is scored only for a natural "
            "complete JOIN. Metrics must precede the trigger by at most 2 s. "
            "Four pressure strata are fixed before scoring, and unsupported "
            "strata back off to their queue family or the task-balanced global "
            "prior. Training folds exclude every batch of the held-out project. "
            "Repeated task IDs across training batches share a bootstrap "
            "workflow cluster. Absolute JOIN time, parent first service, and "
            "predictive transfer remain unverified."
        ),
    }
    if heldout_workflows is not None:
        heldout, source = load(heldout_workflows)
        if {
            project
            for item in train_sources.values()
            for project in item["frozen_projects"]
        } & set(source["frozen_projects"]):
            raise ValueError("training and held-out manifests overlap in projects")
        forecast_rows = forecast(train, heldout)
        report["status"] = "project_disjoint_development_not_action_eligible"
        report["heldout_projects"] = sorted({
            row["project"] for row in heldout
        })
        report["heldout_source"] = source
        report["heldout"] = summarize(forecast_rows)
        report["heldout_by_pressure"] = {
            pressure: summarize([
                row for row in forecast_rows if row["pressure_bin"] == pressure
            ])
            for pressure in ("idle", "busy_no_queue", "light_queue", "heavy_queue")
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-workflows", type=Path, action="append", required=True,
    )
    parser.add_argument("--heldout-workflows", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = evaluate(args.train_workflows, args.heldout_workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
