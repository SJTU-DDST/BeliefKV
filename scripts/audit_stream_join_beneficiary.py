#!/usr/bin/env python3
"""Audit early H2D beneficiaries against every observed JOIN outcome."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys

import numpy as np
from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_stream_final_classifier import (
    MIN_PRECISION, MIN_SELECTED, _fit, _scores, samples,
)


def _first_per_join(rows: list[dict]) -> list[dict]:
    groups = {}
    for row in sorted(rows, key=lambda item: item["trigger_ms"]):
        if row["join_last_outstanding"] and row["join_id"]:
            groups.setdefault((row["trace_path"], row["join_id"]), row)
    return list(groups.values())


def _outcome(row: dict) -> tuple[str, float | None]:
    terminal = row["join_terminal"]
    if terminal is not None and terminal[1] > row["trigger_ms"]:
        if terminal[0] == "join_satisfied":
            returned = row["child_return_ms"]
            if (
                row["final"] and returned is not None
                and row["trigger_ms"] < returned <= terminal[1]
            ):
                return "satisfied", returned - row["trigger_ms"]
            return "premature_then_satisfied", None
        return "timeout", None
    if row["workflow_outcome"] == "completed":
        return "completed_without_join", None
    return "censored", None


def _quality(
    rows: list[dict], eligible_last_children: set[tuple[str, str, str]],
) -> dict:
    outcomes = [_outcome(row) for row in rows]
    lead = [duration for outcome, duration in outcomes if outcome == "satisfied"]
    determined = sum(outcome != "censored" for outcome, _ in outcomes)
    return {
        "signaled_join_groups": len(rows),
        "observed_satisfied": len(lead),
        "observed_timeout": sum(
            outcome == "timeout" for outcome, _ in outcomes
        ),
        "premature_then_satisfied": sum(
            outcome == "premature_then_satisfied" for outcome, _ in outcomes
        ),
        "completed_without_join": sum(
            outcome == "completed_without_join" for outcome, _ in outcomes
        ),
        "censored": len(rows) - determined,
        "precision_on_determined": len(lead) / determined if determined else None,
        "precision_two_sided_95pct_lower": (
            float(beta.ppf(.025, len(lead), determined - len(lead) + 1))
            if len(lead) else 0.0 if determined else None
        ),
        "last_child_join_coverage": (
            len(lead) / len(eligible_last_children)
            if eligible_last_children else None
        ),
        "satisfied_lead_p50_ms": median(lead) if lead else None,
        "satisfied_lead_at_least_2000ms": sum(
            duration >= 2000 for duration in lead
        ),
        "timeout_examples": [
            row["workflow"] for row, (outcome, _) in zip(rows, outcomes)
            if outcome == "timeout"
        ][:12],
        "premature_examples": [
            row["workflow"] for row, (outcome, _) in zip(rows, outcomes)
            if outcome == "premature_then_satisfied"
        ][:12],
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
    evaluation, censored, last_children = samples(args.evaluate_workflows)
    model = _fit(train, join_aware=True)
    train_scores = _scores(train, model, join_aware=True)
    acceptable = [
        cutoff for cutoff in sorted(set(train_scores), reverse=True)
        if (selected := train_scores >= cutoff).sum() >= MIN_SELECTED
        and np.mean([
            row["final"] for row, flag in zip(train, selected) if flag
        ]) >= MIN_PRECISION
    ]
    if not acceptable:
        raise ValueError("no development-qualified return classifier")
    cutoff = min(acceptable)
    scores = _scores(evaluation, model, join_aware=True)
    raw = _first_per_join(evaluation)
    chosen = _first_per_join([
        row for row, score in zip(evaluation, scores) if score >= cutoff
    ])
    report = {
        "status": "read_only_first_join_beneficiary_diagnostic",
        "cutoff": float(cutoff),
        "complete_join_last_children": len(last_children),
        "censored_child_episodes": censored,
        "raw_early_cue": _quality(raw, last_children),
        "model_selected_early_cue": _quality(chosen, last_children),
        "limitation": (
            "First eligible child cue per JOIN only; require that same response "
            "to RETURN. No in-flight rolling re-evaluation when sibling status "
            "changes, no physical H2D."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
