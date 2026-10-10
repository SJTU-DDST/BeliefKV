#!/usr/bin/env python3
"""Link predictive transfers to EOS, first use, leases and repeated parking."""

from __future__ import annotations

import argparse
from bisect import bisect_right
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


def full_reuse_summary(full: list[dict]) -> dict:
    known_full = [row for row in full if row["pool_bytes"] is not None]
    return {
        "full_transfer_commands": len(full),
        "full_reused_commands": sum(row["full_first_service_reused"] is True for row in full),
        "full_not_reused_commands": sum(row["full_first_service_reused"] is False for row in full),
        "full_use_unknown_commands": sum(row["full_first_service_reused"] is None for row in full),
        "full_pool_bytes_unknown_commands": len(full) - len(known_full),
        "full_transferred_bytes_known": sum(row["pool_bytes"].get("kv", 0) for row in known_full),
        "full_verified_reused_bytes_known": sum(
            row["pool_bytes"].get("kv", 0) for row in known_full
            if row["full_first_service_reused"] is True
        ),
        "full_reuse_proof_versions": dict(Counter(
            str(row["full_reuse_proof_version"]) for row in full
            if row["full_reuse_proof_version"] is not None
        )),
        "native_reloaded_prefetched_full_before_first_service": sum(
            any("kv" in load["prefetched_pool_overlap"]
                for load in row["native_reloads_before_first_service"])
            for row in full
        ),
    }


def full_residency_summary(full: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in full:
        lease_events = row.get("lease_events", [])
        registered = next(
            (event for event in lease_events
             if event["event"] == "prefetch_residency_registered"), {},
        )
        released = next(
            (event for event in reversed(lease_events)
             if event["event"] == "prefetch_residency_released"), {},
        )
        locked = registered.get("native_locked")
        if type(locked) is not bool:
            locked = None
        groups[locked, released.get("reason")].append(row)
    return [
        {
            "native_locked_at_registration": locked,
            "last_observed_release_reason": reason,
            **full_reuse_summary(group),
            "ack_to_first_service_ms": distribution(
                row["ack_to_first_service_ms"] for row in group
            ),
        }
        for (locked, reason), group in sorted(
            groups.items(), key=lambda item: (str(item[0][0]), item[0][1] or ""),
        )
    ]


def source_summary(rows: list[dict]) -> dict:
    full = [row for row in rows if (row["pool_units"] or {}).get("kv", 0) > 0]
    return {
        "issued_commands": len(rows),
        "acknowledged_commands": sum(row["actual_bytes"] is not None for row in rows),
        "actual_bytes": sum(row["actual_bytes"] or 0 for row in rows),
        **full_reuse_summary(full),
        "full_reuse_by_residency": full_residency_summary(full),
        "mamba_forward_verified_commands": sum(row["mamba_forward_verified"] for row in rows),
        "ack_to_first_service_ms": distribution(row["ack_to_first_service_ms"] for row in rows),
    }


def transfer_parts(transfer: dict) -> list[dict]:
    """Separate native operations from tagged children in a merged transfer."""
    exact = transfer.get("node_commits") or []
    commits = exact or transfer.get("tagged_child_commits") or []
    covered, accounted, parts = set(), Counter(), []
    for child in commits:
        nodes = child.get("published_node_ids")
        if not nodes:
            anchor = child.get("anchor_node_id")
            if anchor is None:
                continue
            nodes = [anchor]
        covered.update(nodes)
        accounted.update(child["num_tokens_by_pool"])
        parts.append({
            "command_id": child.get("command_id"), "node_ids": nodes,
            "pool_units": child["num_tokens_by_pool"],
            "actual_bytes": child.get("num_bytes"),
            "node_pool_evidence": (
                "reconciled_native_receipt" if exact else "legacy_batch_pool_presence"
            ),
        })
    unseen = [node for node in transfer.get("node_ids", []) if node not in covered]
    if unseen:
        residual = {
            pool: count - accounted[pool]
            for pool, count in (transfer.get("num_tokens_by_pool") or {}).items()
            if count > accounted[pool]
        }
        parts.append({
            "command_id": None, "node_ids": unseen, "pool_units": residual,
            "actual_bytes": transfer.get("actual_bytes") if not commits else None,
            "node_pool_evidence": "legacy_batch_pool_presence",
        })
    return parts


def prepare_restore_attribution(
    prepared: dict, transfers: list[dict], host_evictions: list[dict] | None = None,
) -> list[dict]:
    """Associate same-node/pool writes and reloads, without claiming byte overwrite."""
    results, latest = {}, {}
    eviction_times = defaultdict(list)
    for row in host_evictions or ():
        pool = "kv" if row["pool"] == "full" else row["pool"]
        eviction_times[(row["node_id"], pool)].append(row["ts_ms"])
    for times in eviction_times.values():
        times.sort()
    events = []
    for transfer in transfers:
        ts = transfer.get(
            "complete_ts_ms" if transfer["direction"] == "d2h" else "submit_ts_ms",
        )
        if ts is not None:
            events.append((ts, transfer))
    for ts, transfer in sorted(events, key=lambda pair: pair[0]):
        for part in transfer_parts(transfer):
            command, nodes, units = (
                part["command_id"], part["node_ids"], part["pool_units"],
            )
            pools = [pool for pool, count in units.items() if count > 0]
            if transfer["direction"] == "d2h":
                issue = prepared.get(command)
                if issue is not None:
                    results[command] = {
                        **issue, "ack_ts_ms": ts, "published_node_ids": list(nodes),
                        "prepared_pool_units": units, "restores": [],
                        "later_node_pool_d2h": [],
                    }
                for node in nodes:
                    for pool in pools:
                        prior = latest.get((node, pool))
                        if prior is not None and prior != command:
                            later = {"node_id": node, "pool": pool, "ts_ms": ts}
                            if host_evictions is not None:
                                times = eviction_times[(node, pool)]
                                later["host_evictions_between_writes"] = (
                                    bisect_right(times, ts)
                                    - bisect_right(times, results[prior]["ack_ts_ms"])
                                )
                            results[prior]["later_node_pool_d2h"].append(later)
                        latest[node, pool] = command if issue is not None else None
            else:
                matched = defaultdict(list)
                for node in nodes:
                    for pool in pools:
                        prior = latest.get((node, pool))
                        if prior is not None:
                            matched[prior].append({"node_id": node, "pool": pool})
                for prior, matches in matched.items():
                    results[prior]["restores"].append({
                        "transfer_command_id": transfer.get("command_id"),
                        "source": "controlled_h2d" if command is not None else "native_h2d",
                        "submit_ts_ms": ts, "matched_node_pools": matches,
                        "node_pool_evidence": part["node_pool_evidence"],
                        "scope": "observed Host-to-device restore; not final forward-use credit",
                    })
    return sorted(results.values(), key=lambda row: row["ts_ms"])


def prepare_host_lifetime_summary(
    prepared: list[dict], host_evictions: list[dict] | None,
) -> dict:
    """Separate observed restore associations before and after Host reclaim."""
    eviction_times = defaultdict(list)
    for row in host_evictions or ():
        pool = "kv" if row["pool"] == "full" else row["pool"]
        eviction_times[row["node_id"], pool].append(row["ts_ms"])
    for times in eviction_times.values():
        times.sort()
    categories = Counter({
        "no_restore": 0, "before_observed_eviction_only": 0,
        "after_observed_eviction_only": 0, "mixed": 0, "unknown_eviction_evidence": 0,
    })
    matches = Counter({"before_observed_eviction": 0, "after_observed_eviction": 0, "unknown": 0})
    lifetimes = defaultdict(list)
    evicted = Counter()
    pool_commands = Counter()
    identities = Counter()
    missing_identity = 0
    for row in prepared:
        ack = row["ack_ts_ms"]
        before = after = unknown = 0
        for restore in row["restores"]:
            for match in restore["matched_node_pools"]:
                if host_evictions is None:
                    unknown += 1
                    continue
                times = eviction_times[match["node_id"], match["pool"]]
                reclaimed = (
                    bisect_right(times, restore["submit_ts_ms"]) - bisect_right(times, ack)
                )
                if reclaimed:
                    after += 1
                else:
                    before += 1
        matches.update({
            "before_observed_eviction": before, "after_observed_eviction": after,
            "unknown": unknown,
        })
        category = (
            "unknown_eviction_evidence" if unknown
            else "mixed" if before and after
            else "before_observed_eviction_only" if before
            else "after_observed_eviction_only" if after
            else "no_restore"
        )
        categories[category] += 1
        for pool, units in row["prepared_pool_units"].items():
            if units <= 0:
                continue
            pool_commands[pool] += 1
            first_evictions = []
            for node in row["published_node_ids"]:
                times = eviction_times[node, pool]
                position = bisect_right(times, ack)
                next_write = min(
                    (later["ts_ms"] for later in row["later_node_pool_d2h"]
                     if later["node_id"] == node and later["pool"] == pool),
                    default=float("inf"),
                )
                if position < len(times) and times[position] <= next_write:
                    first_evictions.append(times[position])
            if first_evictions:
                evicted[pool] += 1
                lifetimes[pool].append(min(first_evictions) - ack)
        if any(row.get(name) is None for name in (
            "source", "context_id", "context_epoch", "node_creation_time",
        )):
            missing_identity += 1
        else:
            identities[
                row["source"], row["context_id"], row["context_epoch"],
                row["node_id"], row["node_creation_time"],
            ] += 1
    return {
        "host_eviction_evidence_available": host_evictions is not None,
        "command_categories": dict(categories),
        "restore_node_pool_associations": dict(matches),
        "by_pool": {
            pool: {
                "prepared_commands": count,
                "commands_with_observed_eviction": evicted[pool] if host_evictions is not None else None,
                "ack_to_first_observed_eviction_ms": distribution(lifetimes[pool]),
            }
            for pool, count in sorted(pool_commands.items())
        },
        "repeated_context_epoch_node_creation_groups": sum(count > 1 for count in identities.values()),
        "additional_prepares_on_same_identity": sum(count - 1 for count in identities.values()),
        "max_commands_on_same_identity": max(identities.values(), default=0),
        "prepare_identity_evidence_missing_commands": missing_identity,
        "scope": (
            "Node/pool associations, not allocation continuity or model-forward reuse. "
            "A restore after observed Host eviction cannot establish consumption of "
            "the original prepared copy. Eviction latency stops at the next observed "
            "same-node/pool D2H write; absent eviction records are not zero evictions. "
            "Reported latencies include only observed evictions, excluding censored "
            "copies. Repeated context/epoch/node/creation identities are not exact "
            "duplicate bytes and do not establish the same wait episode across splits."
        ),
    }


def prepare_selection_summary(selections: list[dict], prepared: list[dict]) -> dict:
    """Keep candidate potential separate from completed transfers and consumption."""
    acknowledged = {row["command_id"] for row in prepared}
    burst_commands = {
        command for row in selections
        for command in row.get("burst_command_ids", ())
    }
    sources = defaultdict(list)
    for row in selections:
        sources[row["source"]].append(row)
    return {
        "candidate_records": len(selections),
        "records_without_command_id": sum(
            row.get("command_id") is None for row in selections
        ),
        "burst_records": sum(
            len(row.get("burst_command_ids", ())) > 1 for row in selections
        ),
        "explicit_burst_commands": len(burst_commands),
        "acknowledged_burst_commands": len(burst_commands & acknowledged),
        "by_source": {
            source: {
                "candidate_records": len(rows),
                "acknowledged_command_records": sum(
                    row.get("command_id") in acknowledged for row in rows
                ),
                "direct_reclaim_potential_commands": sum(
                    row.get("reclaimable_pressured_bytes") is not None
                    and row["reclaimable_pressured_bytes"] > 0 for row in rows
                ),
                "zero_direct_reclaim_potential_commands": sum(
                    row.get("reclaimable_pressured_bytes") == 0 for row in rows
                ),
                "direct_reclaim_potential_unknown_commands": sum(
                    row.get("reclaimable_pressured_bytes") is None for row in rows
                ),
                "selected_transfer_bytes_known": sum(
                    row["transfer_bytes"] for row in rows
                    if row.get("transfer_bytes") is not None
                ),
                "selected_transfer_bytes_unknown_records": sum(
                    row.get("transfer_bytes") is None for row in rows
                ),
                "zero_direct_reclaim_selected_transfer_bytes_known": sum(
                    row["transfer_bytes"] for row in rows
                    if row.get("reclaimable_pressured_bytes") == 0
                    and row.get("transfer_bytes") is not None
                ),
                "missing_full_prefix_tokens": distribution(
                    row.get("missing_full_prefix_tokens") for row in rows
                ),
            }
            for source, rows in sorted(sources.items())
        },
        "scope": (
            "Candidate selection-time estimates, not reclaimed bytes, DMA bytes or "
            "forward-use credit. Zero direct reclaim may be a necessary ancestor "
            "backup before an exclusive checkpoint becomes reclaimable. Match ACKs "
            "by command ID only; legacy records without it remain unassociated. "
            "Missing candidate estimates remain unknown. For a burst, candidate "
            "transfer/reclaim estimates describe the selected first extent only; "
            "explicit burst IDs link additional per-node receipts without "
            "multiplying this estimate or granting reuse credit."
        ),
    }


def wait_event_identity(row: dict) -> tuple | None:
    source = {"join_wait": "join_ticket"}.get(row.get("source"), row.get("source"))
    if source not in ("join_ticket", "tool_wait"):
        return None
    if any(row.get(name) is None for name in ("workflow_id", "context_id", "context_epoch")):
        return None
    join = row.get("join_id") if source == "join_ticket" else None
    if source == "join_ticket" and join is None:
        return None
    return source, row["workflow_id"], row["context_id"], row["context_epoch"], join


def wait_event_attribution(
    arm: Path, observations: list[dict], issues: dict, actions: list[dict],
    clock_offset: float | None,
) -> dict:
    """Group repeated plans and node receipts by the actual causal wait episode."""
    groups = {}
    missing_identity = 0
    for observation in [*observations, *issues.values()]:
        identity = wait_event_identity(observation)
        if identity is None:
            missing_identity += observation.get("source") in (
                "join_wait", "join_ticket", "tool_wait",
            )
            continue
        group = groups.setdefault(identity, {
            "source": identity[0], "workflow_id": identity[1],
            "context_id": identity[2], "context_epoch": identity[3], "join_id": identity[4],
            "invocation_id": observation.get("invocation_id"),
            "first_observation_ts_ms": observation["ts_ms"],
            "max_observed_planned_full_tokens": 0, "plan_reasons": Counter(),
            "max_observed_planned_full_bytes": None,
            "active_tool_ids": set(), "actions": [], "sessions": set(),
        })
        group["first_observation_ts_ms"] = min(
            group["first_observation_ts_ms"], observation["ts_ms"],
        )
        if observation.get("invocation_id") is not None:
            group["invocation_id"] = observation["invocation_id"]
        group["active_tool_ids"].update(observation.get("active_tool_ids") or ())
        group["sessions"].add((observation.get("session_id"), observation.get("session_generation")))
        tokens = observation.get("planned_full_tokens", observation.get("required_full_tokens"))
        if type(tokens) is int:
            group["max_observed_planned_full_tokens"] = max(
                group["max_observed_planned_full_tokens"], tokens,
            )
        planned_bytes = (observation.get("planned_pool_bytes") or {}).get("kv")
        if type(planned_bytes) is int:
            group["max_observed_planned_full_bytes"] = max(
                group["max_observed_planned_full_bytes"] or 0, planned_bytes,
            )
        if observation.get("event") != "prefetch_native_issued":
            group["plan_reasons"][observation.get("reason", "unknown")] += 1
    for action in actions:
        identity = wait_event_identity(issues.get(action["command_id"], {}))
        if identity in groups:
            groups[identity]["actions"].append(action)
    ends, joins, submits = {}, {}, defaultdict(list)
    if clock_offset is not None:
        for path in arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"):
            for row in records(path):
                attrs = row.get("attributes") or {}
                workflow = row.get("workflow_id")
                when = row["ts_ms"] + clock_offset
                if row["kind"] == "tool_end":
                    tool_id = attrs.get("tool_run_id") or attrs.get("tool_call_id")
                    if tool_id is not None:
                        ends[workflow, row.get("invocation_id"), tool_id] = when
                elif row["kind"] == "join_satisfied":
                    joins[workflow, row["join_id"]] = when
                elif row["kind"] == "llm_submit" and row.get("context_id"):
                    submits[workflow, row["context_id"]].append({**row, "ts_ms": when})
    for rows in submits.values():
        rows.sort(key=lambda row: row["ts_ms"])
    targets = set()
    for group in groups.values():
        if group["source"] == "join_ticket":
            completion = joins.get((group["workflow_id"], group["join_id"]))
        else:
            tool_times = [
                ends.get((group["workflow_id"], group["invocation_id"], tool))
                for tool in group["active_tool_ids"]
            ]
            completion = (
                max(tool_times) if tool_times and all(when is not None for when in tool_times)
                else None
            )
        group["completion_ts_ms"] = completion
        next_request = next((
            row for row in submits.get((group["workflow_id"], group["context_id"]), ())
            if row.get("context_epoch") is not None
            and row["context_epoch"] > group["context_epoch"]
            and row.get("invocation_id") == group["invocation_id"]
            and row["ts_ms"] >= (
                completion if completion is not None else group["first_observation_ts_ms"]
            )
        ), None)
        group["next_request"] = next_request
        if next_request is not None:
            targets.add(next_request["attributes"]["request_id"])
    native_requests = {
        row["attributes"]["request_id"]: row
        for row in records(arm / "server/runtime_events.sglang.jsonl")
        if row["kind"] == "llm_submit"
        and row.get("attributes", {}).get("request_id") in targets
    } if targets else {}
    services, dependency_waits = {}, defaultdict(list)
    if targets:
        for row in records(arm / "server/runtime_audit.jsonl"):
            if row.get("event") == "gpu_service_sample":
                start = row.get("service_start_ts_ms")
                if start is None:
                    continue
                for sample in row.get("request_samples") or ():
                    rid = sample.get("request_id")
                    if rid in targets:
                        identity = (
                            rid, sample.get("workflow_id"), sample.get("context_id"),
                            sample.get("context_epoch"),
                        )
                        services[identity] = min(services.get(identity, start), start)
            elif row.get("event") == "gpu_restore_dependency_wait":
                for rid in targets.intersection(row.get("request_ids") or ()):
                    dependency_waits[rid].append(row["gpu_layer_dependency_wait_ms"])
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    full_unit = (status.get("host_pool_evidence") or {}).get("full", {}).get("bytes_per_unit")
    handoffs_by_request = defaultdict(list)
    for action in actions:
        if action["source"] == "execution_handoff" and action.get("request_id") is not None:
            handoffs_by_request[action["request_id"]].append(action)
    rows = []
    for group in groups.values():
        pending = group["actions"]
        completion = group["completion_ts_ms"]
        request = group["next_request"]
        rid = request["attributes"]["request_id"] if request else None
        native = native_requests.get(rid)
        if native is not None and any(
            native.get(name) != request.get(name)
            for name in ("workflow_id", "context_id", "context_epoch", "invocation_id")
        ):
            native = None
        service = services.get((
            rid, group["workflow_id"], group["context_id"], request.get("context_epoch"),
        )) if request else None
        host_tokens = (native.get("attributes") or {}).get("cached_tokens_host") if native else None
        early_reused = [
            action for action in pending
            if action["full_first_service_reused"] is True
            and rid is not None and action.get("first_service_request_id") == rid
            and action.get("first_service_context_epoch") == request["context_epoch"]
            and action["submit_ts_ms"] is not None and completion is not None
            and action["submit_ts_ms"] < completion and action["pool_bytes"] is not None
        ]
        ready_reused = [
            action for action in early_reused
            if action["ack_ts_ms"] is not None and action["ack_ts_ms"] <= completion
        ]
        handoff = handoffs_by_request.get(rid, ())
        acks = [action["ack_ts_ms"] for action in pending if action["ack_ts_ms"] is not None]
        rows.append({
            **{name: group[name] for name in (
                "source", "workflow_id", "invocation_id", "context_id", "context_epoch", "join_id",
                "max_observed_planned_full_tokens", "completion_ts_ms",
            )},
            "max_observed_planned_full_bytes": (
                group["max_observed_planned_full_bytes"]
                if group["max_observed_planned_full_bytes"] is not None
                else group["max_observed_planned_full_tokens"] * full_unit
                if type(full_unit) is int else None
            ),
            "observed_plan_reasons": dict(group["plan_reasons"]),
            "observed_session_bindings": len(group["sessions"] - {(None, None)}),
            "next_request_id": rid, "first_service_ts_ms": service,
            "node_command_count": len(pending),
            "early_started_and_reused_full_bytes": sum(
                action["pool_bytes"].get("kv", 0) for action in early_reused
            ),
            "early_ready_and_reused_full_bytes": sum(
                action["pool_bytes"].get("kv", 0) for action in ready_reused
            ),
            "remaining_native_full_host_hit_tokens": host_tokens,
            "remaining_native_full_host_hit_bytes": (
                host_tokens * full_unit if type(host_tokens) is int and type(full_unit) is int
                else None
            ),
            "demand_handoff_full_bytes_known": sum(
                (action["pool_bytes"] or {}).get("kv", 0) for action in handoff
            ),
            "completion_to_client_submit_ms": (
                request["ts_ms"] - completion if request and completion is not None else None
            ),
            "submit_to_first_service_ms": (
                service - native["ts_ms"] if native and service is not None else None
            ),
            "first_ack_to_first_service_ms": service - min(acks)
            if acks and service is not None else None,
            "last_ack_to_first_service_ms": service - max(acks)
            if acks and service is not None else None,
            "sampled_batch_restore_dependency_wait_ms": dependency_waits.get(rid, []),
        })
    return {
        "summary": {
            "observed_wait_events": len(rows),
            "records_without_wait_event_identity": missing_identity,
            "by_source": {
                source: {
                    "observed_events": len(selected := [
                        row for row in rows if row["source"] == source
                    ]),
                    "events_with_observed_full_plan": sum(
                        row["max_observed_planned_full_tokens"] > 0 for row in selected
                    ),
                    "max_snapshot_planned_full_bytes_known": sum(
                        row["max_observed_planned_full_bytes"] or 0 for row in selected
                    ),
                    "planned_full_bytes_unknown_events": sum(
                        row["max_observed_planned_full_bytes"] is None for row in selected
                    ),
                    "events_with_early_reused_full": sum(
                        row["early_started_and_reused_full_bytes"] > 0 for row in selected
                    ),
                    "early_started_and_reused_full_bytes": sum(
                        row["early_started_and_reused_full_bytes"] for row in selected
                    ),
                    "remaining_native_full_host_hit_bytes_known": sum(
                        row["remaining_native_full_host_hit_bytes"] or 0 for row in selected
                    ),
                    "remaining_native_full_evidence_unknown_events": sum(
                        row["remaining_native_full_host_hit_bytes"] is None for row in selected
                    ),
                    "submit_to_first_service_ms": distribution(
                        row["submit_to_first_service_ms"] for row in selected
                    ),
                    "last_ack_to_first_service_ms": distribution(
                        row["last_ack_to_first_service_ms"] for row in selected
                    ),
                }
                for source in ("join_ticket", "tool_wait")
            },
        },
        "rows": rows,
        "semantics": (
            "One causal JOIN or tool wait at a context epoch, not one node command. "
            "Plans are bounded snapshots, not a continuous or counterfactual opportunity "
            "denominator; the maximum snapshot is not summed across repeated samples. "
            "Early FULL requires actual submit before complete JOIN/all-tool END and "
            "verified reuse by that episode's next request. Native Host-hit counters and exact demand "
            "handoff receipts are separate remaining-demand evidence; do not add them "
            "without proving disjoint allocations. Service wait includes admission "
            "and execution queueing, not isolated H2D delay. Sampled GPU dependency "
            "wait is a batch observation and must not be summed across its requests. "
            "Missing identities, clocks, ACK pool bytes or service evidence stay unknown."
        ),
    }


def audit(arm: Path) -> dict:
    issued, prepared, parks, leases, stages = {}, {}, [], defaultdict(list), {}
    prepare_selections = []
    wait_observations, clock_offset = [], None
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
        elif event == "prepare_candidate_selected":
            prepare_selections.append(row)
        elif event in ("prefetch_residency_registered", "prefetch_residency_released"):
            leases[row["command_id"]].append(row)
        elif event in ("wait_prefetch_plan", "session_h2d_opportunity"):
            if row.get("source") in ("join_wait", "join_ticket", "tool_wait"):
                wait_observations.append(row)
        elif event == "safe_point_census" and clock_offset is None:
            clock_offset = row["ts_ms"] - row["monotonic_ms"]
    transfers = list(records(arm / "server/transfer_telemetry.jsonl"))
    native_loads = defaultdict(list)
    for transfer in transfers:
        ts = transfer.get("submit_ts_ms")
        if transfer["direction"] != "h2d" or ts is None:
            continue
        for part in transfer_parts(transfer):
            if part["command_id"] is not None or not any(part["pool_units"].values()):
                continue
            for node_id in part["node_ids"]:
                native_loads[node_id].append((ts, transfer, part))
    for loads in native_loads.values():
        loads.sort(key=lambda item: item[0])
    native_load_times = {
        node_id: [item[0] for item in loads]
        for node_id, loads in native_loads.items()
    }
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
            if row.get("mamba_reuse") in (
                "verified_single_request_cow_forward_completed",
                "verified_per_request_cow_forward_completed",
            ):
                mamba.add(row["command_id"])
    ack_path = arm / "server/physical_action_ack.jsonl"
    pool_bytes = {
        row["command_id"]: row["pool_bytes"]
        for row in records(ack_path)
        if row.get("action") == "PREFETCH_GPU" and row.get("pool_bytes") is not None
    } if ack_path.exists() else {}
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
        pools = pool_bytes.get(command)
        if (
            pools is not None and child.get("num_bytes") is not None
            and sum(pools.values()) != child["num_bytes"]
        ):
            raise ValueError(f"physical ACK pool bytes disagree with transfer receipt: {command}")
        ack = use.get("ack_ts_ms") if use else transfer.get("complete_ts_ms")
        service = use.get("first_service_ts_ms") if use else None
        repeated_parks = [
            row for row in parks if row["context_id"] == issue["context_id"]
            and row["node_id"] == issue["node_id"]
            and ack is not None and service is not None
            and ack < row["ts_ms"] < service
        ]
        native_reloads = []
        if ack is not None and service is not None:
            times = native_load_times.get(issue["node_id"], [])
            loads = native_loads.get(issue["node_id"], [])
            prefetched_pools = {
                pool for pool, count in (child.get("num_tokens_by_pool") or {}).items()
                if count > 0
            }
            for _, row, part in loads[bisect_right(times, ack):bisect_right(times, service)]:
                native_reloads.append({
                    "submit_ts_ms": row["submit_ts_ms"],
                    "complete_ts_ms": row.get("complete_ts_ms"),
                    "actual_bytes": part["actual_bytes"],
                    "pool_units": part["pool_units"],
                    "prefetched_pool_overlap": sorted(
                        prefetched_pools & {
                            pool for pool, count in part["pool_units"].items() if count > 0
                        }
                    ),
                    "node_pool_evidence": part["node_pool_evidence"],
                })
        submit = transfer.get("submit_ts_ms")
        results.append({
            "command_id": command, "source": issue["source"],
            "trigger_kind": issue["trigger_kind"],
            "workflow_id": issue["workflow_id"],
            "invocation_id": issue.get("invocation_id"),
            "request_id": issue.get("request_id"),
            "join_id": issue.get("join_id"),
            "context_id": issue["context_id"], "context_epoch": issue["context_epoch"],
            "node_id": issue["node_id"], "actual_bytes": child.get("num_bytes"),
            "pool_units": child.get("num_tokens_by_pool"),
            "pool_bytes": pools,
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
            "first_service_request_id": use.get("request_id") if use else None,
            "first_service_context_epoch": use.get("service_context_epoch") if use else None,
            "full_reuse_proof_version": (
                use.get("full_reuse_proof_version", 1) if use else None
            ),
            "mamba_forward_verified": command in mamba,
            "lease_events": leases.get(command, []),
            "pressure_demotions_before_first_service": repeated_parks,
            "native_reloads_before_first_service": native_reloads,
        })
    prepared_by_node = defaultdict(list)
    for command, (transfer, child) in tagged.items():
        issue = prepared.get(command)
        if issue is None or transfer["direction"] != "d2h":
            continue
        for node in child.get("published_node_ids") or [child["anchor_node_id"]]:
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
    handoffs = [row for row in results if row["source"] == "execution_handoff"]
    eviction_path = arm / "server/eviction_attribution.jsonl"
    host_evictions = [
        row for row in records(eviction_path) if row["event"] == "host_block_evicted"
    ] if eviction_path.exists() else None
    prepare_restores = prepare_restore_attribution(prepared, transfers, host_evictions)
    wait_events = wait_event_attribution(arm, wait_observations, issued, results, clock_offset)
    summary = {
        "join_commands": len(joins), "tool_commands": len(tools),
        "handoff_commands": len(handoffs),
        "by_source": {
            source: source_summary([row for row in results if row["source"] == source])
            for source in sorted(
                {"join_ticket", "tool_wait", "execution_handoff"}
                | {row["source"] for row in results}
            )
        },
        "trigger_kinds": dict(Counter(row["trigger_kind"] for row in results)),
        "join_bytes": sum(row["actual_bytes"] or 0 for row in joins),
        "tool_bytes": sum(row["actual_bytes"] or 0 for row in tools),
        "handoff_bytes": sum(row["actual_bytes"] or 0 for row in handoffs),
        "join_submitted_before_eos": sum(
            row["submit_after_native_eos_ms"] is not None
            and row["submit_after_native_eos_ms"] < 0 for row in joins
        ),
        "full_reused": sum(row["full_first_service_reused"] is True for row in results),
        "full_not_reused": sum(row["full_first_service_reused"] is False for row in results),
        "full_use_unknown": sum(row["full_first_service_reused"] is None for row in results),
        "full_reuse_proof_versions": dict(Counter(
            str(row["full_reuse_proof_version"]) for row in results
            if row["full_reuse_proof_version"] is not None
        )),
        "mamba_forward_verified": len(mamba & issued.keys()),
        "redemoted_before_first_service": sum(
            bool(row["pressure_demotions_before_first_service"]) for row in results
        ),
        "native_reloaded_before_first_service": sum(
            bool(row["native_reloads_before_first_service"]) for row in results
        ),
        "native_reloaded_prefetched_full_before_first_service": sum(
            any("kv" in load["prefetched_pool_overlap"]
                for load in row["native_reloads_before_first_service"])
            for row in results
        ),
        "native_reloaded_prefetched_mamba_before_first_service": sum(
            any("mamba" in load["prefetched_pool_overlap"]
                for load in row["native_reloads_before_first_service"])
            for row in results
        ),
        "native_reload_pool_associations_by_evidence": {
            pool: {
                evidence: sum(
                    any(
                        pool in load["prefetched_pool_overlap"]
                        and load["node_pool_evidence"] == evidence
                        for load in row["native_reloads_before_first_service"]
                    ) for row in results
                )
                for evidence in ("reconciled_native_receipt", "legacy_batch_pool_presence")
            }
            for pool in ("kv", "mamba")
        },
        "issue_to_submit_ms": distribution(row["issue_to_submit_ms"] for row in results),
        "enqueue_to_submit_ms": distribution(row["enqueue_to_submit_ms"] for row in results),
        "submit_to_ack_ms": distribution(row["submit_to_ack_ms"] for row in results),
        "ack_to_first_service_ms": distribution(row["ack_to_first_service_ms"] for row in results),
        "join_submit_after_eos_ms": distribution(row["submit_after_native_eos_ms"] for row in joins),
        "completion_lead_ms": distribution(row["submit_lead_to_completion_ms"] for row in results),
        "pressure_events": len(parks),
        "pressure_events_with_prior_prepare_ack": len(consumed),
        "unique_prepared_nodes_demoted": len({row["node_id"] for row in consumed}),
        "prepare_issued_commands": len(prepared),
        "prepare_acknowledged_commands": len(prepare_restores),
        "prepare_commands_with_observed_restore": sum(bool(row["restores"]) for row in prepare_restores),
        "prepare_commands_with_native_restore": sum(
            any(restore["source"] == "native_h2d" for restore in row["restores"])
            for row in prepare_restores
        ),
        "prepare_commands_with_controlled_restore": sum(
            any(restore["source"] == "controlled_h2d" for restore in row["restores"])
            for row in prepare_restores
        ),
        "prepare_commands_without_observed_restore": sum(
            not row["restores"] for row in prepare_restores
        ),
        "prepare_commands_with_later_node_pool_d2h": sum(
            bool(row["later_node_pool_d2h"]) for row in prepare_restores
        ),
        "prepare_commands_with_host_eviction_before_later_d2h": (
            sum(
                any(
                    later["host_evictions_between_writes"] > 0
                    for later in row["later_node_pool_d2h"]
                ) for row in prepare_restores
            ) if host_evictions is not None else None
        ),
        "prepare_host_lifetime": prepare_host_lifetime_summary(prepare_restores, host_evictions),
        "prepare_selection": prepare_selection_summary(prepare_selections, prepare_restores),
        "wait_events": wait_events["summary"],
    }
    return {
        "scope": (
            "Observed controller submission/ACK/first-launch chain; no DMA wall "
            "anchor, exposed-stall measurement or counterfactual speedup."
        ),
        "arm": str(arm), "summary": summary, "rows": results,
        "wait_event_attribution": wait_events,
        "source_summary_semantics": (
            "Per-source node commands, not independent requests or workflow events. "
            "FULL reuse counts include only commands with positive FULL transfer units. "
            "Pool bytes come from reconciled physical ACKs; missing legacy pool-byte "
            "evidence is reported, never reconstructed by byte shares. Execution "
            "handoff is submitted demand, separate from anticipatory JOIN/tool actions. "
            "FULL residency groups use the initial observed registration lock and "
            "last observed release reason in log order; absent evidence stays unknown. "
            "Groups are lifecycle associations, not isolated causes. Lease expiry "
            "does not imply a reuse miss, nor does registration prove continuous "
            "protection until service; Mamba-only transfers are excluded."
        ),
        "native_reload_attribution_semantics": (
            "Node/pool overlap before first service, not proof of repeated physical "
            "allocation or duplicate bytes. Mixed tagged batches retain untagged "
            "operations. Exact receipts carry per-operation units and bytes; legacy "
            "batch pool presence remains an ambiguous association. Evidence counts "
            "may overlap when a command has multiple reloads."
        ),
        "prepare_consumption": consumed,
        "prepare_restore_attribution": prepare_restores,
        "prepare_attribution_semantics": (
            "Latest observed D2H writer per node/pool. A later D2H is not proof "
            "of byte overwrite or duplicate transfer: Host eviction/reallocation, "
            "node splitting and legacy merged pool attribution are unresolved. "
            "Intervening same-node/pool Host eviction is reported when available; "
            "it does not recover an allocation identity across radix splits. "
            "The lifetime summary separates restore associations before/after observed "
            "Host eviction without changing legacy latest-writer association counts. "
            "Observed restoration is not final model-forward reuse."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-only", action="store_true",
                        help="Keep summary and evidence scope without duplicating detailed rows.")
    args = parser.parse_args()
    report = audit(args.arm.resolve())
    if args.summary_only:
        report = {
            key: value for key, value in report.items()
            if key not in (
                "rows", "prepare_consumption", "prepare_restore_attribution",
                "wait_event_attribution",
            )
        }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
