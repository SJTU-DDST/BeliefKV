#!/usr/bin/env python3
"""Training-only project-LOO screen for a 500 ms window after 100 ms tool survival."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import (
    _paired_long_gain, cold_calls,
)
from scripts.pilot_cold_child_tool_long import (
    _fit_shape_head, _shape_matrix, _shape_scores,
)


LANDMARK_MS = 100
WINDOW_MS = 500
TARGET_MS = LANDMARK_MS + WINDOW_MS
THRESHOLDS = (.3, .4, .5, .6, .7, .8, .9)
ARMS = {
    "shape_size": (False, False),
    "shape_size_peers": (True, False),
    "shape_size_peers_history": (True, True),
}


def first_inputs(rows: list[dict]) -> list[dict]:
    """Keep the first eligible input, including a failed first return."""

    seen: set[tuple[str, str, str]] = set()
    result = []
    for row in sorted(rows, key=lambda item: (
        item["start_ts_ms"], item["workflow"], item["tool_call_id"],
    )):
        identity = (
            row["workflow"], row["invocation"],
            row["input_sha256"] or row["tool_call_id"],
        )
        if identity in seen:
            continue
        seen.add(identity)
        if row["duration_ms"] > LANDMARK_MS:
            result.append(row)
    return result


def _fit_long_eta(rows: list[dict], *, peers: bool, duration_priors: bool):
    train = [row for row in rows if row["duration_ms"] >= TARGET_MS]
    if len(train) < 20:
        raise ValueError("insufficient long training returns for conditional ETA")
    vocabulary = {
        shape: index for index, shape in enumerate(
            sorted({row["shape"] for row in train})
        )
    }
    matrix = _shape_matrix(
        train, vocabulary, include_live_peers=peers,
        include_duration_priors=duration_priors,
    )
    model = lgb.train({
        "objective": "regression_l1", "learning_rate": .04,
        "num_leaves": 7, "min_data_in_leaf": 12, "lambda_l2": 8,
        "max_bin": 127, "seed": 42, "num_threads": 2, "verbosity": -1,
    }, lgb.Dataset(
        matrix, label=np.log1p([row["duration_ms"] for row in train]),
        categorical_feature=[0],
    ), num_boost_round=100)
    return model, vocabulary


def _predict_long_eta(model, rows: list[dict], *, peers: bool,
                      duration_priors: bool) -> list[float]:
    if not rows:
        return []
    booster, vocabulary = model
    matrix = _shape_matrix(
        rows, vocabulary, include_live_peers=peers,
        include_duration_priors=duration_priors,
    )
    return np.clip(
        np.expm1(booster.predict(matrix, num_threads=2)),
        TARGET_MS, 1_000_000,
    ).tolist()


def _quality(rows: list[dict], scores: np.ndarray, threshold: float,
             shape_eta: list[float], global_eta: float,
             regression_eta: list[float] | None = None) -> dict:
    if not (
        len(rows) == len(scores) == len(shape_eta)
        and (regression_eta is None or len(regression_eta) == len(rows))
    ):
        raise ValueError("window scores and ETA estimates must match rows")
    selected = [
        (row, float(score), eta, regression_eta[index]
         if regression_eta is not None else None)
        for index, (row, score, eta) in enumerate(zip(rows, scores, shape_eta))
        if score >= threshold
    ]
    positives = sum(row["duration_ms"] >= TARGET_MS for row in rows)
    true = sum(row["duration_ms"] >= TARGET_MS for row, _, _, _ in selected)
    errors = [
        abs(row["duration_ms"] - eta) for row, _, eta, _ in selected
    ]
    global_errors = [
        abs(row["duration_ms"] - global_eta) for row, _, _, _ in selected
    ]
    result = {
        "survivors": len(rows),
        "positive_500ms_windows": positives,
        "selected": len(selected),
        "selected_workflows": len({row["workflow"] for row, _, _, _ in selected}),
        "true_windows": true,
        "false_windows": len(selected) - true,
        "precision": true / len(selected) if selected else None,
        "recall": true / positives if positives else None,
        "success": {
            "selected": sum(row["status"] == "success" for row, _, _, _ in selected),
            "true_windows": sum(
                row["status"] == "success" and row["duration_ms"] >= TARGET_MS
                for row, _, _, _ in selected
            ),
        },
        "error": {
            "selected": sum(row["status"] == "error" for row, _, _, _ in selected),
            "true_windows": sum(
                row["status"] == "error" and row["duration_ms"] >= TARGET_MS
                for row, _, _, _ in selected
            ),
        },
        "selected_eta_p50_absolute_error_ms": _quantile(errors, .5),
        "selected_eta_p90_absolute_error_ms": _quantile(errors, .9),
        "global_eta_p50_absolute_error_ms": _quantile(global_errors, .5),
        "global_eta_p90_absolute_error_ms": _quantile(global_errors, .9),
    }
    if regression_eta is not None:
        regression_errors = [
            abs(row["duration_ms"] - estimate)
            for row, _, _, estimate in selected
        ]
        result["regression_eta_p50_absolute_error_ms"] = _quantile(
            regression_errors, .5,
        )
        result["regression_eta_p90_absolute_error_ms"] = _quantile(
            regression_errors, .9,
        )
    return result


def evaluate(rows: list[dict]) -> dict:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("project leave-one-out needs three or more projects")
    kept = first_inputs(rows)
    if not kept:
        raise ValueError("no surviving independent tool inputs")
    reports = {}
    for arm, (peers, duration_priors) in ARMS.items():
        out_of_fold = []
        by_project = {}
        for project in projects:
            train = [row for row in kept if row["project"] != project]
            heldout = [row for row in kept if row["project"] == project]
            model = _fit_shape_head(
                train, include_live_peers=peers,
                include_duration_priors=duration_priors, target_ms=TARGET_MS,
            )
            scores = _shape_scores(
                model, heldout, include_live_peers=peers,
                include_duration_priors=duration_priors,
            )
            regression_eta = (
                _predict_long_eta(
                    _fit_long_eta(
                        train, peers=peers, duration_priors=duration_priors,
                    ),
                    heldout, peers=peers, duration_priors=duration_priors,
                ) if arm == "shape_size_peers_history" else None
            )
            long_train = [
                row for row in train if row["duration_ms"] >= TARGET_MS
            ]
            global_eta = float(np.median([
                row["duration_ms"] for row in long_train
            ]))
            shape_history: dict[str, list[float]] = defaultdict(list)
            for row in long_train:
                shape_history[row["shape"]].append(row["duration_ms"])
            shape_eta = {
                shape: float(np.median(values))
                for shape, values in shape_history.items() if len(values) >= 8
            }
            etas = [
                shape_eta.get(row["shape"], global_eta) for row in heldout
            ]
            by_project[project] = {
                "frozen_train_projects": sorted(set(projects) - {project}),
                "survivors": len(heldout),
                "positive_500ms_windows": sum(
                    row["duration_ms"] >= TARGET_MS for row in heldout
                ),
                "by_threshold": {
                    str(threshold): _quality(
                        heldout, scores, threshold, etas, global_eta,
                        regression_eta,
                    ) for threshold in THRESHOLDS
                },
            }
            out_of_fold.extend(
                (row, float(score)) for row, score in zip(heldout, scores)
            )
        chosen = []
        pooled = {}
        for threshold in THRESHOLDS:
            selected = [
                row for row, score in out_of_fold if score >= threshold
            ]
            true = sum(row["duration_ms"] >= TARGET_MS for row in selected)
            positive = sum(
                row["duration_ms"] >= TARGET_MS for row, _ in out_of_fold
            )
            precision = true / len(selected) if selected else None
            pooled[str(threshold)] = {
                "selected": len(selected), "true_windows": true,
                "false_windows": len(selected) - true,
                "precision": precision,
                "recall": true / positive if positive else None,
                "projects": len({row["project"] for row in selected}),
                "workflows": len({row["workflow"] for row in selected}),
            }
            supported = [
                detail["by_threshold"][str(threshold)]
                for detail in by_project.values()
                if detail["positive_500ms_windows"] >= 20
                and detail["by_threshold"][str(threshold)]["selected"] >= 10
            ]
            if (
                true >= 20 and len(supported) >= 3
                and precision is not None and precision >= .7
                and all(
                    detail["precision"] is not None
                    and detail["precision"] >= .55 for detail in supported
                )
            ):
                chosen.append(threshold)
        reports[arm] = {
            "pooled_project_loo": pooled,
            "by_project": by_project,
            "exploratory_training_threshold": (
                max(chosen, key=lambda threshold: (
                    pooled[str(threshold)]["true_windows"], threshold
                )) if chosen else None
            ),
        }
    return {
        "status": "train_only_project_loo_100ms_window_screen_not_online",
        "landmark_ms": LANDMARK_MS,
        "required_remaining_window_ms": WINDOW_MS,
        "projects": projects,
        "completed_cold_tools": len(rows),
        "survived_100ms_distinct_inputs": len(kept),
        "positive_500ms_windows": sum(
            row["duration_ms"] >= TARGET_MS for row in kept
        ),
        "arms": reports,
        "limitation": (
            "All labels condition on a completed tool surviving 100 ms; "
            "failed first inputs are kept as returned calls, not replaced by "
            "a later successful retry. Project folds fit only other projects. "
            "This is a retrospective upper bound assuming instantaneous "
            "observation at 100 ms; no live timer, JOIN, DMA, or predictive "
            "benefit is established. Shape/global and long-survivor regression "
            "ETA heads are separately frozen from other projects; their error "
            "is conditional on the same retrospectively selected inputs."
        ),
    }


def score_disjoint_heldout(
    train_rows: list[dict], heldout_rows: list[dict], training: dict,
) -> dict:
    train_projects = {row["project"] for row in train_rows}
    heldout_projects = {row["project"] for row in heldout_rows}
    if not train_projects or not heldout_projects or train_projects & heldout_projects:
        raise ValueError("heldout projects must be disjoint from training")
    threshold = training["arms"]["shape_size_peers_history"][
        "exploratory_training_threshold"
    ]
    heldout = first_inputs(heldout_rows)
    if threshold is None:
        return {
            "status": "no_training_threshold_heldout_not_scored",
            "heldout_projects": sorted(heldout_projects),
        }
    train = first_inputs(train_rows)
    model = _fit_shape_head(
        train, include_live_peers=True, include_duration_priors=True,
        target_ms=TARGET_MS,
    )
    scores = _shape_scores(
        model, heldout, include_live_peers=True, include_duration_priors=True,
    )
    regression_eta = _predict_long_eta(
        _fit_long_eta(train, peers=True, duration_priors=True),
        heldout, peers=True, duration_priors=True,
    )
    long_train = [row for row in train if row["duration_ms"] >= TARGET_MS]
    global_eta = float(np.median([
        row["duration_ms"] for row in long_train
    ]))
    by_shape: dict[str, list[float]] = defaultdict(list)
    for row in long_train:
        by_shape[row["shape"]].append(row["duration_ms"])
    frozen_shape = {
        shape: float(np.median(values)) for shape, values in by_shape.items()
        if len(values) >= 8
    }
    by_project = {}
    for project in sorted(heldout_projects):
        indexed = [
            (row, float(score), estimate)
            for row, score, estimate in zip(heldout, scores, regression_eta)
            if row["project"] == project
        ]
        selected_rows = [
            row for row, score, _ in indexed if score >= threshold
        ]
        shape_selected = [
            frozen_shape.get(row["shape"], global_eta) for row in selected_rows
        ]
        regression_selected = [
            estimate for row, score, estimate in indexed if score >= threshold
        ]
        by_project[project] = {
            **_quality(
                [row for row, _, _ in indexed],
                np.asarray([score for _, score, _ in indexed]),
                threshold,
                [
                    frozen_shape.get(row["shape"], global_eta)
                    for row, _, _ in indexed
                ],
                global_eta,
                [estimate for _, _, estimate in indexed],
            ),
            "paired_eta_gain_regression_vs_shape": _paired_long_gain(
                selected_rows, np.asarray(shape_selected),
                np.asarray(regression_selected),
            ),
            "paired_eta_gain_regression_vs_global": _paired_long_gain(
                selected_rows, np.full(len(selected_rows), global_eta),
                np.asarray(regression_selected),
            ),
        }
    return {
        "status": "development_project_disjoint_counterfactual_not_live",
        "frozen_training_threshold": threshold,
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "by_project": by_project,
        "limitation": (
            "A retrospective completed-return screen conditioned on 100 ms "
            "survival and first input. The threshold is frozen on training "
            "project folds; the heldout projects do not fit the classifier. "
            "No 100 ms timer was delivered for newly selected calls, and no "
            "physical transfer, accurate JOIN or scheduling benefit follows."
        ),
    }


def export_frozen_shadow(
    rows: list[dict], training: dict, *,
    train_manifest: Path, directory: Path,
) -> Path:
    threshold = training["arms"]["shape_size_peers_history"][
        "exploratory_training_threshold"
    ]
    if threshold is None:
        raise ValueError("training LOO did not qualify a frozen window threshold")
    if directory.exists():
        raise FileExistsError(directory)
    train = first_inputs(rows)
    classifier, classifier_vocabulary = _fit_shape_head(
        train, include_live_peers=True, include_duration_priors=True,
        target_ms=TARGET_MS,
    )
    eta_model, eta_vocabulary = _fit_long_eta(
        train, peers=True, duration_priors=True,
    )
    directory.mkdir(parents=True)
    models = {}
    for name, model in (
        ("classifier", classifier),
        ("conditional_eta", eta_model),
    ):
        path = directory / f"{name}.txt"
        model.save_model(str(path))
        models[name] = {
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    long = [
        row["duration_ms"] for row in train
        if row["duration_ms"] >= TARGET_MS
    ]
    artifact = {
        "schema_version": 1,
        "target_total_ms": TARGET_MS,
        "frozen_probability_threshold": threshold,
        "training_projects": training["projects"],
        "training_frozen_workflows": training["frozen_workflows"],
        "training_manifest_sha256": hashlib.sha256(
            train_manifest.read_bytes()
        ).hexdigest(),
        "training_independent_100ms_survivors": len(train),
        "training_independent_long_returns": len(long),
        "global_long_total_eta_ms": float(np.median(long)),
        "classifier_vocabulary": classifier_vocabulary,
        "eta_vocabulary": eta_vocabulary,
        "models": models,
        "status": "read_only_shadow_not_action_eligible",
    }
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path)
    parser.add_argument(
        "--export-frozen-shadow", type=Path,
        help="Freeze the train-only classifier and conditional ETA for a shadow run.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    frozen_ids, runner_errors = require_complete_batch(args.train_workflows)
    rows, censor = cold_calls(
        args.train_workflows, include_returned_failures=True,
    )
    result = evaluate(rows)
    result["frozen_workflows"] = len(frozen_ids)
    result["runner_error_workflows"] = runner_errors
    result["censor"] = censor
    if args.export_frozen_shadow is not None:
        result["shadow_artifact"] = str(export_frozen_shadow(
            rows, result,
            train_manifest=args.train_workflows.parent / "manifest.json",
            directory=args.export_frozen_shadow,
        ))
    if args.heldout_workflows is not None:
        heldout_ids, heldout_errors = require_complete_batch(args.heldout_workflows)
        heldout_rows, heldout_censor = cold_calls(
            args.heldout_workflows, include_returned_failures=True,
        )
        result["heldout"] = score_disjoint_heldout(rows, heldout_rows, result)
        result["heldout"]["frozen_workflows"] = len(heldout_ids)
        result["heldout"]["runner_error_workflows"] = heldout_errors
        result["heldout"]["censor"] = heldout_censor
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
