#!/usr/bin/env python3
"""Attribute native hybrid-prefix gaps and backup granularity without GPU probes."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from scripts.audit_native_system_costs import distribution, union_ms


def records(path, bindings, *, contains=None):
    size = path.stat().st_size
    remaining, analyzed = size, 0
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while remaining:
            line = stream.readline(remaining)
            remaining -= len(line)
            if not line or not line.endswith(b"\n"):
                break
            digest.update(line)
            analyzed += len(line)
            if not line.strip() or (contains and not contains(line)):
                continue
            yield json.loads(line)
    bindings.append({
        "path": str(path.resolve()), "observed_size_bytes": size,
        "analyzed_bytes": analyzed, "sha256": digest.hexdigest(),
        "discarded_tail_bytes": size - analyzed,
    })


def read_requests(client, bindings):
    requests = {}
    for path in sorted((client / "workflows").glob("*/runtime_events.deepagents.jsonl")):
        events = list(records(path, bindings))
        creates = {
            event["invocation_id"]: event
            for event in events if event.get("kind") == "invocation_create"
        }
        compacted, previous = set(), {}
        tools, joined, join_waiters = defaultdict(set), set(), defaultdict(set)
        for event in sorted(events, key=lambda row: (row["ts_ms"], row["sequence"])):
            kind, attrs = event.get("kind"), event.get("attributes") or {}
            invocation = event.get("invocation_id")
            if kind == "context_compact":
                compacted.add(invocation)
            elif kind == "tool_end":
                tools[invocation].add(str(attrs.get("tool_name") or "unknown"))
            elif kind == "join_wait":
                if event.get("join_id") and invocation is not None:
                    join_waiters[event["join_id"]].add(invocation)
            elif kind == "join_satisfied":
                waiters = join_waiters.get(event.get("join_id"), set())
                if invocation is not None and waiters and waiters != {invocation}:
                    raise ValueError("JOIN completion differs from its observed waiter")
                owner = invocation
                if owner is None and len(waiters) == 1:
                    owner = next(iter(waiters))
                if owner is not None:
                    joined.add(owner)
            elif kind == "llm_submit":
                request = attrs.get("request_id")
                if not request or request in requests:
                    raise ValueError(f"missing or duplicate client request: {request!r}")
                internal = bool(attrs.get("runtime_internal"))
                create = creates.get(invocation) or {}
                requests[request] = {
                    "role": (
                        "summary" if internal
                        else "child" if create.get("parent_invocation_id")
                        else "root"
                    ),
                    "first_observed_invocation_request": invocation not in previous,
                    "first_after_compaction": invocation in compacted,
                    "follows_observed_join": not internal and invocation in joined,
                    "tools_since_previous_request": sorted(tools[invocation]),
                    "previous_client_request": previous.get(invocation),
                }
                if not internal:
                    compacted.discard(invocation)
                previous[invocation] = request
                tools[invocation].clear()
                if not internal:
                    joined.discard(invocation)
    return requests


def interval_overlap(start, length, left, right):
    return max(0, min(start + length, right) - max(start, left))


def prefill_costs(events, requests):
    totals, groups = Counter(), defaultdict(Counter)
    durations = defaultdict(list)
    per_request = defaultdict(Counter)
    identities = set()
    for event in events:
        if event.get("event") != "gpu_service_sample" or event.get("phase") != "prefill":
            continue
        sample_id = event.get("sample_id")
        if not sample_id or sample_id in identities:
            raise ValueError(f"duplicate or missing service identity: {sample_id!r}")
        identities.add(sample_id)
        samples = event.get("request_samples") or []
        roles = set()
        batch_gap = 0
        unknown = not samples
        for sample in samples:
            request = sample.get("request_id")
            meta = requests.get(request)
            if meta is None:
                totals["samples_without_native_request"] += 1
                unknown = True
                continue
            roles.add(meta.get("role", "unknown"))
            prefix = sample.get("sequence_tokens_before")
            count = sample.get("token_delta")
            if (
                sample.get("token_delta_semantics") != "prefill_extend_input_len"
                or type(prefix) is not int or type(count) is not int
                or prefix < 0 or count < 0
            ):
                totals["samples_without_extend_geometry"] += 1
                unknown = True
                continue
            left, right = meta["effective_prefix"], meta["full_hit"]
            overlap = interval_overlap(prefix, count, left, right)
            totals["extend_tokens"] += count
            totals["full_match_gap_extend_tokens"] += overlap
            per_request[request]["extend_tokens"] += count
            per_request[request]["full_match_gap_extend_tokens"] += overlap
            batch_gap += overlap
        group = (
            "unknown_geometry_or_identity" if unknown
            else "with_full_match_gap" if batch_gap
            else "without_full_match_gap"
        )
        groups[group]["batches"] += 1
        groups[group]["single_request_batches"] += int(len(samples) == 1)
        groups[group]["request_samples"] += len(samples)
        elapsed = event.get("service_elapsed_ms")
        if isinstance(elapsed, (int, float)) and elapsed >= 0:
            durations[group].append(elapsed)
            if len(samples) == 1:
                durations[group + "_single_request"].append(elapsed)
        groups[group]["role_set:" + "+".join(sorted(roles))] += 1
    return {
        "totals": dict(totals), "prefill_batches": len(identities),
        "batch_groups": {
            key: {
                **dict(counts), "worker_interval_ms": distribution(durations[key]),
                "single_request_worker_interval_ms": distribution(
                    durations[key + "_single_request"]
                ),
            }
            for key, counts in groups.items()
        },
        "semantics": (
            "Token overlap is native extend geometry at full-match/effective-prefix "
            "positions, not per-layer kernel work. Whole batch worker intervals "
            "are not allocated to gap tokens or interpreted as removable GPU time."
        ),
    }, per_request


def transfer_costs(events, bytes_per_unit):
    grouped, seen = defaultdict(list), set()
    d2h_by_anchor, later_anchor_acks = defaultdict(list), set()
    h2d_rows = []
    d2h_anchor_counts = Counter()
    for event in events:
        if event.get("event") != "transfer_telemetry" or event.get("status") != "completed":
            continue
        command = event.get("command_id")
        if not command or command in seen:
            raise ValueError(f"duplicate or missing transfer identity: {command!r}")
        seen.add(command)
        pools = event.get("num_tokens_by_pool") or {}
        known_bytes = sum(
            pools.get(pool, 0) * size for pool, size in bytes_per_unit.items()
        )
        if known_bytes != event.get("actual_bytes"):
            raise ValueError(f"pool bytes do not equal actual bytes: {command}")
        shape = (
            "full_and_mamba" if pools.get("kv", 0) and pools.get("mamba", 0)
            else "full_only" if pools.get("kv", 0)
            else "mamba_only" if pools.get("mamba", 0)
            else "empty"
        )
        row = {
            "command_id": command, "bytes": known_bytes,
            "full_tokens": pools.get("kv", 0), "mamba_slots": pools.get("mamba", 0),
            "node_count": len(event.get("node_ids") or []),
            "node_ids": event.get("node_ids") or [],
            "submit_ms": event["submit_ts_ms"], "complete_ms": event["complete_ts_ms"],
            "transfer_stream_ms": event.get("transfer_stream_elapsed_ms"),
            "submit_to_ack_ms": event.get("submit_to_ack_ms"),
        }
        direction = event.get("direction")
        grouped[direction + ":" + shape].append(row)
        if direction == "d2h":
            for node in event.get("node_ids") or []:
                d2h_anchor_counts[node] += 1
                d2h_by_anchor[node].append(row)
        elif direction == "h2d":
            h2d_rows.append(row)
    for row in h2d_rows:
        for node in row["node_ids"]:
            previous = max(
                (
                    candidate for candidate in d2h_by_anchor[node]
                    if candidate["complete_ms"] <= row["submit_ms"]
                ),
                key=lambda candidate: candidate["complete_ms"], default=None,
            )
            if previous is not None:
                later_anchor_acks.add(previous["command_id"])
    result = {}
    for key, rows in grouped.items():
        result[key] = {
            "batches": len(rows), "bytes": sum(row["bytes"] for row in rows),
            "full_tokens": distribution(row["full_tokens"] for row in rows),
            "mamba_slots": distribution(row["mamba_slots"] for row in rows),
            "nodes_per_batch": distribution(row["node_count"] for row in rows),
            "transfer_stream_ms": distribution(
                row["transfer_stream_ms"] for row in rows
                if isinstance(row["transfer_stream_ms"], (int, float))
            ),
            "submit_to_ack_ms": distribution(
                row["submit_to_ack_ms"] for row in rows
                if isinstance(row["submit_to_ack_ms"], (int, float))
            ),
            "submit_to_complete_union_ms": union_ms(
                (row["submit_ms"], row["complete_ms"]) for row in rows
            ),
            "full_lt_512_token_batches": sum(row["full_tokens"] < 512 for row in rows),
        }
    return {
        "by_direction_and_shape": result,
        "unique_d2h_anchors": len(d2h_anchor_counts),
        "d2h_anchor_occurrences": sum(d2h_anchor_counts.values()),
        "anchors_with_multiple_d2h": sum(count > 1 for count in d2h_anchor_counts.values()),
        "d2h_commands_followed_by_same_anchor_h2d_ack": len(later_anchor_acks),
        "semantics": (
            "An anchor may cover other FULL nodes. Same-anchor later ACK is only "
            "an association, not per-component first-service reuse; unmatched "
            "D2H is not classified as wasted. Submit/ACK envelopes and transfer "
            "stream time are not measured scheduler CPU blocking or JCT savings."
        ),
    }


def analyze(arm):
    bindings = []
    client = arm / "client_156"
    client_requests = read_requests(client, bindings)
    requests = {}
    for event in records(arm / "server/runtime_events.sglang.jsonl", bindings):
        if event.get("kind") != "llm_submit":
            continue
        attrs = event["attributes"]
        request = attrs["request_id"]
        if request in requests:
            raise ValueError(f"duplicate native request: {request}")
        prefix = attrs["cached_tokens_device"] + attrs["cached_tokens_host"]
        full = attrs["native_full_kv_hit_length"]
        if not 0 <= prefix <= attrs["prompt_tokens"] or not 0 <= full <= attrs["prompt_tokens"]:
            raise ValueError(f"invalid native prefix lengths: {request}")
        requests[request] = {
            **client_requests.get(request, {"role": "unknown"}),
            "request_id": request, "workflow_id": event["workflow_id"],
            "context_id": event["context_id"], "context_epoch": event["context_epoch"],
            "effective_prefix": prefix, "full_hit": full, "full_gap": max(0, full - prefix),
            "branching_target": attrs.get("native_mamba_branching_seqlen"),
            "prompt_tokens": attrs["prompt_tokens"],
        }
    probes = {}
    for event in records(
        arm / "server/eviction_attribution.jsonl", bindings,
        contains=lambda line: b'"context_prefix_reuse_probe"' in line,
    ):
        if event.get("event") != "context_prefix_reuse_probe":
            continue
        request = event["request_id"]
        if request in probes:
            raise ValueError(f"duplicate prefix probe: {request}")
        probes[request] = event
    for request, meta in requests.items():
        probe = probes.get(request)
        meta["same_session_previous_input_missing_tokens"] = (
            probe["previously_served_prefix_recompute_proxy_tokens"] if probe else None
        )
        meta["previously_served_common_input_tokens"] = (
            probe["common_previously_served_input_tokens"] if probe else None
        )
    service, per_request = prefill_costs(
        records(
            arm / "server/runtime_audit.jsonl", bindings,
            contains=lambda line: b'"gpu_service_sample"' in line and b'"prefill"' in line,
        ), requests,
    )
    for request, counts in per_request.items():
        requests[request].update(counts)
    return bindings, requests, probes, service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bindings, requests, probes, service = analyze(args.arm.resolve())
    status_path = args.arm / "server/native_telemetry_status.json"
    status_bytes = status_path.read_bytes()
    status = json.loads(status_bytes)
    bindings.append({
        "path": str(status_path.resolve()), "analyzed_bytes": len(status_bytes),
        "sha256": hashlib.sha256(status_bytes).hexdigest(),
    })
    pool_evidence = status["host_pool_evidence"]
    transfers = transfer_costs(
        records(args.arm / "server/transfer_telemetry.jsonl", bindings),
        {
            "kv": pool_evidence["full"]["bytes_per_unit"],
            "mamba": pool_evidence["mamba"]["bytes_per_unit"],
        },
    )
    gap_rows = [row for row in requests.values() if row["full_gap"] >= 512]
    groups = defaultdict(list)
    for row in gap_rows:
        stage = (
            "after_compaction" if row.get("first_after_compaction")
            else "first_invocation_request" if row.get("first_observed_invocation_request")
            else "ordinary_continuation"
        )
        groups[row["role"] + ":" + stage].append(row)
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "auditor_path": str(Path(__file__).resolve()),
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sources": bindings, "native_requests": len(requests),
        "native_requests_without_client_identity": sum(
            row["role"] == "unknown" for row in requests.values()
        ),
        "same_session_prefix_probe_requests": len(probes),
        "same_session_previous_input_missing_tokens": distribution(
            event["previously_served_prefix_recompute_proxy_tokens"]
            for event in probes.values()
        ),
        "large_gap_by_role_and_stage": {
            key: {
                "requests": len(rows), "full_gap_tokens": sum(row["full_gap"] for row in rows),
                "same_session_previous_input_missing_tokens": distribution(
                    row["same_session_previous_input_missing_tokens"] for row in rows
                    if row["same_session_previous_input_missing_tokens"] is not None
                ),
                "observed_extend_tokens": sum(row.get("extend_tokens", 0) for row in rows),
                "observed_gap_extend_tokens": sum(
                    row.get("full_match_gap_extend_tokens", 0) for row in rows
                ),
            }
            for key, rows in sorted(groups.items())
        },
        "prefill": service, "transfers": transfers, "large_gap_requests": gap_rows,
        "limits": [
            "Client and native identities are joined exactly; no text similarity matching.",
            "Missing prior-input probes are unknown, not zero.",
            "Native prefix counters and worker intervals are not per-layer kernel FLOPs.",
            "A matching FULL gap is not assumed to be removable Mamba recomputation.",
            "No counterfactual throughput, quality or JCT improvement is established.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output), "native_requests": len(requests),
        "large_gap_by_role_and_stage": report["large_gap_by_role_and_stage"],
        "prefill": service, "transfers": transfers,
    }))


if __name__ == "__main__":
    main()
