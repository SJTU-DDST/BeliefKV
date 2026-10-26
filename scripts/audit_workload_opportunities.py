#!/usr/bin/env python3
"""Describe observed tool/JOIN blocking without inferring cold KV or future work."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from beliefkv.core.events import (
    RuntimeEvent, RuntimeEventKind as Kind, is_unobserved_join_event,
)
from scripts.audit_workflow_service import distribution, merge


METADATA_TOOLS = frozenset({
    "write_todos", "announce_completion_intent", "close_agent",
    "ChildCompletion", "WorkflowCompletion",
})


def overlap_ms(left, right):
    left, right = merge(left), merge(right)
    first = second = 0
    total = 0.
    while first < len(left) and second < len(right):
        start = max(left[first][0], right[second][0])
        stop = min(left[first][1], right[second][1])
        total += max(0., stop - start)
        if left[first][1] <= right[second][1]:
            first += 1
        else:
            second += 1
    return total


def analyze(events):
    events = sorted(events, key=lambda event: event.ts_ms)
    if not events or any(not math.isfinite(event.ts_ms) for event in events):
        raise ValueError("finite runtime events are required")
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("runtime event IDs must be unique")
    agents, arrivals, terminals = {}, {}, {}
    tools, joins, models = {}, {}, {}
    tool_rows, join_rows, model_rows = [], [], []
    unmatched = Counter()
    inferred_join_events = 0
    join_fanout = Counter()
    result_tool_rounds = Counter()
    for event in events:
        owner = event.workflow_id, event.invocation_id
        attrs = event.attributes
        if event.kind is Kind.WORKFLOW_START:
            if event.workflow_id in arrivals:
                raise ValueError("duplicate workflow arrival")
            arrivals[event.workflow_id] = event.ts_ms
        elif event.kind is Kind.WORKFLOW_END:
            if event.workflow_id in terminals:
                raise ValueError("duplicate workflow terminal")
            terminals[event.workflow_id] = event.ts_ms
        elif event.kind is Kind.INVOCATION_CREATE and not attrs.get("runtime_internal"):
            if owner in agents:
                raise ValueError("duplicate invocation identity")
            agents[owner] = {
                "parent": event.parent_invocation_id, "start": event.ts_ms, "end": None,
            }
        elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL) and owner in agents:
            agents[owner]["end"] = event.ts_ms
        elif event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
            key = event.workflow_id, attrs.get("request_id")
            if key[1] is None:
                unmatched["model_missing_request_id"] += 1
                continue
            if event.kind is Kind.LLM_SUBMIT:
                if key in models:
                    raise ValueError("duplicate in-flight model identity")
                models[key] = event
            else:
                start = models.pop(key, None)
                if start is None:
                    unmatched["model_end_without_start"] += 1
                    continue
                if start.invocation_id != event.invocation_id:
                    raise ValueError("model completion changed invocation")
                internal = bool(start.attributes.get("runtime_internal"))
                model_rows.append((owner, start.ts_ms, event.ts_ms, internal))
                if not internal and attrs.get("tool_call_count", 0):
                    result_tool_rounds[owner] += 1
        elif event.kind in (Kind.TOOL_START, Kind.TOOL_END):
            identity = attrs.get("tool_run_id") or attrs.get("tool_call_id")
            if identity is None:
                unmatched["tool_missing_identity"] += 1
                continue
            key = *owner, identity
            if event.kind is Kind.TOOL_START:
                if key in tools:
                    raise ValueError("duplicate in-flight tool identity")
                tools[key] = event
            else:
                start = tools.pop(key, None)
                if start is None:
                    unmatched["tool_end_without_start"] += 1
                    continue
                name = start.attributes.get("tool_name", "unknown")
                tool_rows.append({
                    "owner": owner, "name": name, "start": start.ts_ms,
                    "end": event.ts_ms, "status": attrs.get("status", "unknown"),
                    "metadata": name in METADATA_TOOLS,
                })
        elif event.kind in (
            Kind.JOIN_CREATE, Kind.JOIN_WAIT, Kind.JOIN_SATISFIED, Kind.JOIN_TIMEOUT,
        ):
            if is_unobserved_join_event(event):
                inferred_join_events += 1
                continue
            key = event.workflow_id, event.join_id
            if event.kind is Kind.JOIN_CREATE:
                join_fanout[len(event.member_invocation_ids)] += 1
            elif event.kind is Kind.JOIN_WAIT:
                if key in joins:
                    raise ValueError("duplicate in-flight JOIN wait")
                joins[key] = event
            else:
                start = joins.pop(key, None)
                if start is None:
                    unmatched["join_end_without_wait"] += 1
                    continue
                join_rows.append((
                    (start.workflow_id, start.invocation_id), start.ts_ms, event.ts_ms,
                ))
    unmatched.update({
        "unfinished_tools": len(tools), "unfinished_joins": len(joins),
        "unfinished_models": len(models),
    })
    model_by_owner, tool_by_owner, join_by_owner = (
        defaultdict(list), defaultdict(list), defaultdict(list)
    )
    for owner, start, stop, internal in model_rows:
        if not internal:
            model_by_owner[owner].append((start, stop))
    for row in tool_rows:
        if not row["metadata"]:
            tool_by_owner[row["owner"]].append((row["start"], row["end"]))
    for owner, start, stop in join_rows:
        join_by_owner[owner].append((start, stop))
    records = []
    for owner, agent in sorted(agents.items()):
        terminal = agent["end"]
        if terminal is None and agent["parent"] is None:
            terminal = terminals.get(owner[0])
        tool_intervals = merge(tool_by_owner[owner])
        join_intervals = merge(join_by_owner[owner])
        blocked = merge([*tool_intervals, *join_intervals])
        lifespan = None if terminal is None else terminal - agent["start"]
        if lifespan is not None and lifespan < 0:
            raise ValueError("invocation terminal precedes creation")
        others = [
            (start, stop) for other, start, stop, _internal in model_rows if other != owner
        ]
        records.append({
            "workflow_id": owner[0], "invocation_id": owner[1],
            "parent_invocation_id": agent["parent"],
            "model_calls": len(model_by_owner[owner]),
            "model_results_with_tools": result_tool_rounds[owner],
            "completed_lifetime_ms": lifespan,
            "ordinary_tool_blocked_ms": sum(b - a for a, b in tool_intervals),
            "observed_join_blocked_ms": sum(b - a for a, b in join_intervals),
            "union_blocked_ms": sum(b - a for a, b in blocked),
            "tool_blocked_fraction_of_lifetime": (
                sum(b - a for a, b in tool_intervals) / lifespan
                if lifespan is not None and lifespan > 0 else None
            ),
            "blocked_ms_with_other_submitted_model_work": overlap_ms(blocked, others),
        })
    ordinary = [row for row in tool_rows if not row["metadata"]]
    durations = [row["end"] - row["start"] for row in ordinary]
    duration_by_name = defaultdict(list)
    for row in ordinary:
        duration_by_name[row["name"]].append(row["end"] - row["start"])
    if any(value < 0 for value in durations):
        raise ValueError("tool end precedes start")
    bins = Counter(
        "le_1s" if value <= 1000 else "1_to_10s" if value <= 10000
        else "10_to_60s" if value <= 60000 else "gt_60s"
        for value in durations
    )
    unowned = (
        set(model_by_owner) | set(tool_by_owner) | set(join_by_owner)
    ) - agents.keys()
    unmatched["owners_without_create"] = len(unowned)
    unmatched["workflows_without_terminal"] = len(arrivals.keys() - terminals.keys())
    unmatched["terminals_without_arrival"] = len(terminals.keys() - arrivals.keys())
    unmatched["invocations_without_terminal"] = sum(
        row["completed_lifetime_ms"] is None for row in records
    )
    return {
        "workflow_count": len(arrivals),
        "complete_event_pairing": not any(unmatched.values()),
        "coverage": dict(sorted(unmatched.items())),
        "inferred_join_events_excluded": inferred_join_events,
        "observed_join_member_histogram": dict(sorted(join_fanout.items())),
        "observed_join_wait_ms": distribution(b - a for _, a, b in join_rows),
        "ordinary_tool_count": len(ordinary),
        "metadata_tool_count": len(tool_rows) - len(ordinary),
        "ordinary_tool_duration_ms": distribution(durations),
        "ordinary_tool_duration_ms_by_name": {
            name: distribution(values) for name, values in sorted(duration_by_name.items())
        },
        "ordinary_tool_duration_bins": dict(sorted(bins.items())),
        "ordinary_tool_status_counts": dict(Counter(row["status"] for row in ordinary)),
        "ordinary_tool_names": dict(Counter(row["name"] for row in ordinary)),
        "child_model_calls": distribution(
            row["model_calls"] for row in records if row["parent_invocation_id"] is not None
        ),
        "agents": records,
        "scope": {
            "includes_all_recorded_descendants": True,
            "parallel_tools_counted_as_union_for_agent_blocking": True,
            "other_submitted_work_may_be_queued_or_waiting_for_kv": True,
            "blocked_time_proves_exclusive_cold_kv_or_gpu_overlap": False,
            "requires_separate_page_and_worker_dependency_evidence": True,
            "added_online_instrumentation": False,
            "unfinished_intervals_excluded_from_duration_distributions": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True, action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous workload observations")
    paths = sorted({
        path.resolve() for client in args.client
        for path in (client / "workflows").glob("*/runtime_events.deepagents.jsonl")
    })
    if not paths:
        raise ValueError("no natural runtime event files found")
    events, inputs = [], {}
    for path in paths:
        raw = path.read_bytes()
        inputs[str(path)] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        events.extend(RuntimeEvent.from_dict(json.loads(line)) for line in raw.splitlines())
    report = {
        **analyze(events), "inputs": inputs,
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("agents", "inputs")}, indent=2))


if __name__ == "__main__":
    main()
