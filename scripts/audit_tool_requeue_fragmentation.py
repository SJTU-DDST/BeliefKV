#!/usr/bin/env python3
"""Summarize serial tool continuations without treating queue time as saved JCT."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from scripts.audit_native_system_costs import distribution, finite_number, union_ms


def summarize(episodes, *, short_tool_ms=200.):
    if not finite_number(short_tool_ms) or short_tool_ms < 0:
        raise ValueError("finite nonnegative short-tool threshold required")
    groups, seen = defaultdict(list), set()
    for row in episodes:
        if row["status"] != "first_service_observed":
            continue
        for field in ("workflow_id", "invocation_id", "request_id", "origin_request_id"):
            if type(row.get(field)) is not str or not row[field]:
                raise ValueError(f"missing served identity: {field}")
        if row["request_id"] in seen:
            raise ValueError("duplicate served successor")
        seen.add(row["request_id"])
        for field in (
            "native_queue_ts_ms", "service_start_ts_ms",
            "native_queue_to_service_ms", "tool_round_duration_ms",
        ):
            if not finite_number(row.get(field)) or row[field] < 0:
                raise ValueError(f"invalid served duration or timestamp: {field}")
        if not math.isclose(
            row["service_start_ts_ms"] - row["native_queue_ts_ms"],
            row["native_queue_to_service_ms"], rel_tol=1e-9, abs_tol=1e-6,
        ):
            raise ValueError("queue interval and duration disagree")
        depth = row.get("sole_observed_blocker_depth")
        if depth is not None and (type(depth) is not int or depth < 0):
            raise ValueError("invalid sole-blocker snapshot")
        groups[row["workflow_id"], row["invocation_id"]].append(row)
    agents = []
    for (workflow, invocation), rows in sorted(groups.items()):
        rows.sort(key=lambda row: (row["native_queue_ts_ms"], row["request_id"]))
        intervals = [
            (row["native_queue_ts_ms"], row["service_start_ts_ms"]) for row in rows
        ]
        short = [row for row in rows if row["tool_round_duration_ms"] <= short_tool_ms]
        previous, chain, longest, linked, overlaps = None, 0, 0, 0, 0
        latest_end = -math.inf
        for row in rows:
            overlaps += (
                row["service_start_ts_ms"] > row["native_queue_ts_ms"]
                and row["native_queue_ts_ms"] < latest_end
            )
            latest_end = max(latest_end, row["service_start_ts_ms"])
            follows = previous is not None and row["origin_request_id"] == previous["request_id"]
            linked += follows
            if row["tool_round_duration_ms"] <= short_tool_ms:
                chain = chain + 1 if follows and previous["tool_round_duration_ms"] <= short_tool_ms else 1
            else:
                chain = 0
            longest = max(longest, chain)
            previous = row
        agents.append({
            "workflow_id": workflow, "invocation_id": invocation,
            "served_tool_rounds": len(rows), "short_tool_rounds": len(short),
            "adjacent_pairs": max(0, len(rows) - 1), "linked_pairs": linked,
            "overlapping_queue_intervals": overlaps,
            "longest_linked_short_tool_chain": longest,
            "queue_sum_ms": sum(row["native_queue_to_service_ms"] for row in rows),
            "queue_union_ms": union_ms(intervals),
            "short_queue_sum_ms": sum(row["native_queue_to_service_ms"] for row in short),
            "short_queue_union_ms": union_ms([
                (row["native_queue_ts_ms"], row["service_start_ts_ms"]) for row in short
            ]),
            "tool_sum_ms": sum(row["tool_round_duration_ms"] for row in rows),
            "sole_blocker_snapshot_rounds": sum(
                (row.get("sole_observed_blocker_depth") or 0) > 0 for row in rows
            ),
            "unknown_sole_blocker_snapshots": sum(
                row.get("sole_observed_blocker_depth") is None for row in rows
            ),
        })
    return {
        "summary": {
            "episode_status_counts": dict(Counter(row["status"] for row in episodes)),
            "served_successor_requests": len(seen), "agents": len(agents),
            "agents_with_at_least_10_linked_short_tools": sum(
                row["longest_linked_short_tool_chain"] >= 10 for row in agents
            ),
            "overlapping_queue_intervals": sum(
                row["overlapping_queue_intervals"] for row in agents
            ),
            **{
                field: distribution(row[field] for row in agents) for field in (
                    "served_tool_rounds", "short_tool_rounds", "linked_pairs",
                    "longest_linked_short_tool_chain", "queue_sum_ms", "queue_union_ms",
                    "short_queue_sum_ms", "short_queue_union_ms",
                    "sole_blocker_snapshot_rounds", "unknown_sole_blocker_snapshots",
                )
            },
        },
        "agents": agents,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--short-tool-ms", type=float, default=200.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous evidence")
    arms, inputs = {}, {}
    for path in args.report:
        raw = path.read_bytes()
        inputs[str(path.resolve())] = hashlib.sha256(raw).hexdigest()
        for name, arm in json.loads(raw)["arms"].items():
            if name in arms:
                raise ValueError("do not combine or recount the same arm")
            arms[name] = summarize(
                arm.get("tool", arm)["episodes"], short_tool_ms=args.short_tool_ms,
            )
    result = {
        "schema_version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
        "gpu_performance_accepted": False, "short_tool_ms": args.short_tool_ms,
        "inputs": inputs, "arms": arms,
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "helper_sha256": hashlib.sha256(
            Path(__file__).with_name("audit_native_system_costs.py").read_bytes()
        ).hexdigest(),
        "semantics": [
            "Consumes hashed completed cohort reports; raw trace bytes are not reread or independently revalidated.",
            "Serial chains require exact origin-request to preceding-successor identity and the same workflow/invocation. Missing rounds break chains.",
            "Short tool is an offline diagnostic threshold, not an enabled scheduler priority or a tool-duration prediction.",
            "Queue intervals use only native server timestamps. Client tool durations are never added to server intervals.",
            "Overlaps remain explicit and unions are reported; neither agents nor sibling queue sums are subtracted from workflow JCT.",
            "Sole-blocker labels are submission-time snapshots, not proof that an entire queue interval lies on the eventual critical path.",
            "Long queueing may be productive fair sharing under contention. This audit does not estimate removable overhead or counterfactual JCT.",
            "Unknown/unserved episodes and missing blocker snapshots remain explicit. Different trajectories prevent causal arm comparisons.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({name: arm["summary"] for name, arm in arms.items()}, indent=2))


if __name__ == "__main__":
    main()
