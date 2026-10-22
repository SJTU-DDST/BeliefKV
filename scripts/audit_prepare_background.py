#!/usr/bin/env python3
"""Measure background FULL backup rounds and sampled cold-page coverage offline."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_prefetch_lifecycle import (
    distribution,
    native_full_prepare_attribution,
    transfer_parts,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords


def stats(values):
    values = [
        value for value in values
        if type(value) in (int, float) and math.isfinite(value)
    ]
    return {
        **distribution(values),
        "mean": math.fsum(values) / len(values) if values else None,
        "min": min(values) if values else None,
    }


def load_report(path):
    content = path.read_bytes()
    return json.loads(gzip.decompress(content) if path.suffix == ".gz" else content)


def audit_rounds(rows, transfers, *, byte_limit, node_limit, full_unit):
    by_command = {row["command_id"]: row for row in transfers}
    results = {}
    sources = ("idle", "critical", "spawn") if any(row["source"] == "spawn" for row in rows) else ("idle", "critical")
    for source in sources:
        selected = sorted(
            (row for row in rows if row["source"] == source),
            key=lambda row: row["ts_ms"],
        )
        acknowledged = [
            by_command[row["ack_transfer_command_id"]] for row in selected
            if row["ack_evidence"] == "reconciled_full_batch"
            and row["ack_transfer_command_id"] in by_command
        ]
        limit = (
            byte_limit if source == "idle" else 4 * 1024**3 if source == "spawn"
            else 64 * 1024**2
        )
        # The native budget is rounded down to a whole FULL page/token.
        rounded_limit = (limit // full_unit * full_unit) if full_unit else None
        times = [row["ts_ms"] for row in selected]
        results[source] = {
            "issued_batches": len(selected),
            "issued_full_bytes_known": sum(
                row["issued_full_bytes_known"] or 0 for row in selected
            ),
            "unknown_byte_batches": sum(
                row["issued_full_bytes_known"] is None for row in selected
            ),
            "bytes_per_issued_batch": stats(
                row["issued_full_bytes_known"] for row in selected
            ),
            "nodes_per_issued_batch": stats(len(row["native_nodes"]) for row in selected),
            "issue_gap_ms": stats(after - before for before, after in zip(times, times[1:])),
            "reconciled_ack_batches": len(acknowledged),
            "acknowledged_full_bytes": sum(row["actual_bytes"] for row in acknowledged),
            "stream_ms_per_reconciled_batch": stats(
                row.get("transfer_stream_elapsed_ms") for row in acknowledged
            ),
            "submit_to_ack_ms": stats(row.get("submit_to_ack_ms") for row in acknowledged),
            "batches_reaching_token_rounded_byte_limit": (
                sum(
                    row["issued_full_bytes_known"] is not None
                    and row["issued_full_bytes_known"] >= rounded_limit
                    for row in selected
                ) if rounded_limit else None
            ),
            "batches_reaching_extent_limit": sum(
                len(row["native_nodes"]) >= (
                    node_limit if source == "idle" else row.get("max_merged_nodes", 64)
                    if source == "spawn" else 1
                )
                for row in selected
            ),
        }
    return results


def semantic_prepare_rounds(opportunities, completed, *, full_unit):
    issues = {
        row["command_id"]: row for row in opportunities
        if row.get("event") == "prepare_native_issued"
    }
    receipts = {}
    for transfer in completed:
        if transfer["direction"] == "d2h":
            for part in transfer_parts(transfer):
                if part["command_id"] in issues:
                    receipts[part["command_id"]] = part
    results = {}
    for source in sorted({row["source"] for row in issues.values()}):
        commands = [command for command, row in issues.items() if row["source"] == source]
        full_bytes = [
            receipts[command]["pool_units"].get("kv", 0) * full_unit
            for command in commands if command in receipts and full_unit is not None
        ]
        results[source] = {
            "issued_commands": len(commands),
            "acknowledged_child_receipts": sum(command in receipts for command in commands),
            "full_ack_bytes_known": sum(full_bytes),
            "full_ack_bytes_unknown_commands": len(commands) - len(full_bytes),
            "full_bytes_per_acknowledged_command": stats(full_bytes),
        }
    return results


def audit_data(opportunities, transfers, host_samples, *, native=None, full_unit=None,
               host_evictions=None, byte_limit=1024**3, node_limit=32, window_ms=60_000):
    if window_ms <= 0:
        raise ValueError("window_ms must be positive")
    states = [row for row in opportunities if row.get("event") == "admission_runtime_state"]
    state = max(states, key=lambda row: row["ts_ms"]) if states else {}
    configuration = state.get("idle_full_prepare") or {}
    native_configuration = configuration.get("native") or {}
    byte_limit = native_configuration.get("max_batch_bytes", byte_limit)
    node_limit = native_configuration.get("max_merged_nodes", node_limit)
    completed = [
        row for row in transfers
        if row.get("status") == "completed" and row.get("direction") in ("d2h", "h2d")
    ]
    commands = [row["command_id"] for row in completed]
    if len(commands) != len(set(commands)):
        raise ValueError("duplicate completed transfer command")
    issues = [
        row for row in opportunities
        if row.get("event") in ("idle_cold_full_prepare", "critical_parent_cold_full_prepare", "spawn_full_prepare")
    ]
    pressure = [
        row for row in opportunities
        if row.get("event") in ("critical_parent_headroom", "wait_prefetch_capacity_reclaimed")
    ]
    native = native if native is not None else native_full_prepare_attribution(
        issues, completed, pressure, {}, host_evictions, full_unit=full_unit,
    )
    times = [
        row[field] for row in completed for field in ("submit_ts_ms", "complete_ts_ms")
        if row.get(field) is not None
    ] + [row["ts_ms"] for row in opportunities if row.get("ts_ms") is not None]
    start, end = (min(times), max(times)) if times else (None, None)
    span = end - start if start is not None else 0.
    windows = []
    if span > 0:
        for index in range(math.ceil(span / window_ms)):
            begin = start + index * window_ms
            finish = min(end, begin + window_ms)
            windows.append({
                "start_ts_ms": begin, "end_ts_ms": finish,
                "elapsed_s": (finish - begin) / 1000.,
                "idle_batches": 0, "idle_full_bytes_known": 0,
                "d2h_bytes": 0, "d2h_stream_ms": 0., "d2h_unknown_stream_batches": 0,
                "h2d_bytes": 0, "h2d_stream_ms": 0., "h2d_unknown_stream_batches": 0,
                "pressure_checks": 0, "full_shortfall_checks": 0,
                "checks_with_cold_unbacked_rejection": 0,
            })

    def bucket(when):
        if not windows or when is None or when < start or when > end:
            return None
        return windows[min(int((when - start) // window_ms), len(windows) - 1)]

    directions = {}
    for direction in ("d2h", "h2d"):
        rows = [row for row in completed if row["direction"] == direction]
        elapsed = [
            row["transfer_stream_elapsed_ms"] for row in rows
            if type(row.get("transfer_stream_elapsed_ms")) in (int, float)
            and math.isfinite(row["transfer_stream_elapsed_ms"])
            and row["transfer_stream_elapsed_ms"] >= 0
        ]
        directions[direction] = {
            "completed_batches": len(rows),
            "bytes": sum(row["actual_bytes"] for row in rows),
            "stream_ms_sum": math.fsum(elapsed),
            "unknown_stream_batches": len(rows) - len(elapsed),
            "stream_ms_per_wall_ms": math.fsum(elapsed) / span if span > 0 else None,
            "bytes_per_wall_second": (
                sum(row["actual_bytes"] for row in rows) * 1000. / span if span > 0 else None
            ),
        }
        for row in rows:
            window = bucket(row.get("submit_ts_ms"))
            if window is None:
                continue
            window[f"{direction}_bytes"] += row["actual_bytes"]
            duration = row.get("transfer_stream_elapsed_ms")
            if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
                window[f"{direction}_stream_ms"] += duration
            else:
                window[f"{direction}_unknown_stream_batches"] += 1
    for row in native["rows"]:
        window = bucket(row["ts_ms"])
        if row["source"] == "idle" and window is not None:
            window["idle_batches"] += 1
            window["idle_full_bytes_known"] += row["issued_full_bytes_known"] or 0
    host = sorted(
        (row for row in host_samples if row.get("event") == "host_pool_usage"),
        key=lambda row: row["ts_ms"],
    )
    host_times = [row["ts_ms"] for row in host]
    reasons, ages, free_bytes = Counter(), [], []
    pressure_summary = Counter()
    for row in pressure:
        diagnosis = row.get("capacity") or row.get("capacity_reclaim") or {}
        pressure_summary["checks"] += 1
        pressure_summary["checks_with_diagnostics"] += bool(diagnosis)
        shortfall = diagnosis.get("full_target_satisfied") is False
        unbacked = (diagnosis.get("native_full_rejections") or {}).get("unbacked", 0)
        pressure_summary["full_shortfall_checks"] += shortfall
        pressure_summary["checks_with_cold_unbacked_rejection"] += unbacked > 0
        pressure_summary["cold_unbacked_rejection_observations"] += unbacked
        reasons.update(diagnosis.get("native_full_rejections") or {})
        window = bucket(row["ts_ms"])
        if window is not None:
            window["pressure_checks"] += 1
            window["full_shortfall_checks"] += shortfall
            window["checks_with_cold_unbacked_rejection"] += unbacked > 0
        if unbacked:
            prior = bisect_right(host_times, row["ts_ms"]) - 1
            if prior < 0:
                pressure_summary["unbacked_checks_without_prior_host_sample"] += 1
                continue
            sample = host[prior]
            full = (sample.get("pools") or {}).get("full") or {}
            capacity, used = full.get("capacity_units"), full.get("used_units")
            if full_unit is None or type(capacity) is not int or type(used) is not int:
                pressure_summary["unbacked_checks_without_host_free_bytes"] += 1
                continue
            remaining = (capacity - used) * full_unit
            ages.append(row["ts_ms"] - sample["ts_ms"])
            free_bytes.append(remaining)
            pressure_summary["unbacked_checks_with_at_least_one_batch_host_free"] += (
                remaining >= byte_limit
            )
    counters = {
        key: value for key, value in (state.get("counts") or {}).items()
        if key.startswith("idle_full_prepare_")
    }
    total_tokens = (state.get("counts") or {}).get("idle_cold_full_backup_tokens")
    cumulative_bytes = (
        total_tokens * full_unit if type(total_tokens) is int and full_unit is not None else None
    )
    means = {}
    for kind, counter in (
        ("runtime_check", "idle_full_prepare_checks"),
        ("native_call", "idle_full_prepare_native_calls"),
        ("issued_batch", "idle_full_prepare_issued_batches"),
    ):
        count = counters.get(counter)
        means[kind] = (
            cumulative_bytes / count if cumulative_bytes is not None
            and type(count) is int and count > 0 else None
        )
    return {
        "observation": {"start_ts_ms": start, "end_ts_ms": end, "elapsed_s": span / 1000.},
        "configuration": {
            "background_interval_ms": configuration.get("interval_ms", 400.),
            "background_max_batch_bytes": byte_limit, "background_max_merged_nodes": node_limit,
            "source": "runtime_snapshot" if native_configuration else "current_policy_assumption",
            "full_bytes_per_token": full_unit,
        },
        "rounds": audit_rounds(
            native["rows"], completed, byte_limit=byte_limit,
            node_limit=node_limit, full_unit=full_unit,
        ),
        "directions": directions,
        "semantic_prepare_by_source": semantic_prepare_rounds(
            opportunities, completed, full_unit=full_unit,
        ),
        "native_backup_lifecycle": native["summary"],
        "cold_capacity": {
            **dict(pressure_summary), "native_full_rejection_observations": dict(reasons),
            "prior_host_sample_age_ms_at_unbacked_check": stats(ages),
            "host_full_free_bytes_at_unbacked_check": stats(free_bytes),
        },
        "runtime_checks": counters or None,
        "runtime_snapshot_cumulative_full_bytes_per_round": {
            "snapshot_ts_ms": state.get("ts_ms"),
            "issued_full_bytes_known": cumulative_bytes, **means,
        },
        "native_round_diagnostics": (state.get("idle_full_prepare") or {}).get("native"),
        "windows_by_submit_cohort": windows,
        "semantics": (
            "Rounds are issued native FULL batches; empty and blocked checks require the new "
            "cumulative runtime/native counters and remain unknown in old traces. Background, "
            "urgent and tagged D2H are separate from predictive H2D. Stream elapsed time is "
            "summed device-event duration, not wall-anchored physical PCIe occupancy; it may "
            "include stream stalls. Do not subtract it from JCT or infer exact idle intervals "
            "or bidirectional overlap. Per-window stream time belongs to the submit cohort, "
            "not the unknown actual DMA interval. Small batches and low stream/wall ratio do "
            "not prove eligible unbacked backlog. Node/byte caps are observations, not proof "
            "of a missed opportunity. Cold unbacked rejection counts can revisit the same "
            "node; they are neither unique KV volume nor all-HBM inventory. Prior Host samples "
            "are sampled capacity, not an exact allocation guarantee. Lifecycle restore "
            "associations do not prove unique consumption, avoided wait or speedup."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--lifecycle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output exists; use a fresh observation path")
    args.arm = args.arm.resolve()
    lifecycle = load_report(args.lifecycle) if args.lifecycle else None
    sealed = SealedRecords()
    if lifecycle and lifecycle.get("input_jsonl_prefixes"):
        prefixes = lifecycle["input_jsonl_prefixes"]
        expected = args.arm.resolve()
        for path in prefixes:
            if not Path(path).resolve().is_relative_to(expected):
                parser.error("lifecycle prefixes refer to a different arm")
        sealed.prefixes.update({str(Path(path).resolve()): value for path, value in prefixes.items()})
    opportunities = list(sealed(args.arm / "opportunities/admission_opportunities.jsonl"))
    transfers = list(sealed(args.arm / "server/transfer_telemetry.jsonl"))
    host_samples = list(sealed(args.arm / "server/host_pool_telemetry.jsonl"))
    native = lifecycle.get("native_full_prepare_attribution") if lifecycle else None
    eviction_path = args.arm / "server/eviction_attribution.jsonl"
    host_evictions = (
        [row for row in sealed(eviction_path) if row.get("event") == "host_block_evicted"]
        if native is None and eviction_path.exists() else None
    )
    census = args.arm / "server/native_capacity_census.json"
    capacity = load_report(census)["capacity"] if census.exists() else {}
    tokens, size = capacity.get("host_full_tokens"), capacity.get("host_full_bytes")
    unit = (
        size // tokens if type(tokens) is int and tokens > 0
        and type(size) is int and size > 0 and size % tokens == 0 else None
    )
    result = audit_data(
        opportunities, transfers, host_samples, full_unit=unit,
        native=native, host_evictions=host_evictions,
    )
    result.update({
        "arm": str(args.arm), "input_jsonl_prefixes": sealed.prefixes,
        "audit_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "capacity_census_sha256": hashlib.sha256(census.read_bytes()).hexdigest() if census.exists() else None,
        "lifecycle_report_sha256": (
            hashlib.sha256(args.lifecycle.read_bytes()).hexdigest() if args.lifecycle else None
        ),
        "prefix_semantics": "Independently sealed complete JSONL prefixes, not an atomic snapshot.",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, allow_nan=False).encode() + b"\n"
    args.output.write_bytes(gzip.compress(payload, mtime=0) if args.output.suffix == ".gz" else payload)
    print(json.dumps({
        "output": str(args.output), "observation": result["observation"],
        "rounds": result["rounds"], "directions": result["directions"],
        "cold_capacity": result["cold_capacity"],
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
