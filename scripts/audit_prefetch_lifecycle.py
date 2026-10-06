#!/usr/bin/env python3
"""Link predictive transfers to EOS, first use, leases and repeated parking."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.summarize_semantic_h2d_ab import records


def distribution(values) -> dict:
    values = [value for value in values if value is not None]
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "p50": float(np.median(values)),
        "p90": float(np.quantile(values, .9)),
        "max": float(max(values)),
    }


def audit(arm: Path) -> dict:
    issued, prepared, parks, leases, stages = {}, {}, [], defaultdict(list), {}
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        event = row["event"]
        if event == "prefetch_native_issued":
            stage = stages.get(row.get("child_request_id"), {})
            issued[row["command_id"]] = {
                **row, "trigger_kind": stage.get("trigger_kind", "unrecorded_legacy"),
            }
        elif event == "final_stage_latest_start":
            stages[row["child_request_id"]] = row
        elif event == "prepare_native_issued":
            prepared[row["command_id"]] = row
        elif event == "parent_pressure_demoted":
            parks.append(row)
        elif event in ("prefetch_residency_registered", "prefetch_residency_released"):
            leases[row["command_id"]].append(row)
    transfers = list(records(arm / "server/transfer_telemetry.jsonl"))
    tagged = {
        child["command_id"]: (row, child)
        for row in transfers for child in row.get("tagged_child_commits") or []
    }
    child_requests = {row.get("child_request_id") for row in issued.values()}
    eos = {
        row["attributes"]["request_id"]: row["ts_ms"]
        for row in records(arm / "server/runtime_events.sglang.jsonl")
        if row["kind"] == "llm_result"
        and row.get("attributes", {}).get("request_id") in child_requests
    }
    uses, mamba = {}, set()
    for row in records(arm / "server/physical_action_use.jsonl"):
        if row["event"] == "beliefkv_prefetch_first_service":
            uses[row["command_id"]] = row
        elif row["event"] == "beliefkv_prefetch_mamba_forward_completed":
            if row.get("mamba_reuse") == "verified_single_request_cow_forward_completed":
                mamba.add(row["command_id"])
    windows = {}
    for name in ("join_transfer_windows.json", "tool_transfer_windows.json"):
        path = arm / name
        if path.exists():
            windows.update({
                row["command_id"]: row for row in json.loads(path.read_text())["rows"]
            })
    results = []
    for command, issue in sorted(issued.items(), key=lambda item: item[1]["ts_ms"]):
        pair = tagged.get(command)
        use = uses.get(command)
        window = windows.get(command, {})
        native_eos = eos.get(issue.get("child_request_id"))
        transfer, child = pair if pair else ({}, {})
        ack = use.get("ack_ts_ms") if use else transfer.get("complete_ts_ms")
        service = use.get("first_service_ts_ms") if use else None
        repeated_parks = [
            row for row in parks if row["context_id"] == issue["context_id"]
            and row["node_id"] == issue["node_id"]
            and ack is not None and service is not None
            and ack < row["ts_ms"] < service
        ]
        native_reloads = [
            {key: row.get(key) for key in ("submit_ts_ms", "complete_ts_ms", "actual_bytes")}
            for row in transfers if row["direction"] == "h2d"
            and not row.get("tagged_child_commits")
            and issue["node_id"] in row.get("node_ids", [])
            and ack is not None and service is not None
            and ack < row["submit_ts_ms"] <= service
        ]
        submit = transfer.get("submit_ts_ms")
        results.append({
            "command_id": command, "source": issue["source"],
            "trigger_kind": issue["trigger_kind"],
            "workflow_id": issue["workflow_id"],
            "context_id": issue["context_id"], "context_epoch": issue["context_epoch"],
            "node_id": issue["node_id"], "actual_bytes": child.get("num_bytes"),
            "pool_units": child.get("num_tokens_by_pool"),
            "submit_ts_ms": submit, "ack_ts_ms": ack,
            "issue_to_submit_ms": submit - issue["ts_ms"] if submit is not None else None,
            "enqueue_to_submit_ms": transfer.get("enqueue_to_submit_ms"),
            "submit_to_ack_ms": transfer.get("submit_to_ack_ms"),
            "submit_after_native_eos_ms": (
                submit - native_eos if submit is not None and native_eos is not None else None
            ),
            "submit_lead_to_completion_ms": window.get(
                "submit_lead_to_child_return_ms", window.get("submit_lead_to_tool_end_ms"),
            ),
            "ack_to_first_service_ms": (
                service - ack if service is not None and ack is not None else None
            ),
            "full_first_service_reused": use.get("full_node_reused") if use else None,
            "mamba_forward_verified": command in mamba,
            "lease_events": leases.get(command, []),
            "pressure_demotions_before_first_service": repeated_parks,
            "native_reloads_before_first_service": native_reloads,
        })
    prepared_by_node = defaultdict(list)
    for command, (transfer, _) in tagged.items():
        issue = prepared.get(command)
        if issue is None or transfer["direction"] != "d2h":
            continue
        for node in transfer.get("node_ids", []):
            prepared_by_node[(issue["context_id"], issue["context_epoch"], node)].append(
                (transfer["complete_ts_ms"], command),
            )
    consumed = []
    for park in parks:
        before = [
            (when, command) for when, command in prepared_by_node.get(
                (park["context_id"], park["context_epoch"], park["node_id"]), (),
            ) if when < park["ts_ms"]
        ]
        if before:
            consumed.append({**park, "prior_prepare_command": max(before)[1]})
    joins = [row for row in results if row["source"] == "join_ticket"]
    tools = [row for row in results if row["source"] == "tool_wait"]
    summary = {
        "join_commands": len(joins), "tool_commands": len(tools),
        "trigger_kinds": dict(Counter(row["trigger_kind"] for row in results)),
        "join_bytes": sum(row["actual_bytes"] or 0 for row in joins),
        "tool_bytes": sum(row["actual_bytes"] or 0 for row in tools),
        "join_submitted_before_eos": sum(
            row["submit_after_native_eos_ms"] is not None
            and row["submit_after_native_eos_ms"] < 0 for row in joins
        ),
        "full_reused": sum(row["full_first_service_reused"] is True for row in results),
        "full_not_reused": sum(row["full_first_service_reused"] is False for row in results),
        "full_use_unknown": sum(row["full_first_service_reused"] is None for row in results),
        "mamba_forward_verified": len(mamba & issued.keys()),
        "redemoted_before_first_service": sum(
            bool(row["pressure_demotions_before_first_service"]) for row in results
        ),
        "native_reloaded_before_first_service": sum(
            bool(row["native_reloads_before_first_service"]) for row in results
        ),
        "issue_to_submit_ms": distribution(row["issue_to_submit_ms"] for row in results),
        "enqueue_to_submit_ms": distribution(row["enqueue_to_submit_ms"] for row in results),
        "submit_to_ack_ms": distribution(row["submit_to_ack_ms"] for row in results),
        "ack_to_first_service_ms": distribution(row["ack_to_first_service_ms"] for row in results),
        "join_submit_after_eos_ms": distribution(row["submit_after_native_eos_ms"] for row in joins),
        "completion_lead_ms": distribution(row["submit_lead_to_completion_ms"] for row in results),
        "pressure_events": len(parks),
        "pressure_events_with_prior_prepare_ack": len(consumed),
        "unique_prepared_nodes_demoted": len({row["node_id"] for row in consumed}),
    }
    return {
        "scope": (
            "Observed controller submission/ACK/first-launch chain; no DMA wall "
            "anchor, exposed-stall measurement or counterfactual speedup."
        ),
        "arm": str(arm), "summary": summary, "rows": results,
        "prepare_consumption": consumed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.arm.resolve())
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
