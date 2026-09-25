#!/usr/bin/env python3
"""Audit when observable sandbox stdout first arrives before command exit."""

from __future__ import annotations

import argparse
from bisect import bisect_left
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


def _tool_ends(path: Path) -> tuple[list[float], list[dict]]:
    trace = path.parent / "runtime_events.deepagents.jsonl"
    if not trace.exists():
        return [], []
    starts = {}
    ends = []
    for event in _rows(trace):
        attrs = event.get("attributes") or {}
        call_id = attrs.get("tool_call_id")
        if not call_id or attrs.get("tool_name") != "execute":
            continue
        if event.get("kind") == "tool_start":
            starts[call_id] = (
                float(event["ts_ms"]),
                str(attrs.get("observed_command_shape") or "unknown"),
            )
        elif event.get("kind") == "tool_end" and call_id in starts:
            start, shape = starts.pop(call_id)
            ends.append({
                "ts_ms": float(event["ts_ms"]),
                "duration_ms": float(event["ts_ms"]) - start,
                "shape": shape,
                "status": attrs.get("status"),
            })
    ends.sort(key=lambda event: event["ts_ms"])
    return [event["ts_ms"] for event in ends], ends


def audit(workflows: Path) -> dict:
    traces = list(sorted(workflows.glob("**/sandbox_audit.jsonl")))
    if not traces:
        raise ValueError("no sandbox audit traces")
    by_project = defaultdict(list)
    by_shape_long = defaultdict(list)
    matched = ambiguous = 0
    rows = []
    for path in traces:
        project = path.relative_to(workflows).parts[0].split("__", 1)[0]
        times, tool_ends = _tool_ends(path)
        used = set()
        for row in _rows(path):
            if row.get("event") != "sandbox_execute" or not row.get(
                "output_timing_shadow"
            ):
                continue
            rows.append(row)
            by_project[project].append(row)
            ended = float(row["ts_ms"])
            index = bisect_left(times, ended)
            choices = []
            while index < len(times) and times[index] - ended <= 200:
                end = tool_ends[index]
                if index not in used and abs(
                    end["duration_ms"] - float(row["duration_ms"])
                ) <= max(250, .15 * float(row["duration_ms"])):
                    choices.append(index)
                index += 1
            if len(choices) == 1:
                chosen = choices[0]
                used.add(chosen)
                matched += 1
                if float(row["execute_elapsed_ms"]) >= 2000:
                    by_shape_long[tool_ends[chosen]["shape"]].append(row)
            elif len(choices) > 1:
                ambiguous += 1
    if not rows:
        raise ValueError("no opt-in stdout timing observations")
    return {
        "status": "read_only_sandbox_stdout_timing_no_physical_actions",
        "all_commands": summarize(rows),
        "long_commands_at_least_2s": summarize([
            row for row in rows if float(row["execute_elapsed_ms"]) >= 2000
        ]),
        "matched_tool_end_count": matched,
        "ambiguous_tool_end_count": ambiguous,
        "by_shape_long_commands_matched": {
            shape: summarize(shape_rows)
            for shape, shape_rows in sorted(by_shape_long.items())
        },
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
