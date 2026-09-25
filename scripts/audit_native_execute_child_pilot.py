#!/usr/bin/env python3
"""Check root-trained command-duration medians on new completed child tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_native_execute_timing import _evaluate


def audit(workflows: Path, model_report: Path):
    prior = json.loads(model_report.read_text(encoding="utf-8"))
    if prior.get("status") != "offline_exploration_not_deployable":
        raise ValueError("expected diagnostic-only training medians")
    medians = prior["class_median_ms"]
    global_median = float(prior["global_median_ms"])
    grouped = {"child": [], "root": []}
    unpaired = 0
    for path in sorted(workflows.glob("*/runtime_events.deepagents.jsonl")):
        project = path.parent.name.split("__", 1)[0]
        starts = {}
        with path.open("rb") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = orjson.loads(line)
                attrs = event.get("attributes") or {}
                if attrs.get("tool_name") != "execute":
                    continue
                call_id = attrs.get("tool_call_id")
                if not call_id:
                    continue
                if event["kind"] == "tool_start":
                    starts[call_id] = event
                elif event["kind"] == "tool_end":
                    start = starts.pop(call_id, None)
                    if start is None:
                        unpaired += 1
                        continue
                    duration = float(event["ts_ms"]) - float(start["ts_ms"])
                    if duration < 0:
                        raise ValueError("negative tool duration")
                    child = (start.get("attributes") or {}).get("is_child")
                    if child is None:
                        child = str(start["invocation_id"]).startswith(
                            "deepagents-invocation:"
                        )
                    grouped["child" if child else "root"].append({
                        "project": project,
                        "workflow": event["workflow_id"],
                        "class": (
                            (start.get("attributes") or {}).get(
                                "observed_command_class"
                            ) or "execute"
                        ),
                        "duration_ms": duration,
                    })
        unpaired += len(starts)
    return {
        "status": "offline_child_command_diagnostic_only",
        "unpaired_execute_calls": unpaired,
        "child": _evaluate(grouped["child"], medians, global_median),
        "root": _evaluate(grouped["root"], medians, global_median),
        "note": "Small, low-pressure pilot, not an independent full-workload test.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--model-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.workflows, args.model_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
