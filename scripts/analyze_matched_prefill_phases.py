#!/usr/bin/env python3
"""Compare sampled prefill phases within common work/neighbor cohorts."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import mean


PHASES = (
    "selection", "run_batch", "launch_telemetry",
    "war_barrier_and_queue_publish", "post_launch_physical", "result_processing",
)
METRICS = tuple(
    f"{phase}.{clock}" for phase in PHASES for clock in ("wall_ms", "thread_cpu_ms")
) + (
    "gpu_run_batch_span_ms", "launch_to_processed_completion_ms",
    "gpu_layer_dependency_wait_ms",
)


def metric(row, name):
    if "." in name:
        phase, clock = name.split(".")
        value = row["phases"].get(phase, {}).get(clock)
    else:
        value = row["metrics"].get(name)
    if value is not None and (
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
    ):
        raise ValueError(f"invalid phase timing: {name}={value!r}")
    return value


def geometry(rows):
    return {
        field: {
            "min": min(row[field] for row in rows),
            "mean": mean(row[field] for row in rows),
            "max": max(row[field] for row in rows),
        }
        for field in ("extend_tokens", "prefix_tokens")
    }


def groups(arm, *, work_bin, prefix_bin, neighbors):
    turnover = arm.get("turnover", arm)
    short = arm.get("short_prefill", {}).get("samples", ())
    neighbor_index = {}
    for row in short:
        if row["sample_id"] in neighbor_index:
            raise ValueError("duplicate short-prefill service identity")
        neighbor_index[row["sample_id"]] = row
    grouped, excluded, seen = defaultdict(list), defaultdict(int), set()
    for row in turnover.get("samples", ()):
        identity = row["service_sample_id"]
        if identity in seen:
            raise ValueError("duplicate turnover service identity")
        seen.add(identity)
        size, work, prefix = row["batch_size"], row["extend_tokens"], row["prefix_tokens"]
        if size != 1 or type(work) is not int or not 0 <= work < 512:
            excluded["not_short_single_request"] += 1
            continue
        if type(prefix) is not int or prefix < 0:
            raise ValueError("missing or invalid native prefix geometry")
        key = (work // work_bin * work_bin, prefix // prefix_bin * prefix_bin,
               row["graph"], row["loop"])
        if neighbors:
            adjacent = neighbor_index.get(identity)
            if adjacent is None:
                excluded["neighbor_evidence_missing"] += 1
                continue
            if (adjacent["extend_tokens"], adjacent["prefix_tokens"]) != (work, prefix):
                raise ValueError("turnover/service work geometry disagrees")
            key += tuple(adjacent[field] for field in (
                "previous_phase", "previous_batch_size", "next_phase", "next_batch_size",
            ))
        grouped[key].append(row)
    return grouped, dict(excluded)


def compare(left, right, *, work_bin=64, prefix_bin=2048, minimum=3, neighbors=False):
    if min(work_bin, prefix_bin, minimum) < 1:
        raise ValueError("positive bin widths and minimum required")
    a, excluded_a = groups(left, work_bin=work_bin, prefix_bin=prefix_bin, neighbors=neighbors)
    b, excluded_b = groups(right, work_bin=work_bin, prefix_bin=prefix_bin, neighbors=neighbors)
    common = sorted([
        key for key in a.keys() & b.keys() if min(len(a[key]), len(b[key])) >= minimum
    ], key=json.dumps)
    comparisons = {}
    for name in METRICS:
        cohorts = []
        for key in common:
            left_points = [(row, value) for row in a[key] if (value := metric(row, name)) is not None]
            right_points = [(row, value) for row in b[key] if (value := metric(row, name)) is not None]
            left_values, right_values = (
                [value for _, value in points] for points in (left_points, right_points)
            )
            if min(len(left_values), len(right_values)) < minimum:
                continue
            cohorts.append({
                "group": key, "weight": min(len(left_values), len(right_values)),
                "left_count": len(left_values), "right_count": len(right_values),
                "left_mean_ms": mean(left_values), "right_mean_ms": mean(right_values),
                "actual_geometry": {
                    side: geometry([row for row, _ in points])
                    for side, points in (("left", left_points), ("right", right_points))
                },
            })
        weight = sum(row["weight"] for row in cohorts)
        values = {
            side: sum(row["weight"] * row[f"{side}_mean_ms"] for row in cohorts) / weight
            if weight else None for side in ("left", "right")
        }
        comparisons[name] = {
            **values, "weight_sum": weight, "cohort_count": len(cohorts),
            "ratio": values["left"] / values["right"]
            if weight and values["right"] > 0 else None,
            "cohorts": sorted(cohorts, key=lambda row: json.dumps(row["group"])),
        }
    return {
        "work_bin": work_bin, "prefix_bin": prefix_bin, "minimum_per_arm": minimum,
        "match_neighbors": neighbors, "geometry_cohort_count": len(common),
        "candidate_short_samples": {"left": sum(map(len, a.values())), "right": sum(map(len, b.values()))},
        "matched_geometry_samples": {
            "left": sum(len(a[key]) for key in common),
            "right": sum(len(b[key]) for key in common),
        },
        "excluded": {"left": excluded_a, "right": excluded_b},
        "metrics": comparisons,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-report", type=Path, required=True)
    parser.add_argument("--left", default="predictive_h2d")
    parser.add_argument("--right", default="native")
    parser.add_argument("--minimum", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous phase evidence")
    raw = args.phase_report.read_bytes()
    arms = json.loads(raw)["arms"]
    result = {
        "source_report": str(args.phase_report.resolve()),
        "source_report_sha256": hashlib.sha256(raw).hexdigest(),
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "left": args.left, "right": args.right,
        "cases": [
            compare(arms[args.left], arms[args.right], work_bin=width,
                    minimum=args.minimum, neighbors=neighbors)
            for width in (512, 64, 16) for neighbors in (False, True)
        ],
        "performance_accepted": False,
        "semantics": [
            "Compare only sampled short single-request prefills in common geometry cohorts.",
            "Each timing metric has its own paired coverage and minimum cohort count.",
            "Actual input/prefix ranges and means remain visible within every timing cohort.",
            "Missing timings or unsupported bins remain unknown, never zero.",
            "Phase intervals overlap; launch_telemetry is nested within run_batch.",
            "Thread CPU includes native runtime work and possible busy waiting, not just Python.",
            "GPU run spans include submission gaps/dependencies and are not pure kernels.",
            "Historical traces and coarse bins do not establish causal system or JCT gains.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps([{
        "work_bin": row["work_bin"], "neighbors": row["match_neighbors"],
        "cohorts": row["geometry_cohort_count"],
        "matched_samples": row["matched_geometry_samples"],
        "run_batch": {key: value for key, value in row["metrics"]["run_batch.thread_cpu_ms"].items()
                      if key != "cohorts"},
    } for row in result["cases"]], indent=2))


if __name__ == "__main__":
    main()
