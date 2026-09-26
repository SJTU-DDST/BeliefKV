#!/usr/bin/env python3
"""Evaluate observed pytest phase leads without claiming tool-return prediction."""

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
    leads: dict[str, list[float]] = defaultdict(list)
    incomplete = 0
    truncated = 0
    no_valid_count = 0
    for row in rows:
        events = row.get("recent_test_progress_events") or []
        stages = {
            stage: (float(at_ms), count, total)
            for at_ms, stage, count, total
            in row.get("first_test_progress_stages") or []
        }
        truncated += int(row.get("total_test_progress_events", 0) != len(events))
        if row.get("exit_code") == 124:
            incomplete += 1
            continue
        if row.get("test_progress_collection_count", 1) != 1:
            no_valid_count += 1
            continue
        totals = {event[3] for event in events if event[3] > 0}
        totals.update(value[2] for value in stages.values() if value[2] > 0)
        if len(totals) != 1:
            no_valid_count += 1
            continue
        total = totals.pop()
        completed = [
            event for event in events
            if event[1] == "test_done" and event[3] == total
        ]
        if not completed and "all_tests_done" not in stages:
            no_valid_count += 1
            continue
        elapsed = float(row["execute_elapsed_ms"])
        # The first crossing uses only counters available at that instant.
        for name, criterion in (
            ("ninety_percent_before_last", lambda done: 0 < done < total
             and done * 10 >= total * 9),
            ("all_tests_done", lambda done: done == total),
        ):
            saved = stages.get(name)
            if saved is not None and saved[2] == total and criterion(saved[1]):
                trigger = saved[0]
            else:
                # Without a saved first crossing, the recent window is safe
                # only if it includes the state before the threshold.
                if (name == "ninety_percent_before_last"
                    and (not completed or completed[0][2] * 10 > total * 9)):
                    continue
                trigger = next(
                    (float(event[0]) for event in completed
                     if criterion(event[2]) and float(event[0]) <= elapsed),
                    None,
                )
            if trigger is not None:
                leads[name].append(elapsed - trigger)
    return {
        "command_count": len(rows),
        "timeout_count": incomplete,
        "truncated_progress_count": truncated,
        "without_valid_progress_count": no_valid_count,
        "stages": {
            name: {
                "trigger_count": len(stage),
                "lead_p50_ms": median(stage) if stage else None,
                "lead_p90_ms": _quantile(stage, .9),
                "lead_at_least_500ms": sum(lead >= 500 for lead in stage),
                "lead_500_to_3000ms": sum(500 <= lead <= 3000 for lead in stage),
                "more_than_3000ms_early": sum(lead > 3000 for lead in stage),
            }
            for name, stage in (
                (stage, leads[stage])
                for stage in ("ninety_percent_before_last", "all_tests_done")
            )
        },
    }


def audit(workflows: Path) -> dict:
    by_project: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(workflows.glob("**/sandbox_audit.jsonl")):
        project = path.relative_to(workflows).parts[0].split("__", 1)[0]
        for row in _rows(path):
            if row.get("event") == "sandbox_execute" and row.get(
                "test_progress_shadow"
            ):
                by_project[project].append(row)
    if not by_project:
        raise ValueError("no opt-in pytest progress observations")
    return {
        "status": "read_only_test_progress_not_tool_return_eta",
        "all_commands": summarize([
            row for rows in by_project.values() for row in rows
        ]),
        "by_project": {
            project: summarize(rows) for project, rows in sorted(by_project.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflows", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.workflows)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
