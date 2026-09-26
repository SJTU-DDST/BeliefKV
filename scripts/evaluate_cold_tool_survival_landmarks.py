#!/usr/bin/env python3
"""Project-disjoint conditional tool-return timing after fixed survival landmarks."""

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
from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_structure_holdout import cold_calls


LANDMARKS_MS = (500, 1000, 1500, 2000)


def _summary(rows: list[dict], landmark: int, estimate: dict) -> dict:
    errors = []
    workflows = defaultdict(list)
    true_leads = []
    supported = 0
    for row in rows:
        predicted = estimate["shape"].get(row["shape"], estimate["global"])
        supported += row["shape"] in estimate["shape"]
        error = abs(row["duration_ms"] - predicted)
        errors.append(error)
        workflows[row["workflow"]].append(error)
        true_leads.append(row["duration_ms"] - landmark)
    return {
        "survivors": len(rows),
        "workflow_count": len(workflows),
        "shape_supported": supported,
        "global_p50_error_ms": _quantile([
            abs(row["duration_ms"] - estimate["global"]) for row in rows
        ], .5),
        "p50_error_ms": _quantile(errors, .5),
        "p90_error_ms": _quantile(errors, .9),
        "within_500ms": sum(value <= 500 for value in errors),
        "workflow_weighted_p50_error_ms": _quantile([
            _quantile(group, .5) for group in workflows.values()
        ], .5),
        "zero_remaining_p50_error_ms": _quantile(true_leads, .5),
        "actual_500ms_lead_count": sum(value >= 500 for value in true_leads),
        "actual_1000ms_lead_count": sum(value >= 1000 for value in true_leads),
        "lead_p50_ms": _quantile(true_leads, .5),
    }


def evaluate(train: list[dict], heldout: list[dict]) -> dict:
    train_projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if not train or not heldout or train_projects & heldout_projects:
        raise ValueError("nonempty training and heldout projects must be disjoint")
    result = {
        "status": "read_only_completed_survival_landmark_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "landmarks": {},
        "limitation": (
            "The observed landmark is causal, but evaluation conditions on "
            "successful completed calls; open/error/intervention calls are "
            "not negatives. No real timer delivery, safe point, KV or PCIe."
        ),
    }
    for landmark in LANDMARKS_MS:
        past_survivors = [
            row for row in train if row["duration_ms"] > landmark
        ]
        if len(past_survivors) < 8:
            result["landmarks"][str(landmark)] = {
                "status": "insufficient_train_survivors",
                "train_survivors": len(past_survivors),
            }
            continue
        grouped = defaultdict(list)
        for row in past_survivors:
            grouped[row["shape"]].append(row)
        estimates = {
            "global": median(row["duration_ms"] for row in past_survivors),
            "shape": {
                shape: median(row["duration_ms"] for row in rows)
                for shape, rows in grouped.items()
                if len(rows) >= 8
                and len({row["workflow"] for row in rows}) >= 3
            },
        }
        result["landmarks"][str(landmark)] = {
            "train_survivors": len(past_survivors),
            "train_survivor_workflows": len({
                row["workflow"] for row in past_survivors
            }),
            "shape_supported": {
                shape: len(grouped[shape]) for shape in estimates["shape"]
            },
            "heldout": {
                project: _summary(
                    [row for row in heldout
                     if row["project"] == project and row["duration_ms"] > landmark],
                    landmark, estimates,
                )
                for project in sorted(heldout_projects)
            },
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    train, train_censor = cold_calls(args.train_workflows)
    heldout, heldout_censor = cold_calls(args.heldout_workflows)
    report = evaluate(train, heldout)
    report["train_censor"] = train_censor
    report["heldout_censor"] = heldout_censor
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
