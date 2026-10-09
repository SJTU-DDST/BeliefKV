from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path

from beliefkv.metrics.execution_timeline import (
    ExecutionTimeline,
    _downsample,
    _finite_float,
    _gpu_busy_intervals,
    _interval_overlap_ms,
    _load_gpu_samples,
    _merge_event_bins,
    _merge_intervals,
    _read_jsonl,
    _summarize,
)


def load_native_execution_timeline(
    run_dir: Path,
    *,
    arm: str,
    gpu_busy_threshold: float,
    end_offset_ms: float | None,
) -> ExecutionTimeline:
    clients = sorted(run_dir.glob("client_*"))
    clients = [path for path in clients if path.is_dir()]
    if len(clients) != 1:
        raise ValueError(f"expected one client directory in {run_dir}")
    client = clients[0]
    server = run_dir / "server"
    capacity = json.loads((server / "native_capacity_census.json").read_text())["capacity"]
    services, decode, clock_offset = _services(server / "runtime_audit.jsonl")
    if not services:
        raise ValueError(f"no native GPU service intervals in {run_dir}")
    epoch = datetime.fromtimestamp(0)
    gpu = _load_gpu_samples(client / "gpu_samples.csv", wall_anchor=epoch)
    anchor = min(float(row["t_ms"]) for row in gpu) if gpu else services[0]["start_ms"]
    for row in gpu:
        row["t_ms"] = max(0.0, float(row["t_ms"]) - anchor)
    for row in services + decode:
        for key in ("t_ms", "start_ms", "end_ms"):
            if key in row:
                row[key] = max(0.0, float(row[key]) - anchor)

    resources, queues = _resources(
        run_dir, client, capacity, anchor=anchor, clock_offset=clock_offset,
    )
    waits, bins = _waits(client, anchor=anchor, clock_offset=clock_offset)
    transfers, prepare_count = _transfers(server, capacity, anchor=anchor)
    acknowledgements = {}
    for row in transfers:
        if row["predictive"]:
            slot = acknowledgements.setdefault(int(row["end_ms"] // 1000), {})
            slot["commit"] = slot.get("commit", 0) + 1
    bins = _merge_event_bins(
        {int(row["t_ms"] // 1000): {key: value for key, value in row.items() if key != "t_ms"}
         for row in bins},
        acknowledgements,
    )
    result = json.loads((client / "summary.json").read_text())
    outcomes = Counter(row["outcome"] for row in result["workflows"])
    if end_offset_ms is not None:
        if not math.isfinite(end_offset_ms) or end_offset_ms <= 0:
            raise ValueError("end_offset_ms must be finite and positive")
        gpu, services, resources, queues, waits, bins = [
            [row for row in rows if float(row["t_ms"]) <= end_offset_ms]
            for rows in (gpu, services, resources, queues, waits, bins)
        ]
        services, decode, transfers = [
            [
                {**row, "end_ms": min(row["end_ms"], end_offset_ms)}
                for row in rows if row["start_ms"] <= end_offset_ms
            ]
            for rows in (services, decode, transfers)
        ]
    duration = end_offset_ms or max(
        [row["t_ms"] for rows in (gpu, resources, queues, waits, bins) for row in rows]
        + [row["end_ms"] for rows in (services, transfers) for row in rows]
        + [0.0]
    )
    busy = _gpu_busy_intervals(gpu, threshold=gpu_busy_threshold)
    service_intervals = _merge_intervals(
        (row["start_ms"], row["end_ms"]) for row in services
    )
    busy_starts = [row[0] for row in busy]
    service_starts = [row[0] for row in service_intervals]
    for row in transfers:
        row["duration_ms"] = row["end_ms"] - row["start_ms"]
        row["gpu_busy_overlap_ms"] = _interval_overlap_ms(
            row["start_ms"], row["end_ms"], busy, busy_starts,
        )
        row["decode_overlap_ms"] = _interval_overlap_ms(
            row["start_ms"], row["end_ms"], service_intervals, service_starts,
        )
        row["potentially_hidden_fraction"] = (
            row["gpu_busy_overlap_ms"] / row["duration_ms"]
            if row["duration_ms"] > 0 else 0.0
        )
    summary = _summarize(
        duration_ms=duration, gpu_samples=gpu, services=services,
        decode_windows=decode, transfers=transfers, resources=resources,
        queue_samples=queues, external_waits=waits, event_bins=bins,
        gpu_busy_threshold=gpu_busy_threshold,
    )
    physical = [row["hbm_ratio"] for row in resources if row.get("physical_occupancy")]
    summary.update({
        "telemetry_format": "native_v0520",
        "service_timing_semantics": "scheduler_worker_intervals_not_cuda_kernel_time",
        "native_decode_interval_count": len(decode),
        "decode_inferred_window_count": 0,
        "overlap_semantics": "submit_to_ack_overlap_not_proven_dma_or_speedup",
        "peak_hbm_ratio": max(physical) if physical else None,
        "native_pool_capacity": capacity,
        "prepare_ack_count": prepare_count,
        "workflow_count": result["workflow_count"],
        "completed_workflows": outcomes["completed"],
        "noncompleted_workflows": sum(outcomes.values()) - outcomes["completed"],
        "workflow_outcomes": dict(outcomes),
        "workflow_duration_seconds": result["duration_seconds"],
        "diagnostic_note": (
            "Development diagnostic only: v8c reactive disabled the BeliefKV physical "
            "lane early after a D2H receipt error. Do not use it as a healthy paired "
            "baseline or infer predictive speedup from these timelines."
            if "v8c" in str(run_dir) else
            "Development diagnostic: first-service records and transfer ACKs do not "
            "prove KV reuse or end-to-end speedup."
            if "v8d" in str(run_dir) else
            "Native FCFS/HiCache policy baseline with shared session/NUMA compatibility "
            "patches and read-only telemetry; not an unpatched upstream wheel."
        ),
    })
    return ExecutionTimeline(
        arm=arm, run_dir=str(run_dir),
        start_anchor=datetime.fromtimestamp(anchor / 1000).isoformat(),
        duration_ms=duration, gpu_samples=tuple(_downsample(gpu, 5000)),
        service_observations=tuple(_coalesce_services(services)),
        queue_samples=tuple(_downsample(queues, 5000)),
        decode_windows=tuple(_coalesce_services(decode)),
        transfers=tuple(transfers), resources=tuple(resources),
        external_waits=tuple(_downsample(waits, 5000)), event_bins=tuple(bins),
        summary=summary,
    )


def _coalesce_services(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        if (
            result and result[-1]["phase"] == row["phase"]
            and result[-1]["running"] == row["running"]
            and abs(result[-1]["end_ms"] - row["start_ms"]) <= .001
        ):
            result[-1]["end_ms"] = row["end_ms"]
            result[-1]["observation_count"] += 1
        else:
            result.append({
                "t_ms": row["t_ms"], "start_ms": row["start_ms"], "end_ms": row["end_ms"],
                "phase": row["phase"], "running": row["running"], "new_tokens": 0,
                "observation_count": 1,
            })
    return result


def _services(path: Path) -> tuple[list[dict], list[dict], float]:
    services, decode = [], []
    clock_offset = None
    for row in _read_jsonl(path):
        if row.get("event") != "gpu_service_sample":
            continue
        start = _finite_float(row.get("service_start_ts_ms"))
        end = _finite_float(row.get("complete_ts_ms"))
        mono = _finite_float(row.get("complete_monotonic_ms"))
        if start is None or end is None or end < start:
            continue
        if clock_offset is None and mono is not None:
            clock_offset = end - mono
        count = int(row.get("batch_size") or 0)
        item = {
            "t_ms": start, "start_ms": start, "end_ms": end,
            "phase": row["phase"], "running": count, "waiting": 0,
            "new_sequences": 0, "new_tokens": 0, "cached_tokens": 0,
            "throughput": 0.0, "cuda_graph": None,
            "semantics": "recorded_scheduler_worker_interval",
        }
        services.append(item)
        if row["phase"] == "decode":
            decode.append(dict(item))
    if clock_offset is None:
        raise ValueError(f"native telemetry has no wall/monotonic clock pair: {path}")
    services.sort(key=lambda row: row["start_ms"])
    decode.sort(key=lambda row: row["start_ms"])
    return services, decode, clock_offset


def _resources(
    run: Path, client: Path, capacity: dict, *, anchor: float, clock_offset: float,
) -> tuple[list[dict], list[dict]]:
    resources, queues = [], []
    metrics = client / "sglang_metrics.jsonl"
    if metrics.exists():
        for row in _read_jsonl(metrics):
            if row.get("monotonic_ts_ms") is None or "error" in row:
                continue
            t = max(0.0, row["monotonic_ts_ms"] + clock_offset - anchor)
            queues.append({
                "t_ms": t, "running": row.get("num_running_reqs", 0),
                "waiting": row.get("num_queue_reqs", 0),
            })
            resources.append({
                "t_ms": t, "hbm_ratio": 0.0,
                "full_active_ratio": row.get("num_used_tokens", 0) / capacity["device_full_tokens"],
            })
    host = run / "server/host_pool_telemetry.jsonl"
    if host.exists():
        for row in _read_jsonl(host):
            if row.get("event") == "host_pool_usage":
                resources.append({
                    "t_ms": max(0.0, row["ts_ms"] - anchor), "hbm_ratio": 0.0,
                    "host_full_ratio": row["pools"]["full"]["used_fraction"],
                    "host_mamba_ratio": row["pools"]["mamba"]["used_fraction"],
                })
    opportunities = run / "opportunities/admission_opportunities.jsonl"
    if opportunities.exists():
        per_second = {}
        for row in _read_jsonl(opportunities):
            if (
                not row.get("headroom_observable")
                or row.get("device_full_free_tokens") is None
                or row.get("device_mamba_free_slots") is None
            ):
                continue
            t = max(0.0, row["ts_ms"] - anchor)
            full = 1.0 - row["device_full_free_tokens"] / capacity["device_full_tokens"]
            mamba = 1.0 - row["device_mamba_free_slots"] / capacity["device_mamba_slots"]
            per_second[int(t // 1000)] = {
                "t_ms": t, "full_occupancy_ratio": full,
                "mamba_occupancy_ratio": mamba, "physical_occupancy": True,
                "hbm_ratio": (
                    full * capacity["device_full_bytes"] + mamba * capacity["device_mamba_bytes"]
                ) / capacity["device_total_bytes"],
            }
        resources.extend(per_second.values())
    resources.sort(key=lambda row: row["t_ms"])
    queues.sort(key=lambda row: row["t_ms"])
    # Retain each signal independently when reducing the visualization payload.
    reduced = {}
    for row in resources:
        bucket = reduced.setdefault(int(row["t_ms"] // 1000), {"t_ms": row["t_ms"], "hbm_ratio": 0.0})
        bucket.update({key: value for key, value in row.items() if key != "hbm_ratio"})
        if row.get("physical_occupancy"):
            bucket["hbm_ratio"] = row["hbm_ratio"]
    return sorted(reduced.values(), key=lambda row: row["t_ms"]), queues


def _waits(client: Path, *, anchor: float, clock_offset: float) -> tuple[list[dict], list[dict]]:
    events = []
    for path in sorted((client / "workflows").glob("*/runtime_events.deepagents.jsonl")):
        for row in _read_jsonl(path):
            if row.get("kind") in (
                "tool_start", "tool_end", "join_wait", "join_satisfied", "join_timeout",
            ):
                events.append((row["ts_ms"] + clock_offset - anchor, row["kind"]))
    tools = joins = 0
    waits, bins = [], {}
    for t, kind in sorted(events):
        if kind == "tool_start":
            tools += 1
        elif kind == "tool_end":
            tools = max(0, tools - 1)
        elif kind == "join_wait":
            joins += 1
        else:
            joins = max(0, joins - 1)
        waits.append({"t_ms": max(0.0, t), "active_tools": tools, "active_joins": joins})
        slot = bins.setdefault(max(0, int(t // 1000)), {})
        slot[kind] = slot.get(kind, 0) + 1
    return waits, _merge_event_bins(bins)


def _transfers(server: Path, capacity: dict, *, anchor: float) -> tuple[list[dict], int]:
    ack_path = server / "physical_action_ack.jsonl"
    acks = {
        row["command_id"]: row for row in _read_jsonl(ack_path)
        if row.get("event") == "beliefkv_physical_action_ack"
    } if ack_path.exists() else {}
    sizes = {
        "kv": capacity["device_full_bytes"] // capacity["device_full_tokens"],
        "mamba": capacity["device_mamba_bytes"] // capacity["device_mamba_slots"],
    }
    transfers = []
    for row in _read_jsonl(server / "transfer_telemetry.jsonl"):
        if row.get("event") != "transfer_telemetry":
            continue
        start = _finite_float(row.get("submit_ts_ms"))
        end = _finite_float(row.get("complete_ts_ms"))
        total = int(row.get("actual_bytes") or 0)
        if start is None or end is None or end < start or total <= 0:
            continue
        tagged = row.get("tagged_child_commits") or []
        tagged_bytes = sum(int(item["num_bytes"]) for item in tagged)
        if tagged_bytes > total:
            raise ValueError(f"tagged bytes exceed controller payload: {row['command_id']}")
        pieces = [(item["command_id"], item["num_bytes"], item.get("num_tokens_by_pool") or {})
                  for item in tagged]
        if total > tagged_bytes:
            native_units = dict(row.get("num_tokens_by_pool") or {})
            for item in tagged:
                for name, count in (item.get("num_tokens_by_pool") or {}).items():
                    native_units[name] = native_units.get(name, 0) - count
            pieces.append((row["command_id"], total - tagged_bytes, native_units))
        for command, amount, units in pieces:
            ack = acks.get(command, {})
            action = ack.get("action")
            predictive = action in ("PREPARE_HOST", "PREFETCH_GPU")
            transfers.append({
                "start_ms": max(0.0, start - anchor), "end_ms": max(0.0, end - anchor),
                "direction": row["direction"], "bytes": int(amount),
                "extent_count": len(row.get("node_ids") or []), "kind": action or row["command_kind"],
                "command_id": command, "predictive": predictive,
                "source": "predictive" if predictive else "native",
                "action_source": ack.get("source"),
                "pre_boundary_prediction": (
                    False if ack.get("source") == "execution_handoff" else None
                ),
                "status": row["status"], "predictive_intent_id": command if predictive else None,
                "context_id": ack.get("context_id", ""), "context_epoch": ack.get("context_epoch", 0),
                "telemetry_origin": row.get("telemetry_origin", ""),
                "pool_bytes": {name: int(count) * sizes[name] for name, count in units.items()},
                "transfer_stream_elapsed_ms": row.get("transfer_stream_elapsed_ms"),
                "timing_semantics": "controller_submit_to_ack_not_dma_duration",
            })
    transfers.sort(key=lambda row: (row["start_ms"], row["command_id"]))
    return transfers, sum(row.get("action") == "PREPARE_HOST" for row in acks.values())
