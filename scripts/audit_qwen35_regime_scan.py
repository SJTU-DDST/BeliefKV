#!/usr/bin/env python3
"""Summarize physical opportunity snapshots without claiming transfer benefit."""

from __future__ import annotations

import argparse
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
    for row in rows(opportunities / "admission_opportunities.jsonl"):
        if row.get("event") != "session_h2d_opportunity":
            continue
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
    for row in rows(server / "transfer_telemetry.jsonl"):
        direction = row.get("direction")
        if row.get("status") == "completed" and direction in transfer_counts:
            transfer_counts[direction] += 1
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
    for row in rows(server / "eviction_attribution.jsonl"):
        if row.get("event") == "host_block_evicted" and row.get("pool") in evictions:
            evictions[row["pool"]] += int(row["evicted_units"])

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
    return {
        "collection_complete": bool(
            status is not None and status.get("writer_error") is None
            and opportunity_status is not None
            and opportunity_status.get("complete") is True
        ),
        "workflow_results": len(results),
        "natural_completions": sum(
            item.get("outcome") == "completed"
            and item.get("natural_terminal") is True
            for item in results
        ),
        "h2d_distinct_session_targets": targets,
        "h2d_snapshot_count": sum(item["sample_count"] for item in targets),
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
        "full_recomputed_units": (
            status.get("host_block_eviction_attribution", {}).get(
                "recomputed_full_units"
            ) if status is not None else None
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
