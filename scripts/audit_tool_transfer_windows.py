#!/usr/bin/env python3
"""Native predictive H2D submission versus all remaining tools' completion."""

from collections import defaultdict
import argparse
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.summarize_semantic_h2d_ab import records


def audit(arm: Path) -> dict:
    ops = list(records(arm / "opportunities/admission_opportunities.jsonl"))
    census = next(row for row in ops if row["event"] == "safe_point_census")
    offset = census["ts_ms"] - census["monotonic_ms"]
    ends = {}
    legacy_ends = defaultdict(list)
    for path in arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"):
        for row in records(path):
            if row["kind"] != "tool_end":
                continue
            attrs = row.get("attributes") or {}
            identity = attrs.get("tool_run_id") or attrs.get("tool_call_id")
            if identity:
                ends[(row["invocation_id"], identity)] = row["ts_ms"] + offset
            legacy_ends[row["invocation_id"]].append(row["ts_ms"] + offset)
    issued = {
        row["command_id"]: row for row in ops
        if row["event"] == "prefetch_native_issued" and row.get("source") == "tool_wait"
    }
    uses = {
        row["command_id"]: row for row in records(arm / "server/physical_action_use.jsonl")
        if row["event"] == "beliefkv_prefetch_first_service"
    }
    rows = []
    for transfer in records(arm / "server/transfer_telemetry.jsonl"):
        for child in transfer.get("tagged_child_commits", []):
            issue = issued.get(child["command_id"])
            if issue is None:
                continue
            identities = issue.get("active_tool_ids") or []
            matched = [
                ends[(issue["invocation_id"], identity)] for identity in identities
                if (issue["invocation_id"], identity) in ends
            ]
            completion = max(matched) if len(matched) == len(identities) and identities else None
            lead = (
                completion - transfer["submit_ts_ms"]
                if completion is not None else None
            )
            use = uses.get(child["command_id"])
            rows.append({
                "command_id": child["command_id"], "context_id": issue["context_id"],
                "active_tool_ids": identities, "tool_completion_ts_ms": completion,
                "native_submit_ts_ms": transfer["submit_ts_ms"],
                "submit_lead_to_tool_end_ms": lead,
                "actual_bytes": child["num_bytes"],
                "full_first_service_reused": use["full_node_reused"] if use else None,
            })
    leads = [row["submit_lead_to_tool_end_ms"] for row in rows if row["submit_lead_to_tool_end_ms"] is not None]
    return {
        "scope": "controller submit before tool completion, not before first GPU service",
        "command_count": len(rows), "tool_end_observed_count": len(leads),
        "started_before_tool_end_count": sum(lead > 0 for lead in leads),
        "started_0_to_1000ms_before_tool_end_count": sum(0 < lead <= 1000 for lead in leads),
        "started_100_to_1000ms_before_tool_end_count": sum(100 <= lead <= 1000 for lead in leads),
        "started_at_or_after_tool_end_count": sum(lead <= 0 for lead in leads),
        "median_submit_lead_to_tool_end_ms": median(leads) if leads else None,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.arm)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
