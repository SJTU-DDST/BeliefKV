#!/usr/bin/env python3
"""Compare causal sibling return timing against a frozen project-disjoint prior."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_native_stream_shadow import _quantile
from scripts.audit_stream_content_accounting import audit as audit_content
from scripts.evaluate_stream_stage_holdout import _paths
from scripts.pilot_stream_dynamic_eta import samples


def _project(row: dict) -> str:
    return Path(row["trace_path"]).parent.name.split("__", 1)[0]


def forecast(rows: list[dict], prior_ms: float) -> list[dict]:
    results = []
    grouped = defaultdict(list)
    for row in rows:
        if row["join_id"]:
            grouped[row["trace_path"], row["join_id"]].append(row)
    for row in rows:
        if row["final"] is not True:
            continue
        now = float(row["trigger_ms"])
        past = [
            sibling["lead_ms"]
            for sibling in grouped.get(
                (row["trace_path"], row["join_id"]), ()
            )
            if sibling["child"] != row["child"]
            and sibling["final"] is True
            and float(sibling["trigger_ms"]) + sibling["lead_ms"] < now
        ] if row["join_id"] else []
        local = median(past) if past else None
        results.append({
            "trace_path": row["trace_path"],
            "request_id": row["request_id"],
            "last_join_child": row["last_join_child"],
            "actual_ms": row["lead_ms"],
            "sibling_count": len(past),
            "prior_ms": prior_ms,
            "sibling_shrunk_ms": (
                (prior_ms + local) / 2 if local is not None else prior_ms
            ),
        })
    return results


def _quality(rows: list[dict]) -> dict:
    baseline = [abs(row["actual_ms"] - row["prior_ms"]) for row in rows]
    sibling = [
        abs(row["actual_ms"] - row["sibling_shrunk_ms"]) for row in rows
    ]
    return {
        "natural_returns": len(rows),
        "workflow_count": len({row["trace_path"] for row in rows}),
        "sibling_available": sum(row["sibling_count"] > 0 for row in rows),
        "prior_error_p50_ms": median(baseline) if baseline else None,
        "prior_error_p90_ms": _quantile(baseline, .9),
        "sibling_error_p50_ms": median(sibling) if sibling else None,
        "sibling_error_p90_ms": _quantile(sibling, .9),
        "prior_within_500ms": sum(error <= 500 for error in baseline),
        "sibling_within_500ms": sum(error <= 500 for error in sibling),
    }


def evaluate(train: list[dict], heldout: list[dict], censored: int) -> dict:
    train_projects = {_project(row) for row in train}
    heldout_projects = {_project(row) for row in heldout}
    if overlap := train_projects & heldout_projects:
        raise ValueError(f"projects overlap: {sorted(overlap)}")
    completed = [row["lead_ms"] for row in train if row["final"]]
    if len(completed) < 20:
        raise ValueError("insufficient completed training children")
    prior = median(completed)
    predictions = forecast(heldout, prior)
    by_project = {
        project: _quality([
            row for row in predictions if _project(row) == project
        ])
        for project in sorted(heldout_projects)
    }
    return {
        "status": "read_only_return_conditioned_sibling_eta_no_physical_action",
        "stage_chars": 1700,
        "stage_delay_ms": 250,
        "training_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "training_natural_returns": len(completed),
        "frozen_prior_ms": prior,
        "heldout": _quality(predictions),
        "heldout_last_join_child": _quality([
            row for row in predictions if row["last_join_child"]
        ]),
        "heldout_by_project": by_project,
        "heldout_nonfinal": sum(row["final"] is False for row in heldout),
        "heldout_censored": censored,
        "limitations": (
            "Sibling history uses only a different member of the same JOIN "
            "that returned before the target trigger. Half-weight shrinkage "
            "to the frozen training prior is fixed in advance. Accuracy is "
            "conditioned on a future confirmed natural RETURN; nonfinal and "
            "censored candidates are separate, not successes. This is not "
            "an online action, and cannot prove physical H2D benefit."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train_paths = _paths(args.train_workflows)
    heldout_paths = _paths(args.evaluate_workflows)
    _paths([*args.train_workflows, *args.evaluate_workflows])
    if overlap := {
        path.parent.name.split("__", 1)[0] for path in train_paths
    } & {
        path.parent.name.split("__", 1)[0] for path in heldout_paths
    }:
        raise ValueError(f"projects overlap: {sorted(overlap)}")
    for directory in [*args.train_workflows, *args.evaluate_workflows]:
        if audit_content(directory)["totals"].get(
            "large_milestone_exceeds_final", 0
        ):
            raise ValueError(f"invalid stream content accounting: {directory}")
    train, heldout, censored = [], [], 0
    for directory in args.train_workflows:
        rows, _ = samples(directory, stage_chars=1700)
        train.extend(rows)
    for directory in args.evaluate_workflows:
        rows, missing = samples(directory, stage_chars=1700)
        heldout.extend(rows)
        censored += missing
    report = evaluate(train, heldout, censored)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
