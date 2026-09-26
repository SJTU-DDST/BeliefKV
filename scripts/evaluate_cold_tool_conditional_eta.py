#!/usr/bin/env python3
"""Project-disjoint read-only TOOL_START timing of screened long child calls."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_cold_tool_structure_holdout import (
    _paired_long_gain, _timing, cold_calls,
)
from scripts.pilot_cold_child_tool_long import (
    _fit_shape_head, _shape_matrix, _shape_scores, shape_transfer_pilot,
)


LONG_MS = 2_000
LEAD_BUDGET_MS = 1_000


def _fit_duration(
    rows: list[dict], *, include_live_peers: bool,
    include_long_history: bool = False,
    include_duration_priors: bool = False,
) -> tuple:
    vocabulary = {
        shape: index for index, shape in enumerate(sorted({
            row["shape"] for row in rows
        }))
    }
    model = lgb.train(
        {
            "objective": "regression_l1", "learning_rate": .04,
            "num_leaves": 7, "min_data_in_leaf": 8, "lambda_l2": 10,
            "max_bin": 127, "seed": 42, "num_threads": 2, "verbosity": -1,
        },
        lgb.Dataset(
            _shape_matrix(
                rows, vocabulary, include_live_peers=include_live_peers,
                include_long_history=include_long_history,
                include_duration_priors=include_duration_priors,
            ),
            label=np.log1p([row["duration_ms"] for row in rows]),
            categorical_feature=[0],
        ),
        num_boost_round=100,
    )
    return model, vocabulary


def _predict_duration(
    fitted: tuple, rows: list[dict], *, include_live_peers: bool,
    include_long_history: bool = False,
    include_duration_priors: bool = False,
) -> np.ndarray:
    if not rows:
        return np.empty(0)
    model, vocabulary = fitted
    return np.maximum(0, np.expm1(model.predict(
        _shape_matrix(
            rows, vocabulary, include_live_peers=include_live_peers,
            include_long_history=include_long_history,
            include_duration_priors=include_duration_priors,
        ),
        num_threads=2,
    )))


def _opportunity(rows: list[dict], duration: np.ndarray) -> dict:
    starts = [
        max(0., float(predicted) - LEAD_BUDGET_MS)
        for predicted in duration
    ]
    remaining = [
        row["duration_ms"] - start
        for row, start in zip(rows, starts)
    ]
    return {
        "selected": len(rows),
        "earliest_trigger_at_tool_start": sum(start == 0 for start in starts),
        "expired_before_estimated_latest_start": sum(
            lead < 0 for lead in remaining
        ),
        "actual_lead_at_least_500ms": sum(
            lead >= 500 for lead in remaining
        ),
        "actual_lead_500_to_3000ms": sum(
            500 <= lead <= 3_000 for lead in remaining
        ),
        "too_early_over_3000ms": sum(
            lead > 3_000 for lead in remaining
        ),
    }


def _causal_long_history(
    rows: list[dict], fallback: np.ndarray,
) -> tuple[np.ndarray, int]:
    predicted = np.asarray(fallback, dtype=float).copy()
    supported = 0
    for index, row in enumerate(rows):
        prior = row.get("project_long_completed_median_ms")
        support = row.get("project_long_completed_support")
        if (
            type(prior) in (int, float) and math.isfinite(prior)
            and prior >= LONG_MS
            and type(support) is int and support >= 3
        ):
            predicted[index] = prior
            supported += 1
    return predicted, supported


def evaluate(
    train: list[dict], heldout: list[dict], *,
    include_duration_priors: bool = False,
) -> dict:
    projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if len(projects) < 3 or not heldout_projects or projects & heldout_projects:
        raise ValueError("need three training projects and disjoint held-out calls")
    if {row["workflow"] for row in train} & {
        row["workflow"] for row in heldout
    }:
        raise ValueError("overlapping train and held-out workflows")
    long = [row for row in train if row["duration_ms"] >= LONG_MS]
    result = {
        "status": "read_only_project_disjoint_two_stage_tool_eta_not_online",
        "train_projects": sorted(projects),
        "heldout_projects": sorted(heldout_projects),
        "train_successful_cold_calls": len(train),
        "train_long": len(long),
        "train_long_by_project": dict(sorted(Counter(
            row["project"] for row in long
        ).items())),
        "train_long_workflows": len({row["workflow"] for row in long}),
        "heldout_calls": len(heldout),
        "heldout_long": sum(
            row["duration_ms"] >= LONG_MS for row in heldout
        ),
        "lead_budget_ms": LEAD_BUDGET_MS,
        "include_duration_priors": include_duration_priors,
        "note": (
            "Binary screening and its cutoff use training-project workflow "
            "CV only. The conditional regressor fits only long training calls; "
            "all heads use only TOOL_START shape, input size, already-completed "
            "project support and optionally live other-workflow peers and "
            "completed class/input-neighbor durations. "
            "The causal local prior is the already-completed long-call "
            "project/class median emitted at TOOL_START (at least 3 past "
            "calls); missing support falls back to the frozen regressor. "
            "The held-out project's future calls never fit the model, "
            "though its already-completed calls can update this online "
            "history before the current TOOL_START. "
            "Same selected calls are compared even when a screened call is "
            "actually short. Future duration labels never select held-out "
            "calls. Successful completed calls only: open, error and guard "
            "intervention are censored or excluded, not short negatives. "
            "No real dispatch, safe point, H2D or physical benefit."
        ),
    }
    if (
        len(long) < 20 or len(train) - len(long) < 20
        or len({row["workflow"] for row in long}) < 5
        or len({row["project"] for row in long}) < 2
    ):
        result["status"] = "insufficient_cross_project_long_train_support"
        return result
    unsupported_folds = [
        project for project in sorted(projects)
        if sum(
            row["duration_ms"] >= LONG_MS
            for row in train if row["project"] != project
        ) < 10
        or sum(
            row["duration_ms"] < LONG_MS
            for row in train if row["project"] != project
        ) < 20
    ]
    if unsupported_folds:
        result["status"] = "insufficient_project_cv_fold_support"
        result["unsupported_train_cv_folds"] = unsupported_folds
        return result
    screen = shape_transfer_pilot(
        train, heldout, include_live_peers=True, include_long_history=True,
        include_duration_priors=include_duration_priors,
    )
    result["screen"] = screen
    if include_duration_priors:
        result["screen_without_duration_priors"] = shape_transfer_pilot(
            train, heldout, include_live_peers=True, include_long_history=True,
        )
    else:
        result["screen_without_long_history"] = shape_transfer_pilot(
            train, heldout, include_live_peers=True, include_long_history=False,
        )
    cutoff = screen["threshold_chosen_on_train_cv"]
    if cutoff is None:
        result["status"] = "train_project_cv_no_qualified_long_screen"
        return result

    scores = _shape_scores(
        _fit_shape_head(
            train, include_long_history=True,
            include_duration_priors=include_duration_priors,
        ),
        heldout, include_long_history=True,
        include_duration_priors=include_duration_priors,
    )
    selected = [
        row for row, score in zip(heldout, scores) if score >= cutoff
    ]
    selected_long = [
        row for row in selected if row["duration_ms"] >= LONG_MS
    ]
    result["heldout_selected"] = len(selected)
    result["heldout_selected_long"] = len(selected_long)
    result["heldout_selected_false_short"] = len(selected) - len(selected_long)
    result["heldout_selected_long_workflows"] = len({
        row["workflow"] for row in selected_long
    })
    result["heldout_long_recall"] = (
        len(selected_long) / result["heldout_long"]
        if result["heldout_long"] else None
    )
    result["heldout_selected_precision"] = (
        len(selected_long) / len(selected) if selected else None
    )

    fitted = {
        "all_calls_no_peer": _fit_duration(
            train, include_live_peers=False,
        ),
        "long_only_no_peer": _fit_duration(
            long, include_live_peers=False,
        ),
        "long_only_with_peer": _fit_duration(
            long, include_live_peers=True,
        ),
        "long_only_with_peer_and_history": _fit_duration(
            long, include_live_peers=True, include_long_history=True,
            include_duration_priors=include_duration_priors,
        ),
    }
    for project in sorted(heldout_projects):
        project_rows = [row for row in heldout if row["project"] == project]
        chosen = [row for row in selected if row["project"] == project]
        true = [row for row in chosen if row["duration_ms"] >= LONG_MS]
        modes = {}
        true_predictions = {}
        selected_predictions = {}
        for name, model in fitted.items():
            include_peer = name in {
                "long_only_with_peer", "long_only_with_peer_and_history",
            }
            include_history = name == "long_only_with_peer_and_history"
            predictions = _predict_duration(
                model, chosen, include_live_peers=include_peer,
                include_long_history=include_history,
                include_duration_priors=(
                    include_history and include_duration_priors
                ),
            )
            selected_predictions[name] = predictions
            true_values = predictions[[
                row["duration_ms"] >= LONG_MS for row in chosen
            ]]
            true_predictions[name] = true_values
            modes[name] = {
                "all_selected_timing": _timing(chosen, predictions),
                "true_selected_long_timing": _timing(true, true_values),
                "opportunity_on_all_selected": _opportunity(
                    chosen, predictions,
                ),
            }
        local_predictions, local_support = _causal_long_history(
            chosen, selected_predictions["long_only_with_peer"],
        )
        selected_long_mask = [
            row["duration_ms"] >= LONG_MS for row in chosen
        ]
        local_true = local_predictions[selected_long_mask]
        _, local_true_support = _causal_long_history(
            true, true_predictions["long_only_with_peer"],
        )
        modes["long_only_with_causal_history"] = {
            "all_selected_timing": _timing(chosen, local_predictions),
            "true_selected_long_timing": _timing(true, local_true),
            "opportunity_on_all_selected": _opportunity(
                chosen, local_predictions,
            ),
            "history_supported_selected": local_support,
            "history_supported_true_long": local_true_support,
        }
        result.setdefault("heldout_by_project", {})[project] = {
            "calls": len(project_rows),
            "long": sum(
                row["duration_ms"] >= LONG_MS for row in project_rows
            ),
            "selected": len(chosen),
            "true_selected_long": len(true),
            "false_selected_short": len(chosen) - len(true),
            "selected_long_workflows": len({
                row["workflow"] for row in true
            }),
            "modes": modes,
            "conditional_gain_over_all_call_regressor": _paired_long_gain(
                true, true_predictions["all_calls_no_peer"],
                true_predictions["long_only_with_peer"],
                draws=1_000,
            ),
            "live_peer_gain_over_same_long_only_regressor": _paired_long_gain(
                true, true_predictions["long_only_no_peer"],
                true_predictions["long_only_with_peer"],
                draws=1_000,
            ),
            "causal_history_gain_over_same_long_only_regressor": (
                _paired_long_gain(
                    true, true_predictions["long_only_with_peer"],
                    local_true, draws=1_000,
                )
            ),
            "history_feature_gain_over_peer_only_regressor": _paired_long_gain(
                true, true_predictions["long_only_with_peer"],
                true_predictions["long_only_with_peer_and_history"],
                draws=1_000,
            ),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--duration-priors", action="store_true",
        help="Use completed class/input-neighbor duration priors at TOOL_START.",
    )
    args = parser.parse_args()
    train_ids, train_errors = require_complete_batch(args.train_workflows)
    heldout_ids, heldout_errors = require_complete_batch(args.heldout_workflows)
    train, train_censor = cold_calls(args.train_workflows)
    heldout, heldout_censor = cold_calls(args.heldout_workflows)
    report = evaluate(
        train, heldout, include_duration_priors=args.duration_priors,
    )
    report.update({
        "train_frozen_workflows": len(train_ids),
        "heldout_frozen_workflows": len(heldout_ids),
        "train_runner_errors": train_errors,
        "heldout_runner_errors": heldout_errors,
        "train_censor": train_censor,
        "heldout_censor": heldout_censor,
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
