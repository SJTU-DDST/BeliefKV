#!/usr/bin/env python3
"""Audit complete logical handovers against subsequent native demand restores."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords


def inspect(events, native, host_hits):
    requests = defaultdict(list)
    for row in native:
        if row["kind"] == "llm_submit":
            requests[row["invocation_id"]].append((
                row["ts_ms"], row["attributes"]["request_id"],
            ))
    for rows in requests.values():
        rows.sort()
    times = {name: [row[0] for row in rows] for name, rows in requests.items()}
    copies = {row["request_id"]: row["host_hit_bytes"] for row in host_hits}
    lending, recalls = [], []
    for event in events:
        kind = event.get("event")
        if kind not in ("agent_blocked_budget_lent", "agent_loan_recall_decision"):
            continue
        if kind == "agent_blocked_budget_lent":
            needed = (
                event["target_tokens"] + event["buffer_tokens_per_agent"]
                - event["available_tokens_before"]
            )
            released = event["donor_budget_tokens"]
            identities = event["donor_invocation_ids"]
        else:
            needed, released = event["needed_tokens"], event["selected_credit_tokens"]
            identities = event["selected_invocation_ids"]
        if needed <= 0 or released < needed or len(set(identities)) != len(identities):
            raise ValueError("invalid executed budget handover")
        following = []
        for name in identities:
            index = bisect_right(times.get(name, ()), event["ts_ms"])
            if index < len(requests[name]):
                when, request = requests[name][index]
                following.append({
                    "invocation_id": name, "request_id": request,
                    "next_native_submit_after_handover_ms": when - event["ts_ms"],
                    "next_request_demand_h2d_bytes": copies.get(request, 0),
                })
            else:
                following.append({
                    "invocation_id": name, "request_id": None,
                    "next_native_submit_after_handover_ms": None,
                    "next_request_demand_h2d_bytes": 0,
                })
        item = {
            "ts_ms": event["ts_ms"], "needed_tokens": needed,
            "released_tokens": released, "excess_tokens": released - needed,
            "released_over_needed": released / needed,
            "selected_invocations": identities, "following_native_requests": following,
        }
        (lending if kind == "agent_blocked_budget_lent" else recalls).append(item)

    def summarize(rows):
        fast, tokens = Counter(), Counter()
        for row in rows:
            tokens["needed"] += row["needed_tokens"]
            tokens["released"] += row["released_tokens"]
            tokens["excess"] += row["excess_tokens"]
            for following in row["following_native_requests"]:
                delay = following["next_native_submit_after_handover_ms"]
                if delay is None:
                    fast["no_later_native_submit"] += 1
                    continue
                fast["selected_owners_with_later_native_submit"] += 1
                for threshold in (200, 1000, 5000):
                    if delay <= threshold:
                        fast[f"resubmitted_within_{threshold}ms"] += 1
                        fast[f"following_host_bytes_within_{threshold}ms"] += (
                            following["next_request_demand_h2d_bytes"]
                        )
        return {
            "events": len(rows), "tokens": dict(tokens),
            "released_over_needed": distribution([
                row["released_over_needed"] for row in rows
            ]),
            "excess_tokens": distribution([row["excess_tokens"] for row in rows]),
            "following_requests": dict(fast),
        }

    return {
        "lending_summary": summarize(lending), "recall_summary": summarize(recalls),
        "lending_events": lending, "recall_events": recalls,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--restore-audit", type=Path, required=True)
    parser.add_argument("--restore-label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier handover audits")
    reader = SealedRecords()
    restore = json.loads(args.restore_audit.read_bytes())["arms"][args.restore_label]
    status_path = args.arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    summary_path = args.arm / "replay/summary.json"
    summary = json.loads(summary_path.read_bytes())
    if (
        not summary["causal_execution_completed"] or not summary["exact_tokens_verified"]
        or not summary["original_native_stopping_verified"]
        or status["writer_queue_depth"] or status["writer_error"] is not None
        or status["dropped_records"] or status["failed_records"]
    ):
        raise ValueError("complete healthy replay is required")
    result = inspect(
        reader(args.arm / "opportunities/admission_opportunities.jsonl"),
        reader(args.arm / "server/runtime_events.sglang.jsonl"),
        restore["host_hit_requests"],
    )
    native_path = str(args.arm / "server/runtime_events.sglang.jsonl")
    if reader.prefixes[native_path]["complete_prefix_sha256"] != restore["work"]["events"]["sha256"]:
        raise ValueError("native population differs from the complete restore audit")
    result.update({
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arm": str(args.arm), "input_jsonl_prefixes": reader.prefixes,
        "restore_audit_sha256": sha256_file(args.restore_audit),
        "summary_sha256": sha256_file(summary_path),
        "auditor_sha256": sha256_file(Path(__file__)),
        "scope": [
            "Executed logical handovers and each selected owner's following native request.",
            "Subsequent Host-hit bytes are an association, not proof that the handover caused their eviction.",
            "Logical token excess is not physical free space, duplicate migration or removable JCT.",
            "Future submits are offline diagnostics and must never be online policy features.",
            "No workflow or normal tail is removed.",
        ],
        "performance_accepted": False,
    })
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in ("lending_summary", "recall_summary")
    }, indent=2))


if __name__ == "__main__":
    main()
