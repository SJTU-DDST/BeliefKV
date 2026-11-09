"""Attribute sealed H2D demand batches and diagnose idle-tool window exclusions."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_idle_tool_restore_opportunities import tool_successors
from scripts.audit_join_reentry_service import identity, join_reentries
from scripts.audit_native_h2d_sources import split_h2d_payload
from scripts.audit_restore_wait_cohorts import observed_wait, summarize
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_staged_native_restore import inspect as staged_inspect, transfer_key
from scripts.plot_paused_agent_timeline import samples
from scripts.train_workflow_work import write


def union_duration(intervals):
    merged = []
    for lo, hi in sorted(intervals):
        if hi <= lo:
            continue
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return sum(hi - lo for lo, hi in merged)


def request_metadata(events):
    tools = {row["request_id"]: row for row in tool_successors(events)}
    joins = {row["request_id"] for row in join_reentries(events)[0] if row.get("request_id")}
    roles, first, requests = {}, set(), {}
    for row in sorted(events, key=lambda value: value["ts_ms"]):
        invocation = row.get("invocation_id")
        if row["kind"] == "invocation_create":
            roles[invocation] = (
                "child" if row.get("parent_invocation_id") else
                "root" if row.get("relation_type") == "root" else "unknown"
            )
        if row["kind"] != "llm_submit":
            continue
        attrs = row.get("attributes") or {}
        request = attrs["request_id"]
        if request in requests:
            raise ValueError("duplicate replay model submission")
        role = roles.get(invocation, "unknown")
        initial = invocation not in first
        first.add(invocation)
        kind = (
            "context_summary" if attrs.get("runtime_internal") else
            "post_join" if request in joins else
            "post_tool" if request in tools and not tools[request]["has_join"] else
            "mixed_tool_and_join" if request in tools else
            "first_" + role if initial else "other_continuation"
        )
        requests[request] = {
            "kind": kind, "role": role, "identity": identity(row),
            "first_invocation_request": initial,
            "tool_round_duration_ms": (
                tools[request]["tool_return_ms"]
                - min(lo for lo, _hi in tools[request]["tool_intervals_ms"])
                if request in tools else None
            ),
        }
    return requests


def joint_label(values):
    kinds = sorted(set(values))
    return kinds[0] if len(kinds) == 1 else "mixed:" + "+".join(kinds)


def staged_groups(episodes, metadata):
    grouped = {field: defaultdict(Counter) for field in ("kind", "role", "first_invocation_request")}
    owners = defaultdict(Counter)
    member_kinds, unique_kinds, unique_members = Counter(), defaultdict(set), set()
    for episode in episodes:
        members = episode["requests"]
        details = []
        for member in members:
            meta = metadata.get(member["request_id"])
            if meta is None or meta["identity"] != identity(member):
                raise ValueError("staged request differs from its replay identity")
            details.append(meta)
            member_kinds[meta["kind"]] += 1
            unique_kinds[meta["kind"]].add(member["request_id"])
            unique_members.add(member["request_id"])
        for field, groups in grouped.items():
            label = joint_label(str(meta[field]) for meta in details)
            groups[label]["native_batches"] += 1
            groups[label]["member_associations"] += len(members)
            groups[label]["actual_bytes"] += episode["native_h2d_bytes"]
        if len(members) == 1:
            owner = members[0]["invocation_id"]
            owners[owner]["native_batches"] += 1
            owners[owner]["actual_bytes"] += episode["native_h2d_bytes"]
    return {
        **{"by_" + field: {key: dict(value) for key, value in sorted(groups.items())}
           for field, groups in grouped.items()},
        "member_associations_by_kind": dict(member_kinds),
        "distinct_requests_by_kind": {key: len(value) for key, value in unique_kinds.items()},
        "distinct_requests": len(unique_members),
        "single_member_batches_by_invocation": [
            {"invocation_id": key, **value}
            for key, value in sorted(owners.items(), key=lambda item: -item[1]["actual_bytes"])
        ],
        "mixed_batch_bytes_are_not_allocated_to_individual_members": True,
    }


def window_diagnostics(target, states, aligned, pools, *, reserve_tokens=2048):
    times = [row["ts_ms"] for row in pools]
    state_times = [row["monotonic_ms"] for row in states]
    begin = min(lo for lo, _hi in target["tool_intervals_ms"])
    finish = target["tool_return_ms"]
    start = max(0, bisect_right(state_times, begin) - 1)
    intervals = defaultdict(list)
    for state, following in zip(states[start:], states[start + 1:]):
        now, end = state["monotonic_ms"], following["monotonic_ms"]
        if now >= finish:
            break
        if end - now > 1500 or now not in aligned:
            continue
        if target["invocation_id"] not in state["agent_admission"]["paused_invocation_ids"]:
            continue
        spans = [
            (max(now, lo), min(end, hi, finish)) for lo, hi in target["tool_intervals_ms"]
            if max(now, lo) < min(end, hi, finish)
        ]
        if not spans:
            continue
        intervals["paused_observed_ms"].extend(spans)
        index = bisect_right(times, state["ts_ms"]) - 1
        if index < 0 or state["ts_ms"] - times[index] > 1500:
            intervals["pool_unknown_ms"].extend(spans)
            continue
        pool = (pools[index].get("device_pools") or {}).get("full") or {}
        if pool.get("observable") is not True or type(pool.get("free_units")) is not int:
            intervals["pool_unknown_ms"].extend(spans)
            continue
        free = pool["free_units"] >= target["host_tokens"] + reserve_tokens
        ready = bool(aligned[now]["ready_paused"])
        label = (
            "strict_window_ms" if free and not ready else
            "free_but_ready_competition_ms" if free else
            "insufficient_free_with_ready_ms" if ready else "insufficient_free_without_ready_ms"
        )
        intervals[label].extend(spans)
    durations = {
        field: union_duration(intervals[field]) for field in (
            "paused_observed_ms", "pool_unknown_ms", "strict_window_ms",
            "free_but_ready_competition_ms", "insufficient_free_with_ready_ms",
            "insufficient_free_without_ready_ms",
        )
    }
    if durations["strict_window_ms"] > 0:
        category = "strict_ge_100ms" if durations["strict_window_ms"] >= 100 else "strict_under_100ms"
    elif durations["free_but_ready_competition_ms"] > 0:
        category = "sufficient_free_observed_but_ready_competitors"
    elif sum(durations[field] for field in (
        "insufficient_free_with_ready_ms", "insufficient_free_without_ready_ms",
    )) > 0:
        category = "no_sufficient_free_in_observed_paused_intervals"
    else:
        category = "no_fresh_pool_evidence_during_observed_pause"
    return {"request_id": target["request_id"], "category": category, **durations}


def audit(registration, idle_report):
    reader = SealedRecords()
    reg = json.loads(registration.read_bytes())
    arm = Path(reg["run_root"]) / "full"
    prior = json.loads(idle_report.read_bytes())
    if Path(prior["registration"]).resolve() != registration.resolve():
        raise ValueError("tool opportunity report belongs to a different registration")
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    metadata = request_metadata(events)
    native = list(reader(arm / "server/runtime_events.sglang.jsonl"))
    transfers = list(reader(arm / "server/transfer_telemetry.jsonl"))
    states, journals, counts = [], [], Counter()
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "admission_runtime_state":
            states.append(row)
        elif row.get("event") == "native_demand_restore_staged":
            journals.append(row)
    census_path = arm / "server/native_capacity_census.json"
    capacity = json.loads(census_path.read_bytes())["capacity"]
    unit, remainder = divmod(capacity["host_full_bytes"], capacity["host_full_tokens"])
    if remainder or unit <= 0 or capacity["pool_layout"] != "static_full_only":
        raise ValueError("need exact observed FULL-only byte geometry")
    sources = {
        row["command_id"]: row.get("source")
        for row in reader(arm / "server/physical_action_ack.jsonl")
        if row.get("action") == "PREFETCH_GPU"
    }
    h2d = [
        row for row in transfers if row.get("direction") == "h2d"
        and row.get("status") == "completed"
        and row.get("telemetry_origin") == "native_hicache_ack_v0520"
    ]
    stage = staged_inspect(journals, h2d, bytes_per_token=unit)
    staged_keys = {
        (row["native_submit_ts_ms"], tuple(row["native_node_ids"]), row["native_h2d_bytes"])
        for row in stage["episodes"]
    }
    source_bytes, stream_ms = Counter(), Counter()
    direct = []
    for row in h2d:
        payload, _units = split_h2d_payload(row, sources)
        source_bytes.update(payload)
        elapsed = row.get("transfer_stream_elapsed_ms")
        category = (
            "staged_demand" if transfer_key(row) in staged_keys else
            "controlled_only" if not payload["native"] else
            "direct_native_only" if not sum(payload[name] for name in (
                "predictive", "execution_handoff", "unknown_controlled",
            )) else "mixed_controlled_and_native"
        )
        if isinstance(elapsed, (int, float)):
            stream_ms[category] += elapsed
        if payload["native"] and transfer_key(row) not in staged_keys:
            counts["unstaged_native_batches"] += 1
            counts["unstaged_native_bytes"] += payload["native"]
    host_groups = defaultdict(Counter)
    roles, prompts, cohorts = {}, {}, {}
    native_ids = Counter(row["attributes"]["request_id"] for row in native if row["kind"] == "llm_submit")
    for row in native:
        if row["kind"] != "llm_submit":
            continue
        attrs, request = row["attributes"], row["attributes"]["request_id"]
        meta = metadata.get(request)
        verified = meta is not None and meta["identity"] == identity(row)
        kind = meta["kind"] if verified else "identity_unknown"
        tokens = attrs.get("cached_tokens_host")
        if type(tokens) is not int or tokens < 0:
            raise ValueError("native FULL Host-hit count is unavailable")
        host_groups[kind]["native_submit_records"] += 1
        host_groups[kind]["positive_host_records"] += tokens > 0
        host_groups[kind]["host_hit_tokens"] += tokens
        host_groups[kind]["host_hit_accounting_bytes"] += tokens * unit
        if verified and native_ids[request] == 1:
            roles[request], cohorts[request] = meta["role"], kind
            prompts[request] = attrs.get("prompt_tokens")
        if tokens:
            direct.append({
                "request_id": request, "kind": kind, "role": meta["role"] if verified else "unknown",
                "host_hit_tokens": tokens, "host_hit_accounting_bytes": tokens * unit,
                "native_submit_identity_ambiguous": native_ids[request] != 1,
            })
    host_sum = sum(row["host_hit_accounting_bytes"] for row in direct)
    balance = {
        "total_native_controller_h2d_bytes": sum(row["actual_bytes"] for row in h2d),
        "by_payload_source_bytes": dict(source_bytes),
        "staged_native_demand_bytes": stage["counts"].get("native_h2d_bytes", 0),
        "unstaged_native_remainder_bytes": counts["unstaged_native_bytes"],
        "request_host_hit_accounting_bytes": host_sum,
        "unstaged_remainder_minus_host_accounting_bytes": counts["unstaged_native_bytes"] - host_sum,
    }
    if source_bytes["native"] != balance["staged_native_demand_bytes"] + counts["unstaged_native_bytes"]:
        raise ValueError("native H2D balance does not reconstruct controller bytes")
    aligned, _origin = samples(events, states, reg["workflow_arrivals"])
    aligned = {row["monotonic_ms"]: row for row in aligned}
    pools = sorted(
        (row for row in reader(arm / "server/host_pool_telemetry.jsonl")
         if row.get("event") == "host_pool_usage"), key=lambda row: row["ts_ms"],
    )
    windows = [
        window_diagnostics(target, states, aligned, pools)
        for target in prior["tool_host_hit_successors"] if target["paused_during_tool_observed"]
    ]
    expected_windows = {
        target["request_id"]: target["free_page_and_no_ready_paused_window_ms"]
        for target in prior["tool_host_hit_successors"] if target["paused_during_tool_observed"]
    }
    if any(abs(row["strict_window_ms"] - expected_windows[row["request_id"]]) > 1e-5 for row in windows):
        raise ValueError("new diagnosis changed the previous tool-window population")
    waits = [
        observed_wait(row, cohorts, roles, prompts)
        for row in reader(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_restore_dependency_wait"
    ]
    wait_groups = {}
    for field in ("successor_cohort", "invocation_role"):
        groups = defaultdict(list)
        for row in waits:
            groups[row[field]].append(row)
        wait_groups["by_" + field] = {key: summarize(value) for key, value in sorted(groups.items())}
    for path, evidence in reader.prefixes.items():
        if Path(path).stat().st_size != evidence["complete_prefix_bytes"]:
            raise ValueError("audit requires complete stationary source files")
        previous = prior.get("input_jsonl_prefixes", {}).get(path)
        if previous is not None and previous != evidence:
            raise ValueError("earlier tool opportunity source changed")
    return {
        "observed_at_local": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "registration": str(registration.resolve()), "bytes_per_full_token": unit,
        "byte_balance": balance, "staged_counts": stage["counts"],
        "staged_origins": staged_groups(stage["episodes"], metadata),
        "direct_request_host_hits_by_kind": {key: dict(value) for key, value in sorted(host_groups.items())},
        "direct_host_hit_records": sorted(direct, key=lambda row: -row["host_hit_accounting_bytes"]),
        "native_unstaged_transfer_counts": dict(counts),
        "stream_elapsed_ms_by_whole_batch_kind": dict(stream_ms),
        "restore_wait_samples": {"summary": summarize(waits), **wait_groups},
        "tool_window_exclusions": {
            "paused_host_hit_successors": len(windows),
            "categories": dict(Counter(row["category"] for row in windows)), "rows": windows,
        },
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                registration, idle_report, census_path, Path(__file__),
            )
        },
        "scope": [
            "Complete controller receipts; measured cohort complete, background may be censored.",
            "Staging batches bind exact native submit time/node list/bytes to request sessions.",
            "Mixed-batch bytes stay joint; no arbitrary per-request physical allocation.",
            "Request Host-hit accounting is separate from physical transfer attribution.",
            "Stream elapsed sums and sampled dependency waits are not removable workflow JCT.",
            "Tool windows use retrospective return/Host-hit size only for opportunity diagnosis.",
            "No observed free-page window does not prove agent completion is the only release path.",
        ],
        "GPU_action": False, "tool_prefetch_implemented": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--idle-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve previous evidence and hide CUDA")
    result = audit(args.registration, args.idle_report)
    write(args.output, result)
    print(json.dumps({
        key: result[key] for key in (
            "byte_balance", "staged_counts", "direct_request_host_hits_by_kind",
            "stream_elapsed_ms_by_whole_batch_kind",
        )
    }, indent=2))
    print(json.dumps({
        "staged_by_kind": result["staged_origins"]["by_kind"],
        "staged_by_role": result["staged_origins"]["by_role"],
        "tool_window_categories": result["tool_window_exclusions"]["categories"],
        "restore_wait_summary": result["restore_wait_samples"]["summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
