#!/usr/bin/env python3
"""Read-only, project-isolated child execute timing pilot on completed tool pairs."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_native_execute_timing import _evaluate


def _child_calls(workflows: Path, *, allow_legacy_origin: bool) -> list[dict]:
    calls: list[dict] = []
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        starts: dict[str, dict] = {}
        for line in path.open("rb"):
            if not line.strip():
                continue
            event = orjson.loads(line)
            attrs = event.get("attributes") or {}
            if attrs.get("tool_name") != "execute":
                continue
            call_id = str(attrs.get("tool_call_id") or "")
            if not call_id:
                continue
            if event["kind"] == "tool_start":
                starts[call_id] = event
            elif event["kind"] == "tool_end" and call_id in starts:
                start = starts.pop(call_id)
                origin = start["attributes"].get("is_child")
                if origin is None and allow_legacy_origin:
                    origin = str(start.get("invocation_id") or "").startswith(
                        "deepagents-invocation:"
                    )
                if origin is not True:
                    continue
                duration = float(event["ts_ms"]) - float(start["ts_ms"])
                if duration < 0:
                    raise ValueError(f"negative tool duration in {path}")
                calls.append({
                    "project": str(path.parent.name.split("__", 1)[0]),
                    "workflow": str(event["workflow_id"]),
                    "class": str(
                        start["attributes"].get("observed_command_class")
                        or "unknown"
                    ),
                    "duration_ms": duration,
                })
    return calls


def audit(
    train_workflows: Path,
    evaluation_workflows: Path,
    *,
    minimum_class_samples: int = 8,
    allow_legacy_evaluation_origin: bool = False,
) -> dict:
    if minimum_class_samples < 1:
        raise ValueError("minimum class support must be positive")
    train = _child_calls(train_workflows, allow_legacy_origin=False)
    evaluation = _child_calls(
        evaluation_workflows, allow_legacy_origin=allow_legacy_evaluation_origin
    )
    train_projects = {row["project"] for row in train}
    eval_projects = {row["project"] for row in evaluation}
    if train_projects & eval_projects:
        raise ValueError("train/evaluation projects overlap")
    if not train or not evaluation:
        raise ValueError("no completed child execute pairs on either side")
    grouped: defaultdict[str, list[float]] = defaultdict(list)
    for row in train:
        grouped[row["class"]].append(row["duration_ms"])
    overall = median(row["duration_ms"] for row in train)
    medians = {
        name: median(durations)
        for name, durations in grouped.items()
        if len(durations) >= minimum_class_samples
    }
    return {
        "status": "offline_project_isolated_pilot_not_deployable",
        "train_completed_child_calls": len(train),
        "evaluation_completed_child_calls": len(evaluation),
        "train_projects": sorted(train_projects),
        "evaluation_projects": sorted(eval_projects),
        "train_class_counts": {
            name: len(durations) for name, durations in sorted(grouped.items())
        },
        "training_global_median_ms": overall,
        "trained_class_medians_ms": dict(sorted(medians.items())),
        "evaluation": _evaluate(evaluation, medians, overall),
        "note": (
            "Completed calls only; legacy origin inference is allowed only on "
            "the evaluation side when explicitly requested. This is not an "
            "online, pressure-matched, independently calibrated Frontier model."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, required=True)
    parser.add_argument("--evaluation-workflows", type=Path, required=True)
    parser.add_argument("--allow-legacy-evaluation-origin", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(
        args.train_workflows,
        args.evaluation_workflows,
        allow_legacy_evaluation_origin=args.allow_legacy_evaluation_origin,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
