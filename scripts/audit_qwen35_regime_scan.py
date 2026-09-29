#!/usr/bin/env python3
"""Summarize physical opportunity snapshots without claiming transfer benefit."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def rows(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def summarize(run: Path) -> dict:
    opportunities = run / "opportunities"
    server = run / "server"
    h2d: dict[tuple, dict] = {}
    prepare: dict[tuple, dict] = {}
    sampled_reasons: dict[str, Counter[str]] = {}
    confirmed_tickets: set[tuple[str, str]] = set()
    confirmed_no_step: dict[str, Counter[str]] = {}
    confirmed_closed: Counter[str] = Counter()
    confirmed_closed_ids: set[tuple[str, str]] = set()
    for row in rows(opportunities / "admission_opportunities.jsonl"):
        if row.get("event") == "confirmed_join_ticket":
            confirmed_tickets.add((row["workflow_id"], row["join_id"]))
        elif row.get("event") == "confirmed_join_no_h2d_step":
            detail = row.get("no_live_detail")
            reason = row.get("reason", "unknown")
            if detail:
                reason = f"{reason}:{detail}"
            confirmed_no_step.setdefault(row["workflow_id"], Counter())[reason] += 1
        elif row.get("event") == "confirmed_join_ticket_closed":
            confirmed_closed[row.get("reason", "unknown")] += 1
            confirmed_closed_ids.add((row["workflow_id"], row["join_id"]))
        if row.get("event") != "session_h2d_opportunity":
            continue
        sampled_reasons.setdefault(row["source"], Counter())[
            row.get("reason", "unknown")
        ] += 1
        base = (
            row.get("source"), row.get("context_id"), row.get("context_epoch"),
            row.get("session_id"), row.get("session_generation"),
        )
        ts = row["ts_ms"]
        if (
            row.get("fits_current_free_lists") is True
            and (row.get("required_full_tokens", 0) or
                 row.get("required_mamba_slots", 0))
            and row.get("node_id") is not None
        ):
            key = base + (
                row["node_id"], row.get("node_creation_time"),
                row.get("leaf_node_id"), row.get("leaf_creation_time"),
            )
            item = h2d.setdefault(key, {
                "source": row["source"], "context_id": row["context_id"],
                "node_id": row["node_id"], "first_ts_ms": ts,
                "last_ts_ms": ts, "sample_count": 0,
                "required_full_tokens": row["required_full_tokens"],
                "required_mamba_slots": row["required_mamba_slots"],
            })
            item["last_ts_ms"] = ts
            item["sample_count"] += 1
        if (
            row.get("prepare_fits_current_host_free_lists") is True
            and (row.get("prepare_required_full_tokens", 0) or
                 row.get("prepare_required_mamba_slots", 0))
            and row.get("prepare_node_id") is not None
        ):
            key = base + (
                row["prepare_node_id"], row.get("prepare_node_creation_time"),
                row.get("prepare_leaf_node_id"),
                row.get("prepare_leaf_creation_time"),
            )
            item = prepare.setdefault(key, {
                "source": row["source"], "context_id": row["context_id"],
                "node_id": row["prepare_node_id"], "first_ts_ms": ts,
                "last_ts_ms": ts, "sample_count": 0,
                "required_full_tokens": row["prepare_required_full_tokens"],
                "required_mamba_slots": row["prepare_required_mamba_slots"],
            })
            item["last_ts_ms"] = ts
            item["sample_count"] += 1

    transfer_counts = {"d2h": 0, "h2d": 0}
    later_transfer_nodes: dict[str, dict[int, list[float]]] = {
        "d2h": {}, "h2d": {},
    }
    native_h2d_bytes = 0
    native_h2d_submit_to_ack_ms = 0.0
    h2d_receipts: list[dict] = []
    for row in rows(server / "transfer_telemetry.jsonl"):
        direction = row.get("direction")
        if row.get("status") == "completed" and direction in transfer_counts:
            transfer_counts[direction] += 1
            if direction == "h2d":
                h2d_receipts.append(row)
                native_h2d_bytes += row.get("actual_bytes") or 0
                native_h2d_submit_to_ack_ms += row.get("submit_to_ack_ms") or 0.0
            ts = row.get("complete_ts_ms")
            if isinstance(ts, (int, float)):
                for node_id in row.get("node_ids", []):
                    later_transfer_nodes[direction].setdefault(node_id, []).append(ts)

    action_acks = list(rows(server / "physical_action_ack.jsonl"))
    action_uses = list(rows(server / "physical_action_use.jsonl"))
    host_peak = {"full": 0.0, "mamba": 0.0}
    for row in rows(server / "host_pool_telemetry.jsonl"):
        if row.get("event") == "host_pool_usage":
            for pool in host_peak:
                host_peak[pool] = max(
                    host_peak[pool], row["pools"][pool]["used_fraction"]
                )
    evictions = {"full": 0, "mamba": 0}
    full_reaccessed_evicted_units = 0
    full_recomputed_units = 0
    full_reaccess_events = 0
    for row in rows(server / "eviction_attribution.jsonl"):
        if row.get("event") == "host_block_evicted" and row.get("pool") in evictions:
            evictions[row["pool"]] += int(row["evicted_units"])
        elif (
            row.get("event") == "host_block_reaccess_attributed"
            and row.get("pool") == "full"
        ):
            full_reaccess_events += 1
            full_reaccessed_evicted_units += int(
                row.get("evicted_units_since_prior_reaccess")
                or row.get("evicted_units") or 0
            )
            full_recomputed_units += int(row.get("full_recomputed_units") or 0)

    status_path = server / "native_telemetry_status.json"
    opportunity_status_path = opportunities / "admission_opportunities_status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else None
    opportunity_status = (
        json.loads(opportunity_status_path.read_text())
        if opportunity_status_path.exists() else None
    )
    results = [
        json.loads(path.read_text())
        for path in run.glob("client_*/workflows/*/result.json")
    ]
    targets = sorted(h2d.values(), key=lambda item: item["first_ts_ms"])
    for item in targets:
        item["observed_span_ms"] = item["last_ts_ms"] - item["first_ts_ms"]
    matched_receipts = [
        row for row in h2d_receipts
        if isinstance(row.get("submit_ts_ms"), (int, float))
        and any(
            item["first_ts_ms"] < row["submit_ts_ms"]
            and item["node_id"] in row.get("node_ids", ())
            for item in targets
        )
    ]
    return {
        "collection_complete": bool(
            status is not None and status.get("writer_error") is None
            and status.get("dropped_records") == 0
            and status.get("failed_records") == 0
            and status.get("pending_request_count") == 0
            and status.get("pending_batch_count") == 0
            and opportunity_status is not None
            and opportunity_status.get("complete") is True
            and opportunity_status.get("error") is None
        ),
        "workflow_results": len(results),
        "natural_completions": sum(
            item.get("outcome") == "completed"
            and item.get("natural_terminal") is True
            for item in results
        ),
        "h2d_distinct_session_targets": targets,
        "h2d_snapshot_count": sum(item["sample_count"] for item in targets),
        "sampled_h2d_reasons_by_source": {
            source: dict(counts) for source, counts in sampled_reasons.items()
        },
        "confirmed_join_ticket_count": len(confirmed_tickets),
        "confirmed_join_ticket_closed_reasons": dict(confirmed_closed),
        "confirmed_join_tickets_without_closure_record": len(
            confirmed_tickets - confirmed_closed_ids
        ),
        "confirmed_join_no_step_reasons": dict(
            sum((counts for counts in confirmed_no_step.values()), Counter())
        ),
        "prepare_distinct_session_targets": len(prepare),
        "prepare_snapshot_count": sum(
            item["sample_count"] for item in prepare.values()
        ),
        # Native ACKs omit creation time and per-request consumption. These
        # counts are node-ID-only upper bounds, not evidence of action utility.
        "h2d_targets_with_later_native_h2d_node_id_only": sum(
            any(ts > item["first_ts_ms"] for ts in later_transfer_nodes["h2d"].get(
                item["node_id"], ()
            ))
            for item in targets
        ),
        "prepare_targets_with_later_native_d2h_node_id_only": sum(
            any(ts > item["first_ts_ms"] for ts in later_transfer_nodes["d2h"].get(
                item["node_id"], ()
            ))
            for item in prepare.values()
        ),
        "native_ack_count": transfer_counts,
        "native_completed_h2d_bytes": native_h2d_bytes,
        "native_completed_h2d_submit_to_ack_ms": native_h2d_submit_to_ack_ms,
        "h2d_candidate_node_id_only_later_native_h2d_count": len(matched_receipts),
        "h2d_candidate_node_id_only_later_native_submit_to_ack_ms": sum(
            row.get("submit_to_ack_ms") or 0.0 for row in matched_receipts
        ),
        "predictive_h2d_ack_count": sum(
            row.get("action") == "PREFETCH_GPU" for row in action_acks
        ),
        "verified_first_service_full_reuse_count": sum(
            row.get("event") == "beliefkv_prefetch_first_service"
            and row.get("full_node_reused") is True for row in action_uses
        ),
        "verified_first_service_mamba_reuse_count": sum(
            row.get("event") == "beliefkv_prefetch_mamba_forward_completed"
            and row.get("mamba_reuse")
            == "verified_single_request_cow_forward_completed"
            for row in action_uses
        ),
        "host_peak_fraction": host_peak,
        "host_evicted_units": evictions,
        "full_reaccess_events": full_reaccess_events,
        "full_reaccessed_evicted_units": full_reaccessed_evicted_units,
        "full_recomputed_units": full_recomputed_units,
        "eviction_interpretation": (
            "Recomputation is attributed only for FULL blocks subsequently "
            "reaccessed; evicted blocks not revisited are not misses. Mamba "
            "per-node reaccess location is not available."
        ),
        "qualification": "not_inferred_from_snapshots_or_native_acks",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.run_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
