#!/usr/bin/env python3
"""Compare completed live-policy runs without claiming trajectory-matched speedup."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import gzip
import json
from pathlib import Path
from statistics import mean, median

from beliefkv.metrics.execution_timeline import _merge_intervals


def records(path: Path):
    if path.exists():
        with path.open() as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def transfer_parts(
    row: dict, acknowledgements: dict[str, dict], sizes: dict[str, int],
) -> list[dict]:
    total = int(row["actual_bytes"])
    native_pools = {
        name: int(count) * sizes[name]
        for name, count in row["num_tokens_by_pool"].items()
        if name in sizes
    }
    parts = []
    for receipt in row.get("tagged_child_commits") or ():
        ack = acknowledgements.get(receipt.get("command_id"))
        if ack is None:
            continue
        amount = int(ack["num_bytes"])
        pools = dict(ack.get("pool_bytes") or ())
        total -= amount
        for name, size in pools.items():
            native_pools[name] = native_pools.get(name, 0) - size
        parts.append({
            "kind": ack["action"], "bytes": amount, "pool_bytes": pools,
        })
    if total < 0 or any(size < 0 for size in native_pools.values()):
        raise ValueError("tagged transfer accounting exceeds its native receipt")
    if total:
        parts.append({
            "kind": "native", "bytes": total, "pool_bytes": native_pools,
        })
    if sum(part["bytes"] for part in parts) != int(row["actual_bytes"]):
        raise ValueError("transfer byte accounting is not conserved")
    return parts


def interval_milliseconds(rows: list[dict], start: float, end: float) -> float:
    intervals = _merge_intervals(
        (max(start, row["start_ms"]), min(end, row["end_ms"]))
        for row in rows if row["start_ms"] < end and row["end_ms"] > start
    )
    return sum(right - left for left, right in intervals)


def phase_statistics(rows: list[dict]) -> dict:
    count = sum(row["observation_count"] for row in rows)
    milliseconds = sum(row["end_ms"] - row["start_ms"] for row in rows)
    return {
        "batch_count": count, "worker_interval_seconds": milliseconds / 1000,
        "mean_worker_interval_ms": milliseconds / count if count else None,
        "mean_batch_size": (
            sum(row["running"] * row["observation_count"] for row in rows) / count
            if count else None
        ),
    }


def last_runtime_state(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        offset = max(0, path.stat().st_size - 131072)
        stream.seek(offset)
        if offset:
            stream.readline()
        rows = [json.loads(line) for line in stream if line.strip()]
    return next((
        row for row in reversed(rows) if row.get("event") == "admission_runtime_state"
    ), {})


def restore_wait_statistics(rows) -> dict:
    values = [
        row["gpu_layer_dependency_wait_ms"] for row in rows
        if row.get("event") == "gpu_restore_dependency_wait"
    ]
    return {
        "sampled_batches": len(values), "sampled_gpu_wait_sum_ms": sum(values),
        "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95),
        "mean_ms": mean(values) if values else None,
        "semantics": (
            "Sampled CUDA-stream native layer-dependency waits including event "
            "overhead; not all H2D, not request queue delay or an oracle JCT bound."
        ),
    }


def compare_run(arm: Path, timeline_path: Path, bin_seconds: int) -> dict:
    clients = list(arm.glob("client_*/summary.json"))
    if len(clients) != 1:
        raise ValueError(f"expected one completed summary: {arm}")
    client = clients[0].parent
    summary = json.loads(clients[0].read_text())
    status = json.loads((arm / "server/native_telemetry_status.json").read_text())
    manifest = json.loads((client / "manifest.json").read_text())
    with gzip.open(timeline_path, "rt") as stream:
        timeline = json.load(stream)
    anchor = datetime.fromisoformat(timeline["start_anchor"])
    anchor_ms = anchor.timestamp() * 1000.
    service = next(
        row for row in records(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_service_sample"
    )
    clock_offset = service["complete_ts_ms"] - service["complete_monotonic_ms"]
    duration_ms = timeline["duration_ms"]
    bins = [defaultdict(list) for _ in range(int(duration_ms // (1000 * bin_seconds)) + 1)]

    def bucket(ts_ms: float):
        index = int((ts_ms - anchor_ms) // (1000 * bin_seconds))
        return bins[index] if 0 <= index < len(bins) else None

    restore_waits = [
        row for row in records(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_restore_dependency_wait"
    ]
    for row in restore_waits:
        item = bucket(row["ts_ms"])
        if item is not None:
            item["gpu_restore_dependency_wait_ms"].append(row["gpu_layer_dependency_wait_ms"])
    output_tokens = input_tokens = 0
    request_latencies = []
    request_submits = {}
    for row in records(arm / "server/runtime_events.sglang.jsonl"):
        item = bucket(row["ts_ms"])
        attrs = row.get("attributes") or {}
        if row["kind"] == "llm_submit":
            request_submits[attrs["request_id"]] = row["ts_ms"]
            input_tokens += attrs.get("prompt_tokens") or 0
            if item is not None:
                item["prompt_tokens"].append(attrs.get("prompt_tokens") or 0)
                item["uncached_tokens"].append(attrs.get("uncached_prompt_tokens") or 0)
                item["host_hit_tokens"].append(attrs.get("cached_tokens_host") or 0)
        elif row["kind"] == "llm_result":
            output_tokens += attrs.get("output_tokens") or 0
            submitted = request_submits.pop(attrs["request_id"], None)
            if submitted is not None:
                request_latencies.append(max(0., row["ts_ms"] - submitted))
            if item is not None:
                item["output_tokens"].append(attrs.get("output_tokens") or 0)

    with (client / "gpu_samples.csv").open() as stream:
        for row in csv.DictReader(stream):
            timestamp = datetime.strptime(row["timestamp"].strip(), "%Y/%m/%d %H:%M:%S.%f")
            item = bucket(timestamp.timestamp() * 1000.)
            if item is not None:
                item["gpu_utilization"].append(float(row["gpu_utilization_percent"]))
    for row in records(client / "sglang_metrics.jsonl"):
        if "error" in row or row.get("monotonic_ts_ms") is None:
            continue
        item = bucket(row["monotonic_ts_ms"] + clock_offset)
        if item is not None:
            item["running"].append(row.get("num_running_reqs", 0))
            item["waiting"].append(row.get("num_queue_reqs", 0))
            item["full_active_ratio"].append(row.get("resident_pressure", 0))
    for row in records(arm / "server/host_pool_telemetry.jsonl"):
        if row.get("event") != "host_pool_usage":
            continue
        item = bucket(row["ts_ms"])
        if item is not None:
            for pool in ("full", "mamba"):
                item[f"host_{pool}_fraction"].append(row["pools"][pool]["used_fraction"])
    for row in records(arm / "server/eviction_attribution.jsonl"):
        if row.get("event") != "context_prefix_reuse_probe":
            continue
        item = bucket(row["ts_ms"])
        if item is not None:
            item["common_previously_served_tokens"].append(row["common_previously_served_input_tokens"])
            item["old_prefix_missing_tokens"].append(row["previously_served_prefix_recompute_proxy_tokens"])

    workflows = []
    post_tool_gaps = []
    for path in sorted((client / "workflows").glob("*/runtime_events.deepagents.jsonl")):
        start = end = None
        tool_starts = {}
        joins = {}
        round_state = {}
        for row in records(path):
            invocation = row.get("invocation_id")
            if invocation is not None:
                if row["kind"] == "llm_result":
                    round_state[invocation] = [row["ts_ms"], None]
                elif row["kind"] == "tool_end" and invocation in round_state:
                    if row["ts_ms"] >= round_state[invocation][0]:
                        round_state[invocation][1] = row["ts_ms"]
                elif row["kind"] == "llm_submit" and invocation in round_state:
                    _, tool_end = round_state.pop(invocation)
                    if tool_end is not None:
                        gap = max(0., row["ts_ms"] - tool_end)
                        post_tool_gaps.append(gap)
                        item = bucket(row["ts_ms"] + clock_offset)
                        if item is not None:
                            item["post_tool_to_submit_ms"].append(gap)
            if row["kind"] == "workflow_start":
                start = row["ts_ms"] + clock_offset - anchor_ms
            elif row["kind"] == "workflow_end":
                end = row["ts_ms"] + clock_offset - anchor_ms
            elif row["kind"] == "join_create":
                joins[row["join_id"]] = {
                    "start_ms": row["ts_ms"] + clock_offset - anchor_ms,
                    "end_ms": None,
                }
            elif row["kind"] in ("join_satisfied", "join_timeout") and row["join_id"] in joins:
                joins[row["join_id"]]["end_ms"] = row["ts_ms"] + clock_offset - anchor_ms
            elif row["kind"] == "tool_start":
                tool_starts[row.get("tool_call_id") or (row.get("attributes") or {}).get("tool_call_id")] = row["ts_ms"]
            elif row["kind"] == "tool_end":
                call_id = row.get("tool_call_id") or (row.get("attributes") or {}).get("tool_call_id")
                began = tool_starts.pop(call_id, None)
                item = bucket(row["ts_ms"] + clock_offset)
                if began is not None and item is not None:
                    item["tool_duration_ms"].append(max(0., row["ts_ms"] - began))
        workflows.append({
            "task": path.parent.name, "start_ms": start, "end_ms": end,
            "joins": list(joins.values()),
        })

    acknowledgements = {
        row["command_id"]: row
        for row in records(arm / "server/physical_action_ack.jsonl")
    }
    transfer_totals = defaultdict(Counter)
    transfer_times = defaultdict(list)
    native_h2d = []
    sizes = {
        "kv": status["host_pool_evidence"]["full"]["bytes_per_unit"],
        "mamba": status["host_pool_evidence"]["mamba"]["bytes_per_unit"],
    }
    for row in records(arm / "server/transfer_telemetry.jsonl"):
        for part in transfer_parts(row, acknowledgements, sizes):
            name = f'{part["kind"]}_{row["direction"]}'
            transfer_totals[name]["count"] += 1
            transfer_totals[name]["bytes"] += part["bytes"]
            for pool, size in part["pool_bytes"].items():
                transfer_totals[name][f"{pool}_bytes"] += size
            item = bucket(row["submit_ts_ms"])
            if item is not None:
                item[f"{name}_bytes"].append(part["bytes"])
                item[f"{name}_count"].append(1)
            if name == "native_h2d":
                native_h2d.append({
                    "start_ms": row["submit_ts_ms"] - anchor_ms,
                    "bytes": part["bytes"],
                    "end_ms": row["complete_ts_ms"] - anchor_ms,
                })
        # Time the physical batch once, even if it contains tagged and native pieces.
        transfer_times[f'{row["direction"]}_submit_to_ack_ms'].append(row["submit_to_ack_ms"])
        if row.get("transfer_stream_elapsed_ms") is not None:
            transfer_times[f'{row["direction"]}_stream_ms'].append(row["transfer_stream_elapsed_ms"])

    intervals = timeline["service_observations"]
    decode = [row for row in intervals if row["phase"] == "decode"]
    prefill = [row for row in intervals if row["phase"] == "prefill"]
    decode_count = sum(row["observation_count"] for row in decode)

    def waiting_on_join(milliseconds: float) -> int:
        return sum(any(
            join["start_ms"] <= milliseconds
            and (join["end_ms"] is None or join["end_ms"] > milliseconds)
            for join in row["joins"]
        ) for row in workflows if row["end_ms"] is None or row["end_ms"] > milliseconds)

    bands = []
    for index, item in enumerate(bins):
        begin = index * 1000 * bin_seconds
        end = min(duration_ms, begin + 1000 * bin_seconds)
        if end <= begin:
            continue
        counts = {
            key: sum(values) for key, values in item.items()
            if key.endswith(("_tokens", "_bytes", "_count"))
        }
        bands.append({
            "start_seconds": begin / 1000, "end_seconds": end / 1000,
            **counts,
            **{
                f"{key}_mean": mean(item[key]) if item[key] else None
                for key in (
                    "gpu_utilization", "running", "waiting", "full_active_ratio",
                    "host_full_fraction", "host_mamba_fraction",
                )
            },
            "workflow_active_at_end": sum(
                row["start_ms"] is not None and row["start_ms"] <= end
                and (row["end_ms"] is None or row["end_ms"] > end)
                for row in workflows
            ),
            "workflow_ended_by_end": sum(
                row["end_ms"] is not None and row["end_ms"] <= end for row in workflows
            ),
            "workflow_with_unfinished_join_at_end": waiting_on_join(end),
            "worker_interval_union_seconds": interval_milliseconds(intervals, begin, end) / 1000,
            "tool_duration_p50_ms": percentile(item["tool_duration_ms"], .5),
            "tool_duration_p95_ms": percentile(item["tool_duration_ms"], .95),
            "post_tool_to_submit_p50_ms": percentile(item["post_tool_to_submit_ms"], .5),
            "sampled_restore_wait_batches": len(item["gpu_restore_dependency_wait_ms"]),
            "sampled_restore_wait_p50_ms": percentile(item["gpu_restore_dependency_wait_ms"], .5),
            "sampled_restore_wait_p95_ms": percentile(item["gpu_restore_dependency_wait_ms"], .95),
            "old_prefix_missing_proxy_ratio": (
                sum(item["old_prefix_missing_tokens"]) / sum(item["common_previously_served_tokens"])
                if sum(item["common_previously_served_tokens"]) else None
            ),
        })
        for phase, rows in (("decode", decode), ("prefill", prefill)):
            overlaps = [
                (row["running"], max(0., min(end, row["end_ms"]) - max(begin, row["start_ms"])))
                for row in rows if row["start_ms"] < end and row["end_ms"] > begin
            ]
            milliseconds = sum(duration for _, duration in overlaps)
            bands[-1][f"{phase}_time_weighted_batch_size"] = (
                sum(count * duration for count, duration in overlaps) / milliseconds
                if milliseconds else None
            )
            bands[-1][f"{phase}_worker_interval_seconds"] = milliseconds / 1000
    h2d_bytes = sum(row["bytes"] for row in native_h2d)
    milestones = {}
    for seconds in (1800, 3000, 3600, 5400):
        milliseconds = seconds * 1000
        milestones[str(seconds)] = {
            "native_h2d_bytes_before": sum(row["bytes"] for row in native_h2d if row["start_ms"] < milliseconds),
            "native_h2d_fraction_before": (
                sum(row["bytes"] for row in native_h2d if row["start_ms"] < milliseconds) / h2d_bytes
                if h2d_bytes else None
            ),
            "native_h2d_count_before": sum(row["start_ms"] < milliseconds for row in native_h2d),
            "workflow_ended": sum(
                row["end_ms"] is not None and row["end_ms"] <= milliseconds for row in workflows
            ),
            "workflow_active": sum(
                row["start_ms"] is not None and row["start_ms"] <= milliseconds
                and (row["end_ms"] is None or row["end_ms"] > milliseconds)
                for row in workflows
            ),
            "workflow_with_unfinished_join": waiting_on_join(milliseconds),
        }
    jcts = [
        row["duration_seconds"] for row in summary["workflows"] if row["outcome"] == "completed"
    ]
    reuse = status["context_prefix_reuse_evidence"]["counts"]
    state = last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    cpu_profile = state.get("shared_path_timing") or {}
    return {
        "run": str(arm.resolve()), "timeline": str(timeline_path.resolve()),
        "time_origin": "first GPU-monitor sample; matches the HTML timeline",
        "time_origin_local": timeline["start_anchor"],
        "collection_duration_seconds": summary["duration_seconds"],
        "timeline_duration_seconds": duration_ms / 1000,
        "workflow_count": summary["workflow_count"],
        "outcomes": dict(Counter(row["outcome"] for row in summary["workflows"])),
        "completed_per_hour": summary["completed_workflows"] * 3600 / summary["duration_seconds"],
        "jct_mean_seconds": mean(jcts), "jct_p50_seconds": median(jcts),
        "jct_p95_seconds": percentile(jcts, .95),
        "all_terminal_jct_mean_seconds": mean(
            row["duration_seconds"] for row in summary["workflows"]
        ),
        "llm_requests": summary["llm_request_count"], "tool_calls": summary["tool_call_count"],
        "output_tokens": output_tokens, "input_tokens": input_tokens,
        "output_tokens_per_second": output_tokens / summary["duration_seconds"],
        "llm_result_duration_p50_ms": percentile(request_latencies, .5),
        "llm_result_duration_p95_ms": percentile(request_latencies, .95),
        "post_tool_to_next_submit_gap": {
            "count": len(post_tool_gaps),
            "p50_ms": percentile(post_tool_gaps, .5),
            "p95_ms": percentile(post_tool_gaps, .95),
            "mean_ms": mean(post_tool_gaps) if post_tool_gaps else None,
            "semantics": (
                "Observed same-invocation client gap after a tool round; includes "
                "control delivery, prompt construction and possible other waits, "
                "not isolated scheduler CPU time."
            ),
        },
        "gpu_utilization_mean": timeline["summary"]["gpu_utilization_mean"],
        "worker_interval_union_seconds": interval_milliseconds(intervals, 0, duration_ms) / 1000,
        "decode_batch_count": decode_count,
        "decode_mean_batch_size": (
            sum(row["running"] * row["observation_count"] for row in decode) / decode_count
            if decode_count else None
        ),
        "decode_worker_interval_mean_ms": (
            sum(row["end_ms"] - row["start_ms"] for row in decode) / decode_count
            if decode_count else None
        ),
        "phase_statistics": {
            "decode": phase_statistics(decode), "prefill": phase_statistics(prefill),
        },
        "multi_round_workflows": sum(
            (row.get("trace") or {}).get("delegation_round_count", 0) > 1
            for row in summary["workflows"]
        ),
        "round_counts": dict(Counter(
            (row.get("trace") or {}).get("delegation_round_count", 0)
            for row in summary["workflows"]
        )),
        "native_pool_capacity": timeline["summary"]["native_pool_capacity"],
        "cache_evidence": status["request_cache_evidence"]["all"],
        "host_pool_evidence": status["host_pool_evidence"],
        "full_eviction_recompute_tokens": status["host_block_eviction_attribution"]["recomputed_full_units"],
        "old_prefix_missing_proxy_ratio": (
            reuse["previously_served_prefix_recompute_proxy_tokens"]
            / reuse["previously_served_common_prefix_tokens"]
        ),
        "transfers": dict(transfer_totals),
        "physical_transfer_times": {
            key: {"sum": sum(values), "p50": percentile(values, .5), "p95": percentile(values, .95)}
            for key, values in transfer_times.items()
        },
        "gpu_restore_dependency_wait": {
            **restore_wait_statistics(restore_waits),
            "probe_status": status.get("gpu_restore_wait_probe"),
        },
        "shared_path_cpu_profile": {
            **cpu_profile,
            "instrumented_exclusive_sum_ms": sum(
                phase["self_ms"] for phase in cpu_profile.get("phases", {}).values()
            ) if cpu_profile else None,
            "semantics": "Instrumented Python wall intervals, not CPU cycles or total GPU idle time.",
        },
        "milestones": milestones, "bands": bands,
        "instance_ids": manifest["instance_ids"],
        "harness_config": manifest["config"],
        "runtime_state": state,
        "diagnostic_note": timeline["summary"]["diagnostic_note"],
        "measurement_notes": [
            "All policies used live generation, not identical realized request traces.",
            "Completion is a workflow terminal state, not independently graded task correctness.",
            "Worker intervals are not isolated CUDA-kernel durations.",
            "Submit-to-ACK and CUDA transfer-stream totals are not exposed critical-path H2D wait.",
            "FULL active-token ratio excludes inactive cached occupancy; native v9 has no device occupancy census.",
            "Mamba transfer bytes use model-specific receipt units, not a per-layer recompute measurement.",
            "Per-band output tokens are assigned to request completion, not individual decode steps.",
            "Coalesced same-phase/same-batch intervals yield exact time-weighted per-band batch sizes.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=RUN_PATH")
    parser.add_argument("--timeline", action="append", required=True, help="NAME=JSON_GZ_PATH")
    parser.add_argument("--bin-seconds", type=int, default=600)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.bin_seconds <= 0:
        parser.error("--bin-seconds must be positive")
    arms = dict(item.split("=", 1) for item in args.arm)
    timelines = dict(item.split("=", 1) for item in args.timeline)
    if set(arms) != set(timelines):
        parser.error("each arm requires its corresponding timeline")
    report = {
        "schema_version": 1, "scope": "retrospective live-run diagnosis, not causal speedup",
        "runs": {
            name: compare_run(Path(path), Path(timelines[name]), args.bin_seconds)
            for name, path in arms.items()
        },
    }
    report["same_task_set"] = len({
        tuple(sorted(run["instance_ids"])) for run in report["runs"].values()
    }) == 1
    report["same_physical_pool_capacity"] = len({
        json.dumps(run["native_pool_capacity"], sort_keys=True)
        for run in report["runs"].values()
    }) == 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved {args.output}")
    for name, run in report["runs"].items():
        print(name, json.dumps({
            key: run[key] for key in (
                "collection_duration_seconds", "completed_per_hour",
                "output_tokens_per_second", "gpu_utilization_mean", "milestones",
            )
        }))


if __name__ == "__main__":
    main()
