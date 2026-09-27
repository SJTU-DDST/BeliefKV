#!/usr/bin/env python3
"""Training-only project-LOO tool ETA after observable survival landmarks."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median
import sys

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_structure_holdout import _paired_long_gain
from scripts.evaluate_tool_balanced_clocks import (
    fit as fit_frozen_calls,
    load_training_batches,
    predict as predict_frozen_calls,
)
from scripts.pilot_cold_child_tool_long import _shape_matrix
from scripts.pilot_tool_return_window_100ms import TARGET_MS


LANDMARKS_MS = (100, 250, 500, 1000, 1500, 2000)
MIN_SHAPE_CALLS = 8
MIN_SHAPE_WORKFLOWS = 3
LEAD_BUDGET_MS = 500


def survivor_prior(train: list[dict]) -> dict:
    if not train:
        raise ValueError("no surviving training calls")
    grouped = defaultdict(list)
    for row in train:
        grouped[row["shape"]].append(row)
    return {
        "global": float(median(row["duration_ms"] for row in train)),
        "shape": {
            shape: float(median(row["duration_ms"] for row in rows))
            for shape, rows in grouped.items()
            if len(rows) >= MIN_SHAPE_CALLS and len({
                row["task_id"] for row in rows
            }) >= MIN_SHAPE_WORKFLOWS
        },
    }


def fit(train: list[dict], landmark: int) -> tuple:
    if len(train) < 20 or any(row["duration_ms"] <= landmark for row in train):
        raise ValueError("model needs at least 20 surviving training calls")
    vocabulary = {
        shape: index for index, shape in enumerate(sorted({
            row["shape"] for row in train
        }))
    }
    model = lgb.train({
        "objective": "regression_l1", "learning_rate": .04,
        "num_leaves": 7, "min_data_in_leaf": 12, "lambda_l2": 8,
        "max_bin": 127, "seed": 42, "num_threads": 2, "verbosity": -1,
    }, lgb.Dataset(
        _shape_matrix(
            train, vocabulary, include_live_peers=True,
            include_duration_priors=True,
        ),
        label=np.asarray([
            row["duration_ms"] - landmark for row in train
        ], dtype=np.float64),
        categorical_feature=[0],
    ), num_boost_round=100)
    return model, vocabulary


def predict(model: tuple, rows: list[dict]) -> np.ndarray:
    if not rows:
        return np.empty(0, dtype=np.float64)
    booster, vocabulary = model
    remaining = booster.predict(_shape_matrix(
        rows, vocabulary, include_live_peers=True,
        include_duration_priors=True,
    ), num_threads=2)
    return np.maximum(0., remaining)


def score(
    rows: list[dict], landmark: int, prior: dict, predicted: np.ndarray,
    frozen_total: np.ndarray | None = None,
) -> dict:
    if len(rows) != len(predicted) or (
        frozen_total is not None and len(rows) != len(frozen_total)
    ):
        raise ValueError("survivors and predictions differ")
    actual = np.asarray([
        row["duration_ms"] - landmark for row in rows
    ], dtype=np.float64)
    if np.any(actual <= 0):
        raise ValueError("score needs calls alive at the landmark")
    if not np.all(np.isfinite(predicted)) or np.any(predicted < 0):
        raise ValueError("invalid remaining-time prediction")
    global_eta = np.full(len(rows), max(0., prior["global"] - landmark))
    shape_eta = np.asarray([
        max(0., prior["shape"].get(row["shape"], prior["global"]) - landmark)
        for row in rows
    ])
    # Repeated task IDs across pressure runs are one statistical cluster.
    grouped = [{**row, "workflow": row["task_id"], "duration_ms": float(value)}
               for row, value in zip(rows, actual)]

    def errors(estimate: np.ndarray) -> dict:
        residual = np.abs(actual - estimate)
        return {
            "p50_ms": _quantile(residual.tolist(), .5),
            "p90_ms": _quantile(residual.tolist(), .9),
            "within_500ms": int(np.sum(residual <= 500)),
        }

    chosen = predicted >= LEAD_BUDGET_MS
    report = {
        "survivors": len(rows),
        "distinct_tasks": len({row["task_id"] for row in rows}),
        "real_500ms_windows": int(np.sum(actual >= LEAD_BUDGET_MS)),
        "shape_supported": sum(row["shape"] in prior["shape"] for row in rows),
        "global": errors(global_eta),
        "shape": errors(shape_eta),
        "remaining_model": errors(predicted),
        "paired_gain_vs_shape": _paired_long_gain(
            grouped, shape_eta, predicted, draws=1000,
        ),
        "landmark_decision": {
            "selected": int(np.sum(chosen)),
            "true_500ms_windows": int(np.sum(chosen & (
                actual >= LEAD_BUDGET_MS
            ))),
            "false_500ms_windows": int(np.sum(chosen & (
                actual < LEAD_BUDGET_MS
            ))),
            "missed_500ms_windows": int(np.sum(~chosen & (
                actual >= LEAD_BUDGET_MS
            ))),
            "remaining_over_2000ms": int(np.sum(chosen & (actual > 2000))),
        },
    }
    if frozen_total is not None:
        frozen_remaining = np.maximum(0., frozen_total - landmark)
        if not np.all(np.isfinite(frozen_remaining)):
            raise ValueError("nonfinite frozen prediction")
        report["frozen_calls"] = errors(frozen_remaining)
        report["paired_gain_vs_frozen_calls"] = _paired_long_gain(
            grouped, frozen_remaining, predicted, draws=1000,
        )
        # Oracle conditioning is for comparability with the existing long
        # calls head; no deployment decision may read this subset.
        long = actual + landmark >= TARGET_MS
        selected = [item for item, keep in zip(grouped, long) if keep]
        report["oracle_long_calls"] = {
            "calls": len(selected),
            "remaining_model_p50_ms": _quantile(
                np.abs(actual[long] - predicted[long]).tolist(), .5,
            ),
            "frozen_calls_p50_ms": _quantile(
                np.abs(actual[long] - frozen_remaining[long]).tolist(), .5,
            ),
            "paired_gain_vs_frozen_calls": _paired_long_gain(
                selected, frozen_remaining[long], predicted[long], draws=1000,
            ),
        }
    return report


def evaluate(rows: list[dict]) -> dict:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("need at least three training projects for LOO")
    if not all(
        row["duration_ms"] > 100 and row["task_id"] and row["workflow"]
        for row in rows
    ):
        raise ValueError("invalid first-surviving-call identity or duration")
    if any(len({row["project"] for row in rows if row["task_id"] == task}) != 1
           for task in {row["task_id"] for row in rows}):
        raise ValueError("same task appears in different projects")

    result = {"status": "training_only_project_loo_not_action_eligible",
              "projects": projects, "landmarks": {}}
    frozen_models = {
        project: fit_frozen_calls(
            [row for row in rows if row["project"] != project],
            "calls", regression=True,
        )
        for project in projects
    }
    for landmark in LANDMARKS_MS:
        folds = {}
        pooled = []
        for project in projects:
            train = [row for row in rows if (
                row["project"] != project and row["duration_ms"] > landmark
            )]
            heldout = [row for row in rows if (
                row["project"] == project and row["duration_ms"] > landmark
            )]
            if len(train) < 20:
                raise ValueError(f"insufficient training survivors at {landmark}ms")
            prior = survivor_prior(train)
            prediction = predict(fit(train, landmark), heldout)
            frozen_total = predict_frozen_calls(
                frozen_models[project], heldout, regression=True,
            )
            folds[project] = score(
                heldout, landmark, prior, prediction, frozen_total,
            )
            pooled.extend(
                (row, float(estimate), prior, float(frozen))
                for row, estimate, frozen in zip(
                    heldout, prediction, frozen_total,
                )
            )
        result["landmarks"][str(landmark)] = {
            "by_project": folds,
            "pooled_survivors": sum(
                fold["survivors"] for fold in folds.values()
            ),
            "pooled_distinct_tasks": len({
                row["task_id"] for row, _, _, _ in pooled
            }),
            "pooled_remaining_model_p50_ms": _quantile([
                abs(row["duration_ms"] - landmark - estimate)
                for row, estimate, _, _ in pooled
            ], .5),
            "pooled_shape_p50_ms": _quantile([
                abs(row["duration_ms"] - max(
                    landmark, prior["shape"].get(
                        row["shape"], prior["global"]
                    )
                ))
                for row, _, prior, _ in pooled
            ], .5),
            "pooled_frozen_calls_p50_ms": _quantile([
                abs(row["duration_ms"] - max(landmark, frozen))
                for row, _, _, frozen in pooled
            ], .5),
            "pooled_oracle_long_calls": {
                "calls": sum(
                    row["duration_ms"] >= TARGET_MS
                    for row, _, _, _ in pooled
                ),
                "remaining_model_p50_ms": _quantile([
                    abs(row["duration_ms"] - landmark - estimate)
                    for row, estimate, _, _ in pooled
                    if row["duration_ms"] >= TARGET_MS
                ], .5),
                "frozen_calls_p50_ms": _quantile([
                    abs(row["duration_ms"] - max(landmark, frozen))
                    for row, _, _, frozen in pooled
                    if row["duration_ms"] >= TARGET_MS
                ], .5),
            },
            "pooled_500ms_window": {
                key: sum(fold["landmark_decision"][key] for fold in folds.values())
                for key in ("selected", "true_500ms_windows",
                            "false_500ms_windows", "missed_500ms_windows")
            },
        }
    result["scope"] = (
        "All folds use only other training projects. A row is eligible at a "
        "landmark only if its tool is still live then; success and returned "
        "non-exception errors are included. Open/censored/exception calls are "
        "excluded and must not be counted as negative predictions. The model "
        "uses TOOL_START features plus observed survival only, no future "
        "progress/queue data. ETA precision is conditional on survivors; "
        "500ms-window confusion counts and all-project coverage are reported. "
        "The existing calls head is fitted on natural calls >=600ms; its "
        "scores on *all* landmark survivors are shown separately from the "
        "posthoc oracle-long subset (which is not an online selection). "
        "No frozen external project, physical transfer or JOIN result."
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    rows, projects, sources = load_training_batches(args.train_workflows)
    result = evaluate(rows)
    result["training_sources"] = sources
    result["training_projects"] = sorted(projects)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
