#!/usr/bin/env python3
"""Audit recorded root test results without mistaking setup exits for test work."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from beliefkv.experiments.test_tool_evidence import is_test_command, result_evidence


def audit(client):
    inputs = {}

    def load(path, *, lines=False):
        raw = path.read_bytes()
        inputs[str(path)] = {
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        }
        return [json.loads(line) for line in raw.splitlines()] if lines else json.loads(raw)

    manifest = load(client / "manifest.json")
    rows, all_tools, joins, root_fanout = [], Counter(), Counter(), Counter()
    for trajectory in sorted((client / "workflows").glob("*/trajectory.json")):
        root = trajectory.parent
        transcript = load(trajectory)
        events = load(root / "runtime_events.deepagents.jsonl", lines=True)
        audit_rows = load(root / "sandbox_audit.jsonl", lines=True)
        tool_ends = {
            row["attributes"]["tool_call_id"]: row for row in events
            if row["kind"] == "tool_end"
        }
        for event in events:
            if event["kind"] == "join_create":
                joins[len(event["member_invocation_ids"])] += 1
            elif event["kind"] == "tool_start":
                all_tools[event["attributes"]["tool_name"]] += 1
        commands = defaultdict(list)
        for row in audit_rows:
            if row.get("event") == "sandbox_execute":
                commands[row["command_sha256"]].append(row)
        calls = {}
        used = set()
        for index, message in enumerate(transcript):
            task_calls = [call for call in message.get("tool_calls", ()) if call["name"] == "task"]
            if task_calls:
                root_fanout[len(task_calls)] += 1
            for call in message.get("tool_calls", ()):
                calls[call["id"]] = call
            call = calls.get(message.get("tool_call_id"))
            if not call or call["name"] != "execute":
                continue
            command = call.get("args", {}).get("command", "")
            if not is_test_command(command):
                continue
            output = message["content"]
            if not isinstance(output, str):
                raise ValueError("Expected serialized textual tool output")
            digest = hashlib.sha256(command.encode()).hexdigest()
            end = tool_ends.get(call["id"])
            candidates = [
                row for row in commands[digest] if row["sequence"] not in used
                and end is not None and abs(row["ts_ms"] - end["ts_ms"]) < 500.
            ]
            physical = min(
                candidates, key=lambda row: abs(row["ts_ms"] - end["ts_ms"]),
            ) if candidates else None
            if physical:
                used.add(physical["sequence"])
            exit_code = physical["exit_code"] if physical else None
            rows.append({
                "workflow": root.name, "trajectory_message_index": index,
                "tool_call_id": call["id"], "command": command,
                "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                "exit_code": exit_code,
                "sandbox_execute_ms": physical["execute_elapsed_ms"] if physical else None,
                "sandbox_lock_wait_ms": physical["lock_wait_ms"] if physical else None,
                **result_evidence(output, exit_code),
            })
    return {
        "declared_delegation_contract": manifest["delegation_contract"],
        "join_member_histogram": dict(sorted(joins.items())),
        "root_model_response_task_fanout": dict(sorted(root_fanout.items())),
        "all_recorded_tool_counts_including_children": dict(all_tools),
        "root_test_command_count": len(rows),
        "root_test_result_statuses": dict(Counter(row["status"] for row in rows)),
        "root_django_configuration_exits": sum(row["django_configuration_error"] for row in rows),
        "root_test_commands_with_physical_duration": sum(
            row["sandbox_execute_ms"] is not None for row in rows
        ),
        "root_test_commands": rows, "inputs": inputs,
        "scope": [
            "Test result bodies cover the saved root conversation only; child outputs may be absent.",
            "All-runtime tool counts and JOIN membership also include descendants.",
            "Reported passed/failed calls are separate from collection and fixture errors.",
            "Saved textual summaries are diagnostic evidence, not a replacement for JUnit qualification.",
            "No source duration, prompt, project code, or frozen comparison is changed.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier audits")
    report = audit(args.client.resolve())
    report["auditor_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["diagnostics_implementation_sha256"] = hashlib.sha256((
        Path(__file__).resolve().parents[1] / "beliefkv/experiments/test_tool_evidence.py"
    ).read_bytes()).hexdigest()
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: value for key, value in report.items()
        if key not in ("root_test_commands", "inputs", "scope")
    }, indent=2))


if __name__ == "__main__":
    main()
