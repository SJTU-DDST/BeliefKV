#!/usr/bin/env python3
"""Read-only project-LOO JOIN clock conditioned on causal queue length."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median

import numpy as np
from scipy.stats import theilslopes

try:
    from scripts.audit_repeated_tool_timing import _quantile
    from scripts.evaluate_join_pressure_strata import forecast, load
    from scripts.evaluate_join_selective_pressure import (
        apply_bins, project_oof, select_bins,
    )
except ModuleNotFoundError:
    from audit_repeated_tool_timing import _quantile
    from evaluate_join_pressure_strata import forecast, load
    from evaluate_join_selective_pressure import (
        apply_bins, project_oof, select_bins,
    )


def fit_queue_clock(rows: list[dict]) -> tuple[float, float] | None:
    """Fit a monotone robust clock to distinct tasks, never future notices."""

    by_task = defaultdict(list)
    for row in rows:
        if row["pressure_bin"] == "heavy_queue":
            by_task[row["task_id"]].append(row)
    if len(by_task) < 10 or len({
        values[0]["project"] for values in by_task.values()
    }) < 2:
        return None
    x = np.asarray([
        median(item["queued"] for item in values)
        for _, values in sorted(by_task.items())
    ], dtype=np.float64)
    y = np.asarray([
        median(item["lead_ms"] for item in values)
        for _, values in sorted(by_task.items())
    ], dtype=np.float64)
    if float(x.max() - x.min()) < 10:
        return None
    slope, intercept, _, _ = theilslopes(y, x)
    if not np.isfinite(slope) or not np.isfinite(intercept):
        return None
    slope = max(0., float(slope))
    return max(0., float(median(y - slope * x))), slope


def predict(
    train: list[dict], test: list[dict], *, min_eta_ms: float = 500.,
) -> tuple[list[dict], dict]:
    if min_eta_ms < 0:
        raise ValueError("negative JOIN ETA floor")
    if {row["project"] for row in train} & {
        row["project"] for row in test
    }:
        raise ValueError("queue clock must exclude held-out projects")
    gates, evidence = select_bins(project_oof(train))
    selected = apply_bins(forecast(train, test), gates)
    model = fit_queue_clock(train)
    predictions = []
    for row in selected:
        eta = row["selective_eta_ms"]
        if row["pressure_bin"] == "heavy_queue" and model is not None:
            intercept, slope = model
            eta = min(
                600_000., max(min_eta_ms, intercept + slope * row["queued"]),
            )
        predictions.append({
            **row, "queue_clock_eta_ms": eta,
            "queue_clock_applied": (
                row["pressure_bin"] == "heavy_queue" and model is not None
            ),
        })
    return predictions, {
        "min_eta_ms": min_eta_ms,
        "selected_pressure_gates": sorted(gates),
        "gate_evidence": evidence,
        "queue_intercept_ms": model[0] if model else None,
        "queue_slope_ms_per_request": model[1] if model else None,
        "queue_training_distinct_tasks": len({
            row["task_id"] for row in train
            if row["pressure_bin"] == "heavy_queue"
        }),
    }


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"events": 0}
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(
            abs(row["lead_ms"] - row["selective_eta_ms"])
            - abs(row["lead_ms"] - row["queue_clock_eta_ms"])
        )
    gains = np.asarray([
        float(np.mean(values)) for _, values in sorted(by_task.items())
    ])
    draws = np.random.default_rng(42).choice(
        gains, size=(4000, len(gains)), replace=True,
    ).mean(axis=1)
    return {
        "events": len(rows),
        "distinct_tasks": len(gains),
        "queue_clock_applied": sum(row["queue_clock_applied"] for row in rows),
        "selective_p50_absolute_error_ms": _quantile([
            abs(row["lead_ms"] - row["selective_eta_ms"]) for row in rows
        ], .5),
        "queue_clock_p50_absolute_error_ms": _quantile([
            abs(row["lead_ms"] - row["queue_clock_eta_ms"]) for row in rows
        ], .5),
        "selective_within_500ms": sum(
            abs(row["lead_ms"] - row["selective_eta_ms"]) <= 500
            for row in rows
        ),
        "queue_clock_within_500ms": sum(
            abs(row["lead_ms"] - row["queue_clock_eta_ms"]) <= 500
            for row in rows
        ),
        "task_mean_mae_gain_ms": float(gains.mean()),
        "task_bootstrap_95pct_ci_ms": [
            float(item) for item in np.percentile(draws, (2.5, 97.5))
        ],
    }


def evaluate(
    train_workflows: list[Path], heldout_workflows: Path | None,
    *, notice_source: str = "shadow",
) -> dict:
    if not train_workflows or len({
        path.resolve() for path in train_workflows
    }) != len(train_workflows):
        raise ValueError("training batches must be nonempty and distinct")
    train, sources, projects = [], {}, set()
    for workflows in train_workflows:
        rows, metadata = load(workflows, notice_source=notice_source)
        train.extend(rows)
        sources[str(workflows)] = metadata
        projects.update(metadata["frozen_projects"])
    project_rows = sorted({row["project"] for row in train})
    if len(project_rows) < 4:
        raise ValueError("nested project holdout needs four training projects")
    folds = []
    models = {}
    min_eta_ms = 0. if notice_source == "llm_result" else 500.
    for project in project_rows:
        predictions, model = predict(
            [row for row in train if row["project"] != project],
            [row for row in train if row["project"] == project],
            min_eta_ms=min_eta_ms,
        )
        folds.extend(predictions)
        models[project] = model
    result = {
        "notice_source": notice_source,
        "status": "read_only_nested_project_holdout_not_action_eligible",
        "training_projects": sorted(projects),
        "training_sources": sources,
        "train_loo": summarize(folds),
        "train_loo_heavy": summarize([
            row for row in folds if row["pressure_bin"] == "heavy_queue"
        ]),
        "train_loo_by_project": {
            project: summarize([
                row for row in folds if row["project"] == project
                and row["pressure_bin"] == "heavy_queue"
            ])
            for project in project_rows
        },
        "fold_models": models,
        "scope": (
            "A monotone robust heavy-queue ETA is fitted only on other "
            "projects' naturally completed JOIN notices, with repeated "
            "task IDs reduced to one fitting point. Other bins retain "
            "nested project-held-out pressure-gate selection. Metrics are "
            "sampled strictly before notices. This is not an independent "
            "sealed test or evidence of predictive H2D and first service."
        ),
    }
    if heldout_workflows is not None:
        heldout, metadata = load(
            heldout_workflows, notice_source=notice_source,
        )
        if projects & set(metadata["frozen_projects"]):
            raise ValueError("training and held-out manifests overlap in projects")
        scored, model = predict(
            train, heldout, min_eta_ms=min_eta_ms,
        )
        result["status"] = "project_disjoint_development_not_action_eligible"
        result["heldout_source"] = metadata
        result["heldout_model"] = model
        result["heldout"] = summarize(scored)
        result["heldout_heavy"] = summarize([
            row for row in scored if row["pressure_bin"] == "heavy_queue"
        ])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-workflows", type=Path, action="append", required=True,
    )
    parser.add_argument("--heldout-workflows", type=Path)
    parser.add_argument(
        "--notice-source", choices=("shadow", "llm_result"), default="shadow",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = evaluate(
        args.train_workflows, args.heldout_workflows,
        notice_source=args.notice_source,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
