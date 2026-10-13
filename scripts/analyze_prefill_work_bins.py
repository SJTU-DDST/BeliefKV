#!/usr/bin/env python3
"""Match actual short-prefill work without interpreting worker time as forward."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import mean


METRICS = (
    "worker_interval_ms", "launch_to_processed_completion_ms", "clipped_launch_overlap_ms",
)


def compare(arms, *, work_bin, prefix_bin, minimum, neighbors):
    grouped = {}
    for arm, data in arms.items():
        groups = defaultdict(list)
        seen = set()
        for row in data["samples"]:
            if row["sample_id"] in seen:
                raise ValueError("duplicate short-prefill sample")
            seen.add(row["sample_id"])
            group = (
                row["extend_tokens"] // work_bin * work_bin,
                row["prefix_tokens"] // prefix_bin * prefix_bin,
                row["graph"],
            )
            if neighbors:
                group += (
                    row["previous_phase"], row["previous_batch_size"],
                    row["next_phase"], row["next_batch_size"],
                )
            groups[group].append(row)
        grouped[arm] = groups
    predictive, native = grouped["predictive_h2d"], grouped["native"]
    cohorts = []
    used = {arm: 0 for arm in grouped}
    for group in predictive.keys() & native.keys():
        left, right = predictive[group], native[group]
        if min(len(left), len(right)) < minimum:
            continue
        cohorts.append({
            "group": group, "predictive_count": len(left), "native_count": len(right),
            "weight": min(len(left), len(right)),
            "metrics": {
                metric: {
                    "predictive_mean_ms": mean(row[metric] for row in left),
                    "native_mean_ms": mean(row[metric] for row in right),
                }
                for metric in METRICS
            },
        })
        used["predictive_h2d"] += len(left)
        used["native"] += len(right)
    weight = sum(row["weight"] for row in cohorts)
    aggregate = {}
    for metric in METRICS:
        values = {
            arm: sum(
                row["weight"] * row["metrics"][metric][f"{arm}_mean_ms"]
                for row in cohorts
            ) / weight if weight else None
            for arm in ("predictive", "native")
        }
        aggregate[metric] = {
            **values,
            "difference_ms": (
                values["predictive"] - values["native"] if weight else None
            ),
            "ratio": values["predictive"] / values["native"] if weight else None,
        }
    return {
        "work_bin": work_bin, "prefix_bin": prefix_bin, "minimum_per_arm": minimum,
        "match_neighbors": neighbors, "cohort_count": len(cohorts), "weight_sum": weight,
        "matched_samples": used,
        "unmatched_samples": {
            arm: len(arms[arm]["samples"]) - used[arm] for arm in used
        },
        "aggregate": aggregate,
        "cohorts": sorted(cohorts, key=lambda row: json.dumps(row["group"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--short-report", type=Path, required=True)
    parser.add_argument("--minimum", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.minimum < 1 or args.output.exists():
        raise ValueError("positive minimum and a new output path required")
    raw = args.short_report.read_bytes()
    source = json.loads(raw)
    results = [
        compare(source["arms"], work_bin=work, prefix_bin=2048,
                minimum=args.minimum, neighbors=neighbors)
        for work in (512, 16, 1) for neighbors in (False, True)
    ]
    report = {
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "short_report": str(args.short_report.resolve()),
        "short_report_sha256": hashlib.sha256(raw).hexdigest(),
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": results,
        "semantics": [
            "Read exact short-prefill samples from an already sealed busy-window report.",
            "A cohort has matching extend work, prefix bin and actual graph status.",
            "Neighbor matching also fixes previous/next observed phases and batch sizes.",
            "Both arm means use the minimum cohort count as a common descriptive weight.",
            "Unsupported or sparse cohorts stay unmatched; no missing work is filled with zero.",
            "Worker intervals are overlap-clipped; launch-to-completion includes result handling.",
            "This is descriptive cross-version matching, not causal speedup or full-forward timing.",
        ],
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps([{
        key: case[key] for key in (
            "work_bin", "match_neighbors", "cohort_count", "matched_samples", "aggregate",
        )
    } for case in results], indent=2))


if __name__ == "__main__":
    main()
