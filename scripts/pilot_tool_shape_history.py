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


def stable_long_prior(row: dict, history: list[dict]) -> float | None:
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
            abs(math.log(size / past["input_chars"])), past["duration_ms"]
        )
        for past in completed
        if type(past["input_chars"]) is int and past["input_chars"] > 0
    )[:8]
    times = sorted(
        duration for distance, duration in nearest
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


def replay(evaluation: list[dict]) -> dict:
    if not evaluation:
        raise ValueError("no tool calls to evaluate")
    history_by_class = defaultdict(list)
    history_by_shape = defaultdict(list)
    selected = []
    child_calls = actual_long = 0
    for row in sorted(evaluation, key=lambda item: item["start_ts_ms"]):
        key = row["project"], row["class"]
        shape_key = row["project"], row["shape"]
        if row["is_child"] is True and row["previous"] is None:
            child_calls += 1
            if row["duration_ms"] >= 2000:
                actual_long += 1
            estimate = stable_long_prior(row, history_by_shape[shape_key])
            if estimate is not None:
                comparison, _ = _neighbor_prior(row, history_by_class[key])
                selected.append({
                    **row,
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
    return {
        "status": "read_only_selective_shape_timing_no_physical_action",
        "cold_child_calls": child_calls,
        "actual_cold_long": actual_long,
        "selected_predicted_long": len(selected),
        "selected_true_long": len(true_long),
        "long_precision": len(true_long) / len(selected) if selected else None,
        "long_recall": len(true_long) / actual_long if actual_long else None,
        "shape_timing": metrics,
        "class_neighbor_matched": baseline,
    }


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
