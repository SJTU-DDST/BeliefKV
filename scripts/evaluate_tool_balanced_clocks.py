#!/usr/bin/env python3
"""Read-only project-LOO ablation of workflow-balanced tool-window heads."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median

import lightgbm as lgb
import numpy as np

try:
    from scripts.audit_repeated_tool_timing import _quantile
    from scripts.evaluate_cold_tool_project_loo import require_complete_batch
    from scripts.evaluate_cold_tool_structure_holdout import (
        _paired_long_gain, cold_calls,
    )
    from scripts.pilot_cold_child_tool_long import _shape_matrix
    from scripts.pilot_tool_return_window_100ms import (
        TARGET_MS, first_inputs,
    )
except ModuleNotFoundError:
    from audit_repeated_tool_timing import _quantile
    from evaluate_cold_tool_project_loo import require_complete_batch
    from evaluate_cold_tool_structure_holdout import (
        _paired_long_gain, cold_calls,
    )
    from pilot_cold_child_tool_long import _shape_matrix
    from pilot_tool_return_window_100ms import TARGET_MS, first_inputs


MODES = ("calls", "workflows", "projects_and_workflows")
THRESHOLDS = (.8, .85, .9, .95, .98)


def weights(rows: list[dict], mode: str) -> np.ndarray:
    if mode not in MODES or not rows:
        raise ValueError("unknown weighting mode or empty data")
    if mode == "calls":
        return np.ones(len(rows), dtype=np.float64)
    per_workflow = Counter(row["workflow"] for row in rows)
    workflow_project = {}
    for row in rows:
        workflow = row["workflow"]
        project = row["project"]
        if workflow in workflow_project and workflow_project[workflow] != project:
            raise ValueError("workflow occurs in more than one project")
        workflow_project[workflow] = project
    per_project = Counter(workflow_project.values())
    result = np.asarray([
        1 / per_workflow[row["workflow"]] / (
            per_project[row["project"]]
            if mode == "projects_and_workflows" else 1
        )
        for row in rows
    ], dtype=np.float64)
    return result * (len(rows) / result.sum())


def fit(train: list[dict], mode: str, *, regression: bool) -> tuple:
    if regression:
        train = [row for row in train if row["duration_ms"] >= TARGET_MS]
    if len(train) < 20:
        raise ValueError("insufficient tool returns for timing head")
    vocabulary = {
        shape: index for index, shape in enumerate(sorted({
            row["shape"] for row in train
        }))
    }
    features = _shape_matrix(
        train, vocabulary, include_live_peers=True,
        include_duration_priors=True,
    )
    labels = np.asarray([
        np.log1p(row["duration_ms"]) if regression
        else float(row["duration_ms"] >= TARGET_MS)
        for row in train
    ])
    model = lgb.train({
        "objective": "regression_l1" if regression else "binary",
        "learning_rate": .04, "num_leaves": 7, "min_data_in_leaf": 12,
        "lambda_l2": 8, "max_bin": 127, "seed": 42,
        "num_threads": 2, "verbosity": -1,
    }, lgb.Dataset(
        features, label=labels, weight=weights(train, mode),
        categorical_feature=[0],
    ), num_boost_round=100)
    return model, vocabulary


def predict(model: tuple, rows: list[dict], *, regression: bool) -> np.ndarray:
    if not rows:
        return np.empty(0, dtype=np.float64)
    booster, vocabulary = model
    values = booster.predict(_shape_matrix(
        rows, vocabulary, include_live_peers=True,
        include_duration_priors=True,
    ), num_threads=2)
    return (
        np.clip(np.expm1(values), TARGET_MS, 1_000_000)
        if regression else values
    )


def task_balanced_long_prior(rows: list[dict]) -> float:
    by_task = defaultdict(list)
    for row in rows:
        if row["duration_ms"] >= TARGET_MS:
            by_task[row["task_id"]].append(row["duration_ms"])
    if not by_task:
        raise ValueError("no long tool calls for prior")
    return float(median(median(values) for values in by_task.values()))


def project_folds(rows: list[dict]) -> list[dict]:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("need at least three training projects")
    scored = []
    for project in projects:
        fit_rows = [row for row in rows if row["project"] != project]
        test = [row for row in rows if row["project"] == project]
        long = [row for row in test if row["duration_ms"] >= TARGET_MS]
        global_eta = task_balanced_long_prior(fit_rows)
        predictions = {
            mode: (
                predict(fit(fit_rows, mode, regression=False), test,
                        regression=False),
                predict(fit(fit_rows, mode, regression=True), long,
                        regression=True),
            )
            for mode in MODES
        }
        long_indices = {
            index: offset for offset, index in enumerate(
                index for index, row in enumerate(test)
                if row["duration_ms"] >= TARGET_MS
            )
        }
        for index, row in enumerate(test):
            scored.append({
                "project": project,
                "workflow": row["workflow"],
                "task_id": row["task_id"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
                "global_long_eta_ms": global_eta if index in long_indices else None,
                "probability": {
                    mode: float(values[0][index])
                    for mode, values in predictions.items()
                },
                "eta_ms": (
                    {
                        mode: float(values[1][long_indices[index]])
                        for mode, values in predictions.items()
                    } if index in long_indices else None
                ),
            })
    return scored


def summarize(rows: list[dict]) -> dict:
    long = [row for row in rows if row["eta_ms"] is not None]
    if any(row.get("global_long_eta_ms") is not None for row in long) and any(
        row.get("global_long_eta_ms") is None for row in long
    ):
        raise ValueError("partial global long tool prior")
    by_mode = {}
    for mode in MODES:
        errors = [
            abs(row["duration_ms"] - row["eta_ms"][mode]) for row in long
        ]
        by_threshold = {}
        for threshold in THRESHOLDS:
            selected = [
                row for row in rows if row["probability"][mode] >= threshold
            ]
            by_project = {}
            for project in sorted({row["project"] for row in rows}):
                project_rows = [
                    row for row in rows if row["project"] == project
                ]
                picked = [
                    row for row in selected if row["project"] == project
                ]
                true = sum(
                    row["duration_ms"] >= TARGET_MS for row in picked
                )
                by_project[project] = {
                    "selected": len(picked),
                    "true_windows": true,
                    "actual_windows": sum(
                        row["duration_ms"] >= TARGET_MS for row in project_rows
                    ),
                    "precision": true / len(picked) if picked else None,
                    "selected_workflows": len({
                        row["workflow"] for row in picked
                    }),
                }
            selected_long = [
                row for row in selected if row["eta_ms"] is not None
            ]
            by_threshold[str(threshold)] = {
                "selected": len(selected),
                "true_windows": len(selected_long),
                "selected_workflows": len({
                    row["workflow"] for row in selected
                }),
                "selected_true_eta_calls_p50_absolute_error_ms": _quantile([
                    abs(row["duration_ms"] - row["eta_ms"]["calls"])
                    for row in selected_long
                ], .5),
                "by_project": by_project,
            }
        by_mode[mode] = {
            **{
                key: value for key, value in by_threshold["0.8"].items()
                if key != "selected_true_eta_calls_p50_absolute_error_ms"
            },
            "by_threshold": by_threshold,
            "eta_oracle_long_count": len(long),
            "eta_oracle_long_p50_absolute_error_ms": _quantile(errors, .5),
            "eta_oracle_long_within_500ms": sum(
                error <= 500 for error in errors
            ),
        }
        if mode != "calls":
            by_mode[mode]["eta_paired_gain_vs_calls"] = _paired_long_gain(
                [
                    {**row, "workflow": row.get("task_id", row["workflow"])}
                    for row in long
                ],
                np.asarray([
                    row["eta_ms"]["calls"] for row in long
                ]),
                np.asarray([
                    row["eta_ms"][mode] for row in long
                ]),
            )
    report = {
        "events": len(rows),
        "independent_workflows": len({
            row["workflow"] for row in rows
        }),
        "distinct_tasks": len({
            row.get("task_id", row["workflow"]) for row in rows
        }),
        "real_windows": len(long),
        "modes": by_mode,
    }
    if long and long[0].get("global_long_eta_ms") is not None:
        def compare(cohort: list[dict]) -> dict:
            return {
                "oracle_long_p50_absolute_error_ms": _quantile([
                    abs(row["duration_ms"] - row["global_long_eta_ms"])
                    for row in cohort
                ], .5),
                "calls_p50_absolute_error_ms": _quantile([
                    abs(row["duration_ms"] - row["eta_ms"]["calls"])
                    for row in cohort
                ], .5),
                "paired_calls_gain": _paired_long_gain(
                    [
                        {**row, "workflow": row["task_id"]}
                        for row in cohort
                    ],
                    np.asarray([row["global_long_eta_ms"] for row in cohort]),
                    np.asarray([row["eta_ms"]["calls"] for row in cohort]),
                ),
            }

        report["global_long_prior"] = {
            **compare(long),
            "by_project": {
                project: compare([
                    row for row in long if row["project"] == project
                ]) for project in sorted({row["project"] for row in long})
            },
            "by_status": {
                status: compare([
                    row for row in long if row["status"] == status
                ]) for status in sorted({row["status"] for row in long})
            } if all("status" in row for row in long) else {},
        }
    return report


def namespace_batch(workflows: Path, rows: list[dict]) -> list[dict]:
    identities = {}
    for result_path in workflows.glob("*/result.json"):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        workflow = result.get("workflow_id")
        task = result.get("instance_id")
        if (
            not isinstance(workflow, str) or not workflow
            or task != result_path.parent.name
            or workflow in identities
        ):
            raise ValueError("invalid or duplicate workflow result identity")
        identities[workflow] = task
    batch_id = str(workflows.resolve())
    annotated = []
    for row in rows:
        workflow = row["workflow"]
        if workflow not in identities:
            raise ValueError("tool row lacks completed workflow identity")
        annotated.append({
            **row,
            "task_id": identities[workflow],
            "workflow": f"{batch_id}::{workflow}",
        })
    return annotated


def load_training_batches(
    batches: list[Path],
) -> tuple[list[dict], set[str], dict]:
    if not batches or len({path.resolve() for path in batches}) != len(batches):
        raise ValueError("training batches must be nonempty and distinct")
    ids = []
    rows = []
    sources = {}
    for workflows in batches:
        batch_ids, errors = require_complete_batch(workflows)
        batch_rows, censor = cold_calls(
            workflows, include_returned_failures=True,
        )
        ids.extend(batch_ids)
        rows.extend(namespace_batch(workflows, batch_rows))
        sources[str(workflows)] = {
            "workflows": len(batch_ids),
            "runner_errors": errors,
            "censor": censor,
        }
    return first_inputs(rows), {
        task.split("__", 1)[0] for task in ids
    }, {
        "sources": sources,
        "workflow_runs": len(ids),
        "distinct_tasks": len(set(ids)),
        "replicated_task_ids": sum(
            count > 1 for count in Counter(ids).values()
        ),
    }


def evaluate(
    train_workflows: list[Path], heldout_workflows: Path | None,
) -> dict:
    train, train_projects, train_source = load_training_batches(train_workflows)
    report = {
        "status": "train_only_workflow_weight_ablation_not_action_eligible",
        "train_sources": train_source,
        "train_project_loo": summarize(project_folds(train)),
        "scope": (
            "Each training project is scored only by heads fitted on other "
            "projects across all provided batches. Repeated task IDs in "
            "multiple batches are correlated, not independent projects. "
            "Probability threshold 0.8 is held fixed. Regression ETA is "
            "compared on the same oracle-long completed tool calls, not "
            "cherry-picked by a candidate classifier. At 100 ms survival "
            "this is a retrospective screen, not delivered H2D or JOIN."
        ),
    }
    if heldout_workflows is not None:
        heldout_ids, heldout_errors = require_complete_batch(heldout_workflows)
        if train_projects & {
            task.split("__", 1)[0] for task in heldout_ids
        }:
            raise ValueError("heldout must have disjoint projects")
        heldout_raw, heldout_censor = cold_calls(
            heldout_workflows, include_returned_failures=True,
        )
        heldout = first_inputs(namespace_batch(heldout_workflows, heldout_raw))
        # No model or threshold selection reads the held-out labels.
        scored = []
        long = [row for row in heldout if row["duration_ms"] >= TARGET_MS]
        global_eta = task_balanced_long_prior(train)
        for mode in MODES:
            scores = predict(
                fit(train, mode, regression=False), heldout,
                regression=False,
            )
            etas = iter(predict(
                fit(train, mode, regression=True), long,
                regression=True,
            ))
            for index, (row, probability) in enumerate(zip(heldout, scores)):
                if mode == MODES[0]:
                    scored.append({
                        "project": row["project"],
                        "workflow": row["workflow"],
                        "task_id": row["task_id"],
                        "status": row["status"],
                        "duration_ms": row["duration_ms"],
                        "global_long_eta_ms": (
                            global_eta if row["duration_ms"] >= TARGET_MS
                            else None
                        ),
                        "probability": {},
                        "eta_ms": (
                            {} if row["duration_ms"] >= TARGET_MS else None
                        ),
                    })
                scored[index]["probability"][mode] = float(probability)
                if scored[index]["eta_ms"] is not None:
                    scored[index]["eta_ms"][mode] = float(next(etas))
        report["status"] = "project_disjoint_development_not_action_eligible"
        report["heldout_workflows"] = len(heldout_ids)
        report["heldout_errors"] = heldout_errors
        report["heldout_censor"] = heldout_censor
        report["heldout"] = summarize(scored)
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
    result = evaluate(args.train_workflows, args.heldout_workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
