#!/usr/bin/env python3
"""Audit transfer granularity and cache effects in an observed service window."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from scripts.audit_native_hybrid_reuse import records, transfer_costs
from scripts.audit_native_system_costs import distribution


def counter_delta(before, after):
    result = {}
    for key in before.keys() | after.keys():
        value = after.get(key, 0) - before.get(key, 0)
        if value < 0:
            raise ValueError(f"counter reset in window: {key}")
        result[key] = value
    return result


def host_window(events, begin, end):
    boundaries = [None, None]
    covered = False
    evictions = Counter()
    occupancy = defaultdict(list)
    for row in events:
        timestamp = row.get("ts_ms")
        if type(timestamp) not in (int, float):
            continue
        if row.get("event") == "host_pool_eviction" and begin <= timestamp < end:
            evictions["operations"] += 1
            for name, pool in row.get("pools", {}).items():
                evictions[name + "_units"] += pool["evicted_units"]
                evictions[name + "_bytes"] += pool["evicted_bytes"]
        if row.get("event") != "host_pool_usage":
            continue
        if timestamp <= begin and (
            boundaries[0] is None or timestamp > boundaries[0]["ts_ms"]
        ):
            boundaries[0] = row
        if timestamp <= end and (
            boundaries[1] is None or timestamp > boundaries[1]["ts_ms"]
        ):
            boundaries[1] = row
        if timestamp > end:
            covered = True
        if begin <= timestamp < end:
            for name, pool in row.get("pools", {}).items():
                occupancy[name].append(pool["used_fraction"])
    if not covered or any(row is None for row in boundaries):
        raise ValueError("host usage snapshots do not cover the requested window")
    before, after = boundaries
    batches = [row.get("write_back_batch") for row in boundaries]
    batch_delta = {"available": False, "reason": "batch_counters_not_collected"}
    if all(batch is not None for batch in batches):
        if batches[0]["max_nodes"] != batches[1]["max_nodes"]:
            raise ValueError("write-back node limit changed")
        batch_delta = {
            "available": True,
            "max_nodes": batches[0]["max_nodes"],
            "counts": counter_delta(batches[0]["counts"], batches[1]["counts"]),
        }
    return {
        "boundary_ts_ms": [row["ts_ms"] for row in boundaries],
        "snapshot_duration_ms": after["ts_ms"] - before["ts_ms"],
        "boundary_lag_ms": [begin - before["ts_ms"], end - after["ts_ms"]],
        "evictions": dict(evictions),
        "sampled_occupancy_fraction": {
            name: distribution(values) for name, values in occupancy.items()
        },
        "full_eviction_scan_delta": counter_delta(
            before.get("full_host_eviction_scans", {}),
            after.get("full_host_eviction_scans", {}),
        ),
        "write_back_batch": batch_delta,
    }


def recompute_window(events, begin, end):
    counts = Counter()
    for row in events:
        timestamp = row.get("ts_ms")
        if type(timestamp) not in (int, float) or not begin <= timestamp < end:
            continue
        event = row.get("event")
        counts["event:" + str(event)] += 1
        if event == "context_prefix_reuse_probe":
            value = row.get("previously_served_prefix_recompute_proxy_tokens")
            if type(value) is int and value >= 0:
                counts["same_session_missing_input_proxy_tokens"] += value
        if event == "host_block_reaccess_attributed" and row.get("pool") == "full":
            value = row.get("full_recomputed_units")
            if type(value) is int and value >= 0:
                counts["observed_host_eviction_recompute_tokens"] += value
                counts["observed_full_reaccess_records"] += 1
            else:
                counts["full_reaccess_records_with_unknown_recompute"] += 1
    return dict(counts)


def audit_arm(arm, window):
    begin, end = window["begin_ts_ms"], window["end_ts_ms"]
    bindings = []
    census_path = arm / "server/native_capacity_census.json"
    census_bytes = census_path.read_bytes()
    capacity = json.loads(census_bytes)["capacity"]
    units = {}
    for name, pool in (("full", "kv"), ("mamba", "mamba")):
        quantity = capacity["host_full_tokens" if name == "full" else "host_mamba_slots"]
        size, remainder = divmod(capacity["host_" + name + "_bytes"], quantity)
        if remainder:
            raise ValueError(f"nonintegral pool geometry: {name}")
        units[pool] = size
    bindings.append({
        "path": str(census_path), "analyzed_bytes": len(census_bytes),
        "sha256": hashlib.sha256(census_bytes).hexdigest(),
    })
    transfers = list(records(arm / "server/transfer_telemetry.jsonl", bindings))
    selected = [
        row for row in transfers
        if row.get("status") == "completed"
        and begin <= row["complete_ts_ms"] < end
    ]
    grouped = defaultdict(list)
    for row in selected:
        pools = row.get("num_tokens_by_pool") or {}
        tagged = bool(row.get("tagged_child_commits") or row.get("node_commits"))
        shape = (
            "full_and_mamba" if pools.get("kv") and pools.get("mamba")
            else "full_only" if pools.get("kv")
            else "mamba_only" if pools.get("mamba") else "empty"
        )
        grouped[(row["direction"], shape, tagged)].append(row)
    cohorts = {}
    for (direction, shape, tagged), rows in grouped.items():
        key = direction + ":" + shape + (":tagged" if tagged else ":untagged")
        cohorts[key] = {
            "batches": len(rows),
            "nodes": sum(len(row.get("node_ids") or []) for row in rows),
            "multinode_batches": sum(len(row.get("node_ids") or []) > 1 for row in rows),
            "bytes": sum(row["actual_bytes"] for row in rows),
            "full_tokens": sum((row.get("num_tokens_by_pool") or {}).get("kv", 0) for row in rows),
            "mamba_slots": sum((row.get("num_tokens_by_pool") or {}).get("mamba", 0) for row in rows),
        }
    host = host_window(
        records(arm / "server/host_pool_telemetry.jsonl", bindings), begin, end,
    )
    recompute = recompute_window(
        records(arm / "server/eviction_attribution.jsonl", bindings), begin, end,
    )
    return {
        "arm": str(arm), "window": window, "sources": bindings,
        "pool_bytes_per_unit": units, "capacity": capacity,
        "transfers": transfer_costs(selected, units),
        "tagged_transfer_cohorts": cohorts,
        "host": host, "recompute": recompute,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--busy-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    busy_bytes = args.busy_report.read_bytes()
    busy = json.loads(busy_bytes)
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "busy_report": str(args.busy_report.resolve()),
        "busy_report_sha256": hashlib.sha256(busy_bytes).hexdigest(),
        "analysis_sources_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__).resolve(),
                Path(__file__).with_name("audit_native_hybrid_reuse.py").resolve(),
                Path(__file__).with_name("audit_native_system_costs.py").resolve(),
            )
        },
        "scope": "Observed closed service windows; cross-version diagnosis, not causal speedup",
        "semantics": [
            "Transfers are selected by completion in the half-open wall-clock window.",
            "All source reads are bounded, hashed complete-line prefixes; later growth is excluded.",
            "Batch-counter deltas use preceding Host snapshots and may differ at window boundaries.",
            "Tagged identifies telemetry tags, not confirmed first-service reuse.",
            "Same-anchor recovery is only associated within this window; no wasted-byte claim.",
            "Missing prior-input probes are unknown, not zero total recomputation.",
            "Neither transfer-stream time nor submit-to-ACK sums are removable JCT.",
            "Payload size, traffic, request mix and concurrent work can differ across runs.",
        ],
        "arms": {},
    }
    for name, arm in busy["arms"].items():
        window = arm["monitor_window"]
        result["arms"][name] = audit_arm(Path(arm["source"]).parent.parent, window)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {
            "write_back_batch": arm["host"]["write_back_batch"],
            "transfers": {
                key: {"batches": values["batches"], "bytes": values["bytes"]}
                for key, values in arm["tagged_transfer_cohorts"].items()
            },
            "host_evictions": arm["host"]["evictions"],
            "recompute": arm["recompute"],
        }
        for name, arm in result["arms"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
