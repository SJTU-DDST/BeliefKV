#!/usr/bin/env python3
"""Read-only nested project holdout for selectively using the JOIN pressure clock."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import numpy as np

try:
    from scripts.audit_repeated_tool_timing import _quantile
    from scripts.evaluate_join_pressure_strata import forecast, load
except ModuleNotFoundError:
    from audit_repeated_tool_timing import _quantile
    from evaluate_join_pressure_strata import forecast, load


PRESSURE_BINS = ("idle", "busy_no_queue", "light_queue", "heavy_queue")


def project_oof(rows: list[dict]) -> list[dict]:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("need at least three projects for pressure selection")
    return [
        prediction
        for project in projects
        for prediction in forecast(
            [row for row in rows if row["project"] != project],
            [row for row in rows if row["project"] == project],
        )
    ]


def select_bins(oof: list[dict]) -> tuple[set[str], dict]:
    """Require gains across independent held-out projects, not pooled calls."""

    selected = set()
    evidence = {}
    for pressure in PRESSURE_BINS:
        rows = [row for row in oof if row["pressure_bin"] == pressure]
        by_project = defaultdict(lambda: defaultdict(list))
        for row in rows:
            gain = (
                abs(row["lead_ms"] - row["global_eta_ms"])
                - abs(row["lead_ms"] - row["pressure_eta_ms"])
            )
            by_project[row["project"]][row["task_id"]].append(gain)
        project_gains = {
            project: float(np.mean([
                np.mean(gains) for gains in tasks.values()
            ]))
            for project, tasks in sorted(by_project.items())
        }
        workflows = len({
            row["task_id"] for row in rows
        })
        good_projects = sum(value > 0 for value in project_gains.values())
        qualified = (
            workflows >= 8
            and len(project_gains) >= 3
            and good_projects >= (3 * len(project_gains) + 3) // 4
            and np.median(list(project_gains.values())) > 0
        )
        evidence[pressure] = {
            "events": len(rows),
            "independent_workflows": workflows,
            "projects": project_gains,
            "positive_projects": good_projects,
            "qualified": bool(qualified),
        }
        if qualified:
            selected.add(pressure)
    return selected, evidence


def apply_bins(predictions: list[dict], selected: set[str]) -> list[dict]:
    return [
        {
            **row,
            "selective_eta_ms": (
                row["pressure_eta_ms"]
                if row["pressure_bin"] in selected
                else row["global_eta_ms"]
            ),
        }
        for row in predictions
    ]


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"events": 0}
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(
            abs(row["lead_ms"] - row["global_eta_ms"])
            - abs(row["lead_ms"] - row["selective_eta_ms"])
        )
    gains = np.asarray([
        float(np.mean(values)) for _, values in sorted(by_task.items())
    ])
    draws = np.random.default_rng(42).choice(
        gains, size=(4000, len(gains)), replace=True,
    ).mean(axis=1)
    return {
        "events": len(rows),
        "independent_workflows": len(gains),
        "selected_sources": dict(Counter(
            row["pressure_bin"] for row in rows
            if row["selective_eta_ms"] == row["pressure_eta_ms"]
            and row["pressure_eta_ms"] != row["global_eta_ms"]
        )),
        "global_p50_absolute_error_ms": _quantile([
            abs(row["lead_ms"] - row["global_eta_ms"]) for row in rows
        ], .5),
        "selective_p50_absolute_error_ms": _quantile([
            abs(row["lead_ms"] - row["selective_eta_ms"]) for row in rows
        ], .5),
        "global_within_500ms": sum(
            abs(row["lead_ms"] - row["global_eta_ms"]) <= 500 for row in rows
        ),
        "selective_within_500ms": sum(
            abs(row["lead_ms"] - row["selective_eta_ms"]) <= 500 for row in rows
        ),
        "workflow_mean_mae_gain_ms": float(gains.mean()),
        "workflow_bootstrap_95pct_ci_ms": [
            float(value) for value in np.percentile(draws, (2.5, 97.5))
        ],
    }


def evaluate(
    train_workflows: list[Path], heldout_workflows: Path,
    *, notice_source: str = "shadow",
) -> dict:
    if not train_workflows or len({
        path.resolve() for path in train_workflows
    }) != len(train_workflows):
        raise ValueError("training batches must be nonempty and distinct")
    train, train_projects = [], set()
    for workflows in train_workflows:
        rows, metadata = load(workflows, notice_source=notice_source)
        train.extend(rows)
        train_projects.update(metadata["frozen_projects"])
    heldout, metadata = load(heldout_workflows, notice_source=notice_source)
    if train_projects & set(metadata["frozen_projects"]):
        raise ValueError("training and held-out manifests overlap in projects")
    nested = []
    fold_gates = {}
    for project in sorted({row["project"] for row in train}):
        fit = [row for row in train if row["project"] != project]
        gates, _ = select_bins(project_oof(fit))
        fold_gates[project] = sorted(gates)
        nested.extend(apply_bins(forecast(
            fit, [row for row in train if row["project"] == project],
        ), gates))
    gates, evidence = select_bins(project_oof(train))
    heldout_predictions = apply_bins(forecast(train, heldout), gates)
    return {
        "notice_source": notice_source,
        "status": "read_only_nested_project_holdout_not_action_eligible",
        "training_projects": sorted(train_projects),
        "heldout_projects": metadata["frozen_projects"],
        "nested_train_fold_gates": fold_gates,
        "nested_train": summarize(nested),
        "training_oof_gate_evidence": evidence,
        "selected_gates": sorted(gates),
        "heldout": summarize(heldout_predictions),
        "heldout_by_pressure": {
            pressure: summarize([
                row for row in heldout_predictions
                if row["pressure_bin"] == pressure
            ])
            for pressure in PRESSURE_BINS
        },
        "scope": (
            "Pressure selection for each training outer fold uses only inner "
            "project-held-out predictions from other training projects; the "
            "held-out project is never used for selection or fitting. "
            "Each pressure prior is fitted on different projects. "
            "Repeated workflow IDs across batches are task-clustered in "
            "reporting. This batch has been used for development, so the "
            "heldout result is not sealed validation or physical H2D evidence."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-workflows", type=Path, action="append", required=True,
    )
    parser.add_argument("--heldout-workflows", type=Path, required=True)
    parser.add_argument(
        "--notice-source", choices=("shadow", "llm_result"), default="shadow",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = evaluate(
        args.train_workflows, args.heldout_workflows,
        notice_source=args.notice_source,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
