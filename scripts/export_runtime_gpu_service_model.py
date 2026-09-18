#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Iterable

from beliefkv.predictor.hardware_service import GPUServiceCurveModel


_DECODE_BATCH_PATTERN = re.compile(
    r"Decode batch\. #running-req: (?P<batch>\d+), "
    r"#token: (?P<tokens>\d+), .*?cuda graph: (?P<graph>True|False), "
    r"gen throughput \(token/s\): (?P<throughput>[0-9.]+)"
)


def _records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _intervals(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in _records(path):
        if record.get("event") != "transfer_telemetry":
            continue
        start = record.get("start_ts_ms")
        timing_source = "start_ts_ms"
        if start is None:
            start = record.get("submit_ts_ms")
            timing_source = "submit_ts_ms"
        complete = record.get("complete_ts_ms")
        if start is None or complete is None or float(complete) < float(start):
            continue
        command_kind = str(record.get("command_kind") or "")
        native = bool(
            record.get("compute_phase") == "native_hicache"
            or command_kind.startswith("native_")
        )
        result.append(
            {
                "start": float(start),
                "complete": float(complete),
                "native": native,
                "bytes": max(
                    0,
                    int(
                        record.get("native_concurrent_bytes")
                        or record.get("actual_bytes")
                        or 0
                    ),
                ),
                "timing_source": timing_source,
            }
        )
    return sorted(result, key=lambda item: (item["start"], item["complete"]))


def _samples(path: Path) -> list[dict[str, Any]]:
    return sorted(
        (
            record
            for record in _records(path)
            if record.get("event") == "gpu_service_sample"
            and record.get("timing_semantics_version") == "gpu_service_interval_v1"
        ),
        key=lambda item: (
            float(item.get("service_start_ts_ms") or item.get("launch_ts_ms") or 0),
            float(item.get("complete_ts_ms") or item.get("ts_ms") or 0),
        ),
    )


def _artifact_rows(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for index, observation in enumerate(raw.get("observations", ())):
        features = observation.get("features", {})
        demands = features.get("request_demands", ())
        if not demands:
            continue
        request_samples = [
            {
                "request_id": f"base-{index}:{request_index}",
                "workflow_id": "base-runtime-artifact",
                "phase": str(features.get("phase") or "unknown"),
                "sequence_tokens_before": int(demand["sequence_tokens"]),
                "token_delta": int(demand["token_delta"]),
                "cache_hit_ratio": float(demand.get("cache_hit_ratio", 0.0)),
            }
            for request_index, demand in enumerate(demands)
        ]
        rows.append(
            {
                "row_type": "gpu_batch_service_interval",
                "sample_id": f"base-artifact:{index:09d}",
                "split": "train",
                "source_path": str(path.resolve()),
                "phase": str(features.get("phase") or "unknown"),
                "batch_size": len(request_samples),
                "request_count": len(request_samples),
                "request_samples": request_samples,
                "token_delta_total": sum(
                    item["token_delta"] for item in request_samples
                ),
                "prefill_decode_mixed": bool(
                    features.get("prefill_decode_mixed", False)
                ),
                "chunk_position": str(
                    features.get("chunk_position") or "unknown"
                ),
                "pcie_contention_state": str(
                    features.get("pcie_contention_state") or "unknown"
                ),
                "hicache_inflight_bytes": int(
                    features.get("hicache_inflight_bytes") or 0
                ),
                "service_elapsed_ms": float(observation["elapsed_ms"]),
                "warmup": False,
                "timing_semantics_version": "gpu_service_interval_v1",
                "timing_boundary": "preserved runtime artifact observation",
                "evidence_role": "runtime_validation",
            }
        )
    if not rows:
        raise ValueError(f"base artifact has no runtime observations: {path}")
    return rows


def _decode_log_rows(
    path: Path,
    *,
    source_index: int,
    minimum_batch: int,
) -> list[dict[str, Any]]:
    rows = []
    for line_index, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
    ):
        match = _DECODE_BATCH_PATTERN.search(line)
        if match is None or match.group("graph") != "True":
            continue
        batch_size = int(match.group("batch"))
        total_sequence_tokens = int(match.group("tokens"))
        throughput = float(match.group("throughput"))
        if batch_size < minimum_batch or throughput <= 0:
            continue
        quotient, remainder = divmod(total_sequence_tokens, batch_size)
        request_samples = [
            {
                "request_id": f"graph-log-{source_index}-{line_index}:{index}",
                "workflow_id": "graph96-runtime-log",
                "phase": "decode",
                "sequence_tokens_before": quotient + (index < remainder),
                "token_delta": 1,
                "cache_hit_ratio": 0.0,
            }
            for index in range(batch_size)
        ]
        rows.append(
            {
                "row_type": "gpu_batch_service_interval",
                "sample_id": f"graph-log-{source_index}:{line_index:09d}",
                "split": "train",
                "source_path": str(path.resolve()),
                "phase": "decode",
                "batch_size": batch_size,
                "request_count": batch_size,
                "request_samples": request_samples,
                "token_delta_total": batch_size,
                "prefill_decode_mixed": False,
                "chunk_position": "continuation",
                "pcie_contention_state": "runtime_interval_unknown",
                "hicache_inflight_bytes": 0,
                "service_elapsed_ms": 1000.0 * batch_size / throughput,
                "warmup": False,
                "timing_semantics_version": "decode_log_interval_v1",
                "timing_boundary": (
                    "SGLang periodic decode throughput interval; service time "
                    "is batch_size / generation throughput"
                ),
                "evidence_role": "runtime_validation",
            }
        )
    if not rows:
        raise ValueError(f"server log has no graph decode rows: {path}")
    return rows


def _contention(
    sample: dict[str, Any],
    intervals: list[dict[str, Any]],
) -> tuple[str, int, str]:
    start = sample.get("service_start_ts_ms")
    if start is None:
        start = sample.get("launch_ts_ms")
    complete = sample.get("complete_ts_ms")
    if complete is None:
        complete = sample.get("ts_ms")
    if start is None or complete is None:
        return "unknown", 0, "service_interval_unavailable"
    start = float(start)
    complete = float(complete)
    overlapping = [
        item
        for item in intervals
        if item["start"] < complete and start < item["complete"]
    ]
    if not overlapping:
        return "idle", 0, "observed_no_overlap"
    has_native = any(item["native"] for item in overlapping)
    has_explicit = any(not item["native"] for item in overlapping)
    state = (
        "mixed_transfer_observed"
        if has_native and has_explicit
        else "native_hicache_observed"
        if has_native
        else "explicit_transfer_observed"
    )
    native_bytes = max(
        (item["bytes"] for item in overlapping if item["native"]),
        default=0,
    )
    timing = (
        "start_to_complete_overlap"
        if all(item["timing_source"] == "start_ts_ms" for item in overlapping)
        else "start_or_submit_to_complete_observed_upper_bound"
    )
    return state, native_bytes, timing


def _runtime_rows(path: Path, source_index: int) -> list[dict[str, Any]]:
    intervals = _intervals(path)
    rows: list[dict[str, Any]] = []
    phase_index: Counter[tuple[str, str]] = Counter()
    for sample in _samples(path):
        request_samples = []
        phases = set()
        for request in sample.get("request_samples", ()):
            request_id = str(request.get("request_id") or "")
            phase = str(request.get("phase") or sample.get("phase") or "unknown")
            sequence_tokens = int(
                request.get("effective_sequence_tokens_before")
                if request.get("effective_sequence_tokens_before") is not None
                else request.get("sequence_tokens_before")
                or 0
            )
            chunk_index = phase_index[(request_id, phase)]
            phase_index[(request_id, phase)] += 1
            request_samples.append(
                {
                    "request_id": request_id,
                    "workflow_id": str(request.get("workflow_id") or ""),
                    "phase": phase,
                    "sequence_tokens_before": sequence_tokens,
                    "base_sequence_tokens_before": int(
                        request.get("sequence_tokens_before") or 0
                    ),
                    "output_tokens_before": int(
                        request.get("output_tokens_before") or 0
                    ),
                    "token_delta": int(request.get("token_delta") or 0),
                    "cache_hit_ratio": 0.0,
                    "chunk_index": chunk_index,
                }
            )
            phases.add(phase)
        declared_batch = int(sample.get("batch_size") or 0)
        if (
            not request_samples
            or declared_batch != len(request_samples)
            or float(sample.get("service_elapsed_ms") or 0.0) <= 0
        ):
            continue
        state, hicache_bytes, contention_semantics = _contention(
            sample, intervals
        )
        sample_id = str(sample.get("sample_id") or "")
        if not sample_id:
            continue
        rows.append(
            {
                "row_type": "gpu_batch_service_interval",
                "sample_id": f"runtime-{source_index}:{sample_id}",
                "split": "train",
                "source_path": str(path),
                "source_run_id": str(sample.get("run_id") or ""),
                "case_ids": sorted(
                    {
                        str(request["workflow_id"])
                        for request in request_samples
                        if request["workflow_id"]
                    }
                ),
                "phase": next(iter(phases)) if len(phases) == 1 else "mixed",
                "batch_size": declared_batch,
                "request_count": len(request_samples),
                "request_samples": request_samples,
                "token_delta_total": sum(
                    int(request["token_delta"]) for request in request_samples
                ),
                "prefill_decode_mixed": len(phases) > 1,
                "chunk_position": (
                    "first"
                    if all(
                        int(request["chunk_index"]) == 0
                        for request in request_samples
                    )
                    else "continuation"
                ),
                "pcie_contention_state": state,
                "hicache_inflight_bytes": hicache_bytes,
                "pcie_contention_timing_semantics": contention_semantics,
                "service_elapsed_ms": float(sample["service_elapsed_ms"]),
                "warmup": False,
                "timing_semantics_version": "gpu_service_interval_v1",
                "timing_boundary": (
                    "runtime scheduler/worker interval; graph32 shadow evidence, "
                    "not a CUDA event"
                ),
                "evidence_role": "runtime_validation",
            }
        )
    return rows


def _aggregate_summaries(paths: list[Path]) -> list[dict[str, Any]]:
    summaries = []
    for path in paths:
        for record in _records(path):
            if record.get("event") == "gpu_service_observer_summary":
                summaries.append({"source_path": str(path), "summary": record})
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a GPU service shadow artifact from runtime evidence."
    )
    parser.add_argument("--runtime-audit", type=Path, action="append", default=[])
    parser.add_argument("--aggregate-audit", type=Path, action="append", default=[])
    parser.add_argument("--base-artifact", type=Path)
    parser.add_argument("--decode-server-log", type=Path, action="append", default=[])
    parser.add_argument("--minimum-log-batch", type=int, default=33)
    parser.add_argument("--profile-id", default="h200_bf16_v6")
    parser.add_argument("--cuda-graph-max-bs", type=int, default=32)
    parser.add_argument("--hardware-key", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluation-output", type=Path, required=True)
    parser.add_argument("--minimum-support", type=float, default=8.0)
    parser.add_argument("--neighbor-count", type=int, default=64)
    args = parser.parse_args()

    source_rows: list[list[dict[str, Any]]] = []
    for source_index, path in enumerate(args.runtime_audit):
        rows = _runtime_rows(path.resolve(), source_index)
        if not rows:
            raise ValueError(f"no complete graph32 service rows in {path}")
        source_rows.append(rows)
    rows = [row for source in source_rows for row in source]
    if args.base_artifact is not None:
        rows.extend(_artifact_rows(args.base_artifact.resolve()))
    log_rows = []
    for source_index, path in enumerate(args.decode_server_log):
        log_rows.extend(
            _decode_log_rows(
                path.resolve(),
                source_index=source_index,
                minimum_batch=args.minimum_log_batch,
            )
        )
    rows.extend(log_rows)
    if not rows:
        raise ValueError("no runtime service evidence was provided")
    sample_ids = [str(row["sample_id"]) for row in rows]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("runtime service rows must have unique sample IDs")

    cross_source = []
    if len(source_rows) > 1:
        for index, holdout in enumerate(source_rows):
            train = [
                row
                for source_index, source in enumerate(source_rows)
                if source_index != index
                for row in source
            ]
            fold_model = GPUServiceCurveModel(
                minimum_support=args.minimum_support,
                neighbor_count=args.neighbor_count,
            )
            fold_model.fit_runtime_observations(train)
            cross_source.append(
                {
                    "holdout_source": str(args.runtime_audit[index].resolve()),
                    "report": fold_model.validate_runtime_rows(holdout),
                }
            )

    model = GPUServiceCurveModel(
        minimum_support=args.minimum_support,
        neighbor_count=args.neighbor_count,
    )
    training_summary = model.fit_runtime_observations(rows)
    timing_boundary = (
        "runtime scheduler/worker intervals plus SGLang periodic decode "
        f"throughput observations under graph{args.cuda_graph_max_bs}; "
        "shadow-only and not claimed as pure CUDA kernel time"
    )
    model.training_summary["timing_boundary"] = timing_boundary
    training_summary["timing_boundary"] = timing_boundary
    source_counts = {
        str(path.resolve()): len(source_rows[index])
        for index, path in enumerate(args.runtime_audit)
    }
    if args.base_artifact is not None:
        source_counts[str(args.base_artifact.resolve())] = len(
            _artifact_rows(args.base_artifact.resolve())
        )
    for path in args.decode_server_log:
        source_counts[str(path.resolve())] = sum(
            row["source_path"] == str(path.resolve()) for row in log_rows
        )
    metadata = {
        "profile_id": args.profile_id,
        "runtime_mode": "performance",
        "cuda_graph_max_bs": args.cuda_graph_max_bs,
        "evidence_role": "runtime_validation",
        "shadow_only": True,
        "online_canary_eligible": False,
        "timing_boundary": timing_boundary,
        "source_counts": source_counts,
        "aggregate_coverage_sources": [
            str(path.resolve()) for path in args.aggregate_audit
        ],
    }
    model.bind_hardware(args.hardware_key, metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    model.save(args.output)

    phase_counts = Counter(str(row["phase"]) for row in rows)
    batch_counts = Counter(int(row["batch_size"]) for row in rows)
    contention_counts = Counter(str(row["pcie_contention_state"]) for row in rows)
    evaluation = {
        "schema_version": 1,
        "artifact_kind": "runtime_gpu_service_shadow",
        "hardware_key": args.hardware_key,
        "online_eligible": False,
        "shadow_eligible": True,
        "training_summary": training_summary,
        "source_counts": source_counts,
        "phase_counts": dict(sorted(phase_counts.items())),
        "batch_size_counts": {
            str(key): value for key, value in sorted(batch_counts.items())
        },
        "contention_counts": dict(sorted(contention_counts.items())),
        "cross_source_validation": cross_source,
        "aggregate_coverage": _aggregate_summaries(args.aggregate_audit),
        "decode_log_observation_count": len(log_rows),
        "limitations": [
            "runtime interval boundary is not a CUDA event",
            (
                "graph decode log rows use periodic aggregate throughput and "
                "approximate per-request sequence lengths from batch totals"
            ),
            "artifact is restricted to shadow planning until controlled calibration",
            "cross-source validation is workload-local and not a formal test split",
        ],
    }
    args.evaluation_output.parent.mkdir(parents=True, exist_ok=True)
    args.evaluation_output.write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evaluation, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
