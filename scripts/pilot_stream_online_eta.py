#!/usr/bin/env python3
"""Check causal project-local ETA calibration after a streamed return cue."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import heapq
import json
from pathlib import Path
from statistics import median
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_stream_final_classifier import (
    MIN_PRECISION, MIN_SELECTED, _fit, _scores, samples,
)


def evaluate_online(
    rows: list[dict], scores: np.ndarray, threshold: float,
    fixed_prior_ms: float,
) -> dict:
    complete_heap = []
    completed_by_project = defaultdict(lambda: deque(maxlen=64))
    errors, baseline_errors = [], []
    by_project = defaultdict(list)
    selected_returns = 0
    for index, row in sorted(
        enumerate(rows), key=lambda item: item[1]["trigger_ms"]
    ):
        now = row["trigger_ms"]
        while complete_heap and complete_heap[0][0] < now:
            _, project, duration = heapq.heappop(complete_heap)
            completed_by_project[project].append(duration)
        project = Path(row["trace_path"]).parent.name.split("__", 1)[0]
        if scores[index] >= threshold and row["final"]:
            selected_returns += 1
            prior = completed_by_project[project]
            if len(prior) >= 8:
                error = abs(row["return_lead_ms"] - median(prior))
                errors.append(error)
                baseline_errors.append(
                    abs(row["return_lead_ms"] - fixed_prior_ms)
                )
                by_project[project].append(error)
        if row["final"]:
            heapq.heappush(
                complete_heap,
                (now + row["return_lead_ms"], project, row["return_lead_ms"])
            )
    return {
        "selected_true_returns": selected_returns,
        "project_supported_selected_returns": len(errors),
        "project_local_eta_error_p50_ms": median(errors) if errors else None,
        "fixed_prior_same_cohort_error_p50_ms": (
            median(baseline_errors) if baseline_errors else None
        ),
        "project_local_within_500ms": sum(error <= 500 for error in errors),
        "fixed_prior_same_cohort_within_500ms": sum(
            error <= 500 for error in baseline_errors
        ),
        "supported_projects": {
            project: {"count": len(values), "p50_error_ms": median(values)}
            for project, values in sorted(by_project.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train = []
    for directory in args.train_workflows:
        rows, _, _ = samples(directory)
        train.extend(rows)
    evaluation, censored, _ = samples(args.evaluate_workflows)
    model = _fit(train, join_aware=True)
    scores = _scores(train, model, join_aware=True)
    possible = [
        cutoff for cutoff in sorted(set(scores), reverse=True)
        if (selected := scores >= cutoff).sum() >= MIN_SELECTED
        and np.mean([
            row["final"] for row, flag in zip(train, selected) if flag
        ]) >= MIN_PRECISION
    ]
    if not possible:
        raise ValueError("no qualified training threshold")
    threshold = min(possible)
    fixed_prior = median(
        row["return_lead_ms"] for row, score in zip(train, scores)
        if score >= threshold and row["final"]
    )
    result = {
        "status": "causal_project_local_eta_diagnostic_only",
        "threshold": float(threshold),
        "fixed_prior_ms": fixed_prior,
        "minimum_completed_project_examples": 8,
        "censored_heldout_episodes": censored,
        "heldout": evaluate_online(
            evaluation, _scores(evaluation, model, join_aware=True),
            threshold, fixed_prior,
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
