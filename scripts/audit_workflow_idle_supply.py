#!/usr/bin/env python3
"""Align external GPU samples with observed workflow supply without changing serving."""

import argparse
from bisect import bisect_right
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
from statistics import median
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_sustained_supply import bound_arrivals


def gpu_suffix(path, zone, tail_bytes):
    """Seal a bounded suffix of an existing sampler, keeping its absolute offset."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.readline()
        start = max(len(header), size - tail_bytes)
        stream.seek(start)
        if start != len(header):
            stream.readline()
        start = stream.tell()
        data = stream.read(size - start)
    data = data[:data.rfind(b"\n") + 1]
    rows = []
    for row in csv.DictReader(
        io.StringIO(data.decode()), fieldnames=header.decode().strip().split(","),
    ):
        when = datetime.strptime(
            row["timestamp"].strip(), "%Y/%m/%d %H:%M:%S.%f",
        ).replace(tzinfo=zone).timestamp() * 1000.
        value = float(row["gpu_utilization_percent"])
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("invalid utilization sample")
        if rows and when <= rows[-1]["wall_ms"]:
            raise ValueError("external sampler clock is not increasing")
        rows.append({"wall_ms": when, "gpu_percent": value})
    if len(rows) < 2:
        raise ValueError("external sampler suffix is empty")
    return rows, {
        "path": str(path.resolve()), "timezone": str(zone),
        "header": header.decode().strip(), "start_byte": start,
        "complete_bytes": len(data), "read_limit_bytes": size,
        "suffix_sha256": hashlib.sha256(data).hexdigest(),
    }


def replay_states(events, times):
    """Observe submitted work separately from tool/JOIN-blocked invocations."""
    events = sorted(events, key=lambda row: row["ts_ms"])
    cursor, pending, tools, joins = 0, {}, set(), {}
    arrived, completed = set(), set()
    result = []
    for when in times:
        while cursor < len(events) and events[cursor]["ts_ms"] <= when:
            row = events[cursor]
            cursor += 1
            kind, invocation = row["kind"], row.get("invocation_id")
            attrs = row.get("attributes") or {}
            if kind == "workflow_start":
                arrived.add(row["workflow_id"])
            elif kind == "workflow_end":
                completed.add(row["workflow_id"])
            elif kind == "llm_submit":
                if invocation in pending:
                    raise ValueError("serial invocation submitted overlapping model calls")
                pending[invocation] = attrs["request_id"]
            elif kind == "llm_result":
                if pending.pop(invocation, None) != attrs["request_id"]:
                    raise ValueError("model completion has no matching submission")
            elif kind in ("tool_start", "tool_end"):
                key = invocation, attrs.get("tool_run_id") or attrs.get("tool_call_id")
                if kind == "tool_start":
                    tools.add(key)
                else:
                    tools.discard(key)
            elif kind == "join_wait":
                joins[row["workflow_id"], row.get("join_id")] = invocation
            elif kind in ("join_satisfied", "join_timeout"):
                joins.pop((row["workflow_id"], row.get("join_id")), None)
        blocked = {key[0] for key in tools} | set(joins.values())
        result.append({
            "arrived": len(arrived), "inflight": len(arrived - completed),
            "submitted_pending_agents": len(pending),
            "ready_ids": set(pending) - blocked,
            "tool_blocked_agents": len({key[0] for key in tools}),
            "join_blocked_agents": len(set(joins.values())),
        })
    return result


def weighted_summary(rows, start, end, *, max_sample_gap_s=.4):
    """Hold each observation only to the next nearby sample, never across holes."""
    totals = Counter()
    reasons = Counter()
    for left, right in zip(rows, rows[1:]):
        lo, hi = max(start, left["offset_s"]), min(end, right["offset_s"])
        if hi <= lo or right["offset_s"] - left["offset_s"] > max_sample_gap_s:
            continue
        duration = hi - lo
        totals["covered_s"] += duration
        totals["gpu_percent_s"] += left["gpu_percent"] * duration
        for key in (
            "inflight", "application_ready_agents", "active_ready_agents",
            "ready_paused_agents", "tool_blocked_agents", "join_blocked_agents",
        ):
            if left.get(key) is not None:
                totals[key + "_s"] += left[key] * duration
                totals[key + "_observed_s"] += duration
        totals["ready_paused_present_s"] += bool(left.get("ready_paused_agents")) * duration
        if left.get("logical_active_fraction") is not None:
            totals["logical_observed_s"] += duration
            totals["logical_fraction_s"] += left["logical_active_fraction"] * duration
        if left.get("device_resident_fraction") is not None:
            totals["device_observed_s"] += duration
            totals["device_resident_fraction_s"] += left["device_resident_fraction"] * duration
        if left["gpu_percent"] == 0:
            totals["zero_sample_s"] += duration
            if left["submitted_pending_agents"] == 0:
                reason = "no_client_model_request_pending"
            elif not left["application_ready_agents"]:
                reason = "pending_but_application_blocked"
            elif left.get("snapshot_age_s") is None or left["snapshot_age_s"] > 1.5:
                reason = "ready_native_eligibility_unknown"
            elif left["active_ready_agents"]:
                reason = "client_ready_and_active"
            elif left["ready_paused_agents"]:
                reason = "client_ready_and_paused"
            else:
                reason = "client_ready_native_membership_unobserved"
            reasons[reason] += duration
    covered = totals["covered_s"]
    return {
        "start_s": start, "end_s": end, "duration_s": end - start,
        "covered_sample_s": covered,
        "coverage_fraction": covered / (end - start),
        "time_weighted_gpu_percent": totals["gpu_percent_s"] / covered if covered else None,
        "zero_sample_s": totals["zero_sample_s"],
        "zero_sample_fraction": totals["zero_sample_s"] / covered if covered else None,
        "zero_sample_attribution_s": dict(reasons),
        "ready_paused_present_fraction": totals["ready_paused_present_s"] / covered if covered else None,
        "time_weighted_mean": {
            key: (
                totals[key + "_s"] / totals[key + "_observed_s"]
                if totals[key + "_observed_s"] else None
            ) for key in (
                "inflight", "application_ready_agents", "active_ready_agents",
                "ready_paused_agents", "tool_blocked_agents", "join_blocked_agents",
            )
        },
        "sampled_logical_active_fraction": (
            totals["logical_fraction_s"] / totals["logical_observed_s"]
            if totals["logical_observed_s"] else None
        ),
        "sampled_device_resident_fraction": (
            totals["device_resident_fraction_s"] / totals["device_observed_s"]
            if totals["device_observed_s"] else None
        ),
        "native_eligibility_coverage_fraction": (
            totals["active_ready_agents_observed_s"] / covered if covered else None
        ),
        "device_occupancy_coverage_fraction": (
            totals["device_observed_s"] / covered if covered else None
        ),
    }


def zero_episodes(rows, arrival_offsets, services):
    groups, current = [], []
    for row in rows:
        if row["gpu_percent"] == 0:
            if current and row["offset_s"] - current[-1]["offset_s"] > .4:
                groups.append(current)
                current = []
            current.append(row)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    result = []
    for group in groups:
        start, end = group[0]["offset_s"], group[-1]["offset_s"]
        if end - start < 1.:
            continue
        middle = group[len(group) // 2]
        previous = [value for value in arrival_offsets if value <= start]
        following = [value for value in arrival_offsets if value > start]
        result.append({
            "start_s": start, "last_zero_sample_s": end,
            "first_to_last_zero_s": end - start, "samples": len(group),
            "no_pending_sample_fraction": sum(
                row["submitted_pending_agents"] == 0 for row in group
            ) / len(group),
            "max_application_ready_agents": max(row["application_ready_agents"] for row in group),
            "max_ready_paused_agents": max(row.get("ready_paused_agents") or 0 for row in group),
            "worker_intervals_overlapping": sum(lo < end and hi > start for lo, hi in services),
            "previous_arrival_s": max(previous) if previous else None,
            "next_arrival_s": min(following) if following else None,
            "midpoint": middle,
        })
    return sorted(result, key=lambda row: row["first_to_last_zero_s"], reverse=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--gpu-csv", type=Path, required=True)
    parser.add_argument("--gpu-timezone", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tail-mib", type=int, default=16)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier observations")
    if args.tail_mib <= 0:
        raise ValueError("positive sampler suffix size required")
    registration = json.loads(args.registration.read_bytes())
    arrivals_path, arrivals = bound_arrivals(registration)
    cohort = json.loads(args.cohort.read_bytes())
    validate_cohort(cohort, arrivals)
    arm = Path(registration["run_root"]) / "full"
    reader = SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    pools = [
        row for row in reader(arm / "server/host_pool_telemetry.jsonl")
        if row.get("event") == "host_pool_usage"
    ]
    offsets = {
        row["workflow_id"]: row["offset_seconds"] for row in arrivals["arrival_schedule"]
    }
    origins = [
        row["ts_ms"] - offsets[row["workflow_id"]] * 1000.
        for row in events if row["kind"] == "workflow_start"
    ]
    if not origins or max(origins) - min(origins) > 1e-5 or not states:
        raise ValueError("arrival clock or state evidence is missing")
    origin = origins[0]
    wall_offsets = [row["ts_ms"] - row["monotonic_ms"] for row in states]
    wall_offset = median(wall_offsets)
    if max(wall_offsets) - min(wall_offsets) > 1.:
        raise ValueError("wall-to-monotonic alignment is unstable")
    gpu, suffix = gpu_suffix(args.gpu_csv, ZoneInfo(args.gpu_timezone), args.tail_mib * 1024**2)
    latest = min(max(row["ts_ms"] for row in events), states[-1]["monotonic_ms"])
    gpu = [
        {**row, "monotonic_ms": row["wall_ms"] - wall_offset}
        for row in gpu if origin <= row["wall_ms"] - wall_offset <= latest
    ]
    if len(gpu) < 2:
        raise ValueError("sampler timezone or coverage does not match this experiment")
    state_times = [row["monotonic_ms"] for row in states]
    pool_times = [row["ts_ms"] for row in pools]
    rows = []
    for sample, replay in zip(gpu, replay_states(events, [row["monotonic_ms"] for row in gpu])):
        state_index = bisect_right(state_times, sample["monotonic_ms"]) - 1
        state = states[state_index] if state_index >= 0 else {}
        a = state.get("agent_admission") or {}
        age = (sample["monotonic_ms"] - state["monotonic_ms"]) / 1000. if state else None
        fresh = age is not None and age <= 1.5
        ready = replay.pop("ready_ids")
        pool_index = bisect_right(pool_times, sample["wall_ms"]) - 1
        pool = pools[pool_index] if pool_index >= 0 else {}
        device = (pool.get("device_pools") or {}).get("full") or {}
        rows.append({
            "offset_s": (sample["monotonic_ms"] - origin) / 1000.,
            "gpu_percent": sample["gpu_percent"], **replay,
            "application_ready_agents": len(ready), "snapshot_age_s": age,
            "active_ready_agents": (
                len(ready & set(a.get("active_invocation_ids", ()))) if fresh else None
            ),
            "ready_paused_agents": (
                len(ready & set(a.get("paused_pending_invocation_ids", ()))) if fresh else None
            ),
            "logical_active_fraction": (
                a["logical_active_tokens"] / a["capacity_tokens"]
                if fresh and a.get("capacity_tokens") else None
            ),
            "device_resident_fraction": (
                device.get("resident_fraction")
                if pool and sample["wall_ms"] - pool["ts_ms"] <= 1500 else None
            ),
        })
    services = []
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") == "gpu_service_sample":
            lo = (row["launch_monotonic_ms"] - origin) / 1000.
            hi = (row["complete_monotonic_ms"] - origin) / 1000.
            services.append((lo, hi))
    start, end = cohort["throughput_window_offset_seconds"]
    observed_end = rows[-1]["offset_s"]
    if observed_end <= start:
        raise ValueError("the fixed common window has not started in this prefix")
    bins = [
        weighted_summary(rows, lo, min(lo + 60., end, observed_end))
        for lo in [start + 60. * index for index in range(math.ceil((end - start) / 60.))]
        if lo < observed_end
    ]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(args.registration.resolve()),
        "origin_monotonic_ms": origin,
        "native_wall_minus_monotonic_ms": wall_offset,
        "clock_offset_spread_ms": max(wall_offsets) - min(wall_offsets),
        "observed_through_offset_s": observed_end,
        "complete_common_window_observed": observed_end >= end,
        "common_window": weighted_summary(rows, start, min(end, observed_end)),
        "observed_run_prefix": weighted_summary(rows, 0., observed_end),
        "common_window_60s_bins": bins,
        "long_zero_episodes": zero_episodes(rows, sorted(offsets.values()), services),
        "gpu_suffix": suffix,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.cohort, arrivals_path, Path(__file__),
        )},
        "limitations": [
            "Utilization comes from an existing external sampler; it is not a CUDA kernel trace.",
            "Zero-sample attribution uses application state at observation time; NVML averaging can lag it.",
            "Client submitted work may precede native ingress; active membership does not prove physical feasibility.",
            "Native worker intervals include waits and CPU submission; overlap is not CUDA execution.",
            "Allocator residency includes evictable and pending cache; it is not unique useful live KV.",
            "Fresh preceding native eligibility is held for at most 1.5s; independent prefixes are not atomic.",
            "No experiment arrivals, policies, model processes or GPU sampling processes were changed.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n",
    )
    with (args.output_dir / "samples.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        "common_window": result["common_window"],
        "longest_zero_episodes": result["long_zero_episodes"][:3],
        "output_dir": str(args.output_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
