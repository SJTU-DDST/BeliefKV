#!/usr/bin/env python3
"""Audit recorded JOIN/tool capacity budgets and native reclaim diagnostics."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def wait_identity(row: dict) -> tuple:
    return (
        *(row.get(field) for field in (
            "source", "workflow_id", "invocation_id", "context_id",
            "context_epoch", "join_id",
        )),
        tuple(sorted(row.get("active_tool_ids") or ())),
    )


def shared_waiting_volume(row, diagnosis, *, full_bytes_per_token):
    shared = [
        check for check in diagnosis.get("waiting_candidate_checks") or ()
        if type(check.get("full_session_ref")) is int and check["full_session_ref"] > 1
    ]
    if not shared:
        return None
    nodes = {}
    unknown = 0
    for check in shared:
        node_id, created, tokens = (
            check.get("node_id"), check.get("creation_time"),
            check.get("full_device_tokens"),
        )
        if (
            type(node_id) is not int or node_id < 0
            or type(created) not in (int, float) or not math.isfinite(created)
            or created < 0 or type(tokens) is not int or tokens < 0
        ):
            unknown += 1
            continue
        identity = node_id, created
        nodes[identity] = max(nodes.get(identity, 0), tokens)
    target, available = (
        diagnosis.get("target_full_tokens"), diagnosis.get("free_full_tokens_after"),
    )
    deficit = (
        max(0, target - available)
        if type(target) is int and type(available) is int
        and target >= 0 and available >= 0 else None
    )
    observed = sum(nodes.values())
    complete = unknown == 0
    return {
        "ts_ms": row.get("ts_ms"),
        "wait_identity": wait_identity(row),
        "shared_check_records": len(shared),
        "known_node_identities": len(nodes),
        "checks_without_valid_identity_or_size": unknown,
        "listed_device_tokens_sum_known": observed,
        "listed_device_tokens": observed if complete else None,
        "listed_device_bytes": (
            observed * full_bytes_per_token
            if complete and full_bytes_per_token is not None else None
        ),
        "post_reclaim_full_deficit_tokens": deficit,
        "post_reclaim_full_deficit_bytes": (
            deficit * full_bytes_per_token
            if deficit is not None and full_bytes_per_token is not None else None
        ),
        "listed_tokens_fraction_of_post_reclaim_deficit": (
            observed / deficit if complete and deficit else None
        ),
        "all_referencing_sessions_waiting": None,
        "node_ids": [
            {"node_id": node_id, "creation_time": created, "device_tokens": tokens}
            for (node_id, created), tokens in nodes.items()
        ],
    }


def evaluate(records, *, full_bytes_per_token=None) -> dict:
    if full_bytes_per_token is not None and (
        type(full_bytes_per_token) is not int or full_bytes_per_token <= 0
    ):
        raise ValueError("FULL bytes per token must be a positive integer")
    counts = Counter()
    buckets, reclaim_outcomes = {}, Counter()
    rejections, waiting_checks, failed_full_rejections = Counter(), Counter(), Counter()
    shared_volumes = []

    def add(name, row):
        bucket = buckets.setdefault(name, {
            "snapshots": 0, "waits": set(), "by_source": Counter(), "examples": [],
        })
        bucket["snapshots"] += 1
        bucket["waits"].add(wait_identity(row))
        bucket["by_source"][row["source"]] += 1
        if len(bucket["examples"]) < 3:
            bucket["examples"].append({
                field: row.get(field) for field in (
                    "ts_ms", "source", "reason", "no_step_reason", "blocked_detail",
                    "capacity_budget", "capacity_reclaim_probe", "capacity_reclaim",
                )
            })

    for row in records:
        counts["records"] += 1
        if row.get("event") != "wait_prefetch_plan":
            continue
        if row.get("source") not in ("join_ticket", "tool_wait"):
            continue
        counts["wait_prefetch_plan_records"] += 1
        budget = row.get("capacity_budget")
        if isinstance(budget, dict):
            add("all_recorded_budgets", row)
            requested = budget.get("requested_full_tokens")
            if type(requested) is int and requested > 0:
                add("positive_requested_full", row)
                amount = budget.get("remaining_byte_limit")
                if budget.get("source") == "native_next_prefill" and amount == 0:
                    add("native_zero_bytes", row)
                    if row.get("blocked_detail") == "prefetch_full_capacity_budget":
                        add("native_zero_bytes_full_block", row)
                elif (
                    type(amount) is int and amount > 0
                    and row.get("blocked_detail") == "prefetch_full_capacity_budget"
                ):
                    add("positive_bytes_full_block", row)
        else:
            counts["plan_records_without_capacity_budget"] += 1
        diagnosis = row.get("capacity_reclaim")
        if not isinstance(diagnosis, dict):
            counts["plan_records_without_reclaim_diagnostics"] += 1
            continue
        add("with_reclaim_diagnostics", row)
        full, mamba = (
            diagnosis.get("full_target_satisfied"), diagnosis.get("mamba_target_satisfied")
        )
        reclaim_outcomes[f"full={full},mamba={mamba}"] += 1
        observed_rejections = {
            reason: value for reason, value in
            (diagnosis.get("native_full_rejections") or {}).items()
            if type(value) is int and value >= 0
        }
        rejections.update(observed_rejections)
        if full is False:
            add("reclaim_full_target_unsatisfied", row)
            failed_full_rejections.update(observed_rejections)
            if isinstance(budget, dict) and budget.get("source") == "native_next_prefill":
                target, available, reserve = (
                    diagnosis.get("target_full_tokens"),
                    diagnosis.get("free_full_tokens_after"),
                    budget.get("reserve_full_tokens"),
                )
                if all(type(value) is int and value >= 0 for value in (
                    target, available, reserve,
                )) and target > reserve and available < target:
                    payload = target - reserve
                    add(
                        "recorded_target_payload_fits_without_full_reserve"
                        if available >= payload else
                        "recorded_target_payload_exceeds_free_full",
                        row,
                    )
                    if available < reserve:
                        add("full_reserve_alone_exceeds_free_full", row)
            published = diagnosis.get("published_waiting_candidates")
            if published == 0:
                add("full_unsatisfied_without_published_waiter", row)
            elif type(published) is int and published > 0:
                add("full_unsatisfied_with_published_waiter", row)
            if diagnosis.get("native_full_scan_truncated") is True:
                add("full_unsatisfied_native_scan_truncated", row)
            if diagnosis.get("transaction_truncated") is True:
                add("full_unsatisfied_transactions_truncated", row)
        for check in diagnosis.get("waiting_candidate_checks") or ():
            waiting_checks["recorded_checks"] += 1
            for field in ("identity_matches", "causal_wait_valid", "inflight"):
                if type(check.get(field)) is bool:
                    waiting_checks[f"{field}={check[field]}"] += 1
            for field in ("full_lock_ref", "mamba_lock_ref"):
                value = check.get(field)
                if type(value) is int and value > 0:
                    waiting_checks[f"{field}>0"] += 1
            refs = check.get("full_session_ref")
            if type(refs) is int and refs > 1:
                waiting_checks["full_session_ref>1"] += 1
            device, host = check.get("full_device_tokens"), check.get("full_host_tokens")
            if type(device) is int and device > 0 and type(host) is int and host < device:
                waiting_checks["full_host_shortfall"] += 1
        volume = shared_waiting_volume(
            row, diagnosis, full_bytes_per_token=full_bytes_per_token,
        )
        if volume is not None:
            shared_volumes.append(volume)

    return {
        "scope": (
            "Recorded plan snapshots, including post-completion observations. Wait identities "
            "use source/context/epoch/JOIN/active tools and are not validated completion labels. "
            "Missing diagnostics are unknown, not zero reclaimable capacity. Diagnostic sums "
            "can repeat the same native attempt or victim and are not unique evictions, "
            "continuous opportunities, recoverable bytes, oracle savings or speedup."
        ),
        "counts": dict(counts),
        "buckets": {
            name: {
                "snapshots": bucket["snapshots"],
                "observed_wait_identities": len(bucket["waits"]),
                "by_source": dict(bucket["by_source"]), "examples": bucket["examples"],
            } for name, bucket in buckets.items()
        },
        "recorded_reclaim_outcomes": dict(reclaim_outcomes),
        "native_full_rejection_check_counts": dict(rejections),
        "full_unsatisfied_native_rejection_check_counts": dict(failed_full_rejections),
        "waiting_candidate_check_counts": dict(waiting_checks),
        "reserve_classification_semantics": (
            "For a positive selected target payload, subtract the same plan's recorded "
            "FULL reserve from its reclaim target and compare with post-reclaim free "
            "tokens. This arithmetic does not validate current input/decode needs, "
            "complete parent readiness, Mamba, timing, DMA, next admission or reuse. "
            "Payload fitting without the reserve does not authorize removing it."
        ),
        "recorded_shared_waiting_volume": {
            "full_bytes_per_token": full_bytes_per_token,
            "diagnostic_snapshots": len(shared_volumes),
            "shared_check_records": sum(row["shared_check_records"] for row in shared_volumes),
            "rows": shared_volumes,
            "semantics": (
                "Only listed multi-reference waiting-candidate checks. Deduplicate node ID/"
                "creation within each diagnostic using its maximum recorded size; unknown "
                "identity or size keeps that diagnostic's complete volume unknown. No "
                "cross-time capacity sum, complete cache census, unique eviction or "
                "recoverable-byte claim. FULL references count branch ancestry coverage, "
                "not necessarily distinct sessions. Other owners' states were not recorded; "
                "a causal wait for one recorded context cannot authorize shared reclamation."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opportunities", type=Path, required=True)
    parser.add_argument("--read-bytes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pool-evidence", type=Path)
    args = parser.parse_args()
    if args.read_bytes < 1:
        parser.error("--read-bytes must be positive")
    if args.output.exists():
        raise FileExistsError(args.output)
    digest, consumed = hashlib.sha256(), 0

    def records():
        nonlocal consumed
        with args.opportunities.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n") or consumed + len(line) > args.read_bytes:
                    raise ValueError("read limit or source splits an evidence line")
                consumed += len(line)
                digest.update(line)
                yield json.loads(line)
                if consumed == args.read_bytes:
                    break
        if consumed != args.read_bytes:
            raise ValueError("source shorter than requested prefix")

    pool = None
    if args.pool_evidence is not None:
        pool = json.loads(args.pool_evidence.read_text())
    full_unit = (
        pool.get("host_pool_evidence", {}).get("full", {}).get("bytes_per_unit")
        if pool is not None else None
    )
    if pool is not None and full_unit is None:
        raise ValueError("pool evidence has no measured FULL bytes per unit")
    result = evaluate(records(), full_bytes_per_token=full_unit)
    result["sources"] = {
        "opportunities": str(args.opportunities.resolve()),
        "opportunities_read_bytes": consumed,
        "opportunities_sha256": digest.hexdigest(),
        "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if args.pool_evidence is not None:
        result["sources"]["pool_evidence"] = str(args.pool_evidence.resolve())
        result["sources"]["pool_evidence_sha256"] = hashlib.sha256(
            args.pool_evidence.read_bytes(),
        ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        **result, "buckets": {
            name: {key: value for key, value in bucket.items() if key != "examples"}
            for name, bucket in result["buckets"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
