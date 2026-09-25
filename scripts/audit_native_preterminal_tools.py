#!/usr/bin/env python3
"""Measure how often a child TOOL_END precedes its final model response."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _summary(values: list[float]):
    values.sort()
    return {
        "count": len(values),
        "p50_ms": values[(len(values) - 1) // 2] if values else None,
        "p90_ms": values[math.ceil(.9 * len(values)) - 1] if values else None,
        "at_least_500ms": sum(value >= 500 for value in values),
        "at_least_2000ms": sum(value >= 2000 for value in values),
    }


def audit(root: Path, workflows: Path):
    targets = set()
    for row in _rows(root / "reentries.jsonl"):
        if (
            row.get("reentry_kind") == "join"
            and row.get("terminal_status") == "satisfied"
            and row.get("training_eligible") is True
        ):
            for member in row.get("member_outcomes") or ():
                targets.add((
                    str(row["workflow_id"]), str(member["invocation_id"])
                ))
    by_child = defaultdict(list)
    for path in workflows.glob("*/runtime_events.deepagents.jsonl"):
        for event in _rows(path):
            key = (
                str(event.get("workflow_id") or ""),
                str(event.get("invocation_id") or ""),
            )
            if key in targets:
                by_child[key].append(event)
    leads = []
    candidates = 0
    positive = 0
    for events in by_child.values():
        events.sort(key=lambda event: float(event.get("ts_ms") or 0))
        for index, event in enumerate(events):
            if event.get("kind") != "tool_end":
                continue
            candidates += 1
            next_result = next((
                later for later in events[index + 1:]
                if later.get("kind") in {
                    "llm_result", "tool_start", "return", "invocation_cancel"
                }
            ), None)
            if next_result is None or next_result.get("kind") != "llm_result":
                continue
            attrs = next_result.get("attributes") or {}
            if (
                attrs.get("runtime_internal") is True
                or attrs.get("tool_call_count") != 0
                or attrs.get("invalid_tool_call_count", 0) != 0
                or attrs.get("finish_reason") not in (None, "stop")
                or not isinstance(attrs.get("output_chars"), int)
                or attrs["output_chars"] <= 0
            ):
                continue
            successor = next((
                later for later in events
                if float(later.get("ts_ms") or 0) > float(next_result["ts_ms"])
                and later.get("kind") in {
                    "llm_submit", "tool_start", "return", "invocation_cancel"
                }
            ), None)
            if successor is None or successor.get("kind") != "return":
                continue
            positive += 1
            leads.append(
                float(successor["ts_ms"]) - float(event["ts_ms"])
            )
    return {
        "completed_children": len(targets),
        "tool_end_candidates": candidates,
        "next_model_response_returns": positive,
        "candidate_precision": positive / candidates if candidates else None,
        "lead_for_positive": _summary(leads),
        "qualification": (
            "Post-hoc next-result label on completed children, not an online "
            "prediction; excludes canceled child false positives."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--workflow-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.dataset_dir, args.workflow_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
