#!/usr/bin/env python3
"""Audit causal in-flight project peers as a cold-start long-tool signal."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

import orjson


def _rows(path: Path):
    with path.open("rb") as stream:
        for line in stream:
            if line.strip():
                yield orjson.loads(line)


def audit(dataset: Path) -> dict:
    waits = {}
    for row in _rows(dataset / "external_waits.jsonl"):
        if row.get("tool_name") != "execute" or row.get("is_child") is not True:
            continue
        key = str(row.get("workflow_id")), str(row.get("tool_call_id"))
        waits[key] = row
    attrs = {}
    for row in _rows(dataset / "frontier_decision_points.jsonl"):
        if row.get("trigger_kind") != "tool_start":
            continue
        details = row.get("trigger_attributes") or {}
        key = str(row.get("workflow_id")), str(details.get("tool_call_id"))
        wait = waits.get(key)
        if (
            wait is not None and key not in attrs
            and row.get("trigger_invocation_id") == wait.get("invocation_id")
            and details.get("is_child") is True
        ):
            attrs[key] = details
    by_class = defaultdict(list)
    for key, row in waits.items():
        project = str(row.get("project") or "")
        command = str(row.get("observed_command_class") or "")
        start, end = row.get("start_ts_ms"), row.get("terminal_ts_ms")
        if (
            not project or not command
            or type(start) not in (int, float)
            or not math.isfinite(start)
            or type(end) not in (int, float)
            or not math.isfinite(end)
            or end < start
        ):
            continue
        by_class[project, command].append((float(start), float(end), key))
    checks = defaultdict(lambda: defaultdict(
        lambda: {"samples": 0, "true_long": 0, "predicted_long": 0,
                 "correct_long": 0, "workflows": set()}
    ))
    for (project, _command), entries in by_class.items():
        entries.sort()
        for start, end, key in entries:
            row = waits[key]
            if (
                row.get("training_eligible_survival") is not True
                or row.get("censored") is True or key not in attrs
            ):
                continue
            details = attrs[key]
            if (
                details.get("previous_same_input_status") == "success"
                or type(details.get("project_class_duration_median_ms"))
                in (int, float)
                and int(details.get("project_class_completed_support") or 0) >= 16
            ):
                continue
            for clock in (0, 500):
                now = start + clock
                if now >= end:
                    continue
                other_survivors = sum(
                    previous_start <= now - 2_000
                    and previous_end > now and previous_key != key
                    for previous_start, previous_end, previous_key in entries
                    if previous_start <= now
                )
                actual_long = end - now >= 2_000
                for minimum in (1, 4, 8):
                    predicted = other_survivors >= minimum
                    for group_name in ("all", project):
                        entry = checks[
                            f"elapsed_{clock}ms"
                        ][f"{group_name}:peers_{minimum}"]
                        entry["samples"] += 1
                        entry["true_long"] += actual_long
                        entry["predicted_long"] += predicted
                        entry["correct_long"] += predicted and actual_long
                        entry["workflows"].add(key[0])
    return {
        "status": "offline_as_of_survival_pilot_not_action_eligible",
        "semantics": (
            "same-project same-command child execute, without successful same-input "
            "or supported completed-project prior; only already-started and "
            "currently-unfinished peers aged at least 2s count; completed, "
            "uncensored targets only"
        ),
        "groups": {
            clock: {
                name: {
                    **{key: value for key, value in group.items()
                       if key != "workflows"},
                    "workflow_count": len(group["workflows"]),
                    "precision": (
                        group["correct_long"] / group["predicted_long"]
                        if group["predicted_long"] else None
                    ),
                    "recall": (
                        group["correct_long"] / group["true_long"]
                        if group["true_long"] else None
                    ),
                }
                for name, group in groups.items()
            }
            for clock, groups in checks.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.dataset_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
