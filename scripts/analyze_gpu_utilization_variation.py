#!/usr/bin/env python3
"""Relate GPU utilization variation to existing, sampled runtime observations."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, pstdev
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.compare_native_policy_runs import aligned_native_clock, records


def correlation(pairs):
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    mx, my = mean(xs), mean(ys)
    denominator = math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    )
    return sum((x - mx) * (y - my) for x, y in pairs) / denominator if denominator else None


def analyze(arm, timeline_path, *, begin, end, bin_seconds):
    with gzip.open(timeline_path, "rt") as stream:
        timeline = json.load(stream)
    clock = timeline["summary"]["clock_alignment"]
    anchor, zone = aligned_native_clock(clock, anchor_text=timeline["start_anchor"])
    offset = clock["native_wall_minus_monotonic_ms"]
    bins = defaultdict(lambda: {
        "gpu": [], "sampled_prefills": 0, "native_prefills": 0,
        "sampled_control_thread_cpu_ms": 0., "sampled_forward_thread_cpu_ms": 0.,
        "h2d_bytes": 0, "d2h_bytes": 0,
    })

    def bucket(ts_ms):
        elapsed = (ts_ms - anchor) / 1000.
        return bins[int((elapsed - begin) // bin_seconds)] if begin <= elapsed < end else None

    gpu = []
    client = next(arm.glob("client_*/gpu_samples.csv")).parent
    with (client / "gpu_samples.csv").open() as stream:
        for row in csv.DictReader(stream):
            ts = datetime.strptime(
                row["timestamp"].strip(), "%Y/%m/%d %H:%M:%S.%f",
            ).replace(tzinfo=zone).timestamp() * 1000.
            item = bucket(ts)
            if item is not None:
                value = float(row["gpu_utilization_percent"])
                item["gpu"].append(value)
                gpu.append((ts, value))
    audit_path = arm / "server/runtime_audit.jsonl"
    digest, read = hashlib.sha256(), 0
    with audit_path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            digest.update(line)
            read += len(line)
            if b'"prefill_turnover_sample"' in line:
                row = json.loads(line)
                item = bucket(row["launch_monotonic_ms"] + offset)
                if item is None:
                    continue
                item["sampled_prefills"] += 1
                for name, phase in row.get("phases", {}).items():
                    if name == "run_batch":
                        item["sampled_forward_thread_cpu_ms"] += phase.get("thread_cpu_ms", 0.)
                    elif name in (
                        "selection", "war_barrier_and_queue_publish",
                        "post_launch_physical", "result_processing",
                    ):
                        item["sampled_control_thread_cpu_ms"] += phase.get("thread_cpu_ms", 0.)
            elif b'"gpu_service_sample"' in line and (
                b'"phase":"prefill"' in line or b'"phase": "prefill"' in line
            ):
                row = json.loads(line)
                item = bucket(row["complete_ts_ms"])
                if item is not None:
                    item["native_prefills"] += 1
    for row in records(arm / "server/transfer_telemetry.jsonl"):
        item = bucket(row["submit_ts_ms"])
        if item is not None:
            item[f'{row["direction"]}_bytes'] += row["actual_bytes"]
    rows = []
    for index, item in sorted(bins.items()):
        values = item.pop("gpu")
        samples = item["sampled_prefills"]
        rows.append({
            "start_seconds": begin + index * bin_seconds,
            "end_seconds": min(end, begin + (index + 1) * bin_seconds),
            **item,
            "gpu_sample_count": len(values),
            "gpu_utilization_mean": mean(values) if values else None,
            "sampled_control_cpu_per_prefill_ms": (
                item["sampled_control_thread_cpu_ms"] / samples if samples else None
            ),
        })
    values = [value for _, value in gpu]
    return {
        "arm": str(arm.resolve()), "window_seconds": [begin, end],
        "gpu_sample_count": len(values),
        "gpu_utilization_mean": mean(values) if values else None,
        "gpu_utilization_stddev": pstdev(values) if values else None,
        "gpu_utilization_below_80_fraction": (
            sum(value < 80 for value in values) / len(values) if values else None
        ),
        "adjacent_utilization_change_mean": (
            mean(abs(a[1] - b[1]) for a, b in zip(gpu, gpu[1:])) if len(gpu) > 1 else None
        ),
        "bin_correlations_with_gpu_utilization": {
            name: correlation((row["gpu_utilization_mean"], row[name]) for row in rows)
            for name in (
                "native_prefills", "sampled_control_cpu_per_prefill_ms",
                "sampled_control_thread_cpu_ms", "h2d_bytes", "d2h_bytes",
            )
        },
        "audit_prefix": {"bytes": read, "sha256": digest.hexdigest()},
        "clock_alignment": clock,
        "bins": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True)
    parser.add_argument("--timeline", action="append", required=True)
    parser.add_argument("--begin", type=float, default=600.)
    parser.add_argument("--end", type=float, default=2400.)
    parser.add_argument("--bin-seconds", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    arms, timelines = (
        dict(item.split("=", 1) for item in items) for items in (args.arm, args.timeline)
    )
    if set(arms) != set(timelines) or not 0 <= args.begin < args.end or args.bin_seconds <= 0:
        parser.error("matching arms/timelines, ordered window and positive bins required")
    if args.output.exists():
        parser.error("preserve the previous analysis")
    result = {
        "scope": "tail-free descriptive association; no causal attribution",
        "runs": {
            name: analyze(
                Path(path), Path(timelines[name]), begin=args.begin, end=args.end,
                bin_seconds=args.bin_seconds,
            ) for name, path in arms.items()
        },
        "limitations": [
            "Sampled prefill CPU observations are not extrapolated to all batches.",
            "launch_telemetry is nested in run_batch and is not added to control cost.",
            "Concurrent CPU, GPU and DMA intervals cannot be added or subtracted from JCT.",
            "Correlations share load and work-geometry confounders; they do not establish a cause.",
            "Low-utilization samples alone do not identify Python, GIL or transfer stalls.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {key: value for key, value in run.items() if key not in (
            "bins", "clock_alignment", "audit_prefix",
        )} for name, run in result["runs"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
