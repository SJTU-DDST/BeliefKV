#!/usr/bin/env python3
"""Project-disjoint audit of pre-service windows at terminal JOIN notices."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median

import numpy as np

from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_join_pressure_strata import load


WINDOWS_MS = (500, 1000, 2000, 5000)


def load_batch(workflows: Path, audit_path: Path) -> tuple[list[dict], dict]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    rows, metadata = load(workflows, notice_source="llm_result")
    if audit["workflows"] != metadata["frozen_workflows"]:
        raise ValueError("service audit and frozen workflow count disagree")
    by_group = {}
    for row in rows:
        key = row["task_id"], row["join_id"]
        if key in by_group:
            raise ValueError(f"duplicate JOIN pressure snapshot: {key}")
        by_group[key] = row
    evidence = audit["sources"]["llm_result"]["matched_evidence"]
    if audit["sources"]["llm_result"]["matched"]["groups"] != len(evidence):
        raise ValueError("service audit evidence count disagrees")
    result = []
    for item in evidence:
        key = item["task_id"], item["join_id"]
        metric = by_group.get(key)
        if metric is not None and (
            abs(metric["trigger_ts_ms"] - item["trigger_ts_ms"]) >= .5
            or abs(metric["lead_ms"] - item["lead_ms"]) >= .5
        ):
            raise ValueError(f"JOIN pressure and service identity disagree: {key}")
        result.append({
            **item,
            "pressure_bin": metric["pressure_bin"] if metric else None,
            "queued": metric["queued"] if metric else None,
            "metric_age_ms": metric["metric_age_ms"] if metric else None,
        })
    return result, {
        **metadata,
        "matched_service_groups": len(evidence),
        "missing_prior_metric_groups": sum(
            row["pressure_bin"] is None for row in result
        ),
    }


def lower_bound(train: list[dict]) -> float | None:
    by_task = defaultdict(list)
    for row in train:
        if row["pressure_bin"] == "heavy_queue":
            by_task[row["task_id"]].append(row["parent_service_lead_ms"])
    if len(by_task) < 10 or len({
        row["project"] for row in train if row["task_id"] in by_task
    }) < 2:
        return None
    values = [median(samples) for samples in by_task.values()]
    return float(np.quantile(values, .1))


def join_prior(train: list[dict]) -> float:
    by_task = defaultdict(list)
    for row in train:
        by_task[row["task_id"]].append(row["lead_ms"])
    if not by_task:
        raise ValueError("no training JOIN labels")
    return float(median(median(values) for values in by_task.values()))


def score(
    rows: list[dict], bound: float | None, join_eta_ms: float | None,
) -> dict:
    selected = [
        row for row in rows
        if row["pressure_bin"] == "heavy_queue"
    ]
    gains_by_task = defaultdict(list)
    if join_eta_ms is not None:
        for row in selected:
            gains_by_task[row["task_id"]].append(
                abs(row["lead_ms"]) - abs(row["lead_ms"] - join_eta_ms)
            )
    join_gains = np.asarray([
        np.mean(values) for _, values in sorted(gains_by_task.items())
    ])
    draws = (
        np.random.default_rng(42).choice(
            join_gains, size=(4000, len(join_gains)), replace=True,
        ).mean(axis=1)
        if len(join_gains) else np.asarray([])
    )
    return {
        "natural_parent_service_groups": len(rows),
        "prior_metric_coverage": sum(
            row["pressure_bin"] is not None for row in rows
        ),
        "selected": len(selected),
        "selected_tasks": len({row["task_id"] for row in selected}),
        "selected_window_counts": {
            str(window): sum(
                row["parent_service_lead_ms"] >= window for row in selected
            )
            for window in WINDOWS_MS
        },
        "all_window_counts": {
            str(window): sum(
                row["parent_service_lead_ms"] >= window for row in rows
            )
            for window in WINDOWS_MS
        },
        "selected_window_p10_ms": _quantile([
            row["parent_service_lead_ms"] for row in selected
        ], .1),
        "selected_window_p50_ms": _quantile([
            row["parent_service_lead_ms"] for row in selected
        ], .5),
        "train_only_heavy_queue_p10_prior_ms": bound,
        "selected_prior_overestimates_actual": (
            sum(row["parent_service_lead_ms"] < bound for row in selected)
            if bound is not None else None
        ),
        "selected_prior_point_error_p50_ms": (
            _quantile([
                abs(row["parent_service_lead_ms"] - bound)
                for row in selected
            ], .5)
            if bound is not None else None
        ),
        "train_only_join_eta_ms": join_eta_ms,
        "selected_zero_join_error_p50_ms": _quantile([
            abs(row["lead_ms"]) for row in selected
        ], .5),
        "selected_join_eta_error_p50_ms": (
            _quantile([
                abs(row["lead_ms"] - join_eta_ms)
                for row in selected
            ], .5)
            if join_eta_ms is not None else None
        ),
        "selected_join_eta_within_500ms": (
            sum(abs(row["lead_ms"] - join_eta_ms) <= 500 for row in selected)
            if join_eta_ms is not None else None
        ),
        "join_eta_paired_mean_gain_ms": (
            float(join_gains.mean()) if len(join_gains) else None
        ),
        "join_eta_task_bootstrap_95pct_ci_ms": (
            [float(value) for value in np.percentile(draws, (2.5, 97.5))]
            if len(draws) else None
        ),
    }


def evaluate(
    train_sources: list[tuple[Path, Path]],
    heldout_source: tuple[Path, Path] | None,
) -> dict:
    if not train_sources or len({
        path.resolve() for path, _ in train_sources
    }) != len(train_sources):
        raise ValueError("training sources must be distinct and nonempty")
    training, projects, sources = [], set(), {}
    for workflows, audit in train_sources:
        rows, meta = load_batch(workflows, audit)
        training.extend(rows)
        projects.update(meta["frozen_projects"])
        sources[str(workflows)] = meta
    folds = {}
    for project in sorted(projects):
        other = [row for row in training if row["project"] != project]
        folds[project] = score(
            [row for row in training if row["project"] == project],
            lower_bound(other), join_prior(other),
        )
    bound = lower_bound(training)
    eta = join_prior(training)
    result = {
        "status": "training_project_loo_not_action_eligible",
        "training_sources": sources,
        "train_project_loo": folds,
        "train_all": score(training, bound, eta),
    }
    if heldout_source is not None:
        rows, meta = load_batch(*heldout_source)
        if projects & set(meta["frozen_projects"]):
            raise ValueError("held-out project overlaps frozen training manifest")
        result.update({
            "status": "project_disjoint_development_not_action_eligible",
            "heldout_source": meta,
            "heldout": score(rows, bound, eta),
            "heldout_by_project": {
                project: score([
                    row for row in rows if row["project"] == project
                ], bound, eta)
                for project in meta["frozen_projects"]
            },
        })
    result["scope"] = (
        "A pressure_bin from metrics strictly before the terminal notice "
        "selects the heavy-queue arm; missing as-of metrics do not trigger. "
        "The training-project-balanced P10 is fitted without held-out "
        "labels, and is diagnostic only, not a safe latest-start. The "
        "training-only task-balanced median JOIN ETA is compared against "
        "predicting JOIN immediately on the same selected groups. "
        "Parent first GPU service is posthoc, not a feature; "
        "0.5/1/2/5s windows are not proof of H2D completion, capacity, or "
        "benefit. Held-out projects were previously used for development."
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train", nargs=2, action="append", type=Path, metavar=("WORKFLOWS", "AUDIT"),
        required=True,
    )
    parser.add_argument("--heldout", nargs=2, type=Path, metavar=("WORKFLOWS", "AUDIT"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = evaluate(
        [(workflows, audit) for workflows, audit in args.train],
        tuple(args.heldout) if args.heldout else None,
    )
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
