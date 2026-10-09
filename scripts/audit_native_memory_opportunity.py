#!/usr/bin/env python3
"""Measured transfer budget, reuse loss and terminal-cache observations."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_native_h2d_sources import (
    acknowledged_prefetch_sources,
    audit as source_audit,
    split_h2d_payload,
)
from scripts.summarize_semantic_h2d_ab import records


def timing(values) -> dict:
    values = [value for value in values if isinstance(value, (int, float)) and value >= 0]
    return {
        "count": len(values), "sum_ms": sum(values),
        "p50_ms": median(values) if values else None,
        "max_ms": max(values) if values else None,
    }


def interval_union(spans) -> float:
    total, end = 0., float("-inf")
    for left, right in sorted(spans):
        if right > max(left, end):
            total += right - max(left, end)
        end = max(end, right)
    return total


def audit(arm: Path) -> dict:
    client = next(arm.glob("client_*/summary.json"))
    duration = json.loads(client.read_text())["duration_seconds"]
    native = json.loads((arm / "server/native_telemetry_status.json").read_text())
    sources = acknowledged_prefetch_sources(arm)
    transfers = {
        "native_h2d": [], "predictive_h2d": [], "execution_handoff_h2d": [],
        "unknown_controlled_h2d": [], "mixed_h2d": [], "d2h": [],
    }
    for row in records(arm / "server/transfer_telemetry.jsonl"):
        if row["direction"] == "d2h":
            transfers["d2h"].append(row)
        elif row["direction"] == "h2d":
            payload, _ = split_h2d_payload(row, sources)
            present = [category for category, amount in payload.items() if amount > 0]
            name = f"{present[0]}_h2d" if len(present) == 1 else (
                "mixed_h2d" if present else "native_h2d"
            )
            transfers[name].append(row)
    budget = {}
    for name, rows in transfers.items():
        stream = timing(row.get("transfer_stream_elapsed_ms") for row in rows)
        ack = timing(row.get("submit_to_ack_ms") for row in rows)
        queue = timing(row.get("enqueue_to_submit_ms") for row in rows)
        budget[name] = {
            "batch_count": len(rows), "actual_bytes": sum(row["actual_bytes"] for row in rows),
            "cuda_event_interval": stream, "submit_to_ack": ack, "enqueue_to_submit": queue,
            "submit_to_complete_envelope_union_ms": interval_union([
                (row["submit_ts_ms"], row["complete_ts_ms"]) for row in rows
                if row.get("submit_ts_ms") is not None and row.get("complete_ts_ms") is not None
            ]),
            "ack_sum_share_of_workload_percent": ack["sum_ms"] / (duration * 1000.) * 100.,
        }
    terminal, unknown = {}, 0
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        if row["event"] == "terminal_context_cache_sample":
            terminal[(row["workflow_id"], row["context_id"])] = row
        elif row["event"] == "terminal_cache_anchor_unavailable":
            unknown += 1
    retained = []
    for row in terminal.values():
        resident = [node for node in row["nodes"] if (
            node["full_device_tokens"] > 0 or node["mamba_device_present"]
        )]
        retained.append({
            "workflow_id": row["workflow_id"], "invocation_id": row["invocation_id"],
            "context_id": row["context_id"], "elapsed_since_terminal_ms": row["elapsed_since_terminal_ms"],
            "resident_path_node_count": len(resident),
            "referenced_or_locked_resident_node_count": sum(
                bool(node["full_session_refs"] or node["mamba_session_refs"]
                or node["full_device_locks"] or node["mamba_device_locks"]
                )
                for node in resident
            ),
            "residual_full_path_tokens": sum(node["full_device_tokens"] for node in resident),
            "mamba_resident_path_node_count": sum(node["mamba_device_present"] for node in resident),
            "unknown_anchors": row["unavailable_anchors"],
        })
    closes, close_queue, close_envelope, errors = [], [], [], []
    queued_closes = 0
    for path in arm.glob("client_*/workflows/*/sandbox_audit.jsonl"):
        for row in records(path):
            if row.get("event") == "native_session_retire_complete":
                closes.append(row["close_elapsed_ms"])
                close_queue.append(row.get("enqueue_to_close_start_ms"))
                close_envelope.append(row.get("enqueue_to_http_complete_ms"))
            elif row.get("event") == "native_session_retire_queued":
                queued_closes += 1
            elif row.get("event") == "native_session_retire_failed":
                errors.append({key: row.get(key) for key in ("workflow_id", "context_id", "error_type")})
    group_counts, multi_rounds = Counter(), Counter()
    for path in arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"):
        count = 0
        for row in records(path):
            if row["kind"] == "join_create":
                group_counts[len(row["member_invocation_ids"])] += 1
                count += 1
        multi_rounds[count] += 1
    return {
        "scope": (
            "Observed service budget only, not a whole-system oracle or exposed stall. "
            "Queue-before-enqueue, allocation blocking and per-layer Mamba recomputation "
            "are not bounded by the H2D timer. Mixed-source batch times remain whole-batch "
            "intervals and are not apportioned to commands by byte share."
        ),
        "duration_seconds": duration, "transfer_source_counts": source_audit(arm),
        "transfer_budget": budget,
        "request_cache_evidence": native.get("request_cache_evidence"),
        "host_pool_evidence": native.get("host_pool_evidence"),
        "context_prefix_reuse_evidence": native.get("context_prefix_reuse_evidence"),
        "host_eviction_attribution": native.get("host_block_eviction_attribution"),
        "session_retire": {
            "close_time": timing(closes), "failures": errors,
            "queued_dispatch_count": queued_closes,
            "enqueue_to_close_start": timing(close_queue),
            "enqueue_to_http_complete": timing(close_envelope),
            "semantics": (
                "HTTP completion acknowledges dispatch acceptance, not scheduler "
                "reference release or physical reclamation. Timing sums may overlap "
                "and cannot be subtracted from workflow duration."
            ),
        },
        "terminal_cache": {
            "observed_contexts": len(terminal), "unavailable_anchor_events": unknown,
            "latest_context_samples": retained,
            "semantics": (
                "Per-context leaf ancestry may overlap other contexts and future useful "
                "shared prefixes. Do not sum as exclusive dead bytes or infer "
                "reclamation from HTTP session-close completion."
            ),
        },
        "actual_join_member_distribution": dict(group_counts),
        "workflow_join_round_distribution": dict(multi_rounds),
        "requested_fanout_met_by_all_rounds": bool(group_counts) and all(2 <= count <= 4 for count in group_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.arm.resolve())
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "transfer_budget": result["transfer_budget"],
        "actual_join_member_distribution": result["actual_join_member_distribution"],
        "context_prefix_reuse_evidence": result["context_prefix_reuse_evidence"],
        "terminal_cache_observed_contexts": result["terminal_cache"]["observed_contexts"],
    }, indent=2))


if __name__ == "__main__":
    main()
