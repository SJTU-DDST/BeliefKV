#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable


RUN_PATTERN = re.compile(r"^t(?P<tokens>\d+)-r(?P<repeat>\d+)$")
TARGET_KINDS = {"offload_context", "prefetch_context"}


def _records(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    result.append(value)
    return result


def _percentile(values: Iterable[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _service_intervals(audit_path: Path) -> list[tuple[float, float, str]]:
    intervals = []
    for record in _records(audit_path):
        if record.get("event") != "gpu_service_sample":
            continue
        start = record.get("service_start_ts_ms")
        complete = record.get("complete_ts_ms")
        if start is None or complete is None:
            continue
        intervals.append((float(start), float(complete), str(record.get("phase"))))
    return intervals


def _overlap_phase(
    record: dict[str, Any], intervals: list[tuple[float, float, str]]
) -> tuple[str, int]:
    start = float(record.get("start_ts_ms") or record["submit_ts_ms"])
    complete = float(record["complete_ts_ms"])
    phases = [
        phase
        for service_start, service_complete, phase in intervals
        if service_start < complete and service_complete > start
    ]
    if not phases:
        return "idle", 0
    unique = sorted(set(phases))
    return (unique[0] if len(unique) == 1 else "mixed"), len(phases)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and aggregate the H200 deterministic transfer matrix."
    )
    parser.add_argument("--matrix-root", type=Path, required=True)
    parser.add_argument("--environment-manifest", type=Path, required=True)
    parser.add_argument("--output-telemetry", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    parser.add_argument("--expected-repeat-count", type=int, default=3)
    args = parser.parse_args()

    root = args.matrix_root.expanduser().resolve()
    environment_path = args.environment_manifest.expanduser().resolve()
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    completed: list[dict[str, Any]] = []
    diagnostic_status_counts: dict[str, int] = defaultdict(int)
    run_summaries = []

    for run_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        match = RUN_PATTERN.match(run_dir.name)
        if match is None:
            continue
        analysis_path = run_dir / "restore_gate_analysis.json"
        telemetry_path = run_dir / "server" / "transfer_telemetry.jsonl"
        audit_path = run_dir / "server" / "runtime_audit.jsonl"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        if analysis.get("passed") is not True:
            raise RuntimeError(f"restore correctness gate failed: {run_dir}")
        intervals = _service_intervals(audit_path)
        raw_transfers = [
            record
            for record in _records(telemetry_path)
            if record.get("event") == "transfer_telemetry"
            and record.get("command_kind") in TARGET_KINDS
        ]
        for record in raw_transfers:
            diagnostic_status_counts[str(record.get("status"))] += 1

        valid = [
            dict(record)
            for record in raw_transfers
            if record.get("status") == "completed"
            and int(record.get("actual_bytes") or 0) > 0
        ]
        if {record.get("direction") for record in valid} != {"d2h", "h2d"}:
            raise RuntimeError(f"run lacks completed D2H/H2D pair: {run_dir}")
        for record in valid:
            phase, overlap_count = _overlap_phase(record, intervals)
            if phase not in {"prefill", "decode"} or overlap_count <= 0:
                raise RuntimeError(
                    f"target transfer lacks compute-contention evidence: {run_dir}"
                )
            start = float(record.get("start_ts_ms") or record["submit_ts_ms"])
            complete_ts = float(record["complete_ts_ms"])
            contamination = [
                candidate
                for candidate in raw_transfers
                if candidate["command_id"] != record["command_id"]
                and float(candidate.get("submit_ts_ms") or 0.0) < complete_ts
                and float(candidate.get("complete_ts_ms") or 0.0) > start
            ]
            if contamination:
                raise RuntimeError(
                    f"target transfer overlaps another transfer: {run_dir}"
                )
            record["compute_phase"] = phase
            record["native_concurrent_bytes"] = 0
            record["calibration_pcie_contention"] = "idle_link"
            record["calibration_compute_contention"] = phase
            record["calibration_overlap_service_sample_count"] = overlap_count
            record["calibration_source_run"] = run_dir.name
            completed.append(record)
        run_summaries.append(
            {
                "run_id": run_dir.name,
                "target_tokens": int(match.group("tokens")),
                "repeat": int(match.group("repeat")),
                "gate_passed": True,
                "completed_transfer_count": len(valid),
                "diagnostic_transfer_count": len(raw_transfers),
            }
        )

    groups: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for record in completed:
        groups[
            (
                str(record["direction"]),
                int(record["actual_bytes"]),
                int(record.get("extent_count") or record.get("page_count") or 0),
            )
        ].append(record)
    if len(run_summaries) != 9 or len(completed) != 18 or len(groups) != 6:
        raise RuntimeError(
            "expected nine runs, eighteen completed transfers, and six buckets"
        )

    buckets = []
    for (direction, actual_bytes, extent_count), rows in sorted(groups.items()):
        if len(rows) != args.expected_repeat_count:
            raise RuntimeError(
                f"bucket {direction}/{actual_bytes}/{extent_count} has {len(rows)} samples"
            )
        durations = [
            float(row["complete_ts_ms"])
            - float(row.get("start_ts_ms") or row["submit_ts_ms"])
            for row in rows
        ]
        rates = [actual_bytes / duration / 1_000_000.0 for duration in durations]
        buckets.append(
            {
                "direction": direction,
                "actual_bytes": actual_bytes,
                "extent_count": extent_count,
                "sample_count": len(rows),
                "duration_ms_p50": median(durations),
                "duration_ms_p95": _percentile(durations, 0.95),
                "effective_bandwidth_gbps_p10": _percentile(rates, 0.10),
                "extent_bytes_min": min(int(row["extent_bytes_min"]) for row in rows),
                "extent_bytes_p50": int(
                    median(int(row["extent_bytes_p50"]) for row in rows)
                ),
                "extent_bytes_max": max(int(row["extent_bytes_max"]) for row in rows),
                "pinned_host": all(row.get("pinned_host") is True for row in rows),
                "compute_contention": sorted(
                    {str(row["compute_phase"]) for row in rows}
                ),
                "pcie_contention": "idle_link",
            }
        )

    output_telemetry = args.output_telemetry.expanduser().resolve()
    output_telemetry.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_telemetry.with_suffix(output_telemetry.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for record in completed:
            stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(output_telemetry)

    metadata = {
        "profile_id": "h200_bf16_v1",
        "evidence_role": "controlled_restore_microbenchmark",
        "environment_manifest": str(environment_path),
        "gpu_uuid": environment["gpu"]["uuid"],
        "driver_version": environment["gpu"]["driver_version"],
        "model_path": environment["model"]["model_path"],
        "weight_dtype": environment["model"]["weight_dtype"],
        "kv_cache_dtype": environment["model"]["resolved_kv_dtype"],
        "sglang_commit": environment["software"]["sglang_source"]["commit"],
        "kv_pool_tokens": environment["capacity"]["max_total_num_tokens"],
        "kv_pool_bytes": environment["capacity"]["kv_pool_bytes"],
        "host_pool_bytes": environment["capacity"]["host_pool_bytes"],
        "page_size": environment["capacity"]["page_size"],
        "pinned_host": True,
        "numa_policy": "server process unbound; H200 attached to NUMA node 1",
        "supported_conditions": [
            "d2h_pcie_idle_link_with_decode_contention",
            "h2d_pcie_idle_link_with_prefill_contention",
        ],
        "unsupported_conditions": ["gpu_compute_idle", "concurrent_pcie_transfer"],
        "conditioned_features": [
            "direction",
            "bytes",
            "extent_count",
            "compute_phase",
            "command_kind",
            "host_copy_state",
            "pinned_host",
        ],
        "excluded_diagnostic_status_counts": dict(diagnostic_status_counts),
    }
    _write_json(args.output_metadata.expanduser().resolve(), metadata)
    _write_json(
        args.output_summary.expanduser().resolve(),
        {
            "schema_version": 1,
            "matrix_root": str(root),
            "environment_manifest": str(environment_path),
            "gate": {
                "run_count": len(run_summaries),
                "passed_run_count": sum(
                    bool(item["gate_passed"]) for item in run_summaries
                ),
                "completed_sample_count": len(completed),
                "bucket_count": len(buckets),
                "expected_repetitions_per_bucket": args.expected_repeat_count,
                "passed": True,
            },
            "diagnostic_status_counts": dict(diagnostic_status_counts),
            "runs": run_summaries,
            "buckets": buckets,
            "scope": {
                "supported": [
                    "d2h_pcie_idle_link_with_decode_contention",
                    "h2d_pcie_idle_link_with_prefill_contention",
                ],
                "unsupported": ["gpu_compute_idle", "concurrent_pcie_transfer"],
                "rejected_restore_attempts_used_for_fit": False,
            },
        },
    )
    print(json.dumps({"completed_samples": len(completed), "buckets": len(buckets)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
