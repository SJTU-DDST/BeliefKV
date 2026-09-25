#!/usr/bin/env python3
"""Evaluate frozen stream stages on disjoint projects without physical actions."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median

from scipy.stats import beta

from scripts.audit_native_stream_shadow import _quantile, _rows, _satisfied_last_children
from scripts.audit_stream_stage_eta import STAGES, candidates


def _paths(directories: list[Path]) -> list[Path]:
    paths = [
        path for directory in directories
        for path in sorted(directory.glob("*/runtime_events.deepagents.jsonl"))
    ]
    if not paths:
        raise ValueError("no workflow event traces")
    instances = [path.parent.name for path in paths]
    if len(instances) != len(set(instances)):
        raise ValueError("repeated workflow instances across input batches")
    return paths


def _project(path: Path) -> str:
    return path.parent.name.split("__", 1)[0]


def _last_children(paths: list[Path]) -> set[tuple[str, str]]:
    return {
        (str(path), child)
        for path in paths
        for _, child in _satisfied_last_children(list(_rows(path)))
    }


def _quality(
    rows: list[dict], eligible: set[tuple[str, str]], prior: float | None,
) -> dict:
    positives = [row for row in rows if row["final"]]
    leads = [float(row["lead_ms"]) for row in positives]
    errors = [abs(lead - prior) for lead in leads] if prior is not None else []
    covered = {
        (row["join"][0], row["child"])
        for row in positives
    } & eligible
    workflow_count = len({row["join"][0] for row in rows})
    return {
        "first_join_candidates": len(rows),
        "candidate_workflows": workflow_count,
        "true_next_return_join": len(positives),
        "false_next_return_join": len(rows) - len(positives),
        "precision": len(positives) / len(rows) if rows else None,
        "precision_two_sided_95pct_lower": (
            float(beta.ppf(.025, len(positives), len(rows) - len(positives) + 1))
            if positives else 0.0 if rows else None
        ),
        "complete_last_child_joins": len(eligible),
        "covered_last_child_joins": len(covered),
        "complete_last_child_recall": (
            len(covered) / len(eligible) if eligible else None
        ),
        "true_lead_p50_ms": median(leads) if leads else None,
        "true_lead_at_least_500ms": sum(lead >= 500 for lead in leads),
        "true_lead_at_least_500ms_fraction": (
            sum(lead >= 500 for lead in leads) / len(leads) if leads else None
        ),
        "eta_prior_ms": prior,
        "eta_error_p50_ms": median(errors) if errors else None,
        "eta_error_p90_ms": _quantile(errors, .9),
        "eta_within_500ms": sum(error <= 500 for error in errors),
        "eta_within_500ms_fraction": (
            sum(error <= 500 for error in errors) / len(errors)
            if errors else None
        ),
    }


def evaluate(
    training: dict[str, list[dict]],
    heldout: dict[str, list[dict]],
    train_eligible: set[tuple[str, str]],
    heldout_eligible: set[tuple[str, str]],
) -> dict:
    train_projects = {
        _project(Path(row["join"][0]))
        for rows in training.values() for row in rows
    } | {_project(Path(path)) for path, _ in train_eligible}
    heldout_projects = {
        _project(Path(row["join"][0]))
        for rows in heldout.values() for row in rows
    } | {_project(Path(path)) for path, _ in heldout_eligible}
    if overlap := train_projects & heldout_projects:
        raise ValueError(f"projects overlap: {sorted(overlap)}")
    priors = {
        stage: (
            median(row["lead_ms"] for row in training[stage] if row["final"])
            if any(row["final"] for row in training[stage]) else None
        )
        for stage in STAGES
    }
    dev = {
        stage: _quality(training[stage], train_eligible, priors[stage])
        for stage in STAGES
    }
    eligible_stages = [
        stage for stage in STAGES
        if (
            dev[stage]["first_join_candidates"] >= 10
            and dev[stage]["candidate_workflows"] >= 5
            and dev[stage]["precision"] >= .9
            and dev[stage]["true_lead_at_least_500ms_fraction"] >= .7
            and dev[stage]["eta_error_p50_ms"] is not None
            and dev[stage]["eta_error_p50_ms"] <= 500
            and dev[stage]["eta_error_p90_ms"] <= 1000
        )
    ]
    chosen = max(
        eligible_stages,
        key=lambda stage: (
            dev[stage]["covered_last_child_joins"],
            -dev[stage]["eta_error_p50_ms"],
        ),
        default=None,
    )
    per_project = defaultdict(dict)
    for stage in STAGES:
        for project in heldout_projects:
            rows = [
                row for row in heldout[stage]
                if _project(Path(row["join"][0])) == project
            ]
            eligible = {
                key for key in heldout_eligible
                if _project(Path(key[0])) == project
            }
            per_project[project][stage] = _quality(rows, eligible, priors[stage])
    evaluation = {
        stage: _quality(heldout[stage], heldout_eligible, priors[stage])
        for stage in STAGES
    }
    chosen_quality = evaluation[chosen] if chosen is not None else None
    return {
        "status": "read_only_stage_project_holdout_no_physical_h2d",
        "training_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "development_stage": chosen,
        "training": dev,
        "heldout": evaluation,
        "heldout_by_project": dict(sorted(per_project.items())),
        "pre_registered_holdout_gate_passed": bool(
            chosen_quality is not None
            and chosen_quality["first_join_candidates"] >= 30
            and chosen_quality["candidate_workflows"] >= 20
            and len(heldout_projects) >= 2
            and chosen_quality["precision_two_sided_95pct_lower"] >= .9
            and chosen_quality["complete_last_child_recall"] is not None
            and chosen_quality["complete_last_child_recall"] >= .3
            and chosen_quality["true_lead_at_least_500ms_fraction"] >= .7
            and chosen_quality["eta_error_p50_ms"] <= 500
            and chosen_quality["eta_error_p90_ms"] <= 1000
        ),
        "limitations": (
            "Known first candidate per JOIN/stage with completed successor; "
            "unresolved responses require separate censoring audit. "
            "Transfer availability, control latency and H2D are unverified."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train_paths = _paths(args.train_workflows)
    eval_paths = _paths(args.evaluate_workflows)
    train_projects = {_project(path) for path in train_paths}
    eval_projects = {_project(path) for path in eval_paths}
    if overlap := train_projects & eval_projects:
        raise ValueError(f"projects overlap: {sorted(overlap)}")
    training = {stage: [] for stage in STAGES}
    for directory in args.train_workflows:
        for stage, rows in candidates(directory).items():
            training[stage].extend(rows)
    heldout = {stage: [] for stage in STAGES}
    for directory in args.evaluate_workflows:
        for stage, rows in candidates(directory).items():
            heldout[stage].extend(rows)
    report = evaluate(
        training, heldout, _last_children(train_paths),
        _last_children(eval_paths),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
