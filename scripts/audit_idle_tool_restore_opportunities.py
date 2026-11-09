"""Screen tool-return Host hits for earlier observed free-page windows on CPU."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.plot_paused_agent_timeline import samples
from scripts.train_workflow_work import write


def tool_successors(events):
    windows, result = {}, []
    for row in sorted(events, key=lambda item: item["ts_ms"]):
        owner, kind = row.get("invocation_id"), row["kind"]
        if not owner:
            continue
        window = windows.setdefault(owner, {"starts": {}, "ends": {}, "join": False})
        attrs = row.get("attributes") or {}
        if kind == "tool_start":
            window["starts"][attrs.get("tool_run_id") or attrs.get("tool_call_id")] = row["ts_ms"]
        elif kind == "tool_end":
            window["ends"][attrs.get("tool_run_id") or attrs.get("tool_call_id")] = row["ts_ms"]
        elif kind == "join_wait":
            window["join"] = True
        elif kind == "llm_submit":
            starts, ends = window["starts"], window["ends"]
            if starts and starts.keys() <= ends.keys():
                intervals = [(start, ends[key]) for key, start in starts.items()]
                if any(lo > hi or hi > row["ts_ms"] for lo, hi in intervals):
                    raise ValueError("tool-return boundaries are inconsistent")
                result.append({
                    "request_id": attrs["request_id"], "invocation_id": owner,
                    "workflow_id": row["workflow_id"], "context_id": row["context_id"],
                    "context_epoch": row["context_epoch"], "submit_ms": row["ts_ms"],
                    "tool_return_ms": max(ends[key] for key in starts),
                    "has_join": window["join"], "tool_intervals_ms": intervals,
                })
            windows[owner] = {"starts": {}, "ends": {}, "join": False}
    return result


def fit_window(target, states, aligned, pools, *, reserve_tokens=2048):
    pool_times = [row["ts_ms"] for row in pools]
    needed, duration, pause_observed = target["host_tokens"] + reserve_tokens, 0., False
    peak_free, observations = None, 0
    begin = min(lo for lo, _ in target["tool_intervals_ms"])
    finish = target["tool_return_ms"]
    state_times = [row["monotonic_ms"] for row in states]
    left = max(0, bisect_right(state_times, begin) - 1)
    for state, following in zip(states[left:], states[left + 1:]):
        now, next_time = state["monotonic_ms"], following["monotonic_ms"]
        if now >= finish:
            break
        if next_time - now > 1500 or now not in aligned:
            continue
        if target["invocation_id"] not in state["agent_admission"]["paused_invocation_ids"]:
            continue
        intervals = [
            (max(now, lo), min(next_time, hi, finish)) for lo, hi in target["tool_intervals_ms"]
            if max(now, lo) < min(next_time, hi, finish)
        ]
        if not intervals:
            continue
        pause_observed = True
        index = bisect_right(pool_times, state["ts_ms"]) - 1
        if index < 0 or state["ts_ms"] - pool_times[index] > 1500.:
            continue
        pool = (pools[index].get("device_pools") or {}).get("full") or {}
        if pool.get("observable") is not True or type(pool.get("free_units")) is not int:
            continue
        peak_free = max(peak_free or 0, pool["free_units"])
        if aligned[now]["ready_paused"] or pool["free_units"] < needed:
            continue
        # Parallel tools overlap; count each observed wall-time segment once.
        merged = []
        for lo, hi in sorted(intervals):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        duration += sum(hi - lo for lo, hi in merged)
        observations += 1
    return {
        "paused_during_tool_observed": pause_observed,
        "free_page_and_no_ready_paused_window_ms": duration,
        "qualifying_observations": observations, "maximum_observed_free_tokens": peak_free,
        "required_free_tokens_diagnostic": needed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve existing evidence and hide CUDA for the audit")
    reg = json.loads(args.registration.read_bytes())
    arm = Path(reg["run_root"]) / "full"
    reader = SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    aligned, _ = samples(events, states, reg["workflow_arrivals"])
    aligned = {row["monotonic_ms"]: row for row in aligned}
    pools = sorted(
        (row for row in reader(arm / "server/host_pool_telemetry.jsonl")
         if row.get("event") == "host_pool_usage"), key=lambda row: row["ts_ms"],
    )
    native = defaultdict(list)
    for row in reader(arm / "server/runtime_events.sglang.jsonl"):
        if row["kind"] == "llm_submit":
            native[row["attributes"]["request_id"]].append(row)
    counts, targets = Counter(), []
    for successor in tool_successors(events):
        label = "join_and_tool_successors" if successor["has_join"] else "pure_tool_successors"
        counts[label] += 1
        records = native.get(successor["request_id"], ())
        if len(records) != 1 or any(
            records[0].get(name) != successor[name]
            for name in ("workflow_id", "invocation_id", "context_id", "context_epoch")
        ):
            counts[label + "_native_identity_unknown"] += 1
            continue
        host = records[0]["attributes"].get("cached_tokens_host")
        if type(host) is not int or host < 0:
            counts[label + "_host_evidence_unknown"] += 1
            continue
        counts[label + "_native_host_hit_tokens"] += host
        counts[label + "_with_native_host_hit"] += host > 0
        if host <= 0 or successor["has_join"]:
            continue
        target = {**successor, "host_tokens": host}
        target.update(fit_window(target, states, aligned, pools))
        targets.append(target)
    transfer = Counter()
    for row in reader(arm / "server/transfer_telemetry.jsonl"):
        if row.get("direction") != "h2d" or row.get("status") != "completed" or (
            row.get("telemetry_origin") != "native_hicache_ack_v0520"
        ):
            continue
        transfer["completed_native_h2d_records"] += 1
        transfer["actual_bytes"] += row.get("actual_bytes") or 0
        elapsed = row.get("transfer_stream_elapsed_ms")
        if isinstance(elapsed, (int, float)):
            transfer["recorded_stream_elapsed_ms_sum"] += elapsed
        else:
            transfer["records_without_stream_elapsed"] += 1
    counts["host_hit_tool_successors_paused_while_waiting"] = sum(
        row["paused_during_tool_observed"] for row in targets
    )
    counts["host_hit_tool_successors_with_strict_free_page_window"] = sum(
        row["free_page_and_no_ready_paused_window_ms"] > 0 for row in targets
    )
    counts["host_hit_tool_successors_with_strict_window_ge_100ms"] = sum(
        row["free_page_and_no_ready_paused_window_ms"] >= 100 for row in targets
    )
    write(args.output, {
        "registration": str(args.registration.resolve()), "counts": dict(counts),
        "native_transfer_totals": dict(transfer), "tool_host_hit_successors": targets,
        "diagnostic_reserve_tokens": 2048, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.registration, Path(__file__), ROOT / "scripts/plot_paused_agent_timeline.py",
        )},
        "scope": {
            "uses_historical_tool_return_and_next_request_for_CPU_screen_only": True,
            "native_host_hits_are_not_physical_DMA_bytes": True,
            "free_page_screen_does_not_prove_native_checkpoint_or_queue_feasibility": True,
            "capacity_window_ms_is_union_not_contiguous_or_removable_JCT": True,
            "actual_native_h2d_totals_include_prefetch_and_demand": True,
            "stream_elapsed_sum_is_not_exposed_GPU_stall_or_workflow_JCT": True,
            "no_cold_page_reclaim_or_active_agent_eviction_assumed": True,
            "deployable_prefetch_predictor_or_gain_proven": False,
        }, "GPU_action": False,
    })
    print(json.dumps({"counts": counts, "native_transfer_totals": transfer}))


if __name__ == "__main__":
    main()
