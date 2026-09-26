#!/usr/bin/env python3
"""Project-disjoint conditional tool-return timing after fixed survival landmarks."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
import math
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _quantile
from scripts.evaluate_cold_tool_structure_holdout import (
    _paired_long_gain, cold_calls,
)


LANDMARKS_MS = (500, 1000, 1500, 2000)
ONLINE_PROJECT_SHAPE_WINDOW = 64


def _summary(
    rows: list[dict], landmark: int, estimate: dict,
    predictions: list[float] | None = None,
) -> dict:
    if predictions is not None and len(predictions) != len(rows):
        raise ValueError("predictions must match surviving calls")
    errors = []
    workflows = defaultdict(list)
    true_leads = []
    supported = 0
    for index, row in enumerate(rows):
        predicted = (
            predictions[index] if predictions is not None else
            estimate["shape"].get(row["shape"], estimate["global"])
        )
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


def _online_project_predictions(
    calls: list[dict], landmark: int, estimate: dict, *,
    minimum_support: int = 4, minimum_workflows: int = 3,
) -> tuple[list[float], list[int]]:
    if minimum_support < 2 or minimum_workflows < 2:
        raise ValueError("online history needs independent completed support")
    for row in calls:
        if not (
            all(type(row.get(key)) in (int, float) and math.isfinite(row[key])
                for key in ("start_ts_ms", "terminal_ts_ms", "duration_ms"))
            and row["terminal_ts_ms"] >= row["start_ts_ms"]
        ):
            raise ValueError("online history requires completed call timestamps")

    pending = sorted(enumerate(calls), key=lambda item: (
        item[1]["terminal_ts_ms"], item[0],
    ))
    surviving = [
        (index, row) for index, row in enumerate(calls)
        if row["duration_ms"] > landmark
    ]
    history = defaultdict(lambda: deque(maxlen=ONLINE_PROJECT_SHAPE_WINDOW))
    predictions = [0.] * len(surviving)
    supported_indices = []
    completed = 0
    for output_index, (_, row) in sorted(
        enumerate(surviving), key=lambda item: (
            item[1][1]["start_ts_ms"] + landmark, item[1][0],
        ),
    ):
        observed_at = row["start_ts_ms"] + landmark
        while (
            completed < len(pending)
            and pending[completed][1]["terminal_ts_ms"] < observed_at
        ):
            past = pending[completed][1]
            completed += 1
            if past["duration_ms"] > landmark:
                history[past["shape"]].append(past)
        matches = history[row["shape"]]
        if (
            len(matches) >= minimum_support
            and len({past["workflow"] for past in matches}) >= minimum_workflows
        ):
            predictions[output_index] = median(
                past["duration_ms"] for past in matches
            )
            supported_indices.append(output_index)
        else:
            predictions[output_index] = estimate["shape"].get(
                row["shape"], estimate["global"],
            )
    return predictions, supported_indices


def _scheduled_window(
    survivors: list[dict], predictions: list[float],
    supported_indices: list[int], landmark: int, *,
    lead_budget_ms: int = 1000,
) -> dict:
    eligible = [
        (survivors[index], predictions[index])
        for index in supported_indices
        if predictions[index] - landmark >= lead_budget_ms
    ]
    actual_remaining = [
        row["duration_ms"] - (predicted - lead_budget_ms)
        for row, predicted in eligible
    ]
    return {
        "lead_budget_ms": lead_budget_ms,
        "eligible": len(eligible),
        "workflows": len({row["workflow"] for row, _ in eligible}),
        "actual_return_before_scheduled": sum(
            remaining < 0 for remaining in actual_remaining
        ),
        "actual_lead_at_least_500ms": sum(
            remaining >= 500 for remaining in actual_remaining
        ),
        "actual_lead_500_to_2000ms": sum(
            500 <= remaining <= 2000 for remaining in actual_remaining
        ),
        "actual_lead_over_2000ms": sum(
            remaining > 2000 for remaining in actual_remaining
        ),
    }


def evaluate(
    train: list[dict], heldout: list[dict], *,
    online_project_history: bool = False,
) -> dict:
    train_projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if not train or not heldout or train_projects & heldout_projects:
        raise ValueError("nonempty training and heldout projects must be disjoint")
    result = {
        "status": "read_only_completed_survival_landmark_not_action_eligible",
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "landmarks": {},
        "online_project_history": online_project_history,
        "online_project_shape_window": (
            ONLINE_PROJECT_SHAPE_WINDOW if online_project_history else None
        ),
        "limitation": (
            "The observed landmark is causal, but evaluation conditions on "
            "successful completed calls; open/error/intervention calls are "
            "not negatives. Optional project-local adaptation only observes "
            "calls completed strictly before each live landmark; its frozen "
            "starting prior fits other projects. No real timer delivery, "
            "safe point, KV or PCIe."
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
        project_rows = {
            project: [
                row for row in heldout if row["project"] == project
            ]
            for project in sorted(heldout_projects)
        }
        report = {
            "train_survivors": len(past_survivors),
            "train_survivor_workflows": len({
                row["workflow"] for row in past_survivors
            }),
            "shape_supported": {
                shape: len(grouped[shape]) for shape in estimates["shape"]
            },
            "heldout": {
                project: _summary(
                    [row for row in rows if row["duration_ms"] > landmark],
                    landmark, estimates,
                )
                for project, rows in project_rows.items()
            },
        }
        if online_project_history:
            report["heldout_online_project"] = {}
            for project, rows in project_rows.items():
                survivors = [
                    row for row in rows if row["duration_ms"] > landmark
                ]
                predictions, supported = _online_project_predictions(
                    rows, landmark, estimates,
                )
                score = _summary(
                    survivors, landmark, estimates, predictions,
                )
                score["online_project_shape_supported"] = len(supported)
                selected = [survivors[index] for index in supported]
                score["online_selected"] = {
                    "frozen": _summary(selected, landmark, estimates),
                    "adapted": _summary(
                        selected, landmark, estimates,
                        [predictions[index] for index in supported],
                    ),
                    "paired_gain": _paired_long_gain(
                        selected,
                        [
                            estimates["shape"].get(
                                row["shape"], estimates["global"],
                            )
                            for row in selected
                        ],
                        [predictions[index] for index in supported],
                    ),
                }
                score["scheduled_window"] = _scheduled_window(
                    survivors, predictions, supported, landmark,
                )
                report["heldout_online_project"][project] = score
        result["landmarks"][str(landmark)] = report
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", required=True, type=Path)
    parser.add_argument("--heldout-workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--online-project-history", action="store_true",
        help="Evaluate only already-completed same-project shape history.",
    )
    args = parser.parse_args()
    train, train_censor = cold_calls(args.train_workflows)
    heldout, heldout_censor = cold_calls(args.heldout_workflows)
    report = evaluate(
        train, heldout, online_project_history=args.online_project_history,
    )
    report["train_censor"] = train_censor
    report["heldout_censor"] = heldout_censor
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
