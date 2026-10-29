"""Audit completed warm-start memory, pressure release and paused admission."""

from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from scripts.audit_sealed_wait_prefetch import SealedRecords


MAX_AGE_MS = 2000.


def weighted_memory(samples, start, end, capacity, unit_bytes):
    totals = Counter()
    gaps = []
    for current, following in zip(samples, samples[1:]):
        left = max(start, current["ts_ms"])
        right = min(end, following["ts_ms"], current["ts_ms"] + MAX_AGE_MS)
        if right <= left:
            continue
        duration = right - left
        totals["covered_ms"] += duration
        gaps.append(following["ts_ms"] - current["ts_ms"])
        for field in ("resident_units", "free_units", "evictable_units", "non_evictable_units"):
            totals[field] += duration * current[field]
        totals["at_least_95_ms"] += duration * (current["resident_units"] >= .95 * capacity)
    covered = totals["covered_ms"]
    if not covered:
        raise ValueError("no fresh memory observations")
    result = {
        "duration_s": (end - start) / 1000,
        "covered_s": covered / 1000,
        "coverage_percent": 100 * covered / (end - start),
        "maximum_source_gap_s": max(gaps) / 1000,
        "resident_at_least_95_percent_time_share": 100 * totals["at_least_95_ms"] / covered,
    }
    for field in ("resident_units", "free_units", "evictable_units", "non_evictable_units"):
        result[field + "_mean_percent"] = 100 * totals[field] / covered / capacity
        result[field + "_mean_GB"] = totals[field] / covered * unit_bytes / 1e9
    return result


def paused_wait(states, requests, first_service, start, end, capacity):
    by_invocation = defaultdict(list)
    for rid, request in requests.items():
        by_invocation[request["invocation_id"]].append((request["submit_wall_ms"], rid))
    for rows in by_invocation.values():
        rows.sort()
    first_calls = {rows[0][1] for rows in by_invocation.values()}
    sums, fit_requests, pending_requests = Counter(), set(), set()
    per_request = Counter()
    for state, following in zip(states, states[1:]):
        left = max(start, state["ts_ms"])
        right = min(end, following["ts_ms"], state["ts_ms"] + MAX_AGE_MS)
        if right <= left:
            continue
        agent = state["agent"]
        active_tokens = agent.get("logical_active_tokens")
        if not isinstance(active_tokens, int):
            continue
        duration = right - left
        sums["covered_ms"] += duration
        unused = capacity - active_tokens
        marked = set(agent.get("marked_for_pause_invocation_ids", ()))
        waiting, fitting, large_margin = [], [], []
        for invocation in agent.get("paused_pending_invocation_ids", ()):
            if invocation in marked:
                continue
            rows = by_invocation.get(invocation, ())
            index = bisect_right(rows, (state["ts_ms"], "\uffff")) - 1
            if index < 0:
                continue
            rid = rows[index][1]
            request = requests[rid]
            service = first_service[rid]
            if not request["submit_wall_ms"] <= state["ts_ms"] < service:
                continue
            # These requests have never started native service, so their
            # observed input size has no unobserved in-call decode growth.
            interval_left = max(left, request["submit_wall_ms"])
            interval_right = min(right, service, request["complete_wall_ms"])
            if interval_right <= interval_left:
                continue
            waiting.append((interval_left, interval_right))
            pending_requests.add(rid)
            if request["prompt_tokens"] + 100 <= unused:
                fitting.append((interval_left, interval_right))
                fit_requests.add(rid)
                per_request[rid] += interval_right - interval_left
                if request["prompt_tokens"] + 100 + 4096 <= unused:
                    large_margin.append((interval_left, interval_right))
        if waiting:
            sums["any_paused_pre_service_ms"] += max(
                stop for _begin, stop in waiting
            ) - min(begin for begin, _stop in waiting)
        if fitting:
            sums["any_paused_fits_raw_logical_budget_ms"] += max(
                stop for _begin, stop in fitting
            ) - min(begin for begin, _stop in fitting)
        if large_margin:
            sums["any_paused_fits_with_4096_margin_ms"] += max(
                stop for _begin, stop in large_margin
            ) - min(begin for begin, _stop in large_margin)
    if not sums["covered_ms"]:
        return {
            "status": "unknown_missing_per_program_logical_snapshots",
            "coverage_s": 0, "coverage_percent": 0,
            "any_paused_fits_recorded_raw_logical_budget_s": None,
        }
    waits = sorted(value / 1000 for value in per_request.values())
    return {
        "status": "sampled_recorded_logical_budget_only",
        "coverage_s": sums["covered_ms"] / 1000,
        "coverage_percent": 100 * sums["covered_ms"] / (end - start),
        "any_paused_before_first_service_s": sums["any_paused_pre_service_ms"] / 1000,
        "any_paused_fits_recorded_raw_logical_budget_s":
            sums["any_paused_fits_raw_logical_budget_ms"] / 1000,
        "any_paused_fits_with_extra_4096_logical_tokens_s":
            sums["any_paused_fits_with_4096_margin_ms"] / 1000,
        "distinct_observed_paused_pre_service_requests": len(pending_requests),
        "distinct_requests_with_a_logically_fitting_sample": len(fit_requests),
        "summed_request_fitting_wait_s_not_JCT": sum(per_request.values()) / 1000,
        "fitting_first_calls": len(fit_requests & first_calls),
        "fitting_continuation_calls": len(fit_requests - first_calls),
        "request_fitting_wait_s": {
            "p50": waits[max(0, math.ceil(len(waits) * .5) - 1)] if waits else None,
            "p90": waits[max(0, math.ceil(len(waits) * .9) - 1)] if waits else None,
            "max": max(waits, default=None),
        },
        "semantics": (
            "Two-second-capped sample-and-hold; each candidate is already submitted, "
            "paused, not marked for pause and has never received native service. "
            "Fit uses recorded undiscounted active tokens and original 100-token buffer. "
            "No physical-page, native-slot, dependency or alternative-policy proof. "
            "A fitting sampled interval is not necessarily caused by the tick."
        ),
    }


def audit_arm(reader, label, registration):
    plan = json.loads(registration.read_text())
    arm_name, = plan["order"]
    arm = Path(plan["run_root"]) / arm_name
    summary = json.loads((arm / "replay/summary.json").read_text())
    status = json.loads((arm / "server/native_telemetry_status.json").read_text())
    census = json.loads((arm / "server/native_capacity_census.json").read_text())["capacity"]
    if summary["completed_requests"] != 3674 or summary["exact_tokens_verified"] is not True:
        raise ValueError("require the complete exact matched work")
    requests = {}
    for row in reader(arm / "replay/native_requests.jsonl"):
        rid = row["request_id"]
        if rid in requests:
            raise ValueError("duplicate completed request")
        identity = row["native_receipt"]["identity"]
        requests[rid] = {
            "invocation_id": identity[1], "prompt_tokens": row["prompt_tokens"],
            "complete_monotonic_ms": row["native_complete_monotonic_ms"],
        }
    first, offsets, service_records = {}, [], 0
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        service_records += 1
        offsets.append(row["complete_ts_ms"] - row["complete_monotonic_ms"])
        for sample in row["request_samples"]:
            rid = sample["request_id"]
            if rid in requests:
                first[rid] = min(first.get(rid, math.inf), row["service_start_ts_ms"])
    expected = status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    if service_records != expected or len(first) != 3674 or max(offsets) - min(offsets) > 5:
        raise ValueError("native service and clock evidence incomplete")
    offset = statistics.median(offsets)
    origin = summary["workflow_arrival_origin_monotonic_ms"] + offset
    end = summary["finished_monotonic_ms"] + offset
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        rid = row.get("attributes", {}).get("request_id")
        if rid not in requests or row.get("kind") != "llm_submit":
            continue
        requests[rid].setdefault("submit_wall_ms", row["ts_ms"])
    for rid, request in requests.items():
        request["complete_wall_ms"] = request["complete_monotonic_ms"] + offset
        if not request.get("submit_wall_ms") or first[rid] < request["submit_wall_ms"] - 5:
            raise ValueError("request service does not follow observed submission")
    capacity = census["device_full_tokens"]
    unit_bytes, remainder = divmod(census["host_full_bytes"], census["host_full_tokens"])
    if remainder or census["device_mamba_slots"] or capacity != 805198:
        raise ValueError("unexpected common capacity")
    samples = []
    for row in reader(arm / "server/host_pool_telemetry.jsonl"):
        if row.get("event") != "host_pool_usage":
            continue
        usage = row["device_pools"]["full"]
        if (
            usage["free_units"] + usage["resident_units"] != capacity
            or usage["evictable_units"] + usage["non_evictable_units"] != usage["resident_units"]
        ):
            raise ValueError("physical allocator partitions do not reconcile")
        samples.append({"ts_ms": row["ts_ms"], **usage})
    states, pressure, evictions, backups, acks = [], defaultdict(Counter), [], [], {}
    native_restores = defaultdict(list)
    last_state = None
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        event = row.get("event")
        if event == "admission_runtime_state":
            agent = row.get("agent_admission") or row.get("paper_baseline")
            if isinstance(agent, dict):
                states.append({"ts_ms": row["ts_ms"], "agent": agent})
            last_state = row
        elif event == "parent_pressure_demoted":
            item = pressure[row["source"]]
            item["node_release_events"] += 1
            item["full_tokens"] += row["freed_units"].get("0", 0)
        elif event == "predicted_tool_kv_evicted":
            evictions.append(row)
        elif event == "predicted_tool_offload_backup":
            backups.append(row)
        elif event == "native_demand_restore_staged":
            for member in row["requests"]:
                native_restores[member["request_id"]].append(row["native_submit_ts_ms"])
    ack_path = arm / "server/physical_action_ack.jsonl"
    if ack_path.exists():
        for row in reader(ack_path):
            if row.get("action") == "PREPARE_HOST":
                acks[row["command_id"]] = row
    backup_commands = {command for row in backups for command in row["command_ids"]}
    missing = backup_commands - acks.keys()
    sequences, backup_first_service_counts = [], Counter()
    for backup in backups:
        beneficiary = backup["beneficiary_request_id"]
        service_time = first.get(beneficiary)
        for command in backup["command_ids"]:
            ack = acks.get(command)
            if ack is None or service_time is None:
                backup_first_service_counts["ack_or_service_unknown"] += 1
                continue
            backup_first_service_counts[
                "backup_ack_before_consumer_first_service"
                if ack["ts_ms"] <= service_time else
                "backup_ack_after_consumer_first_service"
            ] += 1
            releases = [
                row for row in evictions
                if row["context_id"] == backup["context_id"]
                and row["node_id"] in ack["node_ids"]
                and row["beneficiary_request_id"] == beneficiary
                and ack["ts_ms"] <= row["ts_ms"] <= service_time
            ]
            for release in releases:
                restores = [
                    timestamp for timestamp in native_restores.get(beneficiary, ())
                    if release["ts_ms"] <= timestamp <= service_time
                ]
                if restores:
                    sequences.append({
                        "command_id": command, "beneficiary_request_id": beneficiary,
                        "backup_ack_ts_ms": ack["ts_ms"], "release_ts_ms": release["ts_ms"],
                        "native_restore_submit_ts_ms": min(restores),
                        "first_service_ts_ms": service_time,
                        "backup_bytes": ack["num_bytes"],
                        "released_full_tokens": release["freed_units"].get("0", 0),
                    })
    windows = {
        "complete": (origin, end),
        "common_300_1800_s": (origin + 300000, origin + 1800000),
    }
    result = {
        "registration": str(registration), "arm": str(arm),
        "requests": len(requests), "native_service_records": service_records,
        "clock_offset_range_ms": max(offsets) - min(offsets),
        "capacity_tokens": capacity, "usable_pool_GB": capacity * unit_bytes / 1e9,
        "memory": {
            name: weighted_memory(samples, start, stop, capacity, unit_bytes)
            for name, (start, stop) in windows.items()
        },
        "paused_admission": {
            name: paused_wait(states, requests, first, start, stop, capacity)
            for name, (start, stop) in windows.items()
        },
        "actual_allocation_shortfall_backed_releases": {
            key: {**dict(value), "full_GB": value["full_tokens"] * unit_bytes / 1e9}
            for key, value in pressure.items()
        },
        "demand_aware_predicted_backed_releases": {
            "node_release_events": len(evictions),
            "full_GB": sum(row["freed_bytes"] for row in evictions) / 1e9,
            "beneficiary_requests": len({row["beneficiary_request_id"] for row in evictions}),
        },
        "demand_aware_new_D2H_backups": {
            "issued_events": len(backups), "commands": len(backup_commands),
            "beneficiary_requests": len({row["beneficiary_request_id"] for row in backups}),
            "acked_commands": len(backup_commands & acks.keys()),
            "actual_acked_GB": sum(acks[key]["num_bytes"] for key in backup_commands if key in acks) / 1e9,
            "missing_acks": sorted(missing),
            "first_service_order": dict(backup_first_service_counts),
            "same_owner_node_consumer_observed_backup_release_restore_sequences": sequences,
            "sequence_semantics": (
                "Requires same owner/node/beneficiary, ACK before actual release, "
                "then a journalled native restore and actual first service. "
                "Missing such a chain is unknown, not proof of no release. "
                "No physical slot lineage or counterfactual mandatory-D2H proof."
            ),
            "semantics": (
                "New backups issued for an actual demand-deficit consumer; ACK proves Host copy, "
                "not subsequent physical release, unique bytes, mandatory sequencing or benefit. "
                "Does not enumerate native LRU writebacks."
            ),
        },
        "last_recorded_agent_counts": (
            (last_state.get("agent_admission") or last_state.get("paper_baseline") or {}).get("counts", {})
        ),
        "last_recorded_runtime_counts": last_state.get("counts", {}),
    }
    if not states:
        raise ValueError(f"{label}: no observed admission state")
    return result


def main():
    destination = HERE / "capacity_and_tick_audit_r2.json"
    if destination.exists():
        raise FileExistsError("preserve original audit")
    reader = SealedRecords()
    registrations = {
        "FULL": ROOT.parent / "ops/warm_start_free_extent_20261029_r1.registration.json",
        "ThunderAgent decay": ROOT.parent / "ops/warm_start_thunder_20261029_r1.registration.json",
    }
    arms = {}
    for label, path in registrations.items():
        print(f"Auditing completed {label}", flush=True)
        arms[label] = audit_arm(reader, label, path)
        print(json.dumps({label: {key: arms[label][key] for key in (
            "memory", "paused_admission", "actual_allocation_shortfall_backed_releases",
            "demand_aware_predicted_backed_releases", "demand_aware_new_D2H_backups",
        )}}, ensure_ascii=False), flush=True)
    for source, seal in reader.prefixes.items():
        if Path(source).stat().st_size != seal["complete_prefix_bytes"]:
            raise ValueError("completed source changed")
    result = {
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "scope": "CPU-only audit of complete matched mean60 arrivals; no runtime mutation.",
        "maximum_sample_age_ms": MAX_AGE_MS,
        "arms": arms, "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": [
            "KV allocator occupancy is not whole-card memory use or SM utilization.",
            "Native evictability does not prove coldness, Host backing or zero-cost discard.",
            "Pressure release events are nodes, not distinct consumers or copy episodes.",
            "All mandatory new-D2H-before-load episodes are not fully identifiable in existing logs.",
            "Logical-fitting sampled waits are candidate opportunities, not tick causality or removable JCT.",
            "The old five-second control snapshot may lag; live recorded active-token sums are used instead.",
            "No source changes, arrival changes, new GPU experiment or live restart.",
        ],
    }
    with destination.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(destination, flush=True)


if __name__ == "__main__":
    main()
