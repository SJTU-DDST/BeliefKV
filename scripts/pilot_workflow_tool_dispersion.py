#!/usr/bin/env python3
"""Read-only, causal screen for stable workflow-local tool return windows."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import heapq
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _quantile, _read_workflow


def screen(
    calls: list[dict], *, minimum_support: int = 4,
    max_deviation_ms: float = 250.0, lead_budget_ms: float = 1000.0,
) -> dict:
    if minimum_support < 2 or max_deviation_ms < 0 or lead_budget_ms <= 0:
        raise ValueError("invalid screening parameters")
    history: dict[tuple[str, str], deque[float]] = defaultdict(
        lambda: deque(maxlen=minimum_support)
    )
    pending: list[tuple[float, int, dict]] = []
    selected = []
    child_long = 0
    supported_long = 0
    for sequence, row in sorted(
        enumerate(calls), key=lambda item: (
            float(item[1]["start_ts_ms"]), item[0],
        ),
    ):
        start = float(row["start_ts_ms"])
        while pending and pending[0][0] < start:
            _, _, previous = heapq.heappop(pending)
            if previous["status"] == "success" and previous["is_child"] is True:
                history[
                    previous["workflow"], previous["class"]
                ].append(float(previous["duration_ms"]))
        heapq.heappush(
            pending, (float(row["terminal_ts_ms"]), sequence, row),
        )
        if row["is_child"] is not True or (
            row.get("previous") is not None
            and row["previous"][2] == "success"
        ):
            continue
        actual = float(row["duration_ms"])
        child_long += actual >= 2000
        prior = history[row["workflow"], row["class"]]
        if len(prior) < minimum_support:
            continue
        supported_long += actual >= 2000
        predicted = median(prior)
        if predicted < 2000 or max(
            abs(value - predicted) for value in prior
        ) > max_deviation_ms:
            continue
        trigger_at = max(0.0, predicted - lead_budget_ms)
        remaining = actual - trigger_at
        selected.append({
            "workflow": row["workflow"],
            "project": row["project"],
            "actual_long": actual >= 2000,
            "point_error_ms": abs(actual - predicted),
            "remaining_at_trigger_ms": remaining,
        })

    def summarize(rows: list[dict]) -> dict:
        errors = [item["point_error_ms"] for item in rows]
        return {
            "selected": len(rows),
            "projects": sorted({item["project"] for item in rows}),
            "workflow_count": len({item["workflow"] for item in rows}),
            "true_long": sum(item["actual_long"] for item in rows),
            "false_long": sum(not item["actual_long"] for item in rows),
            "point_error_p50_ms": _quantile(errors, .5),
            "point_error_p90_ms": _quantile(errors, .9),
            "point_within_500ms": sum(error <= 500 for error in errors),
            "return_before_trigger": sum(
                item["remaining_at_trigger_ms"] < 0 for item in rows
            ),
            "lead_500_to_2000ms": sum(
                500 <= item["remaining_at_trigger_ms"] <= 2000 for item in rows
            ),
            "more_than_2000ms_early": sum(
                item["remaining_at_trigger_ms"] > 2000 for item in rows
            ),
        }

    return {
        "status": "read_only_completed_calls_not_prefetch_eligible",
        "parameters": {
            "minimum_support": minimum_support,
            "max_deviation_ms": max_deviation_ms,
            "lead_budget_ms": lead_budget_ms,
        },
        "completed_cold_child_long": child_long,
        "supported_cold_child_long": supported_long,
        "selection": summarize(selected),
        "by_project": {
            project: summarize([
                row for row in selected if row["project"] == project
            ])
            for project in sorted({row["project"] for row in calls})
        },
        "limitation": (
            "Only finished tool calls are scored; no signal during the first "
            "four successful calls, censored calls or JOIN timing."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-legacy-origin", action="store_true")
    args = parser.parse_args()
    calls = [
        row for path in sorted(args.workflows.glob(
            "*/runtime_events.deepagents.jsonl"
        ))
        for row in _read_workflow(
            path, allow_legacy_origin=args.allow_legacy_origin,
        )
    ]
    result = screen(calls)
    result["legacy_origin_inferred"] = args.allow_legacy_origin
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
