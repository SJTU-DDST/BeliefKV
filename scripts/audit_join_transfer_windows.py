#!/usr/bin/env python3
"""Actual native H2D submission relative to child RETURN and complete JOIN."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.summarize_semantic_h2d_ab import records


def audit(arm: Path) -> dict:
    opportunity = list(records(arm / "opportunities/admission_opportunities.jsonl"))
    clock = next((r for r in opportunity if r["event"] == "safe_point_census"), None)
    if clock is None:
        raise ValueError("missing paired native wall/monotonic clock")
    offset = clock["ts_ms"] - clock["monotonic_ms"]
    returns, joins = {}, {}
    for path in arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"):
        for row in records(path):
            attrs = row.get("attributes") or {}
            if row["kind"] == "return" and attrs.get("source") == "deepagents_task":
                if attrs.get("outcome") == "completed":
                    returns[row["invocation_id"]] = row["ts_ms"] + offset
            elif row["kind"] == "join_satisfied":
                joins[row["join_id"]] = row["ts_ms"] + offset
    issued = {
        row["command_id"]: row for row in opportunity
        if row["event"] == "prefetch_native_issued" and row.get("source") == "join_ticket"
    }
    acks = {
        row["command_id"]: row for row in records(arm / "server/physical_action_ack.jsonl")
        if row["action"] == "PREFETCH_GPU"
    }
    uses = {
        row["command_id"]: row for row in records(arm / "server/physical_action_use.jsonl")
        if row["event"] == "beliefkv_prefetch_first_service"
    }
    rows = []
    for transfer in records(arm / "server/transfer_telemetry.jsonl"):
        if transfer.get("direction") != "h2d":
            continue
        for child in transfer.get("tagged_child_commits") or []:
            command = child["command_id"]
            if command not in issued:
                continue
            issue = issued[command]
            start = transfer.get("submit_ts_ms")
            return_ts = returns.get(issue.get("child_invocation_id"))
            join_ts = joins.get(issue.get("join_id"))
            ack = acks.get(command)
            use = uses.get(command)
            rows.append({
                "command_id": command, "context_id": issue["context_id"],
                "join_id": issue.get("join_id"), "node_id": issue["node_id"],
                "native_submit_ts_ms": start,
                "child_return_ts_ms": return_ts, "join_satisfied_ts_ms": join_ts,
                "submit_lead_to_child_return_ms": (
                    return_ts - start if return_ts is not None and start is not None else None
                ),
                "submit_lead_to_join_ms": (
                    join_ts - start if join_ts is not None and start is not None else None
                ),
                "ack_lead_to_join_ms": (
                    join_ts - ack["ts_ms"] if ack is not None and join_ts is not None else None
                ),
                "full_first_service_reused": use["full_node_reused"] if use else None,
                "actual_bytes": child["num_bytes"],
            })
    leads = [r["submit_lead_to_child_return_ms"] for r in rows
             if r["submit_lead_to_child_return_ms"] is not None]
    return {
        "scope": "actual controller submission, not unanchored DMA-start or forecast ETA",
        "clock": "paired scheduler wall/monotonic clock; client/server on same host",
        "command_count": len(rows), "child_return_observed_count": len(leads),
        "started_before_child_return_count": sum(x > 0 for x in leads),
        "started_0_to_500ms_before_child_return_count": sum(0 < x <= 500 for x in leads),
        "started_100_to_500ms_before_child_return_count": sum(100 <= x <= 500 for x in leads),
        "started_at_or_after_child_return_count": sum(x <= 0 for x in leads),
        "median_submit_lead_to_child_return_ms": median(leads) if leads else None,
        "acked_before_join_count": sum(
            r["ack_lead_to_join_ms"] is not None and r["ack_lead_to_join_ms"] > 0 for r in rows
        ),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.arm)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
