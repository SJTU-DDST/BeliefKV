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
    summary = {
        "join_commands": len(joins), "tool_commands": len(tools),
        "handoff_commands": len(handoffs),
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
    }
    return {
        "scope": (
            "Observed controller submission/ACK/first-launch chain; no DMA wall "
            "anchor, exposed-stall measurement or counterfactual speedup."
        ),
        "arm": str(arm), "summary": summary, "rows": results,
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
            "Observed restoration is not final model-forward reuse."
        ),
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
