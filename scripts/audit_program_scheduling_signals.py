#!/usr/bin/env python3
"""Reassess program scheduling using independent JOIN/tool cohorts and past events."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.control.causal_graph import ExecutionMode, InvocationState, RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEvent
from beliefkv.policy.workflow_frontier import observed_completion_frontier
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def observed_features(graph, invocation_id):
    invocation = graph.invocations[invocation_id]
    active = {
        identity for identity in graph.workflows[invocation.workflow_id].invocation_ids
        if not graph.invocations[identity].state.terminal
        and graph.invocations[identity].execution_mode is ExecutionMode.FOREGROUND
    }
    leaves = {
        identity for identity in active
        if not graph.invocations[identity].child_invocation_ids & active
    }
    tool_leaves = {
        identity for identity in leaves
        if graph.invocations[identity].active_tool_calls
        or graph.invocations[identity].state is InvocationState.WAIT_TOOL
    }
    runnable_leaves = {
        identity for identity in leaves - tool_leaves
        if graph.invocations[identity].state in (
            InvocationState.READY, InvocationState.RUNNING_LLM,
        )
    }
    completion = observed_completion_frontier(graph, invocation.workflow_id)
    completion_tools = {
        identity for identity in completion.leaf_ids
        if graph.invocations[identity].active_tool_calls
        or graph.invocations[identity].state is InvocationState.WAIT_TOOL
    }
    completion_runnable = {
        identity for identity in completion.leaf_ids - completion_tools
        if graph.invocations[identity].state in (
            InvocationState.READY, InvocationState.RUNNING_LLM,
        )
    }
    # This is an observed sole-blocker chain, never a future critical path.
    identity, depth, seen = invocation_id, 0, {invocation_id}
    while (parent_id := graph.invocations[identity].parent_invocation_id) is not None:
        parent = graph.invocations[parent_id]
        if parent_id in seen or parent.active_tool_calls or parent.workflow_id != invocation.workflow_id:
            break
        sole = (
            parent.state is InvocationState.WAIT_CHILD
            and parent.blocking_child_ids == {identity}
        )
        if parent.state is InvocationState.WAIT_JOIN:
            join = graph.joins.get(parent.join_id)
            sole = bool(
                join is not None and not join.satisfied
                and parent_id in join.waiter_invocation_ids
                and join.member_invocation_ids - join.completed_member_ids == {identity}
            )
        if not sole:
            break
        depth += 1
        seen.add(parent_id)
        identity = parent_id
    return {
        "foreground_leaf_count": len(leaves),
        "runnable_leaf_count": len(runnable_leaves),
        "other_tool_blocked_leaves": len(tool_leaves - {invocation_id}),
        "sole_observed_blocker_depth": depth,
        "completion_leaf_count": len(completion.leaf_ids),
        "completion_runnable_leaves": len(completion_runnable),
        "other_completion_tool_blocked_leaves": len(completion_tools - {invocation_id}),
        "completion_scope_unknown_dependencies": len(completion.unknown_dependency_ids),
    }


def snapshots(events, targets):
    graph = RuntimeCausalContextGraph(strict_timestamps=False)
    result, last_sequence, clock_high = {}, 0, 0.
    for raw in events:
        sequence = raw.get("sequence")
        if type(sequence) is not int or sequence <= last_sequence:
            raise ValueError("strictly increasing client event sequence required")
        last_sequence = sequence
        event = RuntimeEvent.from_dict(raw)
        graph.apply(event)
        clock_high = max(clock_high, raw["ts_ms"])
        if raw["kind"] != "llm_submit":
            continue
        identity = (raw.get("attributes") or {}).get("request_id")
        if identity not in targets:
            continue
        target = targets[identity]
        expected = (
            target["workflow_id"],
            target.get("invocation_id", target.get("parent_invocation_id")),
            target["context_id"], target["context_epoch"],
        )
        actual = tuple(raw.get(key) for key in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
        ))
        if actual != expected or identity in result:
            raise ValueError("successor client identity is ambiguous")
        result[identity] = {
            **observed_features(graph, raw["invocation_id"]),
            "earlier_events_clock_ahead_ms": max(0., clock_high - raw["ts_ms"]),
        }
    return result


def summarize(rows):
    served = [row for row in rows if row["status"] == "first_service_observed"]
    comparison = [
        row for row in served
        if finite_number(row.get("tool_round_duration_ms"))
        and finite_number(row.get("native_queue_to_service_ms"))
    ]
    return {
        "episodes": len(rows), "first_service_observed": len(served),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        **{
            field: distribution(row[field] for row in served if field in row)
            for field in (
                "tool_round_duration_ms", "native_queue_to_service_ms",
                "foreground_leaf_count", "runnable_leaf_count",
                "other_tool_blocked_leaves", "sole_observed_blocker_depth",
                "earlier_events_clock_ahead_ms",
                "completion_leaf_count", "completion_runnable_leaves",
                "other_completion_tool_blocked_leaves", "completion_scope_unknown_dependencies",
            )
        },
        "paired_tool_and_queue_durations": len(comparison),
        "queue_longer_than_preceding_tool": sum(
            row["native_queue_to_service_ms"] > row["tool_round_duration_ms"] for row in comparison
        ),
        "tool_le_200ms_queue_ge_1000ms": sum(
            row["tool_round_duration_ms"] <= 200.
            and row["native_queue_to_service_ms"] >= 1000. for row in comparison
        ),
        "logical_host_hit_at_service": sum(row.get("cached_tokens_host", 0) > 0 for row in served),
        "prompt_ge_128k": sum(row.get("prompt_tokens", 0) >= 131072 for row in served),
        "online_snapshot_unavailable": sum("foreground_leaf_count" not in row for row in rows),
    }


def audit_arm(tool, join):
    if tool["arm"] != join["arm"]:
        raise ValueError("JOIN and tool cohorts belong to different arms")
    tool_rows, join_rows = tool["episodes"], join["episodes"]
    targets = {}
    for row in (*tool_rows, *join_rows):
        identity = row.get("request_id")
        if identity:
            if identity in targets:
                raise ValueError("JOIN/tool successors overlap; preserve mixed rounds separately")
            targets[identity] = row
    reader = SealedRecords()
    features = {}
    for filename, prefix in tool["input_jsonl_prefixes"].items():
        if not filename.endswith("/runtime_events.deepagents.jsonl"):
            continue
        path = Path(filename)
        path = path if path.is_absolute() else ROOT / path
        if not path.is_file():
            raise ValueError(f"sealed client trace is unavailable: {path}")
        reader.prefixes[str(path)] = prefix
        observed = snapshots(reader(path), targets)
        if features.keys() & observed.keys():
            raise ValueError("successor occurs in more than one client trace")
        features.update(observed)
        if reader.prefixes[str(path)] != prefix:
            raise ValueError("client input differs from the audited sealed prefix")
    results = {}
    for kind, rows in (("tool", tool_rows), ("join", join_rows)):
        enriched = [
            {**row, **features.get(row.get("request_id"), {})} for row in rows
        ]
        groups = defaultdict(list)
        for row in enriched:
            if "foreground_leaf_count" not in row:
                group = "online_graph_unknown"
            elif row["other_completion_tool_blocked_leaves"]:
                group = "another_completion_dependency_waits_for_tools"
            else:
                group = "no_other_completion_dependency_waits_for_tools"
            groups[group].append(row)
        results[kind] = {
            "summary": summarize(enriched),
            "by_observed_tool_overlap": {
                group: summarize(items) for group, items in sorted(groups.items())
            },
            "episodes": enriched,
        }
    return {**results, "arm": tool["arm"], "input_jsonl_prefixes": reader.prefixes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool-report", type=Path, required=True)
    parser.add_argument("--join-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous evidence")
    tools, joins = (json.loads(path.read_bytes()) for path in (args.tool_report, args.join_report))
    if tools["arms"].keys() != joins["arms"].keys():
        raise ValueError("cohort arms differ")
    arms = {
        name: audit_arm(tools["arms"][name], joins["arms"][name])
        for name in tools["arms"]
    }
    result = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "gpu_performance_accepted": False,
        "scope": "Latest independent JOIN/tool historical cohorts; online client graph snapshots at actual successor submission.",
        "semantics": [
            "Features use only the client sequence prefix through this submission, never later tool ends, returns or the final DAG. Small event-clock regressions do not reorder observations.",
            "Client and server timestamps are never subtracted. Their elapsed durations are compared within the same successor episode.",
            "Queue duration is not saved JCT or isolated restoration stall. Logical Host hits are not physical H2D bytes.",
            "Blocked tool leaves expose an overlap opportunity, not a lower bound on remaining tool time or proof of critical-path slack.",
            "Request snapshots are conditional on these cohorts. Unknown and unserved episodes stay explicit.",
            "Different historical runs have different realized work. No causal speedup or scheduler superiority is inferred.",
        ],
        "inputs": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (args.tool_report, args.join_report)
        },
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "beliefkv/control/causal_graph.py",
                ROOT / "beliefkv/core/events.py",
                ROOT / "beliefkv/policy/workflow_frontier.py",
                ROOT / "scripts/audit_native_system_costs.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "arms": arms,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        name: {kind: value[kind]["summary"] for kind in ("tool", "join")}
        for name, value in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
