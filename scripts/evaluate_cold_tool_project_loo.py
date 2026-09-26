#!/usr/bin/env python3
"""Read-only project-held-out evaluation on a completed cold-tool batch."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.evaluate_cold_tool_structure_holdout import cold_calls, evaluate
from scripts.pilot_cold_child_tool_long import shape_transfer_peer_ablation


def require_complete_batch(workflows: Path) -> tuple[list[str], list[str]]:
    run = workflows.parent
    manifest = run / "manifest.json"
    summary = run / "summary.json"
    if not manifest.is_file() or not summary.is_file():
        raise ValueError("batch must have a final manifest and summary")
    ids = json.loads(manifest.read_text(encoding="utf-8")).get("instance_ids")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise ValueError("batch manifest has missing or duplicate instance IDs")
    recorded = json.loads(summary.read_text(encoding="utf-8"))
    if recorded.get("workflow_count") != len(ids):
        raise ValueError("batch summary does not cover the frozen tasks")
    items = recorded.get("workflows")
    if not isinstance(items, list) or len(items) != len(ids):
        raise ValueError("batch summary lacks terminal workflow records")
    by_id = {item.get("instance_id"): item for item in items}
    if len(by_id) != len(ids) or set(by_id) != set(ids):
        raise ValueError("batch summary identities differ from frozen tasks")
    runner_errors = [
        instance for instance in ids
        if by_id[instance].get("outcome") == "runner_error"
    ]
    missing = [
        instance for instance in ids
        if not (workflows / instance / "result.json").is_file()
        and instance not in runner_errors
    ]
    if missing:
        raise ValueError(f"batch lacks workflow results: {missing[:5]}")
    return ids, runner_errors


def project_leave_one_out(
    rows: list[dict], *, peer_ablation: bool = False,
) -> dict:
    projects = sorted({row["project"] for row in rows})
    if len(projects) < 3:
        raise ValueError("project holdout requires at least three projects")
    folds = {}
    for project in projects:
        test = [row for row in rows if row["project"] == project]
        train = [row for row in rows if row["project"] != project]
        long = [row for row in test if row["duration_ms"] >= 2_000]
        folds[project] = {
            "heldout_calls": len(test),
            "heldout_long": len(long),
            "heldout_long_workflows": len({row["workflow"] for row in long}),
            "report": evaluate(train, test),
        }
        if peer_ablation:
            folds[project]["peer_ablation"] = shape_transfer_peer_ablation(
                train, test,
            )
    supported = [
        project for project, fold in folds.items()
        if fold["heldout_long"] >= 5 and fold["heldout_long_workflows"] >= 5
    ]
    return {
        "status": "read_only_project_leave_one_out_not_online",
        "projects": projects,
        "folds": folds,
        "supported_projects": supported,
        "evidence_gates": {
            "two_supported_heldout_projects": len(supported) >= 2,
            "every_supported_project_passed": (
                bool(supported) and all(
                    folds[project]["report"]["evidence_gates"].get("all_met", False)
                    for project in supported
                )
            ),
        },
        "note": (
            "All projects and insufficient-support folds are reported. Each fold "
            "fits and selects thresholds without its held-out project. These "
            "overlapping training folds are not independent trials; duration "
            "labels describe completed TOOL_START-to-TOOL_END intervals, not "
            "physical prefetch benefit or JOIN time."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--peer-ablation", action="store_true",
        help="Compare frozen project-CV long-call gates with and without live peers.",
    )
    args = parser.parse_args()
    instance_ids, runner_errors = require_complete_batch(args.workflows)
    rows, censor = cold_calls(args.workflows)
    result = project_leave_one_out(rows, peer_ablation=args.peer_ablation)
    result["frozen_workflow_count"] = len(instance_ids)
    result["runner_error_workflows"] = runner_errors
    result["censor"] = censor
    result["long_by_project"] = dict(sorted(Counter(
        row["project"] for row in rows if row["duration_ms"] >= 2_000
    ).items()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "supported_projects": result["supported_projects"],
        "evidence_gates": result["evidence_gates"],
        "long_by_project": result["long_by_project"],
    }, indent=2))


if __name__ == "__main__":
    main()
