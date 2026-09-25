#!/usr/bin/env python3
"""Read-only TOOL_START lower-bound audit using in-flight project peers."""

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
from scripts.audit_repeated_tool_timing import _read_workflow, _summarize


def replay(rows: list[dict], *, minimum_peers: int = 4) -> dict:
    if minimum_peers < 1:
        raise ValueError("minimum_peers must be positive")
    eligible = [
        row for row in rows
        if row["is_child"] is True
        and (row["previous"] is None or row["previous"][2] != "success")
        and type(row["project_class_completed_support"]) is int
        and row["project_class_completed_support"] < 16
    ]
    selected = [
        row for row in eligible
        if type(row["other_workflow_2s_peers"]) is int
        and row["other_workflow_2s_peers"] >= minimum_peers
    ]
    true = [row for row in selected if row["duration_ms"] >= 2000]
    long = [row for row in eligible if row["duration_ms"] >= 2000]
    by_project = defaultdict(list)
    for row in selected:
        by_project[row["project"]].append(row)
    return {
        "status": "read_only_tool_start_lower_bound_not_point_eta",
        "minimum_other_workflow_2s_peers": minimum_peers,
        "eligible_completed_cold_child": len(eligible),
        "eligible_true_at_least_2s": len(long),
        "predicted_at_least_2s": len(selected),
        "correct_at_least_2s": len(true),
        "false_positive": len(selected) - len(true),
        "precision": len(true) / len(selected) if selected else None,
        "recall": len(true) / len(long) if long else None,
        "predicted_workflows": len({row["workflow"] for row in selected}),
        "true_duration_p50_ms": (
            median(row["duration_ms"] for row in true) if true else None
        ),
        "constant_2s_point_error_true_positive": _summarize([
            {**row, "error_ms": abs(row["duration_ms"] - 2000)}
            for row in true
        ]),
        "by_project": {
            project: {
                "selected": len(items),
                "correct": sum(row["duration_ms"] >= 2000 for row in items),
                "workflow_count": len({row["workflow"] for row in items}),
            }
            for project, items in sorted(by_project.items())
        },
        "limitation": (
            "TOOL_START survival lower bound only; not an exact return ETA. "
            "Completed paired calls only; open waits are censored, and no "
            "physical H2D or control-chain time is measured."
        ),
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
