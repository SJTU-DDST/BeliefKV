#!/usr/bin/env python3
"""Measure sealed pool pressure and bounds on retaining returned child contexts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def sweep(intervals, begin, end):
    """Count returned contexts retained until workflow end; bounds are additive."""
    changes = defaultdict(Counter)
    for start, finish, weights in intervals:
        start, finish = max(begin, start), min(end, finish)
        if start >= finish:
            continue
        changes[start].update(weights)
        changes[finish].subtract(weights)
    totals, peak, area = Counter(), Counter(), Counter()
    previous = begin
    for timestamp, delta in sorted(changes.items()):
        for name, value in totals.items():
            area[name] += value * (timestamp - previous)
        totals.update(delta)
        for name, value in totals.items():
            if value < 0:
                raise ValueError("negative retained-state accounting")
            peak[name] = max(peak[name], value)
        previous = timestamp
    for name, value in totals.items():
        area[name] += value * (end - previous)
    return {
        "peak": dict(peak),
        "time_mean": {name: value / (end - begin) for name, value in area.items()},
        "byte_seconds_or_context_seconds": {name: value / 1000. for name, value in area.items()},
    }


def pool_samples(rows):
    result = {}
    for name in ("full", "mamba"):
        values = [row["pools"][name] for row in rows]
        result[name] = {
            "used_bytes": distribution(item["used_bytes"] for item in values),
            "used_fraction": distribution(item["used_fraction"] for item in values),
            "high_water_bytes": max((item["high_water_bytes"] for item in values), default=None),
        }
    return result


def recompute_proxy(rows):
    rows = [row for row in rows if row.get("event") == "context_prefix_reuse_probe"]
    common = sum(row["common_previously_served_input_tokens"] for row in rows)
    missing = sum(row["previously_served_prefix_recompute_proxy_tokens"] for row in rows)
    large = [
        row for row in rows if row["previously_served_prefix_recompute_proxy_tokens"] >= 4096
    ]
    return {
        "requests": len(rows), "common_previously_served_input_tokens": common,
        "missing_previously_served_prefix_proxy_tokens": missing,
        "missing_fraction_of_common": missing / common if common else None,
        "missing_tokens_per_request": distribution(
            row["previously_served_prefix_recompute_proxy_tokens"] for row in rows
        ),
        "requests_missing_at_least_4096_tokens": len(large),
        "missing_tokens_in_large_misses": sum(
            row["previously_served_prefix_recompute_proxy_tokens"] for row in large
        ),
        "semantics": (
            "Same-session previously served input prefix missing in native cache-hit counters. "
            "May include page alignment or hybrid checkpoint effects; not measured kernel "
            "recomputation or proven avoidable eviction."
        ),
    }


def headroom_checks(rows):
    checks = [
        row.get("capacity") or row.get("capacity_reclaim") or {}
        for row in rows
        if row.get("event") in ("critical_parent_headroom", "wait_prefetch_capacity_reclaimed")
    ]
    return {
        "checks": len(checks),
        "full_target_unsatisfied": sum(row.get("full_target_satisfied") is False for row in checks),
        "mamba_target_unsatisfied": sum(row.get("mamba_target_satisfied") is False for row in checks),
        "free_full_tokens_before": distribution(
            row["free_full_tokens_before"] for row in checks
            if finite_number(row.get("free_full_tokens_before"))
        ),
        "full_target_tokens": distribution(
            row["target_full_tokens"] for row in checks
            if finite_number(row.get("target_full_tokens"))
        ),
        "semantics": "Conditioned on capacity actions; not a time-weighted whole-run occupancy census.",
    }


def coexistence_scenario(intervals, host, begin, end):
    names = {
        "served_input_full_accounting_bytes": "served_input_no_sharing_full_bytes",
        "discarded_full_union_lower_bytes": "observed_discarded_full_union_lower_bytes",
        "discarded_full_union_upper_bytes": "observed_discarded_full_union_upper_bytes",
        "one_mamba_slot_per_context_bytes": "one_mamba_slot_per_context_bytes",
    }
    scenario = [
        (start, finish, {name: weights.get(key, 0) for name, key in names.items()})
        for start, finish, weights in intervals
    ]
    for index, row in enumerate(host):
        finish = host[index + 1]["ts_ms"] if index + 1 < len(host) else end
        weights = {
            name: row["pools"]["mamba" if name.startswith("one_mamba") else "full"]["used_bytes"]
            for name in names
        }
        scenario.append((row["ts_ms"], finish, weights))
    return {
        **sweep(scenario, begin, end),
        "semantics": (
            "Time-aligned existing sampled Host occupancy plus retained-child accounting. "
            "No replacement, bandwidth or changed trajectory is simulated. Logical FULL "
            "can double count shared state already present in Host. Discarded-copy bounds "
            "cover only actually released suffixes, not complete dormant histories."
        ),
    }


def analyze(arm, prefix_basis=None):
    sealed = SealedRecords()
    if prefix_basis is not None:
        sealed.prefixes = {name: dict(evidence) for name, evidence in prefix_basis.items()}
    census_path = arm / "server/native_capacity_census.json"
    capacity = json.loads(census_path.read_bytes())["capacity"]
    opportunities = list(sealed(arm / "opportunities/admission_opportunities.jsonl"))
    clocks = [
        row["ts_ms"] - row["monotonic_ms"] for row in opportunities
        if row.get("event") == "admission_runtime_state"
        and finite_number(row.get("monotonic_ms")) and finite_number(row.get("ts_ms"))
    ]
    if not clocks or max(clocks) - min(clocks) > 5.:
        raise ValueError("server wall/monotonic alignment is absent or unstable")
    alignment = distribution(clocks)
    offset = alignment["p50"]
    host = [
        row for row in sealed(arm / "server/host_pool_telemetry.jsonl")
        if row.get("event") == "host_pool_usage"
    ]
    metric_paths = list(arm.glob("client_*/sglang_metrics.jsonl"))
    if len(metric_paths) != 1:
        raise ValueError("expected exactly one metrics stream")
    metrics = list(sealed(metric_paths[0]))
    native = list(sealed(arm / "server/runtime_events.sglang.jsonl"))
    submits = [row for row in native if row.get("kind") == "llm_submit"]
    if not submits or not host or not metrics:
        raise ValueError("active serving/pool evidence is incomplete")
    begin = min(row["ts_ms"] for row in submits)
    end = min(
        max(row["ts_ms"] for row in native),
        max(row["ts_ms"] for row in host),
        max(row["monotonic_ts_ms"] + offset for row in metrics),
        max(row["ts_ms"] for row in opportunities),
    )
    if end <= begin:
        raise ValueError("no common observed serving interval")
    active_host = [row for row in host if begin <= row["ts_ms"] <= end]
    active_metrics = [
        row for row in metrics
        if begin <= row["monotonic_ts_ms"] + offset <= end
    ]
    busy_metrics = [
        row for row in active_metrics
        if row.get("num_running_reqs", 0) > 0 or row.get("num_queue_reqs", 0) > 0
    ]
    eviction = [
        row for row in sealed(arm / "server/eviction_attribution.jsonl")
        if begin <= row["ts_ms"] <= end
    ]
    transfers, host_evictions = Counter(), Counter()
    for row in sealed(arm / "server/host_pool_telemetry.jsonl"):
        if not begin <= row.get("ts_ms", 0) <= end:
            continue
        if row.get("event") == "host_pool_eviction":
            for pool, entry in row["pools"].items():
                host_evictions[pool] += entry["evicted_bytes"]
        elif row.get("event") == "host_pool_transfer_ack":
            for pool, units in row["pool_units"].items():
                transfers[(row["direction"], pool)] += units
    bytes_per_unit = {
        "full": capacity["host_full_bytes"] / capacity["host_full_tokens"],
        "mamba": capacity["host_mamba_bytes"] / capacity["host_mamba_slots"],
    }
    creates, returned, workflow_end, event_counts = {}, {}, {}, Counter()
    for path in sorted(arm.glob("client_*/workflows/*/runtime_events.deepagents.jsonl")):
        for row in sealed(path):
            timestamp = row["ts_ms"] + offset
            if timestamp > end:
                continue
            kind, invocation = row["kind"], row.get("invocation_id")
            event_counts[kind] += 1
            if kind == "invocation_create":
                creates[invocation] = row
            elif kind == "return":
                returned[invocation] = (timestamp, row)
            elif kind == "workflow_end":
                workflow_end[row["workflow_id"]] = timestamp
    requests = {
        row["attributes"]["request_id"]: row for row in submits if row["ts_ms"] <= end
    }
    last_completed = {}
    for row in native:
        if row.get("kind") != "llm_result" or row["ts_ms"] > end:
            continue
        invocation = row["invocation_id"]
        request = requests.get(row["attributes"].get("request_id"))
        if request is None or request["invocation_id"] != invocation:
            continue
        terminal = returned.get(invocation)
        if terminal is not None and row["ts_ms"] > terminal[0] + 5.:
            continue
        prior = last_completed.get(invocation)
        if prior is None or row["ts_ms"] > prior["ts_ms"]:
            last_completed[invocation] = {
                "ts_ms": row["ts_ms"], "input_tokens": request["attributes"]["prompt_tokens"],
                "context_id": request["context_id"],
            }
    released = defaultdict(Counter)
    release_deltas = defaultdict(list)
    child_reclaim_counts = Counter()
    for row in opportunities:
        if (
            row.get("event") != "terminal_child_cache_reclaimed"
            or row.get("invocation_scope") != "dynamic_subagent"
            or row["ts_ms"] > end
        ):
            continue
        child_reclaim_counts["reclaim_records"] += 1
        child_reclaim_counts["retry_records"] += bool(row.get("retry"))
        state = released[row["invocation_id"]]
        prior_lower = max(state["device_full_bytes"], state["host_full_bytes"])
        prior_upper = state["device_full_bytes"] + state["host_full_bytes"]
        for location in ("device", "host"):
            for component, units in row.get(f"freed_{location}_units", {}).items():
                size = row["bytes_per_unit"][str(component)]
                pool = "full" if str(component) == "0" else "mamba"
                state[f"{location}_{pool}_bytes"] += units * size
        release_deltas[row["invocation_id"]].append((row["ts_ms"], {
            "observed_discarded_full_union_lower_bytes": (
                max(state["device_full_bytes"], state["host_full_bytes"]) - prior_lower
            ),
            "observed_discarded_full_union_upper_bytes": (
                state["device_full_bytes"] + state["host_full_bytes"] - prior_upper
            ),
        }))
    intervals, child_rows, unknown_inputs = [], [], 0
    for invocation, (start, terminal) in returned.items():
        create = creates.get(invocation)
        if (
            create is None or create.get("relation_type") != "spawn"
            or create.get("attributes", {}).get("runtime_internal") is True
        ):
            continue
        final = last_completed.get(invocation)
        unknown_inputs += final is None
        freed = released[invocation]
        weights = {
            "contexts": 1,
            "served_input_no_sharing_full_bytes": (
                final["input_tokens"] * bytes_per_unit["full"] if final else 0
            ),
            "one_mamba_slot_per_context_bytes": bytes_per_unit["mamba"],
        }
        finish = workflow_end.get(terminal["workflow_id"], end)
        intervals.append((start, finish, weights))
        # HBM/Host copies can overlap; retries only add capacity at actual release.
        intervals.extend(
            (timestamp, finish, delta)
            for timestamp, delta in release_deltas[invocation]
        )
        child_rows.append({
            "invocation_id": invocation, "workflow_id": terminal["workflow_id"],
            "agent_definition_id": create.get("agent_definition_id"),
            "returned_ts_ms": start, "retained_until_ts_ms": finish,
            "workflow_end_observed": terminal["workflow_id"] in workflow_end,
            "final_completed_input_tokens": final["input_tokens"] if final else None,
            "actual_released_units_bytes": dict(freed),
        })
    identities = defaultdict(set)
    for invocation, row in creates.items():
        if row.get("relation_type") == "spawn" and row.get("agent_instance_id"):
            identities[(row["workflow_id"], row.get("agent_instance_id"))].add(invocation)
    if prefix_basis is not None and sealed.prefixes.keys() != prefix_basis.keys():
        raise ValueError("replay encountered an unbound JSONL source")
    return {
        "arm": str(arm.resolve()), "capacity": capacity,
        "capacity_census_sha256": hashlib.sha256(census_path.read_bytes()).hexdigest(),
        "clock_alignment_wall_minus_monotonic_ms": alignment,
        "window": {"begin_wall_ms": begin, "end_wall_ms": end, "duration_seconds": (end - begin) / 1000},
        "host_all_observed_samples": pool_samples(host),
        "host_common_serving_window_samples": pool_samples(active_host),
        "metrics_common_serving_window": {
            name: distribution(row[name] for row in active_metrics if finite_number(row.get(name)))
            for name in ("num_running_reqs", "num_queue_reqs", "num_used_tokens", "token_usage", "resident_pressure")
        },
        "metrics_with_queued_or_running_requests": {
            name: distribution(row[name] for row in busy_metrics if finite_number(row.get(name)))
            for name in ("num_running_reqs", "num_queue_reqs", "num_used_tokens", "token_usage", "resident_pressure")
        },
        "capacity_action_checks": headroom_checks([
            row for row in opportunities if begin <= row.get("ts_ms", 0) <= end
        ]),
        "host_eviction_bytes": dict(host_evictions),
        "ack_pool_units": {f"{direction}_{pool}": count for (direction, pool), count in transfers.items()},
        "same_session_recompute_proxy": recompute_proxy(eviction),
        "event_counts_at_horizon": dict(event_counts),
        "child_reclaim_counts": dict(child_reclaim_counts),
        "returned_dynamic_children": len(child_rows),
        "returned_children_missing_final_input": unknown_inputs,
        "returned_child_completed_input_tokens": distribution(
            row["final_completed_input_tokens"] for row in child_rows
            if row["final_completed_input_tokens"] is not None
        ),
        "same_agent_instance_multiple_invocations": sum(len(group) > 1 for group in identities.values()),
        "retain_until_workflow_end_counterfactual": sweep(intervals, begin, end),
        "host_plus_retention_accounting_scenario": coexistence_scenario(intervals, host, begin, end),
        "returned_child_rows": child_rows,
        "input_jsonl_prefixes": sealed.prefixes,
        "semantics": (
            "Pool distributions are samples, not useful-state measurements. "
            "Client num_used_tokens/resident_pressure are activity gauges, not a count of "
            "all allocated or evictable HBM pages. Retention replay "
            "does not change scheduling or simulate cache replacement. Served-input FULL is a "
            "no-sharing logical accounting bound for the observed completed-input checkpoints, "
            "excluding later generated tokens and unobserved inputs; not actual extra Host/HBM "
            "demand. Discarded-copy union bounds cover only observed terminal releases, not "
            "all recoverable conversation state. One Mamba slot is a capacity scenario, not "
            "a measured necessary checkpoint count. Current task invocations are terminal; "
            "no counterfactual reuse benefit or performance speedup can be inferred."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="label=path")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replay-report", type=Path, help="Reuse exactly the bound input prefixes.")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous audit outputs")
    basis = json.loads(args.replay_report.read_bytes()) if args.replay_report else None
    arms = {}
    for item in args.arm:
        label, path = item.split("=", 1)
        if label in arms:
            parser.error("duplicate arm label")
        prior = basis["arms"][label] if basis else None
        if prior is not None and Path(prior["arm"]).resolve() != Path(path).resolve():
            parser.error("replay arm differs from the bound source")
        arms[label] = analyze(Path(path), prior["input_jsonl_prefixes"] if prior else None)
        if prior is not None and arms[label]["capacity_census_sha256"] != prior["capacity_census_sha256"]:
            raise ValueError("replayed capacity census changed")
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "arms": arms, "performance_accepted": False,
    }
    if basis:
        report["replayed_report"] = {
            "path": str(args.replay_report.resolve()),
            "sha256": hashlib.sha256(args.replay_report.read_bytes()).hexdigest(),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({
        label: {
            key: row[key] for key in (
                "window", "returned_dynamic_children", "returned_child_completed_input_tokens",
                "host_common_serving_window_samples", "metrics_with_queued_or_running_requests",
                "capacity_action_checks", "same_session_recompute_proxy",
                "retain_until_workflow_end_counterfactual", "host_plus_retention_accounting_scenario",
            )
        } for label, row in arms.items()
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
