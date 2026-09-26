#!/usr/bin/env python3
"""Select an early child-terminal threshold using training projects only.

The candidate 256-token gate is exploratory. Previously inspected Astropy,
Sphinx, and psf results are development diagnostics, not sealed tests.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

import numpy as np

if __package__:
    from scripts.pilot_hidden_eta_project_split import fit_ridge
    from scripts.pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_batch_records,
    )
else:
    from pilot_hidden_eta_project_split import fit_ridge
    from pilot_real_child_hidden_eta import (
        at_stage, content_cues, first_features, load_batch_records,
    )

STAGE_TOKENS = 256
THRESHOLDS = tuple(round(i / 100, 2) for i in range(50, 96, 5))


def fit_head(records: list[dict]) -> tuple:
    positive = sorted(
        (row for row in records if row["terminal"]), key=lambda row: row["rid"],
    )
    negative = sorted(
        (row for row in records if not row["terminal"]),
        key=lambda row: row["rid"],
    )
    if len(positive) < 5 or len(negative) < 5:
        raise ValueError("not enough natural terminal and nonterminal rounds")
    random.Random(17).shuffle(negative)
    sample = positive + negative[: 5 * len(positive)]
    random.Random(23).shuffle(sample)
    return fit_ridge(
        first_features(sample, True),
        np.asarray([row["terminal"] for row in sample], dtype=np.float64),
    )


def scored_candidates(records: list[dict], model: tuple, cues: dict) -> list[dict]:
    if not records:
        return []
    mean, scale, weights, intercept = model
    scores = np.clip(
        (first_features(records, True) - mean) / scale @ weights + intercept,
        0, 1,
    )
    candidates = []
    for record, score in zip(records, scores):
        events = cues.get(record["rid"], {})
        content = events.get("content")
        if content is None:
            continue
        stage_time = record["first_arrival_ms"] + record["samples"][0][1]
        candidate_time = max(stage_time, content)
        if events.get("tool", float("inf")) <= candidate_time:
            continue
        candidates.append({
            "score": float(score),
            "terminal": record["terminal"],
            "lead_ms": (
                record["return_ms"] - candidate_time
                if record["terminal"] else None
            ),
            "project": record["project"],
        })
    return candidates


def report(candidates: list[dict], threshold: float, terminal_total: int) -> dict:
    accepted = [
        row for row in candidates if row["score"] >= threshold
    ]
    leads = [
        row["lead_ms"] for row in accepted if row["terminal"]
    ]
    fp = sum(not row["terminal"] for row in accepted)
    actionable = sum(lead >= 500 for lead in leads)
    return {
        "true_positive": len(leads),
        "false_positive": fp,
        "precision": round(len(leads) / len(accepted), 4) if accepted else None,
        "recall": round(len(leads) / terminal_total, 4) if terminal_total else None,
        "at_least_500ms_early": actionable,
        "actionable_precision": (
            round(actionable / len(accepted), 4) if accepted else None
        ),
        "median_return_lead_ms": (
            round(statistics.median(leads), 2) if leads else None
        ),
    }


def select_threshold(candidates: list[dict], terminal_total: int) -> float | None:
    eligible = []
    for threshold in THRESHOLDS:
        result = report(candidates, threshold, terminal_total)
        if (
            result["true_positive"] >= 5
            and result["actionable_precision"] is not None
            and result["actionable_precision"] >= 0.95
        ):
            eligible.append((result["true_positive"], threshold))
    return max(eligible, key=lambda row: (row[0], -row[1]))[1] if eligible else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument("--heldout-traces", type=Path)
    parser.add_argument(
        "--train-workflows", required=True, type=Path, action="append",
    )
    parser.add_argument(
        "--heldout-workflows", type=Path, action="append",
    )
    args = parser.parse_args()
    train, train_counts = load_batch_records(
        args.train_workflows, args.traces,
    )
    train_cues = {}
    for root in args.train_workflows:
        train_cues.update(content_cues(root))
    stage = at_stage(train, STAGE_TOKENS)
    projects = sorted({row["project"] for row in train})
    if len(projects) < 3:
        parser.error("project-level threshold selection needs at least 3 projects")
    out_of_project = []
    for project in projects:
        train_fold = [row for row in stage if row["project"] != project]
        test_fold = [row for row in stage if row["project"] == project]
        out_of_project.extend(
            scored_candidates(test_fold, fit_head(train_fold), train_cues)
        )
    threshold = select_threshold(
        out_of_project, sum(row["terminal"] for row in stage),
    )
    result = {
        "diagnostic_only": True,
        "stage_tokens": STAGE_TOKENS,
        "threshold_selected_from_training_projects_only": threshold,
        "train_projects": projects,
        "train": train_counts,
        "project_cv_candidate_count": len(out_of_project),
        "project_cv_threshold_sweep": {
            str(value): report(
                out_of_project, value, sum(row["terminal"] for row in stage),
            ) for value in THRESHOLDS
        },
        "project_cv_at_threshold": (
            report(out_of_project, threshold, sum(r["terminal"] for r in stage))
            if threshold is not None else None
        ),
        "project_cv_by_project": {
            project: report(
                [r for r in out_of_project if r["project"] == project],
                threshold, sum(r["terminal"] for r in stage
                               if r["project"] == project),
            ) for project in projects
        } if threshold is not None else None,
    }
    if args.heldout_workflows:
        heldout, counts = load_batch_records(
            args.heldout_workflows, args.heldout_traces or args.traces,
        )
        heldout_projects = {row["project"] for row in heldout}
        if heldout_projects & set(projects):
            parser.error("heldout project overlaps training projects")
        result["heldout_projects"] = sorted(heldout_projects)
        result["heldout"] = counts
        if threshold is not None:
            model = fit_head(stage)
            cues = {}
            for root in args.heldout_workflows:
                cues.update(content_cues(root))
            candidates = scored_candidates(
                at_stage(heldout, STAGE_TOKENS), model, cues,
            )
            result["heldout_at_frozen_threshold"] = report(
                candidates, threshold, sum(r["terminal"] for r in heldout),
            )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
