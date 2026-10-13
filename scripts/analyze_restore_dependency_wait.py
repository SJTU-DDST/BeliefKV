#!/usr/bin/env python3
"""Pair sampled CUDA dependency waits with a sealed busy-window audit prefix."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from math import ceil
from pathlib import Path
from statistics import mean, median


def distribution(values):
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "sum_ms": sum(ordered),
        "p50_ms": median(ordered) if ordered else None,
        "p90_ms": ordered[ceil(.9 * len(ordered)) - 1] if ordered else None,
        "max_ms": max(ordered, default=None),
    }


def analyze(report_path: Path, arm: str) -> dict:
    metadata = json.loads(report_path.read_text())["arms"][arm]
    window = metadata["monitor_window"]
    begin, end = window["begin_ts_ms"], window["end_ts_ms"]
    path = Path(metadata["source"])
    limit = metadata["read_prefix_bytes"]
    digest, seen = hashlib.sha256(), 0
    probes, prefills = [], []
    with path.open("rb") as stream:
        while seen < limit:
            line = stream.readline(limit - seen)
            if not line.endswith(b"\n"):
                raise ValueError("sealed audit prefix contains an incomplete line")
            seen += len(line)
            digest.update(line)
            if b'"gpu_restore_dependency_wait"' in line:
                row = json.loads(line)
                if begin <= row["ts_ms"] < end:
                    probes.append(row)
            elif b'"gpu_service_sample"' in line and b'"prefill"' in line:
                row = json.loads(line)
                if begin <= row["complete_ts_ms"] < end:
                    prefills.append(row)
    if digest.hexdigest() != metadata["read_prefix_sha256"]:
        raise ValueError("sealed audit prefix changed")
    by_requests = defaultdict(list)
    for row in prefills:
        identity = tuple(sorted(item["request_id"] for item in row["request_samples"]))
        by_requests[identity].append(row)
    paired, unmatched = [], []
    for probe in probes:
        candidates = []
        for row in by_requests[tuple(sorted(probe["request_ids"]))]:
            # Overlap can launch before the preceding batch finishes. The
            # published service interval deliberately excludes that overlap.
            launch_wall = row["complete_ts_ms"] - (
                row["complete_monotonic_ms"] - row["launch_monotonic_ms"]
            )
            if launch_wall <= probe["ts_ms"] <= row["complete_ts_ms"]:
                candidates.append(row)
        if len(candidates) != 1:
            unmatched.append({
                "forward_iter": probe["forward_iter"],
                "probe_ts_ms": probe["ts_ms"],
                "candidate_sample_ids": [row["sample_id"] for row in candidates],
            })
            continue
        row = candidates[0]
        samples = row["request_samples"]
        known_geometry = len(samples) == row["batch_size"] and all(
            item.get("prefill_geometry_source") in ("native_batch", "native_request_range")
            for item in samples
        )
        paired.append({
            "forward_iter": probe["forward_iter"],
            "sample_id": row["sample_id"],
            "probe_ts_ms": probe["ts_ms"],
            "complete_ts_ms": row["complete_ts_ms"],
            "request_ids": probe["request_ids"],
            "batch_size": row["batch_size"],
            "hicache_consumer_index": probe["hicache_consumer_index"],
            "sampling_interval": probe["sampling_interval"],
            "known_geometry": known_geometry,
            "extend_tokens": (
                sum(item["token_delta"] for item in samples) if known_geometry else None
            ),
            "mean_prefix_tokens": (
                mean(item["sequence_tokens_before"] for item in samples)
                if known_geometry else None
            ),
            "worker_interval_ms": row["service_elapsed_ms"],
            "sampled_layer_dependency_wait_ms": probe["gpu_layer_dependency_wait_ms"],
            "sampled_max_layer_dependency_wait_ms": probe["gpu_layer_dependency_wait_max_ms"],
            "gpu_observed_layer_span_ms": probe.get("gpu_observed_layer_span_ms"),
            "cpu_observed_layer_span_ms": probe.get("cpu_observed_layer_span_ms"),
            "thread_cpu_observed_layer_span_ms": probe.get("thread_cpu_observed_layer_span_ms"),
            "cpu_layer_wait_enqueue_ms": probe.get("cpu_layer_wait_enqueue_ms"),
        })
    short = [
        row for row in paired if row["batch_size"] == 1
        and row["known_geometry"] and row["extend_tokens"] < 512
    ]
    return {
        "busy_window_report": str(report_path.resolve()),
        "busy_window_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "source": str(path),
        "read_prefix_bytes": seen,
        "read_prefix_sha256": digest.hexdigest(),
        "begin_ts_ms": begin,
        "end_ts_ms": end,
        "prefill_batches_in_window": len(prefills),
        "probes_launched_in_window": len(probes),
        "paired_samples": len(paired),
        "unmatched_samples": unmatched,
        "sampled_wait": distribution([
            row["sampled_layer_dependency_wait_ms"] for row in paired
        ]),
        "sampled_worker_interval": distribution([
            row["worker_interval_ms"] for row in paired
        ]),
        "short_single_request_sampled_wait": distribution([
            row["sampled_layer_dependency_wait_ms"] for row in short
        ]),
        "short_single_request_sampled_worker_interval": distribution([
            row["worker_interval_ms"] for row in short
        ]),
        "observed_layer_span_distributions": {
            group: {
                field: distribution([
                    row[field] for row in rows
                    if type(row[field]) in (int, float)
                ])
                for field in (
                    "gpu_observed_layer_span_ms", "cpu_observed_layer_span_ms",
                    "thread_cpu_observed_layer_span_ms",
                    "cpu_layer_wait_enqueue_ms",
                )
            }
            for group, rows in (("all_paired", paired), ("short_single_request", short))
        },
        "samples": paired,
        "semantics": [
            "Audit reads stop at the previous report's exact hashed complete-line prefix.",
            "Probes use launch wall time; worker samples use completed half-open-window membership.",
            "Pairing requires identical request sets and one launch-to-completion interval containing the probe.",
            "Overlap launch time is reconstructed from the sample's own monotonic timestamps and completion wall time.",
            "Ambiguous and unmatched samples remain unknown; sample ordinal is not treated as forward_iter.",
            "CUDA waits include timing-event overhead; worker intervals include CPU and overlap processing.",
            "No extrapolation to unsampled prefills, exact kernel attribution or removable JCT is inferred.",
            "Small sampled waits do not establish that every speculative dependency is correctly scoped.",
            "Missing span fields in older traces stay unknown, not zero.",
            "GPU observed-layer spans include kernels and CPU submission gaps between waits, not a full forward.",
            "Thread CPU spans exclude other threads and unscheduled time; wall minus CPU is not a GIL attribution.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--busy-report", type=Path, required=True)
    parser.add_argument("--arm", default="predictive_h2d")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing dependency evidence")
    started = datetime.now().astimezone().isoformat()
    report = analyze(args.busy_report, args.arm)
    report.update(
        analysis_started_at_local=started,
        analysis_finished_at_local=datetime.now().astimezone().isoformat(),
        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        key: report[key] for key in (
            "paired_samples", "sampled_wait", "short_single_request_sampled_wait",
            "short_single_request_sampled_worker_interval",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
