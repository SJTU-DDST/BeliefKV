#!/usr/bin/env python3
"""Separate terminal release evidence from shared ancestry and internal requests."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from scripts.audit_native_hybrid_reuse import records
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_prefetch_lifecycle import (
    terminal_capacity_summary,
    terminal_release_totals,
)


RELEASE_EVENTS = {
    "terminal_child_cache_reclaimed", "terminal_workflow_root_cache_reclaimed",
}
SCOPES = {"dynamic_subagent", "runtime_internal", "workflow_root", "other"}
IDENTITY_FIELDS = (
    "workflow_id", "invocation_id", "context_id", "session_id", "session_generation",
)
CHECKPOINT_FIELDS = (
    "workflow_id", "invocation_id", "context_id", "context_epoch", "session_id",
)
LOCK_FIELDS = (
    "full_device_locks", "full_host_locks", "mamba_device_locks", "mamba_host_locks",
)
REF_FIELDS = ("full_session_refs", "mamba_session_refs")


def scope_for(row, scopes):
    client = scopes.get((row["workflow_id"], row["invocation_id"]))
    runtime = row.get("invocation_scope")
    runtime = runtime if runtime in SCOPES else None
    if client is not None and runtime is not None and client != runtime:
        return "conflicting"
    return client or runtime or "unknown"


def scope_from_create(row):
    if (row.get("attributes") or {}).get("runtime_internal") is True:
        return "runtime_internal"
    return {
        "spawn": "dynamic_subagent", "root": "workflow_root",
    }.get(row.get("relation_type"), "other")


def ancestry_counts(row):
    nodes = {}
    duplicates = 0
    for node in row["nodes"]:
        identity = node["node_id"], node["creation_time"]
        if identity in nodes:
            if node != nodes[identity]:
                raise ValueError("conflicting node observations in one snapshot")
            duplicates += 1
        nodes[identity] = node
    return {
        "nodes": len(nodes),
        "duplicate_node_observations": duplicates,
        "full_device_tokens": sum(node["full_device_tokens"] for node in nodes.values()),
        "full_host_tokens": sum(node["full_host_tokens"] for node in nodes.values()),
        "mamba_device_slots": sum(node["mamba_device_present"] for node in nodes.values()),
        "mamba_host_slots": sum(node["mamba_host_present"] for node in nodes.values()),
        "nodes_with_positive_session_refs": sum(
            any(node.get(field, 0) > 0 for field in REF_FIELDS)
            for node in nodes.values()
        ),
        "nodes_with_multiple_session_refs": sum(
            any(node.get(field, 0) > 1 for field in REF_FIELDS)
            for node in nodes.values()
        ),
        "nodes_with_locks": sum(
            any(node.get(field, 0) > 0 for field in LOCK_FIELDS)
            for node in nodes.values()
        ),
        "nodes_with_pending_transfer": sum(
            node.get("pending_write_id") is not None
            or node.get("pending_load_id") is not None
            for node in nodes.values()
        ),
        "nodes_with_unobserved_protection_fields": sum(
            any(field not in node for field in (
                *REF_FIELDS, *LOCK_FIELDS, "pending_write_id", "pending_load_id",
            )) for node in nodes.values()
        ),
        "gone_or_replaced_anchors": len(row.get("gone_or_replaced_anchors", ())),
        "unavailable_anchors": len(row.get("unavailable_anchors", ())),
        "elapsed_since_terminal_ms": row["elapsed_since_terminal_ms"],
    }


def snapshot_distributions(rows):
    counts = [ancestry_counts(row) for row in rows]
    return {
        name: {
            key: value for key, value in distribution(
                row[name] for row in counts
            ).items() if key != "sum"
        } for name in sorted({name for row in counts for name in row})
    }


def summarize_snapshots(rows):
    checkpoints = defaultdict(list)
    for row in rows:
        checkpoints[tuple(row.get(field) for field in CHECKPOINT_FIELDS)].append(row)
    for observations in checkpoints.values():
        observations.sort(key=lambda row: row["ts_ms"])
    return {
        "sample_records": len(rows), "sampled_checkpoint_keys": len(checkpoints),
        "sampled_invocations": len({
            (row["workflow_id"], row["invocation_id"]) for row in rows
        }),
        "first_observation": snapshot_distributions(
            [observations[0] for observations in checkpoints.values()]
        ),
        "last_observation": snapshot_distributions(
            [observations[-1] for observations in checkpoints.values()]
        ),
    }


def summarize_releases(rows):
    grouped = defaultdict(list)
    for row in rows:
        if any(row.get(field) is None for field in IDENTITY_FIELDS):
            raise ValueError("terminal release lacks a complete context/session identity")
        if not finite_number(row.get("ts_ms")):
            raise ValueError("terminal release lacks a finite scheduler timestamp")
        grouped[tuple(row[field] for field in IDENTITY_FIELDS)].append(row)
    delays, unknown_delays, pending, reasons = [], 0, [], Counter()
    discarded = []
    for events in grouped.values():
        events.sort(key=lambda row: row["ts_ms"])
        positive = [
            row for row in events if any(
                value > 0 for field in ("freed_device_units", "freed_host_units")
                for value in row.get(field, {}).values()
            )
        ]
        if positive:
            first = positive[0]
            observed = first.get("child_return_observed_wall_ts_ms")
            if finite_number(observed) and observed <= first["ts_ms"]:
                delays.append(first["ts_ms"] - observed)
            else:
                unknown_delays += 1
        pending.append(events[-1].get("pending_anchors"))
        reasons[str(events[-1].get("reason"))] += 1
        for row in events:
            discarded.extend(tuple(node) for node in row.get("discarded_nodes", ()))
    return {
        **terminal_release_totals(rows),
        "contexts_with_observed_reclaim_record": len(grouped),
        "retry_records": sum(row.get("retry") is True for row in rows),
        "first_positive_release_after_scheduler_terminal_ms": distribution(delays),
        "contexts_with_positive_release_but_unknown_delay": unknown_delays,
        "last_recorded_reason_counts": dict(reasons),
        "contexts_last_recorded_with_pending_anchors": sum(
            finite_number(value) and value > 0 for value in pending
        ),
        "contexts_with_unknown_last_recorded_pending_anchors": sum(
            not finite_number(value) for value in pending
        ),
        "discarded_node_identities": len(set(discarded)),
        "duplicate_discarded_node_identity_records": len(discarded) - len(set(discarded)),
    }


def analyze(events, scopes):
    releases, samples = defaultdict(list), defaultdict(list)
    services, lookaheads = [], []
    for row in events:
        event = row.get("event")
        if event in RELEASE_EVENTS:
            releases[scope_for(row, scopes)].append(row)
        elif event == "terminal_context_cache_sample":
            if not finite_number(row.get("ts_ms")):
                raise ValueError("terminal snapshot lacks a finite timestamp")
            samples[scope_for(row, scopes)].append(row)
        elif event == "parent_capacity_handoff_first_service":
            services.append(row)
        elif event == "parent_capacity_handoff_restore_lookahead":
            lookaheads.append(row)
    normalized = [
        {**row, "invocation_scope": scope}
        for scope, rows in releases.items() for row in rows
    ]
    terminal_checkpoints = {}
    for row in normalized:
        key = tuple(row.get(field) for field in CHECKPOINT_FIELDS)
        observed = row.get("child_return_observed_wall_ts_ms")
        if finite_number(observed):
            terminal_checkpoints[key] = min(
                terminal_checkpoints.get(key, observed), observed,
            )
    snapshots, terminal_snapshots = {}, {}
    for scope, rows in samples.items():
        snapshots[scope] = summarize_snapshots(rows)
        matched = [
            row for row in rows
            if tuple(row.get(field) for field in CHECKPOINT_FIELDS) in terminal_checkpoints
        ]
        terminal_snapshots[scope] = {
            **summarize_snapshots(matched),
            "records_after_scheduler_terminal_observation": sum(
                row["ts_ms"] >= terminal_checkpoints[
                    tuple(row.get(field) for field in CHECKPOINT_FIELDS)
                ] for row in matched
            ),
            "records_without_matching_terminal_release_checkpoint": len(rows) - len(matched),
        }
    return {
        "schema_version": 2,
        "release_by_invocation_scope": {
            scope: summarize_releases(rows) for scope, rows in sorted(releases.items())
        },
        "retired_checkpoint_ancestry_by_invocation_scope": snapshots,
        "observed_terminal_release_checkpoint_ancestry_by_invocation_scope": terminal_snapshots,
        "parent_handoff_observations": terminal_capacity_summary(
            normalized, services, lookaheads,
        ),
        "semantics": (
            "Release bytes are actual cumulative allocator releases, not simultaneous "
            "occupancy, avoided transfers, exclusive parent capacity, or JCT savings. "
            "Only explicit invocation-create metadata or recorded runtime scope classify "
            "contexts; conflicting evidence stays separate. Scheduler terminal-observation "
            "timestamps are used for release delay; client event clocks are not subtracted. "
            "A last recorded pending-anchor count is not a live backlog: retries with "
            "no release need not emit records. The terminal_context_cache_sample "
            "event is also emitted on ordinary context-epoch invalidation: its name "
            "does not prove an invocation RETURN. Retired checkpoint counts are not "
            "returned-agent counts. The matched subset shares the exact workflow/"
            "invocation/context/epoch/session key of an observed terminal release; "
            "only its separately counted later records postdate that scheduler "
            "terminal observation. Earlier matching samples are not post-terminal "
            "occupancy. Missing release evidence remains unproven. "
            "Ancestry includes shared prefixes and "
            "protected state; its per-context distributions are neither globally additive "
            "nor a measurement of exclusively dead/reclaimable bytes. Missing runtime "
            "instrumentation is unavailable evidence, never zero native lifecycle cost. "
            "Transfers lack the node creation/session ownership needed to prove bytes "
            "sent after a terminal boundary; no such avoided-DMA estimate is made."
        ),
    }


def audit_arm(arm):
    bindings, events, scopes = [], [], {}
    opportunities = arm / "opportunities/admission_opportunities.jsonl"
    if not opportunities.exists():
        return {
            "arm": str(arm.resolve()), "status": "terminal_runtime_observations_unavailable",
            "inputs": [], "summary": None,
        }
    for row in records(
        opportunities, bindings,
        contains=lambda line: b"terminal_" in line or b"parent_capacity_handoff_" in line,
    ):
        events.append(row)
    for path in sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
        for row in records(path, bindings, contains=lambda line: b"invocation_create" in line):
            if row.get("kind") == "invocation_create":
                identity = row["workflow_id"], row["invocation_id"]
                scope = scope_from_create(row)
                if identity in scopes and scopes[identity] != scope:
                    raise ValueError("conflicting invocation-create metadata")
                scopes[identity] = scope
    source_paths = (
        Path(__file__), Path(__file__).with_name("audit_prefetch_lifecycle.py"),
        Path(__file__).with_name("audit_native_hybrid_reuse.py"),
        Path(__file__).with_name("audit_native_system_costs.py"),
    )
    return {
        "arm": str(arm.resolve()), "status": "observed_complete_line_prefix",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": bindings,
        "sources": [
            {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in source_paths
        ],
        "client_invocations_with_scope_metadata": len(scopes),
        "summary": analyze(events, scopes),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(audit_arm(args.arm), indent=2) + "\n")


if __name__ == "__main__":
    main()
