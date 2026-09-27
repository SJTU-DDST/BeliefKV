#!/usr/bin/env python3
"""Read-only early JOIN clock using only pre-notice queue drainage."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median

import numpy as np

from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_join_pressure_strata import load
from scripts.evaluate_join_queue_clock import predict as queue_predict
from scripts.evaluate_join_queue_clock import summarize as queue_summarize


WINDOW_MS = 15_000.
MAX_WINDOW_MS = 30_000.
WEIGHTS = (0., .25, .5, .75, 1.)


def attach_drain(rows: list[dict], metrics: list[dict]) -> list[dict]:
    times = [item["monotonic_ts_ms"] for item in metrics]
    if times != sorted(times):
        raise ValueError("metrics must be time ordered")
    result = []
    for row in rows:
        sample_at = row["trigger_ts_ms"] - row["metric_age_ms"]
        index = bisect_left(times, sample_at - WINDOW_MS) - 1
        old = metrics[index] if index >= 0 else {}
        old_queue = old.get("num_queue_reqs")
        age = sample_at - old.get("monotonic_ts_ms", float("-inf"))
        valid = (
            type(old_queue) in (int, float)
            and math.isfinite(old_queue)
            and old_queue >= row["queued"] >= 0
            and WINDOW_MS <= age <= MAX_WINDOW_MS
            and sample_at < row["trigger_ts_ms"]
        )
        rate = (
            (old_queue - row["queued"]) * 1000. / age
            if valid else None
        )
        result.append({**row, "prior_queue_drain_qps": rate})
    return result


def load_batch(workflows: Path, *, notice_source: str) -> tuple[list[dict], dict]:
    rows, metadata = load(workflows, notice_source=notice_source)
    with (workflows.parent / "sglang_metrics.jsonl").open(encoding="utf-8") as stream:
        metrics = [json.loads(line) for line in stream if line.strip()]
    return attach_drain(rows, metrics), metadata


def fit_residual(train: list[dict]) -> float | None:
    by_task = defaultdict(list)
    for row in train:
        rate = row.get("prior_queue_drain_qps")
        if row["pressure_bin"] == "heavy_queue" and rate is not None and rate > 0:
            by_task[row["task_id"]].append(
                row["lead_ms"] - row["queued"] / rate * 1000.
            )
    if len(by_task) < 10 or len({
        row["project"] for row in train if row["task_id"] in by_task
    }) < 2:
        return None
    return float(median(median(values) for values in by_task.values()))


def forecast(
    train: list[dict], test: list[dict], weight: float,
) -> list[dict]:
    if not 0. <= weight <= 1.:
        raise ValueError("drain blend weight outside [0, 1]")
    baseline, _ = queue_predict(train, test)
    residual = fit_residual(train)
    result = []
    for row in baseline:
        rate = row.get("prior_queue_drain_qps")
        applied = (
            weight > 0 and residual is not None
            and row["pressure_bin"] == "heavy_queue"
            and rate is not None and rate > 0
        )
        raw = (
            min(600_000., max(500., row["queued"] / rate * 1000. + residual))
            if applied else row["queue_clock_eta_ms"]
        )
        result.append({
            **row, "drain_clock_applied": applied,
            "drain_clock_eta_ms": (
                (1. - weight) * row["queue_clock_eta_ms"] + weight * raw
            ),
        })
    return result


def task_mean_gain(rows: list[dict]) -> float:
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(
            abs(row["lead_ms"] - row["queue_clock_eta_ms"])
            - abs(row["lead_ms"] - row["drain_clock_eta_ms"])
        )
    return float(np.mean([np.mean(gains) for gains in by_task.values()]))


def choose_weight(train: list[dict]) -> tuple[float, dict]:
    projects = sorted({row["project"] for row in train})
    if len(projects) < 4:
        raise ValueError("weight selection needs four training projects")
    gains = {}
    for weight in WEIGHTS:
        folds = []
        for project in projects:
            folds.extend(forecast(
                [row for row in train if row["project"] != project],
                [row for row in train if row["project"] == project],
                weight,
            ))
        gains[str(weight)] = task_mean_gain(folds)
    selected = max(WEIGHTS, key=lambda weight: (gains[str(weight)], -weight))
    return selected, gains


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"events": 0}
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(
            abs(row["lead_ms"] - row["queue_clock_eta_ms"])
            - abs(row["lead_ms"] - row["drain_clock_eta_ms"])
        )
    gains = np.asarray([
        np.mean(values) for _, values in sorted(by_task.items())
    ])
    draws = np.random.default_rng(42).choice(
        gains, size=(4000, len(gains)), replace=True,
    ).mean(axis=1)
    return {
        "events": len(rows),
        "distinct_tasks": len(gains),
        "rate_available": sum(row.get("prior_queue_drain_qps") is not None for row in rows),
        "drain_clock_applied": sum(row["drain_clock_applied"] for row in rows),
        "queue_clock_p50_absolute_error_ms": _quantile([
            abs(row["lead_ms"] - row["queue_clock_eta_ms"]) for row in rows
        ], .5),
        "drain_clock_p50_absolute_error_ms": _quantile([
            abs(row["lead_ms"] - row["drain_clock_eta_ms"]) for row in rows
        ], .5),
        "drain_clock_within_500ms": sum(
            abs(row["lead_ms"] - row["drain_clock_eta_ms"]) <= 500
            for row in rows
        ),
        "task_mean_gain_ms": float(gains.mean()),
        "task_bootstrap_95pct_ci_ms": [
            float(value) for value in np.percentile(draws, (2.5, 97.5))
        ],
    }


def evaluate(train_batches: list[Path], heldout: Path | None) -> dict:
    if not train_batches or len({path.resolve() for path in train_batches}) != len(train_batches):
        raise ValueError("training batches must be distinct and nonempty")
    training, sources, frozen_projects = [], {}, set()
    for path in train_batches:
        rows, metadata = load_batch(path, notice_source="shadow")
        training.extend(rows)
        sources[str(path)] = metadata
        frozen_projects.update(metadata["frozen_projects"])
    selected, gains = choose_weight(training)
    folds = []
    for project in sorted({row["project"] for row in training}):
        inner, _ = choose_weight([
            row for row in training if row["project"] != project
        ])
        folds.extend(forecast(
            [row for row in training if row["project"] != project],
            [row for row in training if row["project"] == project],
            inner,
        ))
    result = {
        "status": "training_project_loo_read_only",
        "selected_weight": selected,
        "selection_gains_ms": gains,
        "train_loo": summarize(folds),
        "train_sources": sources,
    }
    if heldout is not None:
        test, meta = load_batch(heldout, notice_source="shadow")
        if frozen_projects & set(meta["frozen_projects"]):
            raise ValueError("held-out manifest shares training projects")
        predicted = forecast(training, test, selected)
        result.update({
            "status": "project_disjoint_development_not_action_eligible",
            "heldout_source": meta,
            "heldout": summarize(predicted),
            "heldout_heavy": summarize([
                row for row in predicted if row["pressure_bin"] == "heavy_queue"
            ]),
        })
    result["scope"] = (
        "Net queue drain over a pre-notice 15-30s interval is only a noisy "
        "causal proxy; arrivals can make it unavailable. Blend selected using "
        "nested training-project holdout; no held-out label selects a weight. "
        "Only natural complete JOIN labels are scored. No physical action."
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", action="append", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = evaluate(args.train_workflows, args.heldout_workflows)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
