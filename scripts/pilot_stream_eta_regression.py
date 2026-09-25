#!/usr/bin/env python3
"""Read-only causal ETA regression after a child stream completion cue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from statistics import median

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_stream_final_classifier import (
    MIN_PRECISION, MIN_SELECTED, _features, _fit, _scores, samples,
)


def fit_eta(rows: list[dict], *, join_aware: bool):
    if len(rows) < 20:
        raise ValueError("insufficient completed, selected stream returns")
    matrix = np.array(
        [_features(row, join_aware=join_aware) for row in rows], dtype=float
    )
    mean = matrix.mean(axis=0)
    scale = np.maximum(matrix.std(axis=0), .1)
    design = np.column_stack((np.ones(len(matrix)), (matrix - mean) / scale))
    target = np.log1p(np.array(
        [row["return_lead_ms"] for row in rows], dtype=float
    ) / 1000)
    penalty = np.diag([0.] + [10.] * matrix.shape[1])
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ target)
    return weights, mean, scale


def predict_eta(rows: list[dict], model, *, join_aware: bool) -> np.ndarray:
    if not rows:
        return np.array([])
    weights, mean, scale = model
    matrix = np.array(
        [_features(row, join_aware=join_aware) for row in rows], dtype=float
    )
    design = np.column_stack((np.ones(len(rows)), (matrix - mean) / scale))
    return np.clip(np.expm1(design @ weights) * 1000, 0, 60_000)


def _metrics(rows: list[dict], eta: np.ndarray, prior_ms: float) -> dict:
    actual = np.array([row["return_lead_ms"] for row in rows], dtype=float)
    error = np.abs(eta - actual)
    reference = np.abs(actual - prior_ms)
    return {
        "selected_true_returns": len(rows),
        "eta_error_p50_ms": median(error) if len(error) else None,
        "eta_error_p90_ms": float(np.quantile(error, .9)) if len(error) else None,
        "eta_within_500ms": int((error <= 500).sum()),
        "fixed_prior_ms": prior_ms,
        "fixed_prior_error_p50_ms": median(reference) if len(reference) else None,
        "fixed_prior_within_500ms": int((reference <= 500).sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, required=True)
    parser.add_argument("--join-aware", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    training = []
    for path in args.train_workflows:
        rows, _, _ = samples(path)
        training.extend(rows)
    evaluation, censored, _ = samples(args.evaluate_workflows)
    classifier = _fit(training, join_aware=args.join_aware)
    scores = _scores(training, classifier, join_aware=args.join_aware)
    thresholds = sorted(set(scores), reverse=True)
    acceptable = [
        cutoff for cutoff in thresholds
        if (selected := scores >= cutoff).sum() >= MIN_SELECTED
        and np.mean([
            row["final"] for row, flag in zip(training, selected) if flag
        ]) >= MIN_PRECISION
    ]
    if not acceptable:
        raise ValueError("no precision-qualified development threshold")
    threshold = min(acceptable)
    train_true = [
        row for row, score in zip(training, scores)
        if score >= threshold and row["final"]
    ]
    eval_scores = _scores(evaluation, classifier, join_aware=args.join_aware)
    eval_true = [
        row for row, score in zip(evaluation, eval_scores)
        if score >= threshold and row["final"]
    ]
    prior = median(row["return_lead_ms"] for row in train_true)
    eta_model = fit_eta(train_true, join_aware=args.join_aware)
    report = {
        "status": "read_only_causal_stream_eta_pilot",
        "join_aware": args.join_aware,
        "threshold": float(threshold),
        "training": _metrics(
            train_true, predict_eta(train_true, eta_model,
                                    join_aware=args.join_aware), prior
        ),
        "heldout": _metrics(
            eval_true, predict_eta(eval_true, eta_model,
                                   join_aware=args.join_aware), prior
        ),
        "heldout_censored_episodes": censored,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
