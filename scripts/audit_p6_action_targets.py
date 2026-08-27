#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.predictor.action_targets import load_action_target_rows  # noqa: E402


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": ordered[(len(ordered) - 1) // 2],
        "p95": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "max": ordered[-1],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit schema-v4 operational action targets without fitting."
    )
    parser.add_argument("--action-target", type=Path, action="append", required=True)
    parser.add_argument("--source-report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = load_action_target_rows(args.action_target)
    reports = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.source_report
    ]
    contracts = {str(row.get("contract_id") or "") for row in rows}
    report_contracts = {str(item.get("contract_id") or "") for item in reports}
    if len(contracts) != 1 or contracts != report_contracts:
        raise SystemExit("action targets and source reports use different contracts")

    tau: defaultdict[str, list[float]] = defaultdict(list)
    known: Counter[str] = Counter()
    total: Counter[str] = Counter()
    episodes_by_command: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    workflows_by_command: defaultdict[str, set[str]] = defaultdict(set)
    projects_by_command: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        command = str(row.get("command_class") or "unknown")
        workflow = str(row.get("workflow_id") or "unknown")
        episode = str(row.get("tool_wait_episode_id") or "unknown")
        episodes_by_command[command].add((workflow, episode))
        workflows_by_command[command].add(workflow)
        projects_by_command[command].add(str(row.get("project") or "unknown"))
        for action, target in (row.get("actions") or {}).items():
            total[action] += 1
            tau[action].append(float(target["operational_tau_ms"]))
            known[action] += int(bool(target.get("outcome_known")))

    output = {
        "schema_version": 4,
        "contract_id": next(iter(contracts)),
        "splits": sorted({str(row.get("split") or "unknown") for row in rows}),
        "row_count": len(rows),
        "workflow_count": len(
            {str(row.get("workflow_id") or "unknown") for row in rows}
        ),
        "project_count": len(
            {str(row.get("project") or "unknown") for row in rows}
        ),
        "actions": {
            action: {
                "row_count": total[action],
                "known_outcome_count": known[action],
                "known_outcome_rate": known[action] / max(total[action], 1),
                "operational_tau_ms": _summary(tau[action]),
            }
            for action in sorted(total)
        },
        "command_class_support": {
            command: {
                "decision_row_count": sum(
                    1
                    for row in rows
                    if str(row.get("command_class") or "unknown") == command
                ),
                "tool_episode_count": len(episodes_by_command[command]),
                "workflow_count": len(workflows_by_command[command]),
                "project_count": len(projects_by_command[command]),
            }
            for command in sorted(episodes_by_command)
        },
        "sparse_command_classes": sorted(
            command
            for command, episodes in episodes_by_command.items()
            if len(episodes) < 4
        ),
        "source_target_paths": [str(path.resolve()) for path in args.action_target],
        "source_report_paths": [str(path.resolve()) for path in args.source_report],
        "online_eligibility": False,
        "online_ineligibility_reason": (
            "operational tau is byte-scaled from current-patch anchors; frozen "
            "semantic rows do not carry live physical extent morphology"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
