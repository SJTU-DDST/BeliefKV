#!/usr/bin/env python3
"""Project-disjoint offline screen for a 500 ms window after 250 ms survival."""

from __future__ import annotations

import argparse
from collections import defaultdict
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


LANDMARK_MS = 250
TARGET_MS = 750
THRESHOLDS = (.5, .6, .7, .8, .9)


def first_inputs(rows: list[dict]) -> list[dict]:
    first = []
    seen = set()
    for row in sorted(rows, key=lambda item: (
        item["start_ts_ms"], item["workflow"], item["tool_call_id"],
    )):
        key = (
            row["workflow"], row["invocation"],
            row["input_sha256"] or row["tool_call_id"],
        )
        if key in seen:
            continue
        seen.add(key)
        if row["duration_ms"] > LANDMARK_MS:
            first.append(row)
    return first


def _classifier(train: list[dict], test: list[dict]) -> np.ndarray:
    model = _fit_shape_head(
        train, include_live_peers=True,
        include_duration_priors=True, target_ms=TARGET_MS,
    )
    return _shape_scores(
        model, test, include_live_peers=True,
        include_duration_priors=True,
    )


def _fit_eta(train: list[dict]):
    long = [row for row in train if row["duration_ms"] >= TARGET_MS]
    if len(long) < 20:
        raise ValueError("insufficient natural returns for 250 ms ETA")
    vocabulary = {
        shape: index for index, shape in enumerate(
            sorted({row["shape"] for row in long})
        )
    }
    data = _shape_matrix(
        long, vocabulary, include_live_peers=True,
        include_duration_priors=True,
    )
    model = lgb.train({
        "objective": "regression_l1", "learning_rate": .04,
        "num_leaves": 7, "min_data_in_leaf": 12,
        "lambda_l2": 8, "max_bin": 127, "seed": 42,
        "num_threads": 2, "verbosity": -1,
    }, lgb.Dataset(
        data,
        label=np.log1p([row["duration_ms"] for row in long]),
        categorical_feature=[0],
    ), num_boost_round=100)
    return model, vocabulary, float(np.median([
        row["duration_ms"] for row in long
    ]))


def _eta(model, test: list[dict]) -> list[float]:
    head, vocabulary, _prior = model
    if not test:
        return []
    data = _shape_matrix(
        test, vocabulary, include_live_peers=True,
        include_duration_priors=True,
    )
    return np.clip(
        np.expm1(head.predict(data, num_threads=2)),
        TARGET_MS, 1_000_000,
    ).tolist()


def _counts(rows: list[dict]) -> dict:
    positive = sum(row["duration_ms"] >= TARGET_MS for row in rows)
    return {
        "selected": len(rows),
        "true_windows": positive,
        "false_windows": len(rows) - positive,
        "precision": positive / len(rows) if rows else None,
        "workflows": len({row["workflow"] for row in rows}),
        "success": sum(row["status"] == "success" for row in rows),
        "error": sum(row["status"] == "error" for row in rows),
    }


def select_threshold(scored: list[dict]) -> tuple[float | None, dict]:
    if not scored:
        raise ValueError("no out-of-project tool scores")
    reports = {}
    eligible = []
    for threshold in THRESHOLDS:
        selected = [
            row for row in scored if row["score"] >= threshold
        ]
        project_rows = {
            project: _counts([
                row for row in selected if row["project"] == project
            ])
            for project in sorted({row["project"] for row in scored})
        }
        summary = _counts(selected)
        supported = [
            result for result in project_rows.values()
            if result["selected"] >= 10
        ]
        allowed = (
            summary["true_windows"] >= 20
            and summary["workflows"] >= 10
            and summary["precision"] is not None
            and summary["precision"] >= .8
            and len(supported) >= 3
            and all(
                result["precision"] is not None
                and result["precision"] >= .7
                for result in supported
            )
        )
        reports[str(threshold)] = {
            **summary, "by_project": project_rows, "qualified": allowed,
        }
        if allowed:
            eligible.append((summary["true_windows"], threshold))
    return max(eligible)[1] if eligible else None, reports


def quality(rows: list[dict], scores: np.ndarray, *, threshold: float,
            eta: list[float], prior: float) -> dict:
    if len(rows) != len(scores) or len(rows) != len(eta):
        raise ValueError("prediction and returned tool identities differ")
    selected = [
        {**row, "score": float(score), "predicted_ms": prediction}
        for row, score, prediction in zip(rows, scores, eta)
        if score >= threshold
    ]
    positives = [
        row for row in selected if row["duration_ms"] >= TARGET_MS
    ]
    errors = [
        abs(row["duration_ms"] - row["predicted_ms"])
        for row in selected
    ]
    return {
        "survived_250ms_first_inputs": len(rows),
        "actual_500ms_windows": sum(
            row["duration_ms"] >= TARGET_MS for row in rows
        ),
        "selected": _counts(selected),
        "selected_eta_error_p50_ms": _quantile(errors, .5),
        "selected_eta_error_p90_ms": _quantile(errors, .9),
        "true_window_eta_gain_vs_global": _paired_long_gain(
            positives,
            np.full(len(positives), prior),
            np.asarray([row["predicted_ms"] for row in positives]),
        ),
        "actual_remaining_at_250ms_p50_ms": _quantile([
            row["duration_ms"] - LANDMARK_MS for row in selected
        ], .5),
    }


def evaluate(train_workflows: Path, heldout_workflows: Path) -> dict:
    train_ids, train_errors = require_complete_batch(train_workflows)
    heldout_ids, heldout_errors = require_complete_batch(heldout_workflows)
    train_projects = {item.split("__", 1)[0] for item in train_ids}
    heldout_projects = {item.split("__", 1)[0] for item in heldout_ids}
    if (
        train_projects & heldout_projects
        or set(train_ids) & set(heldout_ids)
    ):
        raise ValueError("train and heldout projects must be disjoint")
    train, train_censor = cold_calls(
        train_workflows, include_returned_failures=True,
    )
    heldout, heldout_censor = cold_calls(
        heldout_workflows, include_returned_failures=True,
    )
    train = first_inputs(train)
    heldout = first_inputs(heldout)
    if len(train_projects) < 3:
        raise ValueError("insufficient training projects")
    out_of_fold = []
    for project in sorted(train_projects):
        fold_train = [
            row for row in train if row["project"] != project
        ]
        fold_test = [
            row for row in train if row["project"] == project
        ]
        fold_scores = _classifier(fold_train, fold_test)
        out_of_fold.extend(
            {**row, "score": float(score)}
            for row, score in zip(fold_test, fold_scores)
        )
    threshold, evidence = select_threshold(out_of_fold)
    result = {
        "status": "train_only_250ms_window_screen_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train_frozen_workflows": len(train_ids),
        "heldout_frozen_workflows": len(heldout_ids),
        "train_runner_errors": train_errors,
        "heldout_runner_errors": heldout_errors,
        "train_censor": train_censor,
        "heldout_censor": heldout_censor,
        "landmark_ms": LANDMARK_MS,
        "required_remaining_window_ms": TARGET_MS - LANDMARK_MS,
        "train_only_threshold": threshold,
        "training_oof": evidence,
        "heldout": None,
        "scope": (
            "A complete-return retrospective screen conditioned on 250 ms "
            "tool survival and first input. Each training project is scored "
            "by a classifier fitted on different projects. Threshold selection "
            "uses training folds alone. Held-out projects do not fit the model "
            "or threshold; negative/failed and censored calls are separate. "
            "This does not establish online timer delivery, JOIN, or KV benefit."
        ),
    }
    if threshold is not None:
        eta = _fit_eta(train)
        scores = _classifier(train, heldout)
        predictions = _eta(eta, heldout)
        result["heldout"] = {
            project: quality(
                [row for row in heldout if row["project"] == project],
                np.asarray([
                    score for row, score in zip(heldout, scores)
                    if row["project"] == project
                ]),
                threshold=threshold,
                eta=[
                    value for row, value in zip(heldout, predictions)
                    if row["project"] == project
                ],
                prior=eta[2],
            )
            for project in sorted(heldout_projects)
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--heldout-workflows", type=Path, required=True)
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
