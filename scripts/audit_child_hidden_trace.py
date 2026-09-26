#!/usr/bin/env python3
"""Audit opt-in hidden-state snapshots against actual child RETURN/JOIN events."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np


def index_workflow(events: list[dict]) -> tuple[dict, dict]:
    children = {
        event["target_invocation_id"]
        for event in events
        if event["kind"] == "spawn" and event.get("target_invocation_id")
    }
    returns = {
        event["invocation_id"]: event["ts_ms"]
        for event in events
        if event["kind"] == "return" and event.get("invocation_id") in children
    }
    results = defaultdict(list)
    for event in events:
        if event["kind"] == "llm_result" and event.get("invocation_id") in children:
            results[event["invocation_id"]].append(event)
    terminal_rids = {}
    for invocation_id, returned_at in returns.items():
        prior = [
            event for event in results[invocation_id]
            if event["ts_ms"] <= returned_at
        ]
        if not prior:
            continue
        terminal = max(prior, key=lambda event: event["ts_ms"])
        attrs = terminal.get("attributes") or {}
        if (
            attrs.get("finish_reason") == "stop"
            and not attrs.get("tool_call_count")
            and attrs.get("output_chars", 0) > 0
            and attrs.get("request_id")
        ):
            terminal_rids[attrs["request_id"]] = (
                invocation_id, returned_at
            )
    join_last_children = set()
    groups = {
        event["join_id"]: event["member_invocation_ids"]
        for event in events
        if event["kind"] == "join_create" and event.get("join_id")
    }
    for event in events:
        if event["kind"] != "join_satisfied" or event.get("join_id") not in groups:
            continue
        members = groups[event["join_id"]]
        if not members or not all(member in returns for member in members):
            continue
        last = max(members, key=lambda member: returns[member])
        if abs(returns[last] - event["ts_ms"]) < 1:
            join_last_children.add(last)
    return terminal_rids, join_last_children


def summarize(
    traces: Path, workflow_root: Path,
) -> dict:
    indexed = {}
    all_child_results = set()
    natural_returns = 0
    for event_file in workflow_root.glob("*/runtime_events.deepagents.jsonl"):
        with event_file.open() as stream:
            events = [json.loads(line) for line in stream]
        terminal, join_last = index_workflow(events)
        natural_returns += sum(
            event["kind"] == "return"
            and str(event.get("invocation_id") or "").startswith(
                "deepagents-invocation:"
            )
            for event in events
        )
        for event in events:
            if event["kind"] != "llm_result":
                continue
            rid = (event.get("attributes") or {}).get("request_id")
            if rid and event.get("invocation_id", "").startswith(
                "deepagents-invocation:"
            ):
                all_child_results.add(rid)
        for rid, (invocation_id, return_ts) in terminal.items():
            indexed[rid] = (invocation_id, return_ts, invocation_id in join_last)

    first_leads = []
    last_leads = []
    last_join_leads = []
    all_matched = 0
    terminal_matched = 0
    invalid_finished = 0
    for path in traces.glob("*.npz"):
        with np.load(path, allow_pickle=False) as trace:
            rid = str(trace["rid"])
            if rid not in all_child_results:
                continue
            all_matched += 1
            if rid not in indexed:
                continue
            _, return_ts, join_last = indexed[rid]
            arrival = trace["arrival_ns"].astype(np.float64) / 1e6
            finish_ms = float(trace["finish_ns"]) / 1e6
            if not len(arrival) or finish_ms > return_ts:
                invalid_finished += 1
                continue
            terminal_matched += 1
            first_leads.append(return_ts - arrival[0])
            last_leads.append(return_ts - arrival[-1])
            if join_last:
                last_join_leads.append(return_ts - arrival[-1])

    def median(values: list[float]) -> float | None:
        return round(float(statistics.median(values)), 2) if values else None

    return {
        "child_llm_results": len(all_child_results),
        "matched_hidden_trace_rounds": all_matched,
        "natural_child_returns": natural_returns,
        "eligible_terminal_rids": len(indexed),
        "matched_terminal_rounds": terminal_matched,
        "invalid_finish_order": invalid_finished,
        "first_hidden_to_return_p50_ms": median(first_leads),
        "last_hidden_to_return_p50_ms": median(last_leads),
        "last_hidden_500ms_before_return": sum(
            bool(lead >= 500) for lead in last_leads
        ),
        "join_last_child_count": len(last_join_leads),
        "join_last_hidden_to_return_p50_ms": median(last_join_leads),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--workflows", type=Path, required=True)
    args = parser.parse_args()
    if not args.traces.is_dir() or not args.workflows.is_dir():
        parser.error("traces and workflow root must exist")
    print(json.dumps(summarize(args.traces, args.workflows), indent=2))


if __name__ == "__main__":
    main()
