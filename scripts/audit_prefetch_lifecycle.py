#!/usr/bin/env python3
"""Link predictive transfers to EOS, first use, leases and repeated parking."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.summarize_semantic_h2d_ab import records, runtime_clock_offset


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
    negative = [row for row in full if row["full_first_service_reused"] is False]
    negative_reloads = sum(
        any("kv" in load["prefetched_pool_overlap"]
            for load in row["native_reloads_before_first_service"])
        for row in negative
    )
    return {
        "full_transfer_commands": len(full),
        "full_reused_commands": sum(row["full_first_service_reused"] is True for row in full),
        "full_not_reused_commands": sum(row["full_first_service_reused"] is False for row in full),
        "full_identity_not_verified_commands": len(negative),
        "full_identity_not_verified_bytes_known": sum(
            row["pool_bytes"].get("kv", 0) for row in negative if row["pool_bytes"] is not None
        ),
        "negative_identity_with_native_full_reload_association": negative_reloads,
        "negative_identity_without_native_full_reload_association": len(negative) - negative_reloads,
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


def protection_revocation_summary(rows: list[dict]) -> dict:
    commands = [
        [event for event in row.get("lease_events", ())
         if event["event"] == "prefetch_protection_revoked"]
        for row in rows
    ]
    return {
        "protection_revoked_commands": sum(bool(events) for events in commands),
        "protection_revocation_reasons": dict(Counter(
            event.get("reason", "unknown") for events in commands for event in events
        )),
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
        groups[locked, registered.get("protection_reason"), released.get("reason")].append(row)
    return [
        {
            "native_locked_at_registration": locked,
            "protection_reason_at_registration": protection_reason,
            "last_observed_release_reason": reason,
            **protection_revocation_summary(group),
            **full_reuse_summary(group),
            "ack_to_first_service_ms": distribution(
                row["ack_to_first_service_ms"] for row in group
            ),
        }
        for (locked, protection_reason, reason), group in sorted(
            groups.items(),
            key=lambda item: (str(item[0][0]), item[0][1] or "", item[0][2] or ""),
        )
    ]


def source_summary(rows: list[dict]) -> dict:
    full = [row for row in rows if (row["pool_units"] or {}).get("kv", 0) > 0]
    return {
        "issued_commands": len(rows),
        "acknowledged_commands": sum(row["actual_bytes"] is not None for row in rows),
        "actual_bytes": sum(row["actual_bytes"] or 0 for row in rows),
        **full_reuse_summary(full),
        **protection_revocation_summary(rows),
        "full_reuse_by_residency": full_residency_summary(full),
        "mamba_forward_verified_commands": sum(row["mamba_forward_verified"] for row in rows),
        "ack_to_first_service_ms": distribution(row["ack_to_first_service_ms"] for row in rows),
    }


def client_event_paths(arm: Path) -> tuple[Path, ...]:
    live = tuple(sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")))
    replay = arm / "replay/runtime_events.replay.jsonl"
    if live and replay.exists():
        raise ValueError("ambiguous live and causal replay client traces")
    return (replay,) if replay.exists() else live


def terminal_invocation_scopes(arm: Path) -> dict:
    scopes = {}
    for path in client_event_paths(arm):
        for row in records(path):
            if row["kind"] != "invocation_create":
                continue
            scope = (
                "runtime_internal"
                if (row.get("attributes") or {}).get("runtime_internal") is True
                else "dynamic_subagent" if row.get("relation_type") == "spawn"
                else "workflow_root" if row.get("relation_type") == "root" else "other"
            )
            scopes[row["workflow_id"], row["invocation_id"]] = scope
    return scopes


def terminal_release_totals(reclaims: list[dict]) -> dict:
    device, host, device_bytes, host_bytes = Counter(), Counter(), Counter(), Counter()
    device_unknown, host_unknown = Counter(), Counter()
    for row in reclaims:
        for target, target_bytes, unknown, field in (
            (device, device_bytes, device_unknown, "freed_device_units"),
            (host, host_bytes, host_unknown, "freed_host_units"),
        ):
            for component, units in row.get(field, {}).items():
                component = str(component)
                target[component] += units
                size = row.get("bytes_per_unit", {}).get(component)
                if size is None:
                    size = row.get("bytes_per_unit", {}).get(int(component))
                if size is not None:
                    target_bytes[component] += units * size
                else:
                    unknown[component] += units
    return {
        "reclaim_records": len(reclaims),
        "terminal_contexts": len({
            (row["workflow_id"], row["context_id"], row["session_id"], row["session_generation"])
            for row in reclaims if not row.get("retry")
        }),
        "freed_device_units": dict(device), "freed_host_units": dict(host),
        "freed_device_bytes_known": dict(device_bytes),
        "freed_host_bytes_known": dict(host_bytes),
        "freed_device_units_with_unknown_byte_size": dict(device_unknown),
        "freed_host_units_with_unknown_byte_size": dict(host_unknown),
    }


def terminal_capacity_summary(
    reclaims: list[dict], services: list[dict], lookaheads: Sequence[dict] = (),
    *, invocation_scopes: dict | None = None,
    join_wait_events: Sequence[dict] = (), prefetch_actions: Sequence[dict] = (),
) -> dict:
    by_scope, linked = defaultdict(list), defaultdict(list)
    scopes = invocation_scopes or {}
    for row in reclaims:
        scope = scopes.get(
            (row["workflow_id"], row.get("invocation_id")),
            row.get("invocation_scope", "unknown"),
        )
        by_scope[scope].append(row)
        if scope == "dynamic_subagent":
            identity = (row["workflow_id"], row.get("parent_context_id"), row.get("join_id"))
            linked[identity].append(row)
    scope_totals = {
        scope: terminal_release_totals(rows) for scope, rows in sorted(by_scope.items())
    }
    linked_services = []
    lookahead_by_request = defaultdict(list)
    for row in lookaheads:
        lookahead_by_request[
            row["workflow_id"], row["context_id"], row["join_id"], row["request_id"]
        ].append(row)
    for service in services:
        rows = linked.get((
            service["workflow_id"], service["context_id"], service["join_id"],
        ), ())
        before = [row for row in rows if row["ts_ms"] <= service["ts_ms"]]
        freed = Counter()
        for row in before:
            freed.update({str(ct): units for ct, units in row["freed_device_units"].items()})
        last_return = service.get("last_observed_child_return_ts_ms")
        selections = [
            row for row in lookahead_by_request.get((
                service["workflow_id"], service["context_id"],
                service["join_id"], service["request_id"],
            ), ())
            if row["ts_ms"] <= service["ts_ms"]
        ]
        selection = min(selections, key=lambda row: row["ts_ms"]) if selections else None
        linked_services.append({
            "workflow_id": service["workflow_id"], "context_id": service["context_id"],
            "join_id": service["join_id"], "request_id": service["request_id"],
            "freed_device_units_before_service": dict(freed),
            "last_child_return_to_first_service_ms": (
                service["ts_ms"] - last_return
                if last_return is not None and service["ts_ms"] >= last_return else None
            ),
            "request_wait_ms": service.get("request_wait_ms"),
            "restore_lookahead_to_first_service_ms": (
                service["ts_ms"] - selection["ts_ms"] if selection is not None else None
            ),
            "queue_wait_before_restore_lookahead_ms": (
                selection.get("queue_wait_ms") if selection is not None else None
            ),
        })
    return {
        **terminal_release_totals(reclaims),
        "terminal_children": scope_totals.get("dynamic_subagent", {}).get("terminal_contexts", 0),
        "by_invocation_scope": scope_totals,
        "parent_first_services": len(services),
        "parent_services_with_prior_device_release": sum(
            any(row["freed_device_units_before_service"].values()) for row in linked_services
        ),
        "last_child_return_to_first_service_ms": distribution(
            row["last_child_return_to_first_service_ms"] for row in linked_services
        ),
        "request_wait_ms": distribution(row["request_wait_ms"] for row in linked_services),
        "restore_lookahead_records": len(lookaheads),
        "parent_services_with_prior_restore_lookahead": sum(
            row["restore_lookahead_to_first_service_ms"] is not None for row in linked_services
        ),
        "restore_lookahead_to_first_service_ms": distribution(
            row["restore_lookahead_to_first_service_ms"] for row in linked_services
        ),
        "queue_wait_before_restore_lookahead_ms": distribution(
            row["queue_wait_before_restore_lookahead_ms"] for row in linked_services
        ),
        "sibling_release_windows": terminal_join_capacity_windows(
            reclaims, join_wait_events, prefetch_actions, invocation_scopes=scopes,
        ),
        "rows": linked_services,
        "semantics": (
            "Actual terminal-suffix allocator release, linked to the first observed "
            "completed parent GPU batch, not kernel start; RETURN delivery observation "
            "and service observation use scheduler wall time, not client event time; "
            "not exclusive slot ownership, predictive H2D reuse, avoided D2H bytes, "
            "or a native-relative latency saving. Restore lookahead is selection "
            "evidence, not submission/ACK evidence. Aggregate releases include internal "
            "calls; scope breakdown and terminal_children use explicit invocation-create "
            "metadata or recorded runtime scope, never ID spelling. Only confirmed "
            "dynamic subagents link to JOIN parent services; absent metadata stays unknown. "
            "Missing pool byte sizes stay unknown."
        ),
    }


def terminal_join_capacity_windows(
    reclaims: Sequence[dict], wait_events: Sequence[dict], actions: Sequence[dict],
    *, invocation_scopes: dict | None = None,
) -> dict:
    """Link settled sibling releases to proven early FULL use without assigning pages."""
    linked = defaultdict(list)
    scopes = invocation_scopes or {}
    for reclaim in reclaims:
        scope = scopes.get(
            (reclaim["workflow_id"], reclaim.get("invocation_id")),
            reclaim.get("invocation_scope", "unknown"),
        )
        if scope == "dynamic_subagent":
            linked[
                reclaim["workflow_id"], reclaim.get("parent_context_id"), reclaim.get("join_id")
            ].append(reclaim)
    by_command = {action["command_id"]: action for action in actions}
    rows, sibling_reclaims = [], []
    for event in wait_events:
        if event["source"] != "join_ticket":
            continue
        identity = event["workflow_id"], event["context_id"], event["join_id"]
        completion = event.get("completion_ts_ms")
        if completion is None:
            rows.append({
                "workflow_id": identity[0], "context_id": identity[1], "join_id": identity[2],
                "completion_ts_ms": None,
                "sibling_device_release_before_join": None,
                "sibling_device_release_lead_ms": None,
                "early_reused_full_commands_after_sibling_release": None,
                "early_reused_full_bytes_after_sibling_release_known": None,
            })
            continue
        before = [
            reclaim for reclaim in linked.get(identity, ())
            if reclaim["ts_ms"] < completion
            and any(reclaim.get("freed_device_units", {}).values())
        ]
        sibling_reclaims.extend(before)
        first_release = min((reclaim["ts_ms"] for reclaim in before), default=None)
        # The wait audit has already verified boundary, following-request and
        # FULL reuse identity. Capacity provenance must not create that proof.
        after_release = []
        for command in event.get("early_started_and_reused_full_command_ids", ()):
            action = by_command.get(command)
            if (
                action is not None and action["source"] == "join_ticket"
                and (action["workflow_id"], action["context_id"], action.get("join_id")) == identity
                and first_release is not None and action.get("submit_ts_ms") is not None
                and first_release < action["submit_ts_ms"] < completion
                and (action.get("pool_bytes") or {}).get("kv", 0) > 0
            ):
                after_release.append(action)
        rows.append({
            "workflow_id": identity[0], "context_id": identity[1], "join_id": identity[2],
            "completion_ts_ms": completion,
            "sibling_device_release_before_join": terminal_release_totals(before),
            "sibling_device_release_lead_ms": (
                completion - first_release if first_release is not None else None
            ),
            "early_reused_full_commands_after_sibling_release": [
                action["command_id"] for action in after_release
            ],
            "early_reused_full_bytes_after_sibling_release_known": sum(
                action["pool_bytes"]["kv"] for action in after_release
            ),
        })
    return {
        "observed_join_events": len(rows),
        "completion_unobserved_events": sum(row["completion_ts_ms"] is None for row in rows),
        "joins_with_sibling_device_release_before_join": sum(
            row["sibling_device_release_lead_ms"] is not None for row in rows
        ),
        "joins_with_early_reused_full_after_sibling_release": sum(
            bool(row["early_reused_full_commands_after_sibling_release"]) for row in rows
        ),
        "early_reused_full_bytes_after_sibling_release_known": sum(
            row["early_reused_full_bytes_after_sibling_release_known"] or 0 for row in rows
        ),
        "cumulative_sibling_device_release_before_join": terminal_release_totals(sibling_reclaims),
        "sibling_device_release_lead_ms": distribution(
            row["sibling_device_release_lead_ms"] for row in rows
        ),
        "rows": rows,
        "semantics": (
            "Independent observed JOIN episodes, not all workload JOINs. Actual allocator "
            "release uses scheduler wall time; complete JOIN uses the existing client-clock "
            "offset conversion. Only explicitly identified dynamic subagents link by "
            "workflow/parent context/JOIN. Host-only or post-JOIN releases cannot support "
            "pre-JOIN H2D. A command qualifies only with the existing proof of actual "
            "pre-JOIN submission and FULL reuse by the following request. Temporal "
            "association does not prove exclusive capacity, child-to-parent page identity, "
            "that these bytes were necessary for prefetch, or a native-relative saving. "
            "Cumulative releases are not simultaneously free capacity; transferred FULL "
            "bytes are command receipts, not a count of unique pages. Unobserved JOIN "
            "completion leaves the release window unknown."
        ),
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


def native_full_prepare_attribution(
    issues: Sequence[dict], transfers: Sequence[dict], reclaims: Sequence[dict],
    uses: dict, host_evictions: Sequence[dict] | None, *, full_unit: int | None,
) -> dict:
    """Audit untagged background copies without granting semantic prefetch credit."""
    rows, pending, copies, events = [], defaultdict(set), {}, []
    unit = full_unit if type(full_unit) is int and full_unit > 0 else None
    for index, issue in enumerate(issues):
        nodes = {}
        for node_id, created, tokens in issue["native_nodes"]:
            if (
                type(node_id) is not int or node_id < 0 or node_id in nodes
                or type(created) not in (int, float) or not math.isfinite(created)
                or type(tokens) is not int or tokens <= 0
            ):
                raise ValueError("invalid native FULL prepare identity")
            nodes[node_id] = (created, tokens)
        if not nodes or issue.get("include_mamba") is not False:
            raise ValueError("native FULL prepare must contain only FULL extents")
        tokens = sum(value[1] for value in nodes.values())
        rows.append({
            **issue, "batch_index": index,
            "source": {
                "idle_cold_full_prepare": "idle",
                "critical_parent_cold_full_prepare": "critical",
                "spawn_full_prepare": "spawn",
            }[issue["event"]],
            "issued_full_tokens": tokens,
            "issued_full_bytes_known": tokens * unit if unit is not None else None,
            "ack_evidence": "unobserved", "ack_ts_ms": None,
            "acknowledged_full_bytes": None, "capacity_releases": [],
            "restore_associations": [], "copy_end_observations": [],
        })
        events.append((issue["ts_ms"], 0, index, nodes))
    if rows:
        for transfer in transfers:
            direction = transfer.get("direction")
            if transfer.get("status") != "completed" or direction not in ("d2h", "h2d"):
                continue
            field = "complete_ts_ms" if direction == "d2h" else "submit_ts_ms"
            if transfer.get(field) is not None:
                events.append((transfer[field], 1 if direction == "d2h" else 4, 0, transfer))
        for eviction in host_evictions or ():
            if eviction.get("pool") in ("full", "kv"):
                events.append((eviction["ts_ms"], 2, 0, eviction))
        for reclaim in reclaims:
            events.append((reclaim["ts_ms"], 3, 0, reclaim))

    def end_copy(node_id, when, reason):
        prior = copies.pop(node_id, None)
        if prior is not None:
            rows[prior]["copy_end_observations"].append({
                "node_id": node_id, "ts_ms": when, "reason": reason,
            })

    identities = [
        {node: (created, tokens) for node, created, tokens in row["native_nodes"]}
        for row in rows
    ]
    for when, kind, index, payload in sorted(events, key=lambda event: event[:3]):
        if kind == 0:
            for node_id, identity in payload.items():
                prior = copies.get(node_id)
                if prior is not None and identities[prior][node_id][0] != identity[0]:
                    end_copy(node_id, when, "new_node_generation")
                pending[node_id].add(index)
        elif kind == 2:
            end_copy(payload["node_id"], when, "host_eviction")
        elif kind == 1:
            parts = transfer_parts(payload)
            full_nodes = {
                node for part in parts if part["pool_units"].get("kv", 0) > 0
                for node in part["node_ids"]
            }
            for node in full_nodes:
                end_copy(node, when, "later_full_d2h")
            candidates = set().union(*(pending[node] for node in full_nodes)) if full_nodes else set()
            pools = payload.get("num_tokens_by_pool") or {}
            for candidate in sorted(candidates):
                row = rows[candidate]
                exact = (
                    len(candidates) == 1
                    and set(payload.get("node_ids", ())) == set(identities[candidate])
                    and pools.get("kv", 0) == row["issued_full_tokens"]
                    and not any(count for pool, count in pools.items() if pool != "kv")
                    and not payload.get("tagged_child_commits")
                    and all(part["command_id"] is None for part in parts)
                    and type(payload.get("actual_bytes")) is int
                    and payload["actual_bytes"] > 0
                    and (unit is None or payload["actual_bytes"] == row["issued_full_tokens"] * unit)
                )
                row.update({
                    "ack_evidence": "reconciled_full_batch" if exact else "ambiguous_node_or_pool_batch",
                    "ack_ts_ms": when,
                    "ack_transfer_command_id": payload.get("command_id"),
                    "acknowledged_full_bytes": payload["actual_bytes"] if exact else None,
                })
                for node in identities[candidate]:
                    pending[node].discard(candidate)
                    if exact:
                        copies[node] = candidate
        elif kind == 3:
            for victim in payload.get("victims") or ():
                node = victim["node_id"]
                prior = copies.get(node)
                freed = victim.get("freed_units") or {}
                tokens = freed.get("0", freed.get(0, 0))
                if (
                    prior is not None and rows[prior]["ack_ts_ms"] < when
                    and victim.get("creation_time") == identities[prior][node][0]
                    and type(tokens) is int and tokens > 0
                ):
                    rows[prior]["capacity_releases"].append({
                        **victim, "ts_ms": when, "full_tokens": tokens,
                        "full_bytes_known": tokens * unit if unit is not None else None,
                        "reclaim_event": payload["event"],
                    })
        else:
            for part in transfer_parts(payload):
                if part["pool_units"].get("kv", 0) <= 0:
                    continue
                command = part["command_id"]
                use = uses.get(command, {})
                service = use.get("first_service_ts_ms")
                for node in part["node_ids"]:
                    prior = copies.get(node)
                    if prior is None or rows[prior]["ack_ts_ms"] >= when:
                        continue
                    verified = (
                        use.get("full_node_reused") is True
                        and type(use.get("full_reuse_proof_version")) is int
                        and use["full_reuse_proof_version"] >= 2
                        and node in (use.get("reused_full_node_ids") or ())
                        and type(service) in (int, float) and math.isfinite(service)
                        and service >= when
                    )
                    rows[prior]["restore_associations"].append({
                        "node_id": node, "submit_ts_ms": when,
                        "transfer_command_id": payload.get("command_id"),
                        "controlled_command_id": command,
                        "node_pool_evidence": part["node_pool_evidence"],
                        "controlled_full_first_service_verified": verified,
                        "first_service_request_id": use.get("request_id") if verified else None,
                        "host_eviction_evidence_available": host_evictions is not None,
                    })
    acknowledged = [row for row in rows if row["ack_evidence"] == "reconciled_full_batch"]
    restores = [restore for row in rows for restore in row["restore_associations"]]
    return {
        "summary": {
            "issued_batches": len(rows),
            "issued_nodes": sum(len(row["native_nodes"]) for row in rows),
            "issued_full_tokens": sum(row["issued_full_tokens"] for row in rows),
            "issued_full_bytes_known": sum(row["issued_full_bytes_known"] or 0 for row in rows),
            "issued_full_bytes_unknown_batches": sum(row["issued_full_bytes_known"] is None for row in rows),
            "reconciled_ack_batches": len(acknowledged),
            "ambiguous_ack_batches": sum(row["ack_evidence"] == "ambiguous_node_or_pool_batch" for row in rows),
            "ack_unobserved_batches": sum(row["ack_evidence"] == "unobserved" for row in rows),
            "acknowledged_full_bytes": sum(row["acknowledged_full_bytes"] for row in acknowledged),
            "batches_with_capacity_release": sum(bool(row["capacity_releases"]) for row in rows),
            "observed_released_full_tokens": sum(
                release["full_tokens"] for row in rows for release in row["capacity_releases"]
            ),
            "batches_with_restore_association": sum(bool(row["restore_associations"]) for row in rows),
            "restore_node_associations": len(restores),
            "restore_associations_with_verified_controlled_first_service": sum(
                restore["controlled_full_first_service_verified"] for restore in restores
            ),
            "host_eviction_evidence_available": host_evictions is not None,
            "by_source": {
                source: {
                    "issued_batches": sum(row["source"] == source for row in rows),
                    "reconciled_ack_batches": sum(row["source"] == source for row in acknowledged),
                } for source in (
                    ("idle", "critical", "spawn")
                    if any(row["source"] == "spawn" for row in rows)
                    else ("idle", "critical")
                )
            },
        },
        "rows": rows,
        "semantics": (
            "Native untagged FULL backups, separate from semantic PREPARE and predictive "
            "H2D. Issuance observation may follow device submission. ACK bytes require "
            "one matching issued batch, identical published node set and FULL units, "
            "no other pool/tagged child, and byte reconciliation when unit size is known. "
            "Splits or merged/partial receipts stay ambiguous. A later possible FULL "
            "writer, observed FULL Host eviction or new observed node generation ends "
            "the source association. Capacity release additionally matches creation time "
            "and observed native freed units; repeated releases are cumulative events, "
            "not unique or simultaneously free capacity. H2D links remain node/pool "
            "associations; legacy mixed pools cannot prove per-node bytes. Verified "
            "controlled first service proves that H2D allocation's FULL use, not complete "
            "Host allocation continuity or unique consumption of the original backup. "
            "Native first-service proof, unobserved eviction/split lineage and byte-level "
            "forward consumption remain unknown; no avoided wait or speedup is assigned."
        ),
    }


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
    # A JOIN keeps its identity when the resumed parent advances its epoch.
    epoch = None if source == "join_ticket" else row["context_epoch"]
    return source, row["workflow_id"], row["context_id"], epoch, join


def observed_capacity_shortfall(row: dict) -> str | None:
    names = (
        "required_full_tokens", "required_mamba_slots",
        "device_full_free_tokens", "device_mamba_free_slots",
    )
    values = tuple(row.get(name) for name in names)
    if (
        row.get("headroom_observable") is not True
        or any(type(value) is not int or value < 0 for value in values)
        or values[0] + values[1] == 0
    ):
        return None
    full, mamba = values[0] > values[2], values[1] > values[3]
    return "both" if full and mamba else "full_only" if full else "mamba_only" if mamba else "none"


def http_transport_waits(
    attempts: list[dict], client_submit: float | None, native_arrival: float | None,
) -> dict:
    def interval(left, right):
        return (
            right - left
            if type(left) in (int, float) and type(right) in (int, float)
            and math.isfinite(left) and math.isfinite(right) and left <= right
            else None
        )

    timing = attempts[0] if len(attempts) == 1 else {}
    start = timing.get("http_request_start_ts_ms")
    if interval(client_submit, start) is None or interval(start, native_arrival) is None:
        start = None
    sent = timing.get("http_body_sent_ts_ms") if start is not None else None
    if interval(start, sent) is None or interval(sent, native_arrival) is None:
        sent = None
    return {
        "http_transport_attempts_observed": len(attempts),
        "http_body_sent_ts_ms": sent,
        "client_submit_to_http_request_ms": interval(client_submit, start),
        "http_request_to_body_sent_ms": interval(start, sent),
        "body_sent_to_native_arrival_ms": interval(sent, native_arrival),
        "http_request_to_response_headers_ms": interval(
            start, timing.get("http_response_headers_ts_ms"),
        ),
    }


NATIVE_INGRESS_INTERVALS = {
    "body_sent_to_api_received_ms": (
        "http_body_sent_ts_ms", "api_received_ts_ms",
    ),
    "api_received_to_chat_conversion_ms": (
        "api_received_ts_ms", "chat_conversion_started_ts_ms",
    ),
    "chat_conversion_ms": (
        "chat_conversion_started_ts_ms", "chat_conversion_finished_ts_ms",
    ),
    "chat_conversion_to_tokenized_ms": (
        "chat_conversion_finished_ts_ms", "api_tokenized_ts_ms",
    ),
    "tokenized_to_dispatch_ms": (
        "api_tokenized_ts_ms", "api_dispatch_started_ts_ms",
    ),
    "dispatch_to_scheduler_received_ms": (
        "api_dispatch_started_ts_ms", "scheduler_received_ts_ms",
    ),
    "scheduler_received_to_queue_registered_ms": (
        "scheduler_received_ts_ms", "native_queue_registered_ts_ms",
    ),
}


def native_ingress_waits(native: dict | None, *, body_sent_ts_ms=None) -> dict:
    timing = (native.get("attributes") or {}).get("native_ingress_timing_ms") if native else None
    if not isinstance(timing, dict):
        timing = {}
    else:
        timing = dict(timing)
    if timing and body_sent_ts_ms is not None:
        timing["http_body_sent_ts_ms"] = body_sent_ts_ms
    intervals = {}
    for field, (left, right) in NATIVE_INGRESS_INTERVALS.items():
        start, end = timing.get(left), timing.get(right)
        intervals[field] = (
            end - start if type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end) and start <= end else None
        )
    return {"native_ingress_timing_ms": timing or None, **intervals}


def full_expiration_evidence(
    actions: list[dict], completion: float | None, client_submit: float | None,
    native_arrival: float | None, service: float | None,
) -> list[dict]:
    """Locate observed FULL lock release in the request chain without inferring a miss."""
    result = []
    for action in actions:
        amount = (action.get("pool_bytes") or {}).get("kv")
        if type(amount) is not int or amount <= 0:
            continue
        events = action.get("lease_events") or ()
        registered = next(
            (event for event in events if event["event"] == "prefetch_residency_registered"),
            {},
        )
        released = next(
            (event for event in reversed(events)
             if event["event"] == "prefetch_residency_released"), {},
        )
        if (
            registered.get("native_locked") is not True
            or released.get("reason") != "service_window_expired"
            or any(event["event"] == "prefetch_protection_revoked" for event in events)
        ):
            continue
        when = released.get("ts_ms")
        ack = action.get("ack_ts_ms")
        boundaries = (completion, client_submit, native_arrival, service)
        known = [value for value in boundaries if value is not None]
        ordered = (
            type(when) in (int, float) and math.isfinite(when)
            and type(ack) in (int, float) and math.isfinite(ack) and ack <= when
            and all(type(value) in (int, float) and math.isfinite(value) for value in known)
            and all(left <= right for left, right in zip(known, known[1:]))
        )
        stage = "unknown"
        if ordered and completion is not None:
            if when < completion:
                stage = "before_completion"
            elif client_submit is not None:
                if when < client_submit:
                    stage = "awaiting_client_submit"
                elif native_arrival is not None:
                    if when < native_arrival:
                        stage = "awaiting_native_arrival"
                    elif service is not None:
                        stage = "awaiting_service" if when < service else "after_first_service"
        result.append({
            "command_id": action["command_id"], "full_bytes": amount,
            "release_ts_ms": when, "release_stage": stage,
            "full_first_service_reused": action.get("full_first_service_reused"),
            "reentry_ready_observed": any(
                event["event"] == "prefetch_reentry_ready" for event in events
            ),
            "client_submit_observed_by_runtime": any(
                event["event"] == "prefetch_client_submitted" for event in events
            ),
            "native_demand_observed_by_runtime": any(
                event["event"] == "prefetch_demand_submitted" for event in events
            ),
        })
    return result


def full_expiration_summary(rows: list[dict]) -> dict:
    counts, amounts = Counter(), Counter()
    for row in rows:
        for evidence in row["full_residency_expirations"]:
            stage = evidence["release_stage"]
            counts[stage] += 1
            amounts[stage] += evidence["full_bytes"]
    return {
        "full_expiration_commands_by_stage": dict(counts),
        "full_expiration_bytes_by_stage": dict(amounts),
    }


def wait_plan_gap_summary(rows: list[dict]) -> dict:
    """Associate residual native demand with independent waits, not scan counts."""
    categories = defaultdict(list)
    burst_sizes = defaultdict(list)
    reasons = {
        name: defaultdict(list)
        for name in (
            "observed_plan_reasons", "observed_planning_no_step_reasons",
            "observed_planning_blocked_details",
            "observed_no_live_details",
        )
    }
    for row in rows:
        tokens = row["max_observed_planned_full_tokens"]
        category = (
            "positive_full_plan" if tokens is not None and tokens > 0
            else "only_zero_full_plan" if tokens == 0
            else "full_plan_unobserved"
        )
        categories[category].append(row)
        count = row.get("max_observed_planned_node_count")
        burst_category = (
            "sixteen_or_more_nodes_observed" if count is not None and count >= 16
            else "one_to_fifteen_nodes_observed" if count is not None and count > 0
            else "only_empty_plan_observed" if count == 0
            else "node_count_unobserved"
        )
        burst_sizes[burst_category].append(row)
        for field, groups in reasons.items():
            for reason in row.get(field, {}):
                groups[reason].append(row)

    def totals(selected: list[dict]) -> dict:
        return {
            "independent_events": len(selected),
            "events_with_known_completion_boundary": sum(
                row["completion_ts_ms"] is not None for row in selected
            ),
            "remaining_native_full_host_hit_bytes_known": sum(
                row["remaining_native_full_host_hit_bytes"] or 0 for row in selected
            ),
            "remaining_native_full_evidence_unknown_events": sum(
                row["remaining_native_full_host_hit_bytes"] is None for row in selected
            ),
            "events_with_early_reused_full": sum(
                row["early_started_and_reused_full_bytes"] > 0 for row in selected
            ),
        }

    return {
        "plan_evidence_categories": {
            category: totals(selected) for category, selected in sorted(categories.items())
        },
        "restore_burst_size_evidence": {
            category: totals(selected) for category, selected in sorted(burst_sizes.items())
        },
        "residual_native_demand_by_observed_planning_evidence": {
            field: {
                reason: totals(selected) for reason, selected in sorted(groups.items())
            }
            for field, groups in reasons.items()
        },
        "planning_gap_semantics": (
            "One count per independent wait per observed reason, regardless of repeated "
            "samples. Evidence categories partition waits; reason/detail groups overlap "
            "and must not be summed. Zero or missing plans do not prove zero physical "
            "demand. Residual bytes are next-request native FULL Host hits, not a proven "
            "pre-boundary opportunity, exclusive rejection cause or oracle speedup. "
            "Hybrid Host-hit spans may include resident FULL pages with Host copies "
            "and must not be converted into physical DMA bytes. "
            "Burst categories partition waits by the largest explicitly observed plan, "
            "not lifetime node commands. A sixteen-node plan is not proof of truncation "
            "or that a larger early transfer would fit capacity or the service window."
        ),
    }


def wait_plan_timing_attribution(
    rows: list[dict], observations: Iterable[dict], *, full_unit: int | None,
) -> dict:
    """Separate pre-boundary plan snapshots from later physical demand."""
    grouped = {
        wait_event_identity(row): {
            **{name: row.get(name) for name in (
                "source", "workflow_id", "invocation_id", "context_id",
                "context_epoch", "join_id", "completion_ts_ms",
                "remaining_native_full_host_hit_bytes",
                "early_started_and_reused_full_bytes",
            )},
            "first_plan_relative_to_completion_ms": None,
            "first_positive_full_plan_relative_to_completion_ms": None,
            "pre_completion_plan_observations": 0,
            "pre_completion_plan_reasons": Counter(),
            "pre_completion_blocked_details": Counter(),
            "max_pre_completion_planned_full_tokens": None,
            "max_pre_completion_planned_full_bytes": None,
            "max_pre_completion_fitting_full_bytes": None,
            "positive_full_plan_at_or_after_completion": False,
        }
        for row in rows
    }
    for observation in observations:
        if observation.get("event") not in (
            "wait_prefetch_plan", "session_h2d_opportunity",
        ):
            continue
        group = grouped.get(wait_event_identity(observation))
        if group is None or (completion := group["completion_ts_ms"]) is None:
            continue
        relative = observation["ts_ms"] - completion
        previous = group["first_plan_relative_to_completion_ms"]
        group["first_plan_relative_to_completion_ms"] = (
            relative if previous is None else min(relative, previous)
        )
        tokens = observation.get("planned_full_tokens", observation.get("required_full_tokens"))
        if type(tokens) is int and tokens > 0:
            previous = group["first_positive_full_plan_relative_to_completion_ms"]
            group["first_positive_full_plan_relative_to_completion_ms"] = (
                relative if previous is None else min(relative, previous)
            )
            if relative >= 0:
                group["positive_full_plan_at_or_after_completion"] = True
        if relative >= 0:
            continue
        group["pre_completion_plan_observations"] += 1
        group["pre_completion_plan_reasons"][observation.get("reason", "unknown")] += 1
        if observation.get("blocked_detail"):
            group["pre_completion_blocked_details"][observation["blocked_detail"]] += 1
        if type(tokens) is int:
            group["max_pre_completion_planned_full_tokens"] = max(
                group["max_pre_completion_planned_full_tokens"] or 0, tokens,
            )
        amount = (observation.get("planned_pool_bytes") or {}).get("kv")
        if amount is None and type(tokens) is int and type(full_unit) is int:
            amount = tokens * full_unit
        if type(amount) is int:
            group["max_pre_completion_planned_full_bytes"] = max(
                group["max_pre_completion_planned_full_bytes"] or 0, amount,
            )
            if observation.get("fits_current_free_lists") is True:
                group["max_pre_completion_fitting_full_bytes"] = max(
                    group["max_pre_completion_fitting_full_bytes"] or 0, amount,
                )
    result = list(grouped.values())
    for row in result:
        tokens = row["max_pre_completion_planned_full_tokens"]
        row["plan_timing_category"] = (
            "completion_boundary_unknown" if row["completion_ts_ms"] is None
            else "positive_full_plan_before_completion" if tokens is not None and tokens > 0
            else "only_zero_full_plan_before_completion" if tokens == 0
            else "full_size_unknown_before_completion" if row["pre_completion_plan_observations"]
            else "positive_full_plan_only_at_or_after_completion"
            if row["positive_full_plan_at_or_after_completion"]
            else "no_plan_observation_before_completion"
        )
        for field in ("pre_completion_plan_reasons", "pre_completion_blocked_details"):
            row[field] = dict(row[field])
    return {
        "summary": {
            "by_source": {
                source: {
                    "observed_wait_events": len(selected := [
                        row for row in result if row["source"] == source
                    ]),
                    "first_plan_relative_to_completion_ms": distribution(
                        row["first_plan_relative_to_completion_ms"] for row in selected
                    ),
                    "first_positive_full_plan_relative_to_completion_ms": distribution(
                        row["first_positive_full_plan_relative_to_completion_ms"] for row in selected
                    ),
                    "plan_timing_categories": {
                        category: {
                            "independent_events": len(subset := [
                                row for row in selected if row["plan_timing_category"] == category
                            ]),
                            "max_snapshot_pre_completion_full_bytes_known": sum(
                                row["max_pre_completion_planned_full_bytes"] or 0 for row in subset
                            ),
                            "max_snapshot_pre_completion_fitting_full_bytes_known": sum(
                                row["max_pre_completion_fitting_full_bytes"] or 0 for row in subset
                            ),
                            "remaining_native_full_host_hit_bytes_known": sum(
                                row["remaining_native_full_host_hit_bytes"] or 0 for row in subset
                            ),
                            "remaining_native_full_evidence_unknown_events": sum(
                                row["remaining_native_full_host_hit_bytes"] is None for row in subset
                            ),
                            "events_with_early_reused_full": sum(
                                (row["early_started_and_reused_full_bytes"] or 0) > 0
                                for row in subset
                            ),
                            "early_started_and_reused_full_bytes": sum(
                                row["early_started_and_reused_full_bytes"] or 0 for row in subset
                            ),
                        }
                        for category in sorted({row["plan_timing_category"] for row in selected})
                    },
                }
                for source in ("join_ticket", "tool_wait")
            },
        },
        "rows": result,
        "semantics": (
            "Signed plan timestamps relative to the observed complete JOIN/all-tool END; "
            "negative values precede completion. Exactly-at-boundary plans are reactive. "
            "Only recorded plan snapshots count; issued commands do not prove a plan "
            "was available earlier. Unknown completion or size remains unknown. "
            "Maximum snapshot bytes are not cumulative DMA or a counterfactual opportunity "
            "volume. Fits-current-free-lists describes an observed plan, not a reservation, "
            "timing feasibility or eventual benefit. No plan observation before completion "
            "does not prove no opportunity. Next-request native Host hits can become "
            "available after completion and are not an early-prefetch denominator."
        ),
    }


REENTRY_REUSE_PHASES = (
    "completion_to_client_submit", "client_submit_to_native_arrival",
    "native_arrival_to_first_service",
)


def reentry_full_reuse_phases(
    reused: list[dict], *, completion: float | None, client_submit: float | None,
    native_arrival: float | None, service: float | None,
) -> dict:
    boundaries = (completion, client_submit, native_arrival, service)
    result = {}
    for index, name in enumerate(REENTRY_REUSE_PHASES):
        prefix = boundaries[:index + 2]
        valid = all(
            type(value) in (int, float) and math.isfinite(value) for value in prefix
        ) and all(left <= right for left, right in zip(prefix, prefix[1:]))
        selected = [
            action for action in reused
            if valid and action["submit_ts_ms"] is not None
            and prefix[-2] <= action["submit_ts_ms"] < prefix[-1]
        ]
        result[name] = {
            "boundaries_valid": valid,
            "command_ids": [action["command_id"] for action in selected],
            "full_bytes": sum(action["pool_bytes"].get("kv", 0) for action in selected),
        }
    return result


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
            "context_id": identity[2], "context_epoch": observation["context_epoch"],
            "join_id": identity[4], "observed_context_epochs": set(),
            "invocation_id": observation.get("invocation_id"),
            "first_observation_ts_ms": observation["ts_ms"],
            "max_observed_planned_full_tokens": None, "plan_reasons": Counter(),
            "max_observed_planned_node_count": None,
            "planning_no_step_reasons": Counter(),
            "planning_blocked_details": Counter(), "no_live_details": Counter(),
            "max_observed_planned_full_bytes": None,
            "observed_capacity_conditions": Counter(),
            "max_observed_full_tokens_with_mamba_only_shortfall": None,
            "active_tool_ids": set(), "actions": [], "sessions": set(),
        })
        group["first_observation_ts_ms"] = min(
            group["first_observation_ts_ms"], observation["ts_ms"],
        )
        group["observed_context_epochs"].add(observation["context_epoch"])
        group["context_epoch"] = min(group["context_epoch"], observation["context_epoch"])
        if observation.get("invocation_id") is not None:
            group["invocation_id"] = observation["invocation_id"]
        group["active_tool_ids"].update(observation.get("active_tool_ids") or ())
        group["sessions"].add((observation.get("session_id"), observation.get("session_generation")))
        shortfall = observed_capacity_shortfall(observation)
        if shortfall is not None:
            group["observed_capacity_conditions"][shortfall] += 1
            if shortfall == "mamba_only":
                group["max_observed_full_tokens_with_mamba_only_shortfall"] = max(
                    group["max_observed_full_tokens_with_mamba_only_shortfall"] or 0,
                    observation["required_full_tokens"],
                )
        tokens = observation.get("planned_full_tokens", observation.get("required_full_tokens"))
        if type(tokens) is int:
            group["max_observed_planned_full_tokens"] = max(
                group["max_observed_planned_full_tokens"] or 0, tokens,
            )
        planned_nodes = observation.get("planned_nodes")
        if type(planned_nodes) in (list, tuple):
            group["max_observed_planned_node_count"] = max(
                group["max_observed_planned_node_count"] or 0, len(planned_nodes),
            )
        planned_bytes = (observation.get("planned_pool_bytes") or {}).get("kv")
        if type(planned_bytes) is int:
            group["max_observed_planned_full_bytes"] = max(
                group["max_observed_planned_full_bytes"] or 0, planned_bytes,
            )
        if observation.get("event") != "prefetch_native_issued":
            group["plan_reasons"][observation.get("reason", "unknown")] += 1
            for field, target in (
                ("no_step_reason", "planning_no_step_reasons"),
                ("blocked_detail", "planning_blocked_details"),
                ("no_live_detail", "no_live_details"),
            ):
                if observation.get(field):
                    group[target][observation[field]] += 1
    for action in actions:
        identity = wait_event_identity(issues.get(action["command_id"], {}))
        if identity in groups:
            groups[identity]["actions"].append(action)
    ends, joins, submits = {}, {}, defaultdict(list)
    tool_starts = defaultdict(list)
    if clock_offset is not None:
        for path in client_event_paths(arm):
            for row in records(path):
                attrs = row.get("attributes") or {}
                workflow = row.get("workflow_id")
                when = row["ts_ms"] + clock_offset
                if row["kind"] in ("tool_start", "tool_end"):
                    tool_id = attrs.get("tool_run_id") or attrs.get("tool_call_id")
                    if tool_id is not None:
                        if row["kind"] == "tool_start":
                            tool_starts[workflow, row.get("invocation_id")].append((when, tool_id))
                        else:
                            ends[workflow, row.get("invocation_id"), tool_id] = when
                elif row["kind"] == "join_satisfied":
                    joins[workflow, row["join_id"]] = when
                elif row["kind"] == "llm_submit" and row.get("context_id"):
                    submits[workflow, row["context_id"]].append({**row, "ts_ms": when})
    for rows in submits.values():
        rows.sort(key=lambda row: row["ts_ms"])
    for starts in tool_starts.values():
        starts.sort()
    tool_start_times = {
        identity: [when for when, _ in starts]
        for identity, starts in tool_starts.items()
    }
    targets = set()
    for group in groups.values():
        requests = submits.get((group["workflow_id"], group["context_id"]), ())
        origin = next((
            row for row in reversed(requests)
            if row.get("context_epoch") == group["context_epoch"]
            and row.get("invocation_id") == group["invocation_id"]
            and row["ts_ms"] <= group["first_observation_ts_ms"]
        ), None)
        # A delayed observation can follow the beneficiary's submission. Bind
        # the round to its request epoch before considering observation latency.
        following = next((
            row for row in requests
            if row.get("context_epoch") is not None
            and row["context_epoch"] > group["context_epoch"]
            and row.get("invocation_id") == group["invocation_id"]
            and row["ts_ms"] >= (
                origin["ts_ms"] if origin is not None
                else group["first_observation_ts_ms"]
            )
        ), None)
        group["observed_tools_completion_ts_ms"] = None
        group["completion_evidence"] = None
        group["tool_ids_outside_request_round"] = ()
        if group["source"] == "join_ticket":
            completion = joins.get((group["workflow_id"], group["join_id"]))
            if completion is not None:
                group["completion_evidence"] = "join_satisfied"
        else:
            if origin is not None:
                identity = group["workflow_id"], group["invocation_id"]
                starts = tool_starts.get(identity, ())
                times = tool_start_times.get(identity, ())
                left = bisect_left(times, origin["ts_ms"])
                right = (
                    bisect_left(times, following["ts_ms"])
                    if following is not None else len(starts)
                )
                round_tools = {tool for _, tool in starts[left:right]}
                if following is not None:
                    group["tool_ids_outside_request_round"] = tuple(sorted(
                        group["active_tool_ids"] - round_tools,
                    ))
                    group["active_tool_ids"] = round_tools
                else:
                    group["active_tool_ids"].update(round_tools)
            tool_times = [
                ends.get((group["workflow_id"], group["invocation_id"], tool))
                for tool in group["active_tool_ids"]
            ]
            observed_completion = (
                max(tool_times) if tool_times and all(when is not None for when in tool_times)
                else None
            )
            group["observed_tools_completion_ts_ms"] = observed_completion
            completion = (
                observed_completion
                if origin is not None and following is not None
                and observed_completion is not None
                and origin["ts_ms"] <= observed_completion <= following["ts_ms"]
                else None
            )
            group["completion_evidence"] = (
                "whole_tool_request_round" if completion is not None
                else "tool_request_round_unproven"
            )
        group["completion_ts_ms"] = completion
        next_request = None if (
            group["source"] == "join_ticket" and completion is None
        ) else next((
            row for row in submits.get((group["workflow_id"], group["context_id"]), ())
            if row.get("context_epoch") is not None
            and (
                group["source"] == "join_ticket"
                or row["context_epoch"] > group["context_epoch"]
            )
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
    http_attempts = defaultdict(list)
    if targets:
        for path in arm.glob("client_*/workflows/*/child_stream_content.jsonl"):
            for row in records(path):
                if (
                    row.get("event") == "llm_request_http_transport"
                    and row.get("request_id") in targets
                ):
                    http_attempts[row["request_id"]].append(row)
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
        reused = [
            action for action in pending
            if action["full_first_service_reused"] is True
            and rid is not None and action.get("first_service_request_id") == rid
            and action.get("first_service_context_epoch") == request["context_epoch"]
            and action["pool_bytes"] is not None
        ]
        early_reused = [
            action for action in reused
            if action["submit_ts_ms"] is not None and completion is not None
            and action["submit_ts_ms"] < completion
        ]
        ready_reused = [
            action for action in early_reused
            if action["ack_ts_ms"] is not None and action["ack_ts_ms"] <= completion
        ]
        handoff = handoffs_by_request.get(rid, ())
        acks = [action["ack_ts_ms"] for action in pending if action["ack_ts_ms"] is not None]
        client_submit = request["ts_ms"] if request else None
        native_arrival = native["ts_ms"] if native else None
        expirations = full_expiration_evidence(
            pending, completion, client_submit, native_arrival, service,
        )
        transport = http_transport_waits(
            http_attempts.get(rid, []), client_submit, native_arrival,
        )
        rows.append({
            **{name: group[name] for name in (
                "source", "workflow_id", "invocation_id", "context_id", "context_epoch", "join_id",
                "max_observed_planned_full_tokens", "completion_ts_ms",
                "max_observed_planned_node_count",
                "completion_evidence", "observed_tools_completion_ts_ms",
            )},
            "observed_context_epochs": sorted(group["observed_context_epochs"]),
            "observed_tool_call_count": len(group["active_tool_ids"]),
            "tool_ids_outside_request_round": list(group["tool_ids_outside_request_round"]),
            "first_observation_after_completion_ms": (
                max(0., group["first_observation_ts_ms"] - completion)
                if completion is not None else None
            ),
            "first_observation_relative_to_completion_ms": (
                group["first_observation_ts_ms"] - completion
                if completion is not None else None
            ),
            "max_observed_planned_full_bytes": (
                group["max_observed_planned_full_bytes"]
                if group["max_observed_planned_full_bytes"] is not None
                else group["max_observed_planned_full_tokens"] * full_unit
                if type(group["max_observed_planned_full_tokens"]) is int
                and type(full_unit) is int else None
            ),
            "observed_plan_reasons": dict(group["plan_reasons"]),
            "observed_planning_no_step_reasons": dict(group["planning_no_step_reasons"]),
            "observed_planning_blocked_details": dict(group["planning_blocked_details"]),
            "observed_no_live_details": dict(group["no_live_details"]),
            "observed_capacity_conditions": dict(group["observed_capacity_conditions"]),
            "max_observed_full_bytes_with_mamba_only_shortfall": (
                group["max_observed_full_tokens_with_mamba_only_shortfall"] * full_unit
                if type(group["max_observed_full_tokens_with_mamba_only_shortfall"]) is int
                and type(full_unit) is int else None
            ),
            "observed_session_bindings": len(group["sessions"] - {(None, None)}),
            "next_request_id": rid, "first_service_ts_ms": service,
            "client_submit_ts_ms": client_submit, "native_arrival_ts_ms": native_arrival,
            "full_residency_expirations": expirations,
            "node_command_count": len(pending),
            "early_started_and_reused_full_command_ids": [
                action["command_id"] for action in early_reused
            ],
            "early_started_and_reused_full_bytes": sum(
                action["pool_bytes"].get("kv", 0) for action in early_reused
            ),
            "early_ready_and_reused_full_bytes": sum(
                action["pool_bytes"].get("kv", 0) for action in ready_reused
            ),
            "post_completion_reused_full_by_phase": reentry_full_reuse_phases(
                reused, completion=completion, client_submit=client_submit,
                native_arrival=native_arrival, service=service,
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
            "client_submit_to_native_arrival_ms": (
                native["ts_ms"] - request["ts_ms"] if native and request else None
            ),
            **transport,
            **native_ingress_waits(native, body_sent_ts_ms=transport["http_body_sent_ts_ms"]),
            "client_submit_to_first_service_ms": (
                service - request["ts_ms"] if request and service is not None else None
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
    timing = wait_plan_timing_attribution(rows, observations, full_unit=full_unit)
    for row, plan in zip(rows, timing["rows"], strict=True):
        row.update({
            name: value for name, value in plan.items()
            if name.startswith(("first_plan_", "first_positive_", "pre_completion_", "max_pre_"))
            or name in ("plan_timing_category", "positive_full_plan_at_or_after_completion")
        })
    return {
        "summary": {
            "observed_wait_events": len(rows),
            "records_without_wait_event_identity": missing_identity,
            "plan_timing": timing["summary"],
            "by_source": {
                source: {
                    "observed_events": len(selected := [
                        row for row in rows if row["source"] == source
                    ]),
                    "events_with_observed_full_plan": sum(
                        (row["max_observed_planned_full_tokens"] or 0) > 0 for row in selected
                    ),
                    "events_with_capacity_observation": sum(
                        bool(row["observed_capacity_conditions"]) for row in selected
                    ),
                    "events_with_capacity_condition": {
                        condition: sum(
                            row["observed_capacity_conditions"].get(condition, 0) > 0
                            for row in selected
                        )
                        for condition in ("none", "full_only", "mamba_only", "both")
                    },
                    "max_snapshot_full_bytes_under_mamba_only_shortfall_known": sum(
                        row["max_observed_full_bytes_with_mamba_only_shortfall"] or 0
                        for row in selected
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
                    "completion_boundary_unknown_events": sum(
                        row["completion_ts_ms"] is None for row in selected
                    ),
                    "events_first_observed_after_completion": sum(
                        (row["first_observation_after_completion_ms"] or 0) > 0
                        for row in selected
                    ),
                    "events_with_tool_ids_outside_request_round": sum(
                        bool(row["tool_ids_outside_request_round"]) for row in selected
                    ),
                    "early_started_and_reused_full_bytes": sum(
                        row["early_started_and_reused_full_bytes"] for row in selected
                    ),
                    "post_completion_reused_full_by_phase": {
                        phase: {
                            "events_with_reused_full": sum(
                                row["post_completion_reused_full_by_phase"][phase]["full_bytes"] > 0
                                for row in selected
                            ),
                            "full_bytes": sum(
                                row["post_completion_reused_full_by_phase"][phase]["full_bytes"]
                                for row in selected
                            ),
                            "unknown_or_unordered_boundary_events": sum(
                                not row["post_completion_reused_full_by_phase"][phase]["boundaries_valid"]
                                for row in selected
                            ),
                        }
                        for phase in REENTRY_REUSE_PHASES
                    },
                    "remaining_native_full_host_hit_bytes_known": sum(
                        row["remaining_native_full_host_hit_bytes"] or 0 for row in selected
                    ),
                    "remaining_native_full_evidence_unknown_events": sum(
                        row["remaining_native_full_host_hit_bytes"] is None for row in selected
                    ),
                    **wait_plan_gap_summary(selected),
                    "client_submit_to_native_arrival_ms": distribution(
                        row["client_submit_to_native_arrival_ms"] for row in selected
                    ),
                    **{
                        field: distribution(row[field] for row in selected)
                        for field in (
                            "client_submit_to_http_request_ms",
                            "http_request_to_body_sent_ms",
                            "body_sent_to_native_arrival_ms",
                            "http_request_to_response_headers_ms",
                            *NATIVE_INGRESS_INTERVALS,
                        )
                    },
                    "client_submit_to_first_service_ms": distribution(
                        row["client_submit_to_first_service_ms"] for row in selected
                    ),
                    "submit_to_first_service_ms": distribution(
                        row["submit_to_first_service_ms"] for row in selected
                    ),
                    "last_ack_to_first_service_ms": distribution(
                        row["last_ack_to_first_service_ms"] for row in selected
                    ),
                    **full_expiration_summary(selected),
                }
                for source in ("join_ticket", "tool_wait")
            },
        },
        "rows": rows,
        "semantics": (
            "One causal JOIN per workflow/context/JOIN identity across parent epoch "
            "advances, or one tool wait at a context epoch, not one node command. "
            "JOIN service belongs to the first parent request after the observed "
            "complete JOIN, including plans logged after that request is submitted; "
            "an unobserved JOIN boundary leaves that request association unknown. "
            "Plans are bounded snapshots, not a continuous or counterfactual opportunity "
            "denominator; the maximum snapshot is not summed across repeated samples. "
            "Capacity conditions require observable exact FULL/Mamba free lists and a "
            "nonempty plan. An event may appear in multiple conditions at different "
            "snapshots; conditions do not prove unavailable cold victims, the cause "
            "of an actual rejection, or recoverable bytes after a policy change. "
            "Early FULL requires actual submit before complete JOIN/all-tool END and "
            "verified reuse by that episode's next request. Post-completion FULL is "
            "partitioned by actual transfer submission into completion-to-client-submit, "
            "client-submit-to-native-arrival and native-arrival-to-observed-service. "
            "These disjoint half-open intervals require ordered observed boundaries "
            "and the same next-request reuse proof; they do not measure isolated "
            "stall reduction, and issue phase alone does not establish early timing. "
            "Tool boundaries require "
            "the origin and following request plus every intervening tool, including "
            "sequential calls not active at the sampled plan. The following request "
            "is bound by the origin epoch, even if observation delivery occurs later. "
            "Sampled tool IDs outside that bounded request round are reported separately "
            "and cannot extend its completion boundary. Unclosed or unproven "
            "tool rounds keep their completion boundary unknown. Native Host-hit counters and exact demand "
            "handoff receipts are separate remaining-demand evidence; do not add them "
            "without proving disjoint allocations. Client-to-native-arrival includes "
            "request preparation, transport and serving frontend work, not an isolated "
            "client cost. HTTP hook timestamps separate client preparation from pool/"
            "transport and the body-sent-to-native-arrival interval, which still "
            "includes server ingress, validation, template/tokenization and IPC; "
            "it is not isolated tokenizer time. Ambiguous attempts, missing receipts "
            "and unordered boundaries keep the corresponding intervals unknown. "
            "Native-arrival-to-service wait includes admission "
            "and execution queueing, not isolated H2D delay. Sampled GPU dependency "
            "wait is a batch observation and must not be summed across its requests. "
            "FULL expiration stages locate observed locked-receipt releases relative "
            "to the causal boundary, client submit, native arrival and first service. "
            "They do not prove a reuse miss or an isolated cause; reuse evidence is "
            "reported separately. Missing or unordered boundaries remain unknown. "
            "Missing identities, clocks, ACK pool bytes or service evidence stay unknown."
        ),
    }


def audit(arm: Path) -> dict:
    issued, prepared, parks, leases, stages = {}, {}, [], defaultdict(list), {}
    native_prepares, native_prepare_reclaims = [], []
    prepare_selections = []
    terminal_reclaims, parent_handoff_services, parent_handoff_lookaheads = [], [], []
    parent_handoff_rearms = []
    wait_observations, clock_offset = [], None
    for row in records(arm / "opportunities/admission_opportunities.jsonl"):
        event = row["event"]
        if event == "prefetch_native_issued":
            stage = stages.get(row.get("child_request_id"), {})
            issued[row["command_id"]] = {
                **row, "trigger_kind": (
                    "confirmed_join" if row.get("source") == "join_ticket"
                    and row.get("phase") == "confirmed"
                    else row.get("trigger_kind") or stage.get("trigger_kind", "unrecorded_legacy")
                ),
            }
        elif event == "final_stage_latest_start":
            stages[row["child_request_id"]] = row
        elif event == "prepare_native_issued":
            prepared[row["command_id"]] = row
        elif event in ("idle_cold_full_prepare", "critical_parent_cold_full_prepare", "spawn_full_prepare"):
            native_prepares.append(row)
        elif event in ("critical_parent_headroom", "wait_prefetch_capacity_reclaimed"):
            native_prepare_reclaims.append(row)
        elif event == "parent_pressure_demoted":
            parks.append(row)
        elif event == "prepare_candidate_selected":
            prepare_selections.append(row)
        elif event in (
            "terminal_child_cache_reclaimed", "terminal_workflow_root_cache_reclaimed",
        ):
            terminal_reclaims.append(row)
        elif event == "parent_capacity_handoff_first_service":
            parent_handoff_services.append(row)
        elif event == "parent_capacity_handoff_restore_lookahead":
            parent_handoff_lookaheads.append(row)
        elif event == "parent_capacity_handoff_restore_rearmed":
            parent_handoff_rearms.append(row)
        elif event in (
            "prefetch_residency_registered", "prefetch_residency_released",
            "prefetch_protection_revoked",
            "prefetch_reentry_ready", "prefetch_client_submitted", "prefetch_demand_submitted",
        ):
            leases[row["command_id"]].append(row)
        elif event in ("wait_prefetch_plan", "session_h2d_opportunity"):
            if row.get("source") in ("join_wait", "join_ticket", "tool_wait"):
                wait_observations.append(row)
        elif clock_offset is None:
            clock_offset = runtime_clock_offset(row)
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
            "issue_phase": issue.get("phase"),
            "timing_policy": issue.get("timing_policy"),
            "pre_boundary_prediction": issue.get("pre_boundary_prediction"),
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
            "full_use_witness": {
                name: use.get(name) for name in (
                    "node_ids", "matched_node_ids", "reused_full_node_ids",
                    "native_full_kv_hit_length", "device_prefix_indices_len",
                    "full_reuse_proof", "first_service_before_ack",
                )
            } if use else None,
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
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    native_prepared = native_full_prepare_attribution(
        native_prepares, transfers, native_prepare_reclaims, uses, host_evictions,
        full_unit=(status.get("host_pool_evidence") or {}).get("full", {}).get("bytes_per_unit"),
    )
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
        "by_join_trigger": {
            trigger: source_summary([row for row in joins if row["trigger_kind"] == trigger])
            for trigger in sorted({row["trigger_kind"] for row in joins})
        },
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
        "native_full_prepare": native_prepared["summary"],
        "wait_events": wait_events["summary"],
        "terminal_capacity_handoff": terminal_capacity_summary(
            terminal_reclaims, parent_handoff_services, parent_handoff_lookaheads,
            invocation_scopes=terminal_invocation_scopes(arm),
            join_wait_events=wait_events["rows"], prefetch_actions=results,
        ),
        "parent_capacity_restore_retries": {
            "rearm_records": len(parent_handoff_rearms),
            "rearmed_requests": len({
                (row["workflow_id"], row["context_id"], row["join_id"], row["request_id"])
                for row in parent_handoff_rearms
            }),
            "rearmed_requests_with_observed_parent_service": len({
                (row["workflow_id"], row["context_id"], row["join_id"], row["request_id"])
                for row in parent_handoff_rearms
                if any(
                    all(row[field] == service[field] for field in (
                        "workflow_id", "context_id", "join_id", "request_id",
                    )) and row["ts_ms"] <= service["ts_ms"]
                    for service in parent_handoff_services
                )
            }),
            "scope": "linked child allocator release enables a demand retry; not pre-boundary H2D",
        },
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
            "protection until service; Mamba-only transfers are excluded. Pressure "
            "revocation is reported separately and is not eviction or freed-byte "
            "evidence. A later tracking expiry is not another lock expiration. "
            "The legacy full_not_reused_commands field counts negative identity "
            "witnesses, not proven physical nonuse or wasted transfer bytes. Native "
            "radix splits can preserve KV slot indices while cloning index tensors; "
            "v2 object identity can then fail after actual prefix reuse. Neither "
            "a negative witness nor absence of same-node reload establishes its "
            "cause. Historical traces lack split lineage, so do not reconstruct "
            "a corrected reuse rate or infer JCT savings."
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
        "native_full_prepare_attribution": native_prepared,
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
    parser.add_argument(
        "--wait-plan-timing-from", type=Path,
        help="Reuse a completed lifecycle report and stream only its plan timing evidence.",
    )
    args = parser.parse_args()
    if args.wait_plan_timing_from is not None:
        previous = json.loads(args.wait_plan_timing_from.read_text())
        if Path(previous["arm"]).resolve() != args.arm.resolve():
            parser.error("the completed lifecycle report belongs to a different arm")
        status_path = args.arm / "server/native_telemetry_status.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        report = wait_plan_timing_attribution(
            previous["wait_event_attribution"]["rows"],
            records(args.arm / "opportunities/admission_opportunities.jsonl"),
            full_unit=(status.get("host_pool_evidence") or {}).get("full", {}).get("bytes_per_unit"),
        )
        report["arm"] = str(args.arm.resolve())
        report["source_lifecycle"] = str(args.wait_plan_timing_from.resolve())
    else:
        report = audit(args.arm.resolve())
    if args.summary_only:
        report = {
            key: value for key, value in report.items()
            if key not in (
                "rows", "prepare_consumption", "prepare_restore_attribution",
                "native_full_prepare_attribution", "wait_event_attribution",
            )
        }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
