#!/usr/bin/env python3
"""Summarize observed command durations and potential timeout truncation."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
from zoneinfo import ZoneInfo

import orjson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _quantile


CAPS_SECONDS = (30, 60, 90, 120, 180, 300, 600)


def frozen_rows(path: Path, size: int, marker: bytes):
    with path.open("rb") as stream:
        while stream.tell() < size:
            line = stream.readline(size - stream.tell())
            if not line.endswith(b"\n"):
                break
            if marker in line:
                yield orjson.loads(line)


def stats(rows: list[dict], field: str) -> dict:
    observed = [row for row in rows if isinstance(row.get(field), (int, float))]
    values = [row[field] / 1000. for row in observed]
    return {
        "count": len(observed),
        "workflow_count": len({row["workflow"] for row in observed}),
        "p50_s": _quantile(values, .5),
        "p90_s": _quantile(values, .9),
        "p95_s": _quantile(values, .95),
        "p99_s": _quantile(values, .99),
        "max_s": max(values) if values else None,
        "mean_s": statistics.mean(values) if values else None,
        "sum_command_seconds": sum(values),
        "above_cap": {
            str(cap): {
                "calls": sum(value > cap for value in values),
                "fraction": sum(value > cap for value in values) / len(values) if values else None,
                "workflows": len({
                    row["workflow"] for row in observed if row[field] > cap * 1000.
                }),
            }
            for cap in CAPS_SECONDS
        },
    }


def command_stats(rows: list[dict]) -> dict:
    return {
        "command_count": len(rows),
        "execution": stats(rows, "execute_elapsed_ms"),
        "lock_wait": stats(rows, "lock_wait_ms"),
        "sandbox_total": stats(rows, "duration_ms"),
        "exit_codes": dict(Counter(str(row.get("exit_code")) for row in rows)),
        "lock_wait_dominant_count": sum(
            row.get("lock_wait_ms", 0.) > row.get("execute_elapsed_ms", float("inf"))
            for row in rows
        ),
    }


def arm_report(arm: Path) -> dict:
    workflows = next(arm.glob("client_*/workflows"))
    manifest = json.loads((workflows.parent / "manifest.json").read_text())
    paths = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    audit_paths = sorted(workflows.glob("*/sandbox_audit.jsonl"))
    sizes = {path: path.stat().st_size for path in paths + audit_paths}
    commands, completed, unfinished = [], [], []
    match_counts = Counter()
    workflow_outcomes = {}
    for path in paths:
        workflow = path.parent.name
        result = path.parent / "result.json"
        workflow_outcomes[workflow] = (
            json.loads(result.read_text()).get("outcome", "unknown")
            if result.exists() else "not_terminal_at_read"
        )
        events = [
            row for row in frozen_rows(path, sizes[path], b'"tool_')
            if row.get("kind") in ("tool_start", "tool_end")
        ]
        events.sort(key=lambda row: (row["ts_ms"], row["sequence"]))
        starts, tools = {}, []
        for event in events:
            attrs = event.get("attributes") or {}
            call = attrs.get("tool_run_id") or attrs.get("tool_call_id")
            if call is None:
                continue
            if event["kind"] == "tool_start":
                starts[call] = event
            elif call in starts:
                start = starts.pop(call)
                row = {
                    "workflow": workflow,
                    "tool": attrs.get("tool_name"),
                    "class": start["attributes"].get("observed_command_class", "unknown"),
                    "is_child": start["attributes"].get("is_child"),
                    "input_sha256": start["attributes"].get("input_sha256"),
                    "tool_call_id": attrs.get("tool_call_id"),
                    "start_ts_ms": start["ts_ms"],
                    "terminal_ts_ms": event["ts_ms"],
                    "duration_ms": event["ts_ms"] - start["ts_ms"],
                    "status": attrs.get("status", "unknown"),
                    "tool_error_class": attrs.get("tool_error_class"),
                }
                tools.append(row)
                completed.append(row)
        unfinished.extend({
            "workflow": workflow, "tool": start["attributes"].get("tool_name"),
            "class": start["attributes"].get("observed_command_class", "unknown"),
            "start_ts_ms": start["ts_ms"],
        } for start in starts.values())
        audit_path = path.parent / "sandbox_audit.jsonl"
        if audit_path not in sizes:
            continue
        local_commands = [
            {**row, "workflow": workflow}
            for row in frozen_rows(audit_path, sizes[audit_path], b'"sandbox_execute"')
            if row.get("event") == "sandbox_execute"
        ]
        local_commands.sort(key=lambda row: row["ts_ms"])
        times = [row["ts_ms"] for row in local_commands]
        used = set()
        for tool in tools:
            if tool["tool"] != "execute":
                continue
            begin = bisect_left(times, tool["start_ts_ms"])
            end = bisect_right(times, tool["terminal_ts_ms"])
            candidates = [
                index for index in range(begin, end)
                if index not in used
                and tool["terminal_ts_ms"] - times[index] <= 200.
                and local_commands[index]["duration_ms"] <= tool["duration_ms"] + 2.
            ]
            if len(candidates) != 1:
                match_counts["ambiguous" if candidates else "unmatched"] += 1
                continue
            index = candidates[0]
            used.add(index)
            local_commands[index].update({
                "class": tool["class"], "is_child": tool["is_child"],
                "tool_status": tool["status"],
                "tool_error_class": tool["tool_error_class"],
                "tool_duration_ms": tool["duration_ms"],
                "tool_call_id": tool["tool_call_id"],
                "callback_gap_ms": tool["terminal_ts_ms"] - times[index],
            })
            match_counts["matched"] += 1
        for row in local_commands:
            row.setdefault("class", "unmatched")
        commands.extend(local_commands)
    natural = [
        row for row in commands
        if isinstance(row.get("exit_code"), int) and row["exit_code"] >= 0
        and row["exit_code"] not in (124, 137)
    ]
    successful = [row for row in natural if row["exit_code"] == 0]
    failed = [row for row in natural if row["exit_code"] != 0]
    candidates = [row for row in commands if row.get("exit_code") in (124, 137)]
    by_class, by_tool, by_workflow, by_project = (
        defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    )
    for row in commands:
        by_class[row["class"]].append(row)
        by_workflow[row["workflow"]].append(row)
        by_project[row["workflow"].split("__", 1)[0]].append(row)
    for row in completed:
        by_tool[row["tool"]].append(row)
    return {
        "arm": str(arm),
        "snapshot_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "scope": (
            "Completed observations read up to each file's captured byte length; "
            "the cross-file snapshot is not atomic. Unfinished calls are separate. "
            "Natural means completed with a nonnegative exit code other than "
            "124/137, not independently correct execution. Exit 137 may be a "
            "timeout, OOM or another SIGKILL; do not label it a proven timeout. "
            "Execution time excludes the per-workflow sandbox lock wait. "
            "Class matching uses a unique 200ms callback gap candidate, not "
            "command identity. Above-cap counts are hypothetical truncations "
            "of observed calls, not counterfactual JCT or throughput savings."
        ),
        "default_sandbox_timeout_s": manifest["config"]["sandbox_command_timeout_s"],
        "workflow_outcomes_at_read": dict(Counter(workflow_outcomes.values())),
        "matching": dict(match_counts),
        "all_sandbox_commands": command_stats(commands),
        "natural_completed": command_stats(natural),
        "successful_exit_0": command_stats(successful),
        "natural_nonzero_exit": command_stats(failed),
        "timeout_or_sigkill_candidates": command_stats(candidates),
        "by_command_class": {
            name: {
                "all": command_stats(rows),
                "successful": command_stats([row for row in rows if row.get("exit_code") == 0]),
                "natural_nonzero": command_stats([
                    row for row in rows
                    if isinstance(row.get("exit_code"), int) and row["exit_code"] > 0
                    and row["exit_code"] not in (124, 137)
                ]),
            } for name, rows in sorted(by_class.items())
        },
        "by_project": {name: command_stats(rows) for name, rows in sorted(by_project.items())},
        "callback_by_tool": {
            name: {"all": stats(rows, "duration_ms"),
                   "successful": stats([row for row in rows if row["status"] == "success"], "duration_ms")}
            for name, rows in sorted(by_tool.items())
        },
        "unfinished": unfinished,
        "top_execution_commands": sorted(
            commands, key=lambda row: row.get("execute_elapsed_ms", -1.), reverse=True,
        )[:30],
        "top_workflows_by_execution_time": [
            {
                "workflow": workflow, "outcome_at_read": workflow_outcomes[workflow],
                "stats": command_stats(rows),
                "repeated_commands": [
                    {
                        "command_sha256": signature,
                        "stats": command_stats([
                            row for row in rows if row["command_sha256"] == signature
                        ]),
                    }
                    for signature, count in Counter(
                        row["command_sha256"] for row in rows
                    ).most_common(5) if count > 1
                ],
            }
            for workflow, rows in sorted(
                by_workflow.items(),
                key=lambda pair: sum(row.get("execute_elapsed_ms", 0.) for row in pair[1]),
                reverse=True,
            )[:10]
        ],
        "source_files": [{"path": str(path), "bytes": size} for path, size in sizes.items()],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {}
    for specification in args.arm:
        name, path = specification.split("=", 1)
        report[name] = arm_report(Path(path))
        print(name, json.dumps({
            category: report[name][category]["execution"]
            for category in (
                "natural_completed", "successful_exit_0", "natural_nonzero_exit",
                "timeout_or_sigkill_candidates",
            )
        }, sort_keys=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
