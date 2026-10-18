#!/usr/bin/env python3
"""Compare completed live-policy runs without claiming trajectory-matched speedup."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import gzip
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
from statistics import mean, median
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.metrics.execution_timeline import _merge_intervals
from beliefkv.metrics.native_execution_timeline import resolve_native_gpu_clock
from scripts.audit_native_h2d_sources import (
    CONTROLLED_CATEGORIES, acknowledged_prefetch_sources, split_h2d_payload,
)


def records(path: Path):
    if path.exists():
        with path.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    break
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


def jct_eligibility_counts(workflows: list[dict]) -> dict:
    return {
        field: {
            state: sum(
                ("true" if row.get(field) is True else
                 "false" if row.get(field) is False else "unknown") == state
                for row in workflows
            )
            for state in ("true", "false", "unknown")
        }
        for field in (
            "measurement_valid", "system_jct_eligible",
            "native_agent_jct_eligible", "task_correctness_valid",
        )
    }


def _paired_jct_statistics(rows: list[dict]) -> dict:
    deltas = [row["delta_seconds"] for row in rows]
    durations = {}
    for name in ("left", "right"):
        values = [row[f"{name}_seconds"] for row in rows]
        durations[name] = {
            "mean": mean(values) if values else None,
            "p50": percentile(values, .5),
            "p95": percentile(values, .95),
        }
    return {
        "paired_completed_workflows": len(rows),
        "duration_seconds": durations,
        "mean_delta_seconds": mean(deltas) if deltas else None,
        "p50_delta_seconds": percentile(deltas, .5),
        "p95_delta_seconds": percentile(deltas, .95),
        "left_shorter_workflows": sum(delta < 0 for delta in deltas),
        "right_shorter_workflows": sum(delta > 0 for delta in deltas),
        "tied_workflows": sum(delta == 0 for delta in deltas),
    }


def paired_workflow_jct(left_summary: dict, right_summary: dict) -> dict:
    indexed = []
    for summary in (left_summary, right_summary):
        workflows = {}
        for row in summary["workflows"]:
            instance = row.get("instance_id")
            if not isinstance(instance, str) or not instance or instance in workflows:
                raise ValueError("missing or duplicate workflow instance")
            duration = row.get("duration_seconds")
            if row["outcome"] == "completed" and (
                type(duration) not in (int, float)
                or not math.isfinite(duration) or duration < 0
            ):
                raise ValueError("invalid completed workflow JCT")
            workflows[instance] = row
        indexed.append(workflows)
    left, right = indexed
    shared = left.keys() & right.keys()
    completed = sorted(
        instance for instance in shared
        if left[instance]["outcome"] == right[instance]["outcome"] == "completed"
    )
    rows = []
    for instance in completed:
        before, after = left[instance], right[instance]
        rows.append({
            "instance_id": instance,
            "left_seconds": before["duration_seconds"],
            "right_seconds": after["duration_seconds"],
            "delta_seconds": before["duration_seconds"] - after["duration_seconds"],
            "left_jct_eligible": before.get("native_agent_jct_eligible"),
            "right_jct_eligible": after.get("native_agent_jct_eligible"),
            **{
                f"{name}_{field}": source.get(field)
                for name, source in (("left", before), ("right", after))
                for field in (
                    "system_jct_eligible", "system_jct_exclusion_reasons",
                    "native_agent_jct_exclusion_reasons",
                )
            },
            **{
                f"{name}_{metric}": (source.get("trace") or {}).get(metric)
                for name, source in (("left", before), ("right", after))
                for metric in ("llm_request_count", "tool_call_count", "delegation_round_count")
            },
        })
    cohorts = {}
    for name, field in (
        ("system_jct_common_eligible", "system_jct_eligible"),
        ("native_agent_jct_common_eligible", "jct_eligible"),
    ):
        selected = [
            row for row in rows
            if row[f"left_{field}"] is True and row[f"right_{field}"] is True
        ]
        cohorts[name] = {
            **_paired_jct_statistics(selected),
            "instances": [row["instance_id"] for row in selected],
            "excluded_paired_completed_workflows": len(rows) - len(selected),
        }
    return {
        **_paired_jct_statistics(rows),
        "shared_instances": len(shared),
        "shared_outcome_pairs": dict(sorted(Counter(
            f'{left[instance]["outcome"]}/{right[instance]["outcome"]}'
            for instance in shared
        ).items())),
        "paired_completed_with_explicit_jct_eligibility": sum(
            row["left_jct_eligible"] is True and row["right_jct_eligible"] is True
            for row in rows
        ),
        "unpaired_completed_instances": {
            name: sorted(
                instance for instance, row in source.items()
                if row["outcome"] == "completed" and instance not in completed
            )
            for name, source in (("left", left), ("right", right))
        },
        "eligibility_counts": {
            name: jct_eligibility_counts(summary["workflows"])
            for name, summary in (("left", left_summary), ("right", right_summary))
        },
        "eligibility_cohorts": cohorts,
        "eligibility_cohort_semantics": (
            "Additional descriptive cohorts require explicit True eligibility in "
            "both arms and completion in both arms. Missing or non-boolean flags "
            "remain unknown; measurement validity and task correctness are not "
            "inferred from completion. The outcome-based headline remains unchanged. "
            "Intervention-dependent filtering may select different realized work "
            "and is not an unbiased causal KV-policy effect or formal gate pass."
        ),
        "rows": rows,
        "semantics": (
            "The same instances completed in both arms; negative deltas favor left. "
            "Unpaired completions and shared outcome differences remain explicit. "
            "Eligibility flags are reported without silently changing this outcome-based "
            "cohort. Request, tool and delegation counts describe realized demand; "
            "matching tasks does not match execution traces or establish causal speedup."
        ),
    }


def native_reference_metadata(current_run: Path, reference_run: Path) -> dict:
    reference_path = reference_run / "ab_plan.json"
    reference_bytes = reference_path.read_bytes()
    reference = json.loads(reference_bytes)
    current = json.loads((current_run / "ab_plan.json").read_bytes())
    fields = (
        "workload_sha256", "root_count", "arrival_schedule", "server_running",
        "context_tokens", "max_completion_tokens", "host_gb", "host_split",
        "mamba_full_memory_ratio", "mem_fraction_static", "native_write_policy",
        "fanout_profile", "sampling_seed", "temperature", "sandbox_command_timeout_s",
        "activation_wall_clock_seconds", "child_final_report_shadow",
        "host_numa_node", "host_numa_memory_policy",
    )
    recorded = [field for field in fields if field in current and field in reference]
    return {
        "kind": "historical_native_reference",
        "selection": (
            "fixed reference recorded in collection plan"
            if current.get("native_reference_run") == str(reference_run.resolve())
            else "reference supplied to retrospective analysis"
        ),
        "reference_run": str(reference_run.resolve()),
        "reference_code_commit": reference["code_commit"],
        "current_code_commit": current["code_commit"],
        "reference_plan_sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "same_engine_patch": current["sglang_patch_sha256"] == reference["sglang_patch_sha256"],
        "matched_configuration_fields": [
            field for field in recorded if current[field] == reference[field]
        ],
        "configuration_differences": {
            field: {"current": current[field], "reference": reference[field]}
            for field in recorded if current[field] != reference[field]
        },
        "unrecorded_configuration_fields": [field for field in fields if field not in recorded],
        "formal_paired_evidence": False,
        "semantics": (
            "Development comparison against a preselected historical native run; "
            "task pairing does not match live trajectories. Patch equality alone "
            "does not prove every native/client path unchanged."
        ),
    }


def historical_reference_current_run(arms: dict[str, str], reference_run: Path) -> Path:
    native = arms.get("native")
    current_roots = {
        Path(path).resolve().parent for name, path in arms.items() if name != "native"
    }
    if (
        native is None or Path(native).resolve() != reference_run.resolve() / "native"
        or len(current_roots) != 1
    ):
        raise ValueError(
            "historical reference requires its native arm and current arms from one run directory"
        )
    return current_roots.pop()


def aligned_native_clock(evidence: dict, *, anchor_text: str | None = None) -> tuple[float, ZoneInfo]:
    if not isinstance(evidence, dict):
        raise ValueError("regenerate native metrics with explicit, validated clock alignment")
    anchor = evidence.get("anchor_unix_ms")
    if (
        evidence.get("schema_version") != 2 or type(anchor) not in (int, float)
        or not math.isfinite(anchor) or not isinstance(evidence.get("gpu_timezone"), str)
    ):
        raise ValueError("regenerate native metrics with explicit, validated clock alignment")
    try:
        zone = ZoneInfo(evidence["gpu_timezone"])
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("regenerate native metrics with a valid capture timezone") from error
    if anchor_text is not None:
        parsed = datetime.fromisoformat(anchor_text)
        if parsed.tzinfo is None or not math.isclose(
            parsed.timestamp() * 1000., anchor, rel_tol=0., abs_tol=.001,
        ):
            raise ValueError("timeline start_anchor disagrees with its absolute clock evidence")
    return anchor, zone


def cached_native_reference(
    reference_run: Path, bin_seconds: int, *, cache_path: Path | None = None,
) -> dict | None:
    path = cache_path if cache_path is not None else reference_run / "native_policy_comparison.json"
    if not path.is_file():
        return None
    cached = json.loads(path.read_bytes()).get("runs", {}).get("native")
    if cached is None or cached.get("run") != str((reference_run / "native").resolve()):
        return None
    clock = cached.get("clock_alignment") or {}
    try:
        aligned_native_clock(clock, anchor_text=cached.get("time_origin_local"))
    except ValueError:
        return None
    if any(
        phase.get("batch_count", 0) > 0 and phase.get("worker_interval_seconds", 0) <= 0
        for phase in cached.get("phase_statistics", {}).values()
    ):
        return None
    bands = cached.get("bands", ())
    if len(bands) < 2 or bands[1]["start_seconds"] - bands[0]["start_seconds"] != bin_seconds:
        return None
    return cached


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


def h2d_source_parts(row: dict, sources: dict, sizes: dict[str, int]) -> list[dict]:
    payload, units = split_h2d_payload(row, sources)
    return [
        {
            "category": category, "bytes": amount,
            "pool_units": dict(units[category]),
            "pool_bytes": {
                pool: count * sizes[pool] for pool, count in units[category].items()
                if pool in sizes
            },
        }
        for category, amount in payload.items() if amount
    ]


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


def busy_window_statistics(
    arm: Path, windows: list[tuple[float, float]], *, gpu_timezone: str | None = None,
) -> dict:
    """Read completed service batches without requiring a terminal client summary."""
    clients = list(arm.glob("client_*/gpu_samples.csv"))
    if len(clients) != 1:
        raise ValueError(f"expected one GPU monitor: {arm}")
    with clients[0].open() as stream:
        gpu_rows = list(csv.DictReader(stream))
    first_service = next(
        row for row in records(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_service_sample"
    )
    zone, anchor_ms, clock_evidence = resolve_native_gpu_clock(
        clients[0], clients[0].parent / "sglang_metrics.jsonl",
        clock_offset_ms=first_service["complete_ts_ms"] - first_service["complete_monotonic_ms"],
        timezone_name=gpu_timezone,
    )
    horizon_ms = anchor_ms + max(end for _, end in windows) * 1000.
    bands = [{
        "start_seconds": begin, "end_seconds": end,
        "gpu_utilization": [], "running": [], "waiting": [],
        "request_completed_output_tokens": 0, "decode_output_tokens": 0,
        "submitted_requests": 0, "submitted_prompt_tokens": 0,
        "submitted_uncached_prompt_tokens": 0, "submitted_host_hit_tokens": 0,
        "service_rows": [], "restore_wait_rows": [],
        "transfer_batches": defaultdict(Counter), "h2d_sources": defaultdict(Counter),
        "d2h_actions": defaultdict(Counter),
    } for begin, end in windows]
    for row in gpu_rows:
        ts_ms = datetime.strptime(
            row["timestamp"].strip(), "%Y/%m/%d %H:%M:%S.%f",
        ).replace(tzinfo=zone).timestamp() * 1000.
        for band in bands:
            if band["start_seconds"] <= (ts_ms - anchor_ms) / 1000. < band["end_seconds"]:
                band["gpu_utilization"].append(float(row["gpu_utilization_percent"]))
    clock_offset = None
    last_complete_ms = None
    service_path = arm / "server/runtime_audit.jsonl"
    with service_path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            if b"gpu_service_sample" not in line and b"gpu_restore_dependency_wait" not in line:
                continue
            row = json.loads(line)
            if row.get("event") == "gpu_restore_dependency_wait":
                for band in bands:
                    if band["start_seconds"] <= (row["ts_ms"] - anchor_ms) / 1000. < band["end_seconds"]:
                        band["restore_wait_rows"].append(row)
                continue
            if row.get("event") != "gpu_service_sample":
                continue
            complete = row["complete_ts_ms"]
            if last_complete_ms is not None and complete < last_complete_ms:
                raise ValueError(f"service observations are not ordered: {service_path}")
            last_complete_ms = complete
            if clock_offset is None:
                clock_offset = complete - row["complete_monotonic_ms"]
            for band in bands:
                begin_ms = anchor_ms + band["start_seconds"] * 1000.
                end_ms = anchor_ms + band["end_seconds"] * 1000.
                if row["service_start_ts_ms"] < end_ms and complete > begin_ms:
                    band["service_rows"].append({
                        "start_ms": max(begin_ms, row["service_start_ts_ms"]),
                        "end_ms": min(end_ms, complete), "phase": row["phase"],
                        "running": row["batch_size"], "observation_count": 1,
                    })
                if row["phase"] == "decode" and begin_ms <= complete < end_ms:
                    band["decode_output_tokens"] += sum(
                        sample["token_delta"] for sample in row["request_samples"]
                    )
            if complete >= horizon_ms:
                break
    for row in records(arm / "server/runtime_events.sglang.jsonl"):
        for band in bands:
            if band["start_seconds"] <= (row["ts_ms"] - anchor_ms) / 1000. < band["end_seconds"]:
                attrs = row.get("attributes") or {}
                if row["kind"] == "llm_result":
                    band["request_completed_output_tokens"] += attrs.get("output_tokens") or 0
                elif row["kind"] == "llm_submit":
                    band["submitted_requests"] += 1
                    for field, source in (
                        ("submitted_prompt_tokens", "prompt_tokens"),
                        ("submitted_uncached_prompt_tokens", "uncached_prompt_tokens"),
                        ("submitted_host_hit_tokens", "cached_tokens_host"),
                    ):
                        band[field] += attrs.get(source) or 0
    if clock_offset is not None:
        for row in records(clients[0].parent / "sglang_metrics.jsonl"):
            mono = row.get("monotonic_ts_ms")
            if "error" in row or mono is None:
                continue
            for band in bands:
                if band["start_seconds"] <= (mono + clock_offset - anchor_ms) / 1000. < band["end_seconds"]:
                    band["running"].append(row.get("num_running_reqs", 0))
                    band["waiting"].append(row.get("num_queue_reqs", 0))
    acknowledgements = {
        row["command_id"]: row
        for row in records(arm / "server/physical_action_ack.jsonl")
    }
    sources = acknowledged_prefetch_sources(arm, acknowledgements.values())
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    pools = status.get("host_pool_evidence") or {}
    sizes = {
        pool: evidence["bytes_per_unit"]
        for pool, name in (("kv", "full"), ("mamba", "mamba"))
        if (evidence := pools.get(name) or {}).get("bytes_per_unit") is not None
    }
    transfer_path = arm / "server/transfer_telemetry.jsonl"
    for row in records(transfer_path):
        selected = [
            band for band in bands
            if band["start_seconds"] <= (row["submit_ts_ms"] - anchor_ms) / 1000. < band["end_seconds"]
        ]
        if not selected:
            continue
        direction = row["direction"]
        parts = (
            h2d_source_parts(row, sources, sizes) if direction == "h2d"
            else transfer_parts(row, acknowledgements, sizes)
        )
        for band in selected:
            batch = band["transfer_batches"][direction]
            batch["count"] += 1
            batch["bytes"] += row["actual_bytes"]
            batch["submit_to_ack_ms"] += row["submit_to_ack_ms"]
            if row.get("transfer_stream_elapsed_ms") is not None:
                batch["stream_measured_count"] += 1
                batch["transfer_stream_ms"] += row["transfer_stream_elapsed_ms"]
            for pool, units in row["num_tokens_by_pool"].items():
                batch[f"{pool}_units"] += units
                if pool in sizes:
                    batch[f"{pool}_bytes"] += units * sizes[pool]
            source_counts = band["h2d_sources" if direction == "h2d" else "d2h_actions"]
            for part in parts:
                source = part["category" if direction == "h2d" else "kind"]
                counts = source_counts[source]
                counts["batch_parts"] += 1
                counts["bytes"] += part["bytes"]
                for pool, amount in part["pool_bytes"].items():
                    counts[f"{pool}_bytes"] += amount
    result = []
    for band in bands:
        duration = band["end_seconds"] - band["start_seconds"]
        service_rows = band.pop("service_rows")
        restore_waits = band.pop("restore_wait_rows")
        transfers = {
            name: {source: dict(counts) for source, counts in band.pop(name).items()}
            for name in ("transfer_batches", "h2d_sources", "d2h_actions")
        }
        item = {
            **{key: value for key, value in band.items()
               if key not in ("gpu_utilization", "running", "waiting")},
            **transfers,
            "transfer_telemetry_present": transfer_path.exists(),
            "pool_bytes_per_unit": sizes,
            "gpu_restore_dependency_wait": restore_wait_statistics(restore_waits),
            **{f"{key}_mean": mean(band[key]) if band[key] else None
               for key in ("gpu_utilization", "running", "waiting")},
            "service_window_observed_complete": (
                last_complete_ms is not None
                and last_complete_ms >= anchor_ms + band["end_seconds"] * 1000.
            ),
            "decode_output_tokens_per_second": band["decode_output_tokens"] / duration,
            "request_completed_output_tokens_per_second": (
                band["request_completed_output_tokens"] / duration
            ),
            "worker_interval_union_seconds": interval_milliseconds(
                service_rows, anchor_ms + band["start_seconds"] * 1000.,
                anchor_ms + band["end_seconds"] * 1000.,
            ) / 1000.,
        }
        for phase in ("decode", "prefill"):
            selected = [row for row in service_rows if row["phase"] == phase]
            elapsed = sum(row["end_ms"] - row["start_ms"] for row in selected)
            item[f"{phase}_batch_count"] = len(selected)
            item[f"{phase}_worker_interval_seconds"] = elapsed / 1000.
            item[f"{phase}_mean_clipped_worker_interval_ms"] = (
                elapsed / len(selected) if selected else None
            )
            item[f"{phase}_time_weighted_batch_size"] = (
                sum(row["running"] * (row["end_ms"] - row["start_ms"]) for row in selected)
                / elapsed if elapsed else None
            )
        result.append(item)
    return {
        "run": str(arm.resolve()), "time_origin": "first GPU-monitor sample",
        "time_origin_local": datetime.fromtimestamp(anchor_ms / 1000., zone).isoformat(),
        "clock_alignment": clock_evidence,
        "windows": result,
        "measurement_notes": [
            "Live generation can produce different request traces in each policy.",
            "No incomplete-run JCT or completed-only workflow comparison is included.",
            "Decode token deltas belong to their completed worker batch's window.",
            "LLM output lengths separately belong to the request completion window.",
            "Worker intervals are scheduler/worker wall intervals, not CUDA kernel durations.",
            "Input/cache token counts belong to submission, not the actual GPU-service window.",
            "Transfer payloads and measured batch durations belong to the physical submission window.",
            "Only completed transfer receipts are included; live pending batches are not credited.",
            "Physical batch durations are counted once, even when payload sources share a batch.",
            "JOIN/tool source bytes require separate event-boundary and first-reuse checks to prove anticipation.",
            "Restore dependency waits are sampled CUDA layer waits, not per-request queue wait or all-batch totals.",
            "GPU utilization is the mean of monitor samples within each window.",
            "A trailing unfinished JSONL record is excluded from this read.",
        ],
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
    clock_evidence = timeline.get("summary", {}).get("clock_alignment") or {}
    anchor_ms, zone = aligned_native_clock(clock_evidence, anchor_text=timeline["start_anchor"])
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
            item = bucket(timestamp.replace(tzinfo=zone).timestamp() * 1000.)
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
    prefetch_sources = acknowledged_prefetch_sources(arm, acknowledgements.values())
    transfer_totals = defaultdict(Counter)
    h2d_source_totals = {
        category: Counter(controller_batches=0, bytes=0)
        for category in (*CONTROLLED_CATEGORIES, "native")
    }
    transfer_times = defaultdict(list)
    native_h2d = []
    sizes = {
        "kv": status["host_pool_evidence"]["full"]["bytes_per_unit"],
        "mamba": status["host_pool_evidence"]["mamba"]["bytes_per_unit"],
    }
    for row in records(arm / "server/transfer_telemetry.jsonl"):
        if row["direction"] == "h2d":
            for part in h2d_source_parts(row, prefetch_sources, sizes):
                category = part["category"]
                counts = h2d_source_totals[category]
                counts["controller_batches"] += 1
                counts["bytes"] += part["bytes"]
                for pool, units in part["pool_units"].items():
                    counts[f"{pool}_units"] += units
                for pool, size in part["pool_bytes"].items():
                    counts[f"{pool}_bytes"] += size
                item = bucket(row["submit_ts_ms"])
                if item is not None:
                    item[f"h2d_source_{category}_bytes"].append(part["bytes"])
                    item[f"h2d_source_{category}_count"].append(1)
                if category == "native":
                    native_h2d.append({
                        "start_ms": row["submit_ts_ms"] - anchor_ms,
                        "bytes": part["bytes"],
                        "end_ms": row["complete_ts_ms"] - anchor_ms,
                    })
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
        "clock_alignment": clock_evidence,
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
        "h2d_sources": {
            "schema_version": 2,
            "categories": {
                category: dict(counts) for category, counts in h2d_source_totals.items()
            },
            "semantics": (
                "predictive is JOIN/tool anticipation; execution_handoff is submitted "
                "demand before first service; unknown_controlled is tagged payload "
                "without a known source; native is the untagged receipt remainder. "
                "Actual lead and reuse require separate evidence. Bytes and pool "
                "units partition each batch; mixed-batch category counts can overlap."
            ),
        },
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
            "available": bool(cpu_profile and cpu_profile.get("enabled", True)),
            "instrumented_exclusive_sum_ms": sum(
                phase["self_ms"] for phase in cpu_profile.get("phases", {}).values()
            ) if cpu_profile and cpu_profile.get("enabled", True) else None,
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
            "Legacy transfers groups physical action names; PREFETCH_GPU includes demand handoff. Use h2d_sources for predictive coverage.",
            "Per-band output tokens are assigned to request completion, not individual decode steps.",
            "Coalesced same-phase/same-batch intervals yield exact time-weighted per-band batch sizes.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=RUN_PATH")
    parser.add_argument("--timeline", action="append", default=[], help="NAME=JSON_GZ_PATH")
    parser.add_argument("--busy-window", action="append", help="START_SECONDS:END_SECONDS; supports live arms")
    parser.add_argument(
        "--gpu-timezone", action="append", default=[],
        help="NAME=IANA_ZONE for --busy-window; completed comparisons use each timeline's clock evidence.",
    )
    parser.add_argument("--bin-seconds", type=int, default=600)
    parser.add_argument("--native-reference-run", type=Path)
    parser.add_argument(
        "--native-reference-cache", type=Path,
        help="Corrected reference comparison JSON; keeps legacy artifacts untouched.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.bin_seconds <= 0:
        parser.error("--bin-seconds must be positive")
    arms = dict(item.split("=", 1) for item in args.arm)
    try:
        gpu_timezones = dict(item.split("=", 1) for item in args.gpu_timezone)
        for name, zone_name in gpu_timezones.items():
            if name not in arms:
                raise ValueError(f"unknown arm in --gpu-timezone: {name}")
            ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        parser.error(f"invalid --gpu-timezone NAME=IANA_ZONE: {error}")
    if args.native_reference_cache is not None and args.native_reference_run is None:
        parser.error("--native-reference-cache requires --native-reference-run")
    if args.busy_window:
        if args.native_reference_cache is not None:
            parser.error("--native-reference-cache applies to completed comparisons")
        windows = [tuple(float(value) for value in window.split(":")) for window in args.busy_window]
        if any(len(window) != 2 or not 0 <= window[0] < window[1] for window in windows):
            parser.error("busy windows must have nonnegative start and greater end")
        report = {
            "schema_version": 1,
            "scope": "observed live busy windows, not a completed pair or causal speedup",
            "runs": {
                name: busy_window_statistics(
                    Path(path), windows, gpu_timezone=gpu_timezones.get(name),
                )
                for name, path in arms.items()
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(f"Saved {args.output}")
        for name, run in report["runs"].items():
            print(name, json.dumps(run["windows"]))
        return
    if gpu_timezones:
        parser.error("set capture timezone while rendering each completed timeline")
    timelines = dict(item.split("=", 1) for item in args.timeline)
    if set(arms) != set(timelines):
        parser.error("each arm requires its corresponding timeline")
    cached_native = None
    reference = None
    if args.native_reference_run is not None:
        reference_run = args.native_reference_run.resolve()
        try:
            current_run = historical_reference_current_run(arms, reference_run)
        except ValueError as error:
            parser.error(str(error))
        reference = native_reference_metadata(current_run, reference_run)
        cache_path = args.native_reference_cache or reference_run / "native_policy_comparison.json"
        cached_native = cached_native_reference(
            reference_run, args.bin_seconds, cache_path=cache_path,
        )
        if args.native_reference_cache is not None and cached_native is None:
            parser.error("the supplied native reference cache lacks matching clock, run, or bin evidence")
        reference["reused_cached_native_metrics"] = cached_native is not None
        reference["native_metrics_cache"] = (
            {
                "path": str(cache_path.resolve()),
                "sha256": hashlib.sha256(cache_path.read_bytes()).hexdigest(),
            } if cached_native is not None else None
        )
    report = {
        "schema_version": 2, "scope": "retrospective live-run diagnosis, not causal speedup",
        "runs": {
            name: cached_native if name == "native" and cached_native is not None
            else compare_run(Path(path), Path(timelines[name]), args.bin_seconds)
            for name, path in arms.items()
        },
    }
    if reference is not None:
        report["native_reference"] = reference
        report["scope"] = "development comparison with historical native; not a fresh paired experiment"
    report["same_task_set"] = len({
        tuple(sorted(run["instance_ids"])) for run in report["runs"].values()
    }) == 1
    report["same_physical_pool_capacity"] = len({
        json.dumps(run["native_pool_capacity"], sort_keys=True)
        for run in report["runs"].values()
    }) == 1
    summaries = {
        name: json.loads(next(Path(path).glob("client_*/summary.json")).read_text())
        for name, path in arms.items()
    }
    report["paired_completed_jct"] = {
        f"{left}_vs_{right}": {
            "left_arm": left, "right_arm": right,
            **paired_workflow_jct(summaries[left], summaries[right]),
        }
        for left, right in combinations(arms, 2)
    }
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
