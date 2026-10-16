#!/usr/bin/env python3
"""Audit waiting-state demotion and subsequent demand from sealed trace prefixes."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_prefetch_lifecycle import distribution, transfer_parts
from scripts.audit_sealed_wait_prefetch import SealedRecords


def scope(row):
    return tuple(row.get(field) for field in (
        "workflow_id", "invocation_id", "context_id", "context_epoch",
    ))


def recoveries(demotions, prepares, transfers, requests):
    """Associate observed node IDs; never promote this to component reuse proof."""
    groups, requests_by_owner, loads = defaultdict(list), defaultdict(list), defaultdict(list)
    prepare_acks = defaultdict(list)
    seen_requests, seen_transfers = set(), set()
    for row in requests:
        if row.get("kind") != "llm_submit":
            continue
        rid = (row.get("attributes") or {}).get("request_id")
        if not rid or rid in seen_requests:
            raise ValueError(f"missing or duplicate first-service request: {rid!r}")
        seen_requests.add(rid)
        if any(value is None for value in scope(row)):
            continue
        requests_by_owner[scope(row)[:3]].append(row)
    for rows in requests_by_owner.values():
        rows.sort(key=lambda row: row["ts_ms"])
    for row in demotions:
        if any(value is None for value in scope(row)):
            raise ValueError("demotion has incomplete invocation/context identity")
        groups[scope(row)].append(row)
    for transfer in transfers:
        command = transfer.get("command_id")
        if not command or command in seen_transfers:
            raise ValueError(f"missing or duplicate transfer: {command!r}")
        seen_transfers.add(command)
        if transfer.get("status") != "completed":
            continue
        when = transfer.get("submit_ts_ms")
        for part in transfer_parts(transfer):
            nodes = part["node_ids"]
            if transfer["direction"] == "h2d" and when is not None:
                for node in nodes:
                    loads[node].append((when, transfer, part))
            elif transfer["direction"] == "d2h":
                issue = prepares.get(part["command_id"])
                ack = transfer.get("complete_ts_ms")
                if issue is not None and ack is not None:
                    for node in nodes:
                        prepare_acks[issue["context_id"], issue["context_epoch"], node].append({
                            "command_id": part["command_id"], "ack_ts_ms": ack,
                            "anchor_creation_time": (
                                issue.get("node_creation_time")
                                if node == issue.get("node_id") else None
                            ),
                        })
    for rows in loads.values():
        rows.sort(key=lambda item: item[0])
    times = {node: [item[0] for item in rows] for node, rows in loads.items()}
    results = []
    for identity, parks in sorted(groups.items(), key=lambda item: min(
        row["ts_ms"] for row in item[1]
    )):
        parks.sort(key=lambda row: row["ts_ms"])
        workflow, invocation, context, epoch = identity
        first = parks[0]["ts_ms"]
        candidates = [
            row for row in requests_by_owner.get(identity[:3], ())
            if row["context_epoch"] > epoch
        ]
        following = min(
            candidates, key=lambda row: (row["context_epoch"], row["ts_ms"]),
            default=None,
        )
        attrs = (following or {}).get("attributes") or {}
        match = attrs.get("native_host_hit_match_path") or {}
        observed = match.get("observed_ts_ms")
        if observed is not None and (
            not isinstance(observed, (int, float))
            or observed < following["ts_ms"]
        ):
            raise ValueError("unordered first-service path observation")
        path_nodes = defaultdict(set)
        for name in ("last_device_path", "last_host_path", "best_match_path"):
            for node, created in match.get(name) or ():
                path_nodes[node].add(created)
        freed = Counter()
        parked_nodes, links, cycles = set(), {}, []
        prior_backups = []
        for park in parks:
            node, parked = park["node_id"], park["ts_ms"]
            parked_nodes.add(node)
            freed.update({str(key): count for key, count in park["freed_units"].items()})
            earlier = [
                row for row in prepare_acks.get((context, epoch, node), ())
                if row["ack_ts_ms"] < parked
            ]
            if earlier:
                latest = max(earlier, key=lambda row: row["ack_ts_ms"])
                prior_backups.append({
                    "node_id": node, "demotion_ts_ms": parked, **latest,
                    "service_path_creation_times": sorted(path_nodes.get(node, ())),
                    "creation_identity_at_demotion": "unrecorded",
                })
            if observed is None:
                continue
            left = bisect_right(times.get(node, ()), parked)
            right = bisect_right(times.get(node, ()), observed)
            for when, transfer, part in loads.get(node, ())[left:right]:
                pools = [
                    pool for component, pool in (("0", "kv"), ("2", "mamba"))
                    if park["freed_units"].get(component, park["freed_units"].get(
                        int(component), 0,
                    )) > 0 and part["pool_units"].get(pool, 0) > 0
                ]
                if not pools:
                    continue
                key = transfer["command_id"], part["command_id"], tuple(part["node_ids"])
                link = links.setdefault(key, {
                    "transfer_command_id": transfer["command_id"],
                    "controlled_command_id": part["command_id"],
                    "submit_ts_ms": when,
                    "complete_ts_ms": transfer.get("complete_ts_ms"),
                    "transfer_stream_elapsed_ms": transfer.get("transfer_stream_elapsed_ms"),
                    "batch_actual_bytes": transfer.get("actual_bytes"),
                    "part_actual_bytes": part["actual_bytes"],
                    "part_pool_units": part["pool_units"],
                    "part_node_ids": part["node_ids"],
                    "single_node_part": len(part["node_ids"]) == 1,
                    "node_pool_evidence": part["node_pool_evidence"],
                    "matched_parked_node_ids": [],
                    "parked_ids_in_following_match_path": [],
                    "overlapping_pool_presence": [],
                    "creation_identity_in_native_receipt": "unrecorded",
                    "component_forward_reuse": "unproven",
                })
                link["matched_parked_node_ids"] = sorted(set(
                    link["matched_parked_node_ids"] + [node]
                ))
                if node in path_nodes:
                    link["parked_ids_in_following_match_path"] = sorted(set(
                        link["parked_ids_in_following_match_path"] + [node]
                    ))
                link["overlapping_pool_presence"] = sorted(set(
                    link["overlapping_pool_presence"] + pools
                ))
            node_parks = [row for row in parks if row["node_id"] == node]
            next_index = bisect_right([row["ts_ms"] for row in node_parks], parked)
            if next_index < len(node_parks):
                next_park = node_parks[next_index]
                for _, transfer, part in loads.get(node, ())[left:bisect_left(
                    times.get(node, ()), next_park["ts_ms"],
                )]:
                    ack = transfer.get("complete_ts_ms")
                    overlapping = [
                        pool for component, pool in (("0", "kv"), ("2", "mamba"))
                        if all(
                            row["freed_units"].get(component, row["freed_units"].get(
                                int(component), 0,
                            )) > 0 for row in (park, next_park)
                        ) and part["pool_units"].get(pool, 0) > 0
                    ]
                    if (
                        overlapping and ack is not None
                        and ack <= next_park["ts_ms"] <= observed
                    ):
                        cycles.append({
                            "node_id": node, "first_demotion_ts_ms": parked,
                            "restore_command_id": transfer["command_id"],
                            "restore_ack_ts_ms": ack,
                            "next_demotion_ts_ms": next_park["ts_ms"],
                            "single_node_part": len(part["node_ids"]) == 1,
                            "overlapping_pool_presence": overlapping,
                            "identity_scope": "node_id_only",
                            "component_forward_reuse": "unproven",
                        })
        results.append({
            "workflow_id": workflow, "invocation_id": invocation,
            "context_id": context, "context_epoch": epoch,
            "sources": sorted({row["source"] for row in parks}),
            "demotion_records": len(parks), "parked_node_ids": sorted(parked_nodes),
            "first_demotion_ts_ms": first, "last_demotion_ts_ms": parks[-1]["ts_ms"],
            "cumulative_freed_units": dict(freed),
            "following_request_id": attrs.get("request_id"),
            "following_request_epoch": (following or {}).get("context_epoch"),
            "following_native_queue_ts_ms": (following or {}).get("ts_ms"),
            "following_first_service_observed": following is not None,
            "first_service_path_observed_ts_ms": observed,
            "following_first_service_precedes_demotion": (
                observed < first if observed is not None else None
            ),
            "demotion_to_first_service_observation_ms": (
                observed - first if observed is not None else None
            ),
            "following_cached_tokens_host": attrs.get("cached_tokens_host"),
            "following_mamba_host_hit_slots": attrs.get("mamba_host_hit_slots"),
            "parked_ids_in_following_match_path": sorted(parked_nodes & path_nodes.keys()),
            "prior_same_node_prepare_acks": prior_backups,
            "subsequent_same_node_h2d": list(links.values()) if observed is not None else None,
            "reload_then_repark_before_following_service": cycles if observed is not None else None,
            "demotion_creation_identity": "unrecorded",
            "interpretation": (
                "First higher-epoch native request for the same invocation/context; "
                "native llm_submit is emitted at first service with queue time. "
                "Match-path observed_ts bounds the observer's first launch, not CUDA "
                "kernel start. Node IDs and overlapping batch pool presence are "
                "associations, not generation identity or component reuse proof."
            ),
        })
    return results


def summarize(rows):
    counts, units, batches = Counter(), Counter(), {}
    for row in rows:
        counts["wait_context_epochs"] += 1
        counts["demotion_records"] += row["demotion_records"]
        units.update(row["cumulative_freed_units"])
        counts["following_first_service_observed"] += row["following_first_service_observed"]
        counts["following_first_service_time_unknown"] += (
            row["first_service_path_observed_ts_ms"] is None
        )
        counts["following_first_service_precedes_demotion"] += (
            row["following_first_service_precedes_demotion"] is True
        )
        counts["with_prior_same_node_prepare_ack"] += bool(row["prior_same_node_prepare_acks"])
        counts["with_parked_id_in_following_match_path"] += bool(
            row["parked_ids_in_following_match_path"]
        )
        counts["with_subsequent_same_node_h2d"] += bool(row["subsequent_same_node_h2d"])
        counts["with_reload_then_repark_association"] += bool(
            row["reload_then_repark_before_following_service"]
        )
        for link in row["subsequent_same_node_h2d"] or ():
            batches[link["transfer_command_id"]] = link
    return {
        "counts": dict(counts), "cumulative_freed_units": dict(units),
        "demotion_to_first_service_observation_ms": distribution(
            row["demotion_to_first_service_observation_ms"] for row in rows
        ),
        "associated_h2d_batches": len(batches),
        "associated_h2d_whole_batch_bytes": sum(
            row["batch_actual_bytes"] or 0 for row in batches.values()
        ),
        "associated_h2d_whole_batch_stream_ms": sum(
            row["transfer_stream_elapsed_ms"] or 0 for row in batches.values()
        ),
        "associated_batch_semantics": (
            "Deduplicated complete batches containing a parked node ID and overlapping "
            "pool presence before the following service observation. Bytes and time "
            "include other nodes/pools and are not attributed to the parked agent, "
            "proof of component reuse, wasted traffic, or removable JCT."
        ),
    }


def audit(arm):
    sealed = SealedRecords()
    required = [
        arm / "opportunities/admission_opportunities.jsonl",
        arm / "server/transfer_telemetry.jsonl",
        arm / "server/runtime_events.sglang.jsonl",
        arm / "server/physical_action_ack.jsonl",
        arm / "server/native_capacity_census.json",
    ]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)

    prepares, parks, clocks = {}, [], 0
    for row in sealed(required[0]):
        if row["event"] == "prepare_native_issued":
            prepares[row["command_id"]] = row
        elif row["event"] == "parent_pressure_demoted":
            parks.append(row)
        elif row["event"] == "safe_point_census":
            clocks += 1
    acknowledgments = {
        row["command_id"]: row for row in sealed(required[3])
        if row.get("action") == "PREPARE_HOST"
    }
    backups = {}
    for source in sorted({row["source"] for row in prepares.values()}):
        selected = [row for row in prepares.values() if row["source"] == source]
        acked = [
            acknowledgments[row["command_id"]] for row in selected
            if row["command_id"] in acknowledgments
        ]
        backups[source] = {
            "node_commands": len(selected), "acknowledged_commands": len(acked),
            "actual_pool_bytes": {
                pool: sum((row.get("pool_bytes") or {}).get(pool, 0) for row in acked)
                for pool in ("kv", "mamba")
            },
        }
    capacity_bytes = required[4].read_bytes()
    capacity = json.loads(capacity_bytes)["capacity"]
    rows = recoveries(
        parks, prepares,
        sealed(required[1]), sealed(required[2]),
    )
    return {
        "schema_version": 1, "arm": str(arm),
        "summary": summarize(rows),
        "by_source": {
            source: summarize([row for row in rows if source in row["sources"]])
            for source in sorted({source for row in rows for source in row["sources"]})
        },
        "prepare_backups_by_source": backups,
        "capacity": capacity,
        "capacity_source": {
            "path": str(required[4]), "bytes": len(capacity_bytes),
            "sha256": hashlib.sha256(capacity_bytes).hexdigest(),
        },
        "tool_prediction_timing": {
            "measured": False, "scheduler_clock_census_records": clocks,
            "reason": (
                "This audit uses server wall clocks for recovery associations. "
                "It does not convert client monotonic tool boundaries into wall time; "
                "no tool timing precision is inferred."
            ),
        },
        "rows": rows, "input_jsonl_prefixes": sealed.prefixes,
        "prefix_semantics": (
            "Each file is capped at its first-read stat size and last complete newline. "
            "Repeated reads verify byte length and SHA256. This is not an atomic "
            "cross-file snapshot; missing following requests and open tools are censored, "
            "not failure or no-reuse labels. Native untagged eviction is separate."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--collection-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output already exists; choose a new report ID")
    started = datetime.now(timezone.utc).isoformat()
    report = audit(args.arm.resolve())
    report.update({
        "collection_application_commit": args.collection_commit,
        "audit_started_at": started, "audit_finished_at": datetime.now(timezone.utc).isoformat(),
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/audit_sealed_wait_prefetch.py",
                ROOT / "scripts/audit_prefetch_lifecycle.py",
            )
        },
    })
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output), "summary": report["summary"],
        "by_source": report["by_source"],
        "prepare_backups_by_source": report["prepare_backups_by_source"],
        "tool_prediction_timing": report["tool_prediction_timing"],
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
