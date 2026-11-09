"""Bound admission-to-staging leads and idle-tool JOIN opportunities on sealed logs."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_agent_admission_wait import partition
from scripts.audit_free_dispatch_service import identity
from scripts.audit_h2d_agent_origins import union_duration, window_diagnostics
from scripts.audit_native_system_costs import distribution
from scripts.audit_periodic_admission_decisions import EXTRA_GRANTS
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.plot_paused_agent_timeline import samples
from scripts.train_workflow_work import write


def decisions(row):
    """Return observed grants and pauses, without interpreting marks as a pause."""
    name, now = row.get("event"), row.get("monotonic_s")
    if name == "agent_periodic_admission_decision":
        for item in row["programs"]:
            key = item.get("key")
            if key is None:
                continue
            if not item["active_after_pressure"]:
                yield key, False, now
            elif not item["active_before"]:
                yield key, True, now
    elif name in EXTRA_GRANTS:
        for item in row["programs"]:
            yield item["key"], True, now
        for item in row.get("donors", ()):
            yield item["key"], False, now
    elif name == "agent_initial_registration_decision":
        yield row["key"], row["active_after_arrival"], now
    elif name == "agent_full_offload_paused":
        yield row["key"], False, now
    elif name == "agent_predicted_tool_replacement":
        # Its adjacent free grant is the consumer's actual publication.
        yield row["donor"], False, now


def retained_grant_lead(key, begin, stage, grants, pauses):
    candidates = [item for item in grants.get(key, ()) if item[:2] <= stage]
    if not candidates:
        return {"status": "no_exact_request_grant_record"}
    grant = max(candidates)
    if any(grant[:2] < pause <= stage for pause in pauses.get(key[2], ())):
        return {"status": "pause_observed_after_last_exact_grant"}
    return {
        "status": "exact_grant_without_later_observed_pause",
        "grant_event": grant[2],
        "recorded_grant_to_staging_ms": stage[0] - grant[0],
        "submitted_and_granted_to_staging_ms": stage[0] - max(begin, grant[0]),
    }


def join_populations(events, states):
    joins, terminal, ended = {}, set(), set()
    counts, cursor = {}, 0
    events = sorted(events, key=lambda row: row["ts_ms"])
    for state in states:
        now = state["monotonic_ms"]
        while cursor < len(events) and events[cursor]["ts_ms"] <= now:
            row = events[cursor]
            cursor += 1
            pair = row["workflow_id"], row.get("join_id")
            if row["kind"] == "join_wait":
                joins[pair] = row["invocation_id"]
            elif row["kind"] in ("join_satisfied", "join_timeout"):
                joins.pop(pair, None)
            elif row["kind"] in ("return", "invocation_cancel"):
                terminal.add(row["invocation_id"])
            elif row["kind"] == "workflow_end":
                ended.add(row["workflow_id"])
        counts[now] = len({
            owner for (workflow, _join), owner in joins.items()
            if workflow not in ended and owner not in terminal
        })
    return counts


def join_window(target, states, aligned, pools, joins):
    """Count structural possibilities only; JOIN state never proves cold KV."""
    times = [row["ts_ms"] for row in pools]
    begin = min(lo for lo, _ in target["tool_intervals_ms"])
    finish = target["tool_return_ms"]
    state_times = [row["monotonic_ms"] for row in states]
    first = max(0, bisect_right(state_times, begin) - 1)
    intervals = defaultdict(list)
    for state, following in zip(states[first:], states[first + 1:]):
        now, end = state["monotonic_ms"], following["monotonic_ms"]
        if now >= finish:
            break
        if end - now > 1500 or now not in aligned:
            continue
        if target["invocation_id"] not in state["agent_admission"]["paused_invocation_ids"]:
            continue
        index = bisect_right(times, state["ts_ms"]) - 1
        if index < 0 or state["ts_ms"] - times[index] > 1500:
            continue
        pool = (pools[index].get("device_pools") or {}).get("full") or {}
        if pool.get("observable") is not True or type(pool.get("free_units")) is not int:
            continue
        if aligned[now]["ready_paused"]:
            continue
        label = (
            "strict_free_ms" if pool["free_units"] >= target["host_tokens"] + 2048 else
            "insufficient_free_join_present_ms" if joins[now] else
            "insufficient_free_no_join_ms"
        )
        intervals[label].extend(
            (max(now, lo), min(end, hi, finish))
            for lo, hi in target["tool_intervals_ms"]
            if max(now, lo) < min(end, hi, finish)
        )
    return {name: union_duration(intervals[name]) for name in (
        "strict_free_ms", "insufficient_free_join_present_ms", "insufficient_free_no_join_ms",
    )}


def granted_free_window(begin, end, native_tokens, states, pools):
    """Screen the actual staged batch footprint during its recorded grant lead."""
    state_times = [row["monotonic_ms"] for row in states]
    pool_times = [row["ts_ms"] for row in pools]
    first = max(0, bisect_right(state_times, begin) - 1)
    covered, fitting = [], []
    peak_free = None
    for state, following in zip(states[first:], states[first + 1:]):
        now, next_time = state["monotonic_ms"], following["monotonic_ms"]
        if now >= end:
            break
        lo, hi = max(begin, now), min(end, next_time)
        if hi <= lo or next_time - now > 1500:
            continue
        index = bisect_right(pool_times, state["ts_ms"]) - 1
        if index < 0 or state["ts_ms"] - pool_times[index] > 1500:
            continue
        pool = (pools[index].get("device_pools") or {}).get("full") or {}
        if pool.get("observable") is not True or type(pool.get("free_units")) is not int:
            continue
        covered.append((lo, hi))
        peak_free = max(peak_free or 0, pool["free_units"])
        reserve = max(2048, state["reserve_full_tokens"])
        if pool["free_units"] >= native_tokens + reserve:
            fitting.append((lo, hi))
    observed = union_duration(covered)
    return {
        "sampled_free_window_ms": union_duration(fitting),
        "fresh_pool_coverage_ms": observed,
        "pool_unknown_ms": max(0., end - begin - observed),
        "maximum_observed_free_tokens": peak_free,
        "actual_staged_batch_full_tokens": native_tokens,
        "reserve_floor_tokens": 2048,
    }


def audit(recovery_path, idle_path, origins_path):
    recovery = json.loads(recovery_path.read_bytes())
    idle = json.loads(idle_path.read_bytes())
    origins = json.loads(origins_path.read_bytes())
    registration = Path(idle["registration"])
    reg = json.loads(registration.read_bytes())
    if (
        Path(origins["registration"]).resolve() != registration.resolve()
        or recovery["inputs_sha256"].get(str(origins_path.resolve())) != sha256_file(origins_path)
    ):
        raise ValueError("different source experiments or changed H2D attribution")
    arm = Path(reg["run_root"]) / "full"
    reader = SealedRecords()
    states, stages = [], {}
    grants, pauses = defaultdict(list), defaultdict(list)
    stage_rows = {}
    for sequence, row in enumerate(reader(arm / "opportunities/admission_opportunities.jsonl")):
        event = row.get("event")
        if event == "admission_runtime_state":
            a = row["agent_admission"]
            states.append({
                "ts_ms": row["ts_ms"], "monotonic_ms": row["monotonic_ms"],
                "physical_disabled": row["physical_disabled"],
                "reserve_full_tokens": row["prefetch_residency_budget"]["reserve_full_tokens"],
                "agent_admission": {name: a[name] for name in (
                    "active_invocation_ids", "paused_invocation_ids",
                    "capacity_tokens", "logical_active_tokens",
                )},
            })
            for owner in a["paused_invocation_ids"]:
                pauses[owner].append((row["monotonic_ms"], sequence))
        elif event == "native_demand_restore_staged":
            for member in row["requests"]:
                rid = member["request_id"]
                if rid not in stages:
                    stages[rid] = row["monotonic_ms"], sequence, identity(member)
                    stage_rows[rid] = row
        elif event == "workflow_remaining_pressure_pause" and not row["marked_for_pause"]:
            pauses[row["selected"]].append((row["monotonic_s"] * 1000, sequence))
        for key, active, now in decisions(row):
            if not isinstance(now, (int, float)) or not math.isfinite(now):
                raise ValueError("admission record lacks a finite monotonic time")
            if active:
                grants[identity(key, grant=True)].append((now * 1000, sequence, event))
            else:
                pauses[key["invocation_id"]].append((now * 1000, sequence))
    normalized = [
        {"ts_ms": row["monotonic_ms"], "agent_admission": row["agent_admission"]}
        for row in states
    ]
    pools = sorted(
        (row for row in reader(arm / "server/host_pool_telemetry.jsonl")
         if row["event"] == "host_pool_usage"), key=lambda row: row["ts_ms"],
    )
    rows = []
    for target in recovery["requests"]:
        phase = target["staged_phases"]
        if not phase["staging_before_first_service"]:
            continue
        rid = target["request_id"]
        when, sequence, key = stages[rid]
        if (key[1], key[2], key[3], key[4]) != tuple(target[name] for name in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
        )):
            raise ValueError("request staging identity changed")
        begin = when - phase["submit_to_first_staging_ms"]
        end = when + phase["first_staging_to_service_ms"]
        before = partition(begin, when, target["invocation_id"], normalized)
        after = partition(when, end, target["invocation_id"], normalized)
        if any(not math.isclose(
            before[name] + after[name], target["eligibility"][name], abs_tol=1e-4,
        ) for name in before):
            raise ValueError("phase eligibility does not reconstruct original wait")
        lead = retained_grant_lead(key, begin, (when, sequence), grants, pauses)
        capacity = None
        if lead["status"] == "exact_grant_without_later_observed_pause":
            capacity = granted_free_window(
                when - lead["submitted_and_granted_to_staging_ms"], when,
                stage_rows[rid]["native_full_tokens"], states, pools,
            )
        rows.append({
            "request_id": rid, "cohort": target["cohort"],
            "submit_monotonic_ms": begin, "first_staging_monotonic_ms": when,
            "before_staging_eligibility_ms": before,
            "after_staging_eligibility_ms": after,
            "grant_lead": lead, "granted_capacity_screen": capacity,
            "native_batch_members": len(stage_rows[rid]["requests"]),
        })
    groups = {}
    for cohort in sorted({row["cohort"] for row in rows}):
        members = [row for row in rows if row["cohort"] == cohort]
        leads = [
            row["grant_lead"]["submitted_and_granted_to_staging_ms"]
            for row in members if row["grant_lead"]["status"]
            == "exact_grant_without_later_observed_pause"
        ]
        groups[cohort] = {
            "requests": len(members),
            "before_staging_mean_per_workflow_s": {
                name: sum(row["before_staging_eligibility_ms"][name] for row in members)
                / 1000 / recovery["measured_workflows"]
                for name in ("active_ms", "inactive_ms", "unknown_ms")
            },
            "exact_grant_record_status": dict(Counter(
                row["grant_lead"]["status"] for row in members
            )),
            "submitted_and_granted_lead_ms": distribution(leads),
            "recorded_leads_ge_100ms": sum(value >= 100 for value in leads),
            "recorded_leads_ge_500ms": sum(value >= 500 for value in leads),
            "recorded_leads_ge_2000ms": sum(value >= 2000 for value in leads),
            "recorded_grants_with_sampled_free_window": sum(
                (row["granted_capacity_screen"] or {}).get("sampled_free_window_ms", 0) > 0
                for row in members
            ),
            "recorded_grants_with_sampled_free_ge_100ms": sum(
                (row["granted_capacity_screen"] or {}).get("sampled_free_window_ms", 0) >= 100
                for row in members
            ),
            "sampled_free_window_union_sum_ms": sum(
                (row["granted_capacity_screen"] or {}).get("sampled_free_window_ms", 0)
                for row in members
            ),
        }
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    aligned, _ = samples(events, states, reg["workflow_arrivals"])
    aligned = {row["monotonic_ms"]: row for row in aligned}
    joins = join_populations(events, states)
    windows = []
    for target in idle["tool_host_hit_successors"]:
        if not target["paused_during_tool_observed"]:
            continue
        detail = join_window(target, states, aligned, pools, joins)
        old = window_diagnostics(target, states, aligned, pools)
        if not math.isclose(detail["strict_free_ms"], old["strict_window_ms"], abs_tol=1e-4):
            raise ValueError("changed original free-only window")
        windows.append({
            "request_id": target["request_id"], **detail,
            "host_tokens": target["host_tokens"],
        })
    for path, prefix in reader.prefixes.items():
        for prior in (origins, idle, recovery):
            previous = prior["input_jsonl_prefixes"].get(path)
            if previous is not None and prefix != previous:
                raise ValueError("source prefix changed")
        if Path(path).stat().st_size != prefix["complete_prefix_bytes"]:
            raise ValueError("expected stationary complete source")
    return {
        "observed_at_local": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "registration": str(registration),
        "measured_workflows": recovery["measured_workflows"],
        "staged_critical_requests_by_cohort": groups, "requests": rows,
        "idle_tool_windows": {
            "paused_host_hit_successors": len(windows),
            "strict_free_any": sum(row["strict_free_ms"] > 0 for row in windows),
            "strict_free_ge_100ms": sum(row["strict_free_ms"] >= 100 for row in windows),
            "additional_no_free_no_ready": sum(
                row["strict_free_ms"] == 0
                and row["insufficient_free_join_present_ms"] + row["insufficient_free_no_join_ms"] > 0
                for row in windows
            ),
            "additional_with_structural_join_presence": sum(
                row["strict_free_ms"] == 0 and row["insufficient_free_join_present_ms"] > 0
                for row in windows
            ),
            "safe_join_donor_opportunities": None,
            "rows": windows,
        },
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            recovery_path, idle_path, origins_path, registration, Path(__file__),
            ROOT / "scripts/audit_agent_admission_wait.py",
            ROOT / "scripts/audit_h2d_agent_origins.py",
            ROOT / "scripts/plot_paused_agent_timeline.py",
        )},
        "scope": [
            "Same fixed18 critical paths; whole-run background supplies observed contention.",
            "Fresh eligibility samples partition pre-staging waiting; not physical free space.",
            "Exact grants match request/epoch/attempt/session; later observed pauses revoke them.",
            "No later observed pause does not prove all unlogged transitions were absent.",
            "Grant lead is not a native capacity, input, slot, queue or service commitment.",
            "Granted capacity screens the entire actual staged batch plus sampled reserve>=2048.",
            "Free-window samples are not safe native allocation, available DMA slots or GPU gains.",
            "Per-request windows may overlap the same batch; their sum is not unique device time.",
            "JOIN waiters do not establish HBM residency, settled Host copies or safe native reclaim.",
            "Idle windows use retrospective tool returns and future Host hits only for diagnosis.",
            "Strict windows are unions, not contiguous transfer proofs or removable JCT.",
            "No migration or scheduler behavior changed; no GPU work or prefetch implementation.",
        ],
        "GPU_action": False, "tool_prefetch_implemented": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("recovery", "idle", "origins", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve evidence and hide CUDA")
    result = audit(args.recovery, args.idle, args.origins)
    write(args.output, result)
    print(json.dumps({
        "staged_critical_requests_by_cohort": result["staged_critical_requests_by_cohort"],
        "idle_tool_windows": {
            key: value for key, value in result["idle_tool_windows"].items() if key != "rows"
        },
    }, indent=2))


if __name__ == "__main__":
    main()
