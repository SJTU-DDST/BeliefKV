#!/usr/bin/env python3
"""Causal cold-tool timing replay with project-local input-size neighbors."""

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


def _neighbor_prior(row: dict, history: list[dict]) -> tuple[float | None, int]:
    size = row["input_chars"]
    if type(size) is not int or size <= 0:
        return None, 0
    ranked = sorted(
        (
            abs(math.log(size / past["input_chars"])),
            past["duration_ms"],
        )
        for past in history
        if past["terminal_ts_ms"] < row["start_ts_ms"]
        and past["status"] == "success"
        and type(past["input_chars"]) is int
        and past["input_chars"] > 0
    )
    neighbors = [
        duration for distance, duration in ranked[:8]
        if distance <= math.log(2)
    ]
    return (median(neighbors), len(neighbors)) if len(neighbors) >= 4 else (
        None, len(neighbors)
    )


def _outcome(rows: list[dict]) -> dict:
    actual_long = [row for row in rows if row["duration_ms"] >= 2000]
    report = {
        "actual_long": len(actual_long),
        "predicted_long": {},
        "false_imminent_when_actual_long": {},
    }
    for name in ("baseline", "candidate"):
        predicted = [row for row in rows if row[f"{name}_forecast_ms"] >= 2000]
        correct = sum(row["duration_ms"] >= 2000 for row in predicted)
        report["predicted_long"][name] = {
            "count": len(predicted),
            "precision": correct / len(predicted) if predicted else None,
            "recall": correct / len(actual_long) if actual_long else None,
        }
        report["false_imminent_when_actual_long"][name] = sum(
            row[f"{name}_forecast_ms"] <= 500 for row in actual_long
        )
    return report


def replay(train: list[dict], evaluation: list[dict]) -> dict:
    by_class = defaultdict(list)
    for row in train:
        if row["status"] == "success":
            by_class[row["class"]].append(row["duration_ms"])
    default = median(row["duration_ms"] for row in train)
    priors = {
        kind: median(values) for kind, values in by_class.items()
        if len(values) >= 8
    }
    history = defaultdict(list)
    metrics = defaultdict(list)
    counts = defaultdict(int)
    for row in sorted(evaluation, key=lambda item: item["start_ts_ms"]):
        scope = row["project"], row["class"]
        baseline = priors.get(row["class"], default)
        # This history is strictly completed before the target's TOOL_START.
        local, support = _neighbor_prior(row, history[scope])
        candidate = local if local is not None else baseline
        data = {
            **row,
            "baseline_error_ms": abs(row["duration_ms"] - baseline),
            "candidate_error_ms": abs(row["duration_ms"] - candidate),
            "baseline_forecast_ms": baseline,
            "candidate_forecast_ms": candidate,
        }
        group = "supported" if support >= 4 else "fallback"
        metrics["all"].append(data)
        metrics[group].append(data)
        if row["is_child"] is True and row["previous"] is None:
            metrics["cold_child"].append(data)
            metrics[f"cold_child_{group}"].append(data)
            if row["duration_ms"] >= 2000:
                metrics["cold_long_child"].append(data)
                metrics[f"cold_long_child_{group}"].append(data)
        counts[group] += 1
        history[scope].append(row)

    return {
        "status": "causal_nearest_history_diagnostic_only",
        "counts": dict(counts),
        "groups": {
            key: {
                "baseline": _summarize([
                    {**row, "error_ms": row["baseline_error_ms"]}
                    for row in rows
                ]),
                "candidate": _summarize([
                    {**row, "error_ms": row["candidate_error_ms"]}
                    for row in rows
                ]),
                "duration_classification": _outcome(rows),
            }
            for key, rows in sorted(metrics.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--evaluate-workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train = [
        row for directory in args.train_workflows
        for path in sorted(directory.glob("*/runtime_events.deepagents.jsonl"))
        for row in _read_workflow(path)
    ]
    evaluation = [
        row for path in sorted(args.evaluate_workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        ))
        for row in _read_workflow(path)
    ]
    report = replay(train, evaluation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "status": report["status"],
        "counts": report["counts"],
        "groups": {
            key: {
                model: {
                    "count": stats["count"],
                    "p50_error_ms": stats["p50_error_ms"],
                    "within_500ms": stats["within_500ms"],
                }
                for model, stats in group.items()
                if model in ("baseline", "candidate")
            }
            for key, group in report["groups"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
