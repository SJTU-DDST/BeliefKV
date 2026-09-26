#!/usr/bin/env python3
"""Evaluate a frozen final-chunk-to-RETURN timing prior across project splits."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median

if __package__:
    from scripts.audit_child_hidden_trace import index_workflow
else:
    from audit_child_hidden_trace import index_workflow


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[math.ceil(len(ordered) * fraction) - 1], 2)


def collect(roots: list[Path], *, require_signal: bool) -> dict:
    rows = []
    tasks = set()
    projects = set()
    child_returns = 0
    join_last_returns = 0
    invalid_signals = 0
    missing_results = 0
    for root in roots:
        paths = sorted(root.glob("*/runtime_events.deepagents.jsonl"))
        if not paths:
            raise FileNotFoundError(f"workflow events are absent from {root}")
        for path in paths:
            task_id = path.parent.name
            project = task_id.split("__", 1)[0]
            tasks.add(task_id)
            projects.add(project)
            with path.open(encoding="utf-8") as stream:
                events = [json.loads(line) for line in stream if line.strip()]
            terminal, join_last = index_workflow(events)
            terminal_by_child = {
                invocation_id: (rid, return_ts)
                for rid, (invocation_id, return_ts) in terminal.items()
            }
            child_ids = {
                event["target_invocation_id"]
                for event in events
                if event["kind"] == "spawn" and event.get("target_invocation_id")
            }
            returned = {
                event["invocation_id"]
                for event in events
                if event["kind"] == "return"
                and event.get("invocation_id") in child_ids
            }
            child_returns += len(returned)
            join_last_returns += len(join_last)
            results = defaultdict(list)
            tool_rids = set()
            signals = defaultdict(list)
            for event in events:
                attrs = event.get("attributes") or {}
                if event["kind"] == "llm_result" and event.get(
                    "invocation_id"
                ) in child_ids and not attrs.get("runtime_internal"):
                    results[event["invocation_id"]].append(event)
                elif event["kind"] == "structured_action":
                    if attrs.get("beliefkv_child_first_tool_chunk_shadow"):
                        tool_rids.add(attrs.get("request_id"))
                    if attrs.get("beliefkv_child_final_chunk_shadow"):
                        signals[attrs.get("request_id")].append(event)
            observed_rids = {
                (item.get("attributes") or {}).get("request_id")
                for items in results.values() for item in items
            }
            missing_results += sum(
                len(notices) for rid, notices in signals.items()
                if rid not in observed_rids
            )
            for invocation_id, items in results.items():
                for index, item in enumerate(items):
                    attrs = item.get("attributes") or {}
                    rid = attrs.get("request_id")
                    chunk_ts = attrs.get("stream_final_chunk_ts_ms")
                    if not isinstance(chunk_ts, (int, float)):
                        continue
                    if require_signal:
                        notices = signals.get(rid, [])
                        if not notices:
                            continue
                        if (
                            len(notices) != 1
                            or notices[0].get("invocation_id") != invocation_id
                            or abs(notices[0]["ts_ms"] - chunk_ts) > 1
                        ):
                            invalid_signals += len(notices)
                            continue
                    if (
                        attrs.get("finish_reason") != "stop"
                        or not (attrs.get("stream_content_counted_chars") or 0)
                        or rid in tool_rids
                    ):
                        if require_signal and rid in signals:
                            invalid_signals += len(signals[rid])
                        continue
                    confirmed = terminal_by_child.get(invocation_id)
                    if confirmed is not None and rid == confirmed[0]:
                        label = "true"
                        lead_ms = confirmed[1] - chunk_ts
                    elif index < len(items) - 1 or invocation_id in returned:
                        label = "false"
                        lead_ms = None
                    else:
                        label = "censored"
                        lead_ms = None
                    rows.append({
                        "task_id": task_id,
                        "project": project,
                        "label": label,
                        "lead_ms": lead_ms,
                        "control_saved_ms": item["ts_ms"] - chunk_ts,
                        "join_last": invocation_id in join_last and label == "true",
                    })
    return {
        "rows": rows,
        "tasks": tasks,
        "projects": projects,
        "child_returns": child_returns,
        "join_last_returns": join_last_returns,
        "invalid_signals": invalid_signals,
        "signal_without_result": missing_results,
    }


def evaluate(train: dict, heldout: dict) -> dict:
    if not train["projects"] or not heldout["projects"]:
        raise ValueError("both splits need workflow events")
    if train["projects"] & heldout["projects"]:
        raise ValueError("training and held-out projects overlap")
    if train["tasks"] & heldout["tasks"]:
        raise ValueError("training and held-out task IDs overlap")
    positive_by_task = defaultdict(list)
    for row in train["rows"]:
        if row["label"] == "true" and row["lead_ms"] >= 0:
            positive_by_task[row["task_id"]].append(row["lead_ms"])
    if not positive_by_task:
        raise ValueError("no natural child RETURN signal in training split")
    # Repeated observations of the same task must not dominate the prior.
    prior_ms = float(median([
        median(values) for values in positive_by_task.values()
    ]))
    positives = [
        row for row in heldout["rows"]
        if row["label"] == "true" and row["lead_ms"] >= 0
    ]
    false_count = sum(row["label"] == "false" for row in heldout["rows"])
    censored_count = sum(row["label"] == "censored" for row in heldout["rows"])
    errors = [abs(row["lead_ms"] - prior_ms) for row in positives]
    return {
        "diagnostic_only": True,
        "train_projects": sorted(train["projects"]),
        "heldout_projects": sorted(heldout["projects"]),
        "train_task_count": len(train["tasks"]),
        "train_positive_count": sum(len(v) for v in positive_by_task.values()),
        "train_unique_positive_tasks": len(positive_by_task),
        "frozen_train_median_lead_ms": round(prior_ms, 2),
        "heldout_child_returns": heldout["child_returns"],
        "heldout_join_last_returns": heldout["join_last_returns"],
        "heldout_true_candidates": len(positives),
        "heldout_false_candidates": false_count,
        "heldout_censored_candidates": censored_count,
        "heldout_invalid_signal_events": heldout["invalid_signals"],
        "heldout_signal_without_result": heldout["signal_without_result"],
        "heldout_join_last_true_candidates": sum(
            row["join_last"] for row in positives
        ),
        "heldout_at_least_500ms_early": sum(
            row["lead_ms"] >= 500 for row in positives
        ),
        "heldout_candidate_precision": (
            round(len(positives) / (len(positives) + false_count), 4)
            if positives or false_count else None
        ),
        "heldout_lead_p50_ms": quantile(
            [row["lead_ms"] for row in positives], 0.5
        ),
        "heldout_control_saved_p50_ms": quantile(
            [row["control_saved_ms"] for row in positives], 0.5
        ),
        "heldout_eta_error_p50_ms": quantile(errors, 0.5),
        "heldout_eta_error_p90_ms": quantile(errors, 0.9),
        "heldout_zero_prior_error_p50_ms": quantile(
            [row["lead_ms"] for row in positives], 0.5
        ),
        "heldout_within_500ms": (
            round(sum(error <= 500 for error in errors) / len(errors), 4)
            if errors else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-workflows", type=Path, action="append", required=True)
    parser.add_argument("--heldout-workflows", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(
        collect(args.train_workflows, require_signal=False),
        collect(args.heldout_workflows, require_signal=True),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
