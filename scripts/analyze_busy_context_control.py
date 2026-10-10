#!/usr/bin/env python3
"""Compare visible decode geometry and runtime control cost in a matched window."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo


def monitor_window(arm: Path, start: float, end: float, timezone: ZoneInfo) -> dict:
    paths = list(arm.glob("client_*/gpu_samples.csv"))
    if len(paths) != 1:
        raise ValueError(f"expected one GPU monitor in {arm}")
    path = paths[0]
    with path.open() as stream:
        rows = csv.DictReader(stream)
        first = next(rows)

        def timestamp(row):
            return datetime.strptime(
                row["timestamp"].strip(), "%Y/%m/%d %H:%M:%S.%f",
            ).replace(tzinfo=timezone).timestamp() * 1000.

        origin = timestamp(first)
        coverage = origin
        for row in rows:
            coverage = timestamp(row)
            if coverage >= origin + end * 1000.:
                break
    if coverage < origin + end * 1000.:
        raise ValueError(f"GPU monitor has not covered the requested window: {path}")
    return {
        "source": str(path), "first_row": first, "origin_ts_ms": origin,
        "begin_ts_ms": origin + start * 1000., "end_ts_ms": origin + end * 1000.,
        "observed_through_ts_ms": coverage, "timezone": str(timezone),
    }


def decode_geometry(arm: Path, begin: float, end: float) -> dict:
    path = arm / "server/runtime_audit.jsonl"
    contexts, sums, durations, batches = [], [], [], []
    groups = defaultdict(list)
    prefix, bytes_read = hashlib.sha256(), 0
    covered = False
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            prefix.update(line)
            bytes_read += len(line)
            if b'"gpu_service_sample"' not in line:
                continue
            row = json.loads(line)
            if row["complete_ts_ms"] >= end:
                covered = True
                break
            if row["complete_ts_ms"] < begin or row["phase"] != "decode":
                continue
            observed = [
                item["sequence_tokens_before"] for item in row["request_samples"]
                if type(item.get("sequence_tokens_before")) is int
            ]
            if len(observed) != row["batch_size"]:
                raise ValueError("incomplete visible decode geometry")
            duration = row["service_elapsed_ms"]
            contexts.extend(observed)
            sums.append(sum(observed))
            durations.append(duration)
            average = mean(observed)
            groups[(row["batch_size"], int(average) // 2048)].append(duration)
            batches.append((average, duration))
    if not covered or not batches:
        raise ValueError(f"runtime samples have not covered the requested window: {path}")
    return {
        "source": str(path), "read_prefix_bytes": bytes_read,
        "read_prefix_sha256": prefix.hexdigest(),
        "decode_batches": len(batches), "observed_request_steps": len(contexts),
        "sequence_tokens_per_request_mean": mean(contexts),
        "sequence_tokens_per_request_median": median(contexts),
        "sequence_tokens_per_batch_sum_mean": mean(sums),
        "worker_decode_duration_mean_ms": mean(durations),
        "worker_decode_duration_median_ms": median(durations),
        "time_weighted_mean_request_sequence_tokens": (
            sum(average * duration for average, duration in batches) / sum(durations)
        ),
        "cohorts": {
            f"{size}/{bucket * 2048}": {
                "batches": len(values), "mean_ms": mean(values), "median_ms": median(values),
            } for (size, bucket), values in sorted(groups.items())
        },
    }


def control_profile(arm: Path, begin: float, end: float) -> dict:
    path = arm / "opportunities/admission_opportunities.jsonl"
    boundary, selected_lines = [None, None], [None, None]
    prefix, bytes_read = hashlib.sha256(), 0
    covered = False
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            prefix.update(line)
            bytes_read += len(line)
            if b'"admission_runtime_state"' not in line:
                continue
            state = json.loads(line)
            if state["ts_ms"] > end:
                covered = True
                break
            index = 0 if state["ts_ms"] <= begin else 1
            boundary[index], selected_lines[index] = state, line
    if not covered or any(state is None for state in boundary):
        raise ValueError(f"control profile has not covered the requested window: {path}")
    phases = {}
    for phase, upper in boundary[1]["shared_path_timing"]["phases"].items():
        lower = boundary[0]["shared_path_timing"]["phases"].get(phase, {})
        phases[phase] = {
            metric: upper[metric] - lower.get(metric, 0)
            for metric in ("count", "self_ms", "total_ms")
        }
    return {
        "source": str(path), "read_prefix_bytes": bytes_read,
        "read_prefix_sha256": prefix.hexdigest(),
        "boundary_state_ts_ms": [state["ts_ms"] for state in boundary],
        "raw_boundary_line_sha256": [hashlib.sha256(line).hexdigest() for line in selected_lines],
        "observed_duration_ms": boundary[1]["ts_ms"] - boundary[0]["ts_ms"],
        "summed_python_self_ms": sum(item["self_ms"] for item in phases.values()),
        "phases": dict(sorted(phases.items(), key=lambda item: item[1]["self_ms"], reverse=True)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-seconds", type=float, default=600.)
    parser.add_argument("--end-seconds", type=float, default=900.)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    args = parser.parse_args()
    if not 0 <= args.start_seconds < args.end_seconds:
        parser.error("window must satisfy 0 <= start < end")
    plan = json.loads((args.run_root / "ab_plan.json").read_text())
    report = {
        "scope": "Complete observed window; not identical-demand causality or completed-pair JCT",
        "code_commit": plan["code_commit"], "patch_sha256": plan["sglang_patch_sha256"],
        "start_seconds": args.start_seconds, "end_seconds": args.end_seconds, "arms": {},
        "semantics": [
            "Context is input plus generated tokens visible before each decode batch.",
            "Only batches completed in the half-open window are counted; intervals are not clipped.",
            "Cohorts match batch size and a 2048-token bin of batch-mean context, not exact demand.",
            "Worker intervals include scheduler processing and are not CUDA kernel timings.",
            "Control counters use preceding snapshots; exclusive Python wall time is not removable JCT.",
        ],
    }
    for name in ("predictive_h2d", "native"):
        arm = args.run_root / name
        window = monitor_window(
            arm, args.start_seconds, args.end_seconds, ZoneInfo(args.timezone),
        )
        values = decode_geometry(arm, window["begin_ts_ms"], window["end_ts_ms"])
        values["monitor_window"] = window
        if name == "predictive_h2d":
            values["control_profile"] = control_profile(
                arm, window["begin_ts_ms"], window["end_ts_ms"],
            )
        report["arms"][name] = values
    left, right = (report["arms"][name]["cohorts"] for name in ("predictive_h2d", "native"))
    report["matched_cohorts"] = {
        key: {
            "predictive": left[key], "native": right[key],
            "predictive_mean_ratio": left[key]["mean_ms"] / right[key]["mean_ms"],
        } for key in sorted(left.keys() & right.keys())
        if left[key]["batches"] >= 32 and right[key]["batches"] >= 32
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {
            "decode_batches": values["decode_batches"],
            "mean_visible_context": values["sequence_tokens_per_request_mean"],
            "mean_worker_interval_ms": values["worker_decode_duration_mean_ms"],
        } for name, values in report["arms"].items()
    }, indent=2))


if __name__ == "__main__":
    main()
