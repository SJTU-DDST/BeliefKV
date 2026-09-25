#!/usr/bin/env python3
"""Causal selective long-tool timing from completed command-shape peers."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _read_workflow, _summarize
from scripts.pilot_tool_nearest_history import _neighbor_prior


def stable_long_prior(
    row: dict, history: list[dict], *, recent_ties: bool = False,
) -> float | None:
    if row["shape"] == "unknown":
        return None
    size = row["input_chars"]
    if type(size) is not int or size <= 0:
        return None
    completed = sorted(
        (
            past for past in history
            if past["terminal_ts_ms"] < row["start_ts_ms"]
            and past["is_child"] is True and past["status"] == "success"
        ),
        key=lambda past: past["terminal_ts_ms"],
    )[-64:]
    nearest = sorted(
        (
            abs(math.log(size / past["input_chars"])),
            -past["terminal_ts_ms"] if recent_ties else past["duration_ms"],
            past["duration_ms"],
        )
        for past in completed
        if type(past["input_chars"]) is int and past["input_chars"] > 0
    )[:8]
    times = sorted(
        duration for distance, _, duration in nearest
        if distance <= math.log(2)
    )
    if len(times) < 4:
        return None
    estimate = median(times)
    if (
        estimate < 2000
        or times[-1] - times[0] > max(500, .5 * estimate)
    ):
        return None
    return estimate


def acceptance(report: dict) -> dict:
    timing = report["selected_true_long_timing"]
    count = report["selected_true_long"]
    useful = report["true_long_scheduling_windows_zero_overhead_upper_bound"][
        "desired_lead_500ms"
    ]["at_least_500ms_before_end"]
    checks = {
        "at_least_30_actual_long": report["actual_cold_long"] >= 30,
        "at_least_5_long_workflows": report["actual_long_workflow_count"] >= 5,
        "predicted_long_precision_at_least_70pct": (
            report["long_precision"] is not None
            and report["long_precision"] >= .7
        ),
        "actual_long_recall_at_least_30pct": (
            report["long_recall"] is not None
            and report["long_recall"] >= .3
        ),
        "true_long_p50_at_most_500ms": (
            timing["p50_error_ms"] is not None
            and timing["p50_error_ms"] <= 500
        ),
        "true_long_p90_at_most_1000ms": (
            timing["p90_error_ms"] is not None
            and timing["p90_error_ms"] <= 1000
        ),
        "true_long_workflow_weighted_p50_at_most_500ms": (
            timing["workflow_weighted_p50_ms"] is not None
            and timing["workflow_weighted_p50_ms"] <= 500
        ),
        "true_long_500ms_lead_at_least_70pct": (
            count > 0 and useful / count >= .7
        ),
    }
    return {"accepted": all(checks.values()), "checks": checks}


def replay(evaluation: list[dict]) -> dict:
    if not evaluation:
        raise ValueError("no tool calls to evaluate")
    history_by_class = defaultdict(list)
    history_by_shape = defaultdict(list)
    selected = []
    recent_selected = []
    actual_long_workflows = set()
    child_calls = actual_long = 0
    for row in sorted(evaluation, key=lambda item: item["start_ts_ms"]):
        key = row["project"], row["class"]
        shape_key = row["project"], row["shape"]
        if row["is_child"] is True and row["previous"] is None:
            child_calls += 1
            if row["duration_ms"] >= 2000:
                actual_long += 1
                actual_long_workflows.add(row["workflow"])
            estimate = stable_long_prior(row, history_by_shape[shape_key])
            recency_estimate = stable_long_prior(
                row, history_by_shape[shape_key], recent_ties=True
            )
            if recency_estimate is not None:
                recent_selected.append({
                    **row,
                    "error_ms": abs(row["duration_ms"] - recency_estimate),
                    "legacy_error_ms": (
                        abs(row["duration_ms"] - estimate)
                        if estimate is not None else None
                    ),
                })
            if estimate is not None:
                comparison, _ = _neighbor_prior(row, history_by_class[key])
                selected.append({
                    **row,
                    "estimate_ms": estimate,
                    "error_ms": abs(row["duration_ms"] - estimate),
                    "class_neighbor_error_ms": (
                        abs(row["duration_ms"] - comparison)
                        if comparison is not None else None
                    ),
                })
        if row["is_child"] is True and row["class"] != "unknown":
            history_by_class[key].append(row)
            if row["shape"] != "unknown":
                history_by_shape[shape_key].append(row)
    true_long = [row for row in selected if row["duration_ms"] >= 2000]
    metrics = _summarize(selected)
    baseline = _summarize([
        {**row, "error_ms": row["class_neighbor_error_ms"]}
        for row in selected if row["class_neighbor_error_ms"] is not None
    ])
    recent_true_long = [
        row for row in recent_selected if row["duration_ms"] >= 2000
    ]
    recency_matched = [
        row for row in recent_selected if row["legacy_error_ms"] is not None
    ]
    scheduling_windows = {}
    true_scheduling_windows = {}
    for budget in (500, 1000, 2000):
        # Both forecasts and true durations are measured from TOOL_START.
        # This is only a zero-control-overhead upper bound for a timed action.
        leads = [
            row["duration_ms"] - max(0, row["estimate_ms"] - budget)
            for row in selected
        ]
        scheduling_windows[f"desired_lead_{budget}ms"] = {
            "before_tool_end": sum(lead >= 0 for lead in leads),
            "at_least_500ms_before_end": sum(lead >= 500 for lead in leads),
            "over_2000ms_before_end": sum(lead > 2000 for lead in leads),
            "after_tool_end": sum(lead < 0 for lead in leads),
        }
        true_leads = [
            row["duration_ms"] - max(0, row["estimate_ms"] - budget)
            for row in true_long
        ]
        true_scheduling_windows[f"desired_lead_{budget}ms"] = {
            "at_least_500ms_before_end": sum(
                lead >= 500 for lead in true_leads
            ),
            "after_tool_end": sum(lead < 0 for lead in true_leads),
        }
    report = {
        "status": "read_only_selective_shape_timing_no_physical_action",
        "cold_child_calls": child_calls,
        "actual_cold_long": actual_long,
        "actual_long_workflow_count": len(actual_long_workflows),
        "selected_predicted_long": len(selected),
        "selected_true_long": len(true_long),
        "selected_false_long": len(selected) - len(true_long),
        "long_precision": len(true_long) / len(selected) if selected else None,
        "long_recall": len(true_long) / actual_long if actual_long else None,
        "shape_timing": metrics,
        "selected_true_long_timing": _summarize(true_long),
        "class_neighbor_matched": baseline,
        "recency_tie_ablation_read_only": {
            "selected_predicted_long": len(recent_selected),
            "selected_true_long": len(recent_true_long),
            "precision": (
                len(recent_true_long) / len(recent_selected)
                if recent_selected else None
            ),
            "recall": (
                len(recent_true_long) / actual_long if actual_long else None
            ),
            "timing": _summarize(recent_selected),
            "paired_recency_timing": _summarize(recency_matched),
            "paired_legacy_timing": _summarize([
                {**row, "error_ms": row["legacy_error_ms"]}
                for row in recency_matched
            ]),
        },
        "scheduling_windows_zero_overhead_upper_bound": scheduling_windows,
        "true_long_scheduling_windows_zero_overhead_upper_bound": (
            true_scheduling_windows
        ),
    }
    report["pre_registered_acceptance"] = acceptance(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [
        row for path in sorted(args.workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        ))
        for row in _read_workflow(path)
    ]
    report = replay(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
