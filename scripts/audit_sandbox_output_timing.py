#!/usr/bin/env python3
"""Audit when observable sandbox stdout first arrives before command exit."""

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
from scripts.audit_native_stream_shadow import _quantile, _rows


def summarize(rows: list[dict]) -> dict:
    observed = [
        row for row in rows
        if row.get("first_output_after_execute_ms") is not None
    ]
    leads = [
        max(0.0, float(row["execute_elapsed_ms"])
            - float(row["first_output_after_execute_ms"]))
        for row in observed
    ]
    return {
        "command_count": len(rows),
        "nonempty_output_count": len(observed),
        "no_output_count": len(rows) - len(observed),
        "host_timeout_count": sum(int(row.get("exit_code") == 124) for row in rows),
        "first_output_to_exit_p50_ms": median(leads) if leads else None,
        "first_output_to_exit_p90_ms": _quantile(leads, .9),
        "first_output_at_least_500ms_before_exit": sum(
            lead >= 500 for lead in leads
        ),
        "first_output_at_least_2000ms_before_exit": sum(
            lead >= 2000 for lead in leads
        ),
        "first_output_within_500ms_of_exit": sum(
            lead < 500 for lead in leads
        ),
    }


def audit(workflows: Path) -> dict:
    traces = list(sorted(workflows.glob("**/sandbox_audit.jsonl")))
    if not traces:
        raise ValueError("no sandbox audit traces")
    by_project = defaultdict(list)
    rows = []
    for path in traces:
        project = path.relative_to(workflows).parts[0].split("__", 1)[0]
        for row in _rows(path):
            if row.get("event") != "sandbox_execute" or not row.get(
                "output_timing_shadow"
            ):
                continue
            rows.append(row)
            by_project[project].append(row)
    if not rows:
        raise ValueError("no opt-in stdout timing observations")
    return {
        "status": "read_only_sandbox_stdout_timing_no_physical_actions",
        "all_commands": summarize(rows),
        "long_commands_at_least_2s": summarize([
            row for row in rows if float(row["execute_elapsed_ms"]) >= 2000
        ]),
        "by_project_long_commands": {
            project: summarize([
                row for row in project_rows
                if float(row["execute_elapsed_ms"]) >= 2000
            ])
            for project, project_rows in sorted(by_project.items())
        },
        "limitation": (
            "First byte receipt is a causal observation, not a guaranteed "
            "near-return marker. Timestamp precedes exit only if the host "
            "read it while the command was still running. No tool/JOIN "
            "beneficiary or DMA transfer is implied."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
