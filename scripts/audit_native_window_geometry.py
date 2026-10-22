#!/usr/bin/env python3
"""Compare native service geometry and measured writer samples on verified clocks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_busy_context_control import decode_geometry
from scripts.audit_native_system_costs import finite_number


def balanced_prefill(left, right, *, minimum_batches, short_only):
    rows = []
    for name in sorted(left.keys() & right.keys()):
        before, after = left[name], right[name]
        if before["batches"] < minimum_batches or after["batches"] < minimum_batches:
            continue
        if before.get("graph") not in ("true", "false"):
            continue
        if any(before.get(field) != after.get(field) for field in (
            "batch_size", "extend_bin_start", "extend_bin_tokens", "graph",
        )):
            raise ValueError("matching prefill key has inconsistent geometry")
        if short_only and not (
            before["batch_size"] == 1
            and before["extend_bin_start"] + before["extend_bin_tokens"] <= 512
        ):
            continue
        weight = min(before["batches"], after["batches"])
        rows.append((before["mean_ms"], after["mean_ms"], weight))
    weight = sum(row[2] for row in rows)
    before = sum(row[0] * row[2] for row in rows) / weight if weight else None
    after = sum(row[1] * row[2] for row in rows) / weight if weight else None
    return {
        "minimum_batches_per_arm": minimum_batches, "cohorts": len(rows),
        "common_batch_weight": weight,
        "left_worker_mean_ms": before, "right_worker_mean_ms": after,
        "left_to_right_ratio": before / after if after else None,
    }


def audit(basis, start, end, *, prefill_work_bin=512, minimum_batches=32):
    if not (
        finite_number(start) and finite_number(end) and 0 <= start < end
        and isinstance(basis.get("runs"), dict) and basis["runs"]
    ):
        raise ValueError("nonempty arms and a finite increasing window are required")
    if any(type(value) is not int or value < 1 for value in (prefill_work_bin, minimum_batches)):
        raise ValueError("positive integer bin width and support required")
    windows = {}
    for name, arm in basis["runs"].items():
        selected = [
            window for window in arm["windows"]
            if window["start_seconds"] == start and window["end_seconds"] == end
        ]
        alignment = arm["clock_alignment"]
        anchor = alignment.get("anchor_unix_ms")
        if (
            len(selected) != 1 or selected[0].get("service_window_observed_complete") is not True
            or alignment.get("native_client_clock_validated") is not True
            or not finite_number(anchor)
        ):
            raise ValueError("one complete window and a validated native/client clock are required")
        windows[name] = (Path(arm["run"]), anchor + 1000 * start, anchor + 1000 * end)
    arms = {
        name: {
            **decode_geometry(
                path, begin, finish,
                **({"prefill_work_bin": prefill_work_bin} if prefill_work_bin != 512 else {}),
            ),
            "monitor_window": {
                "begin_ts_ms": begin, "end_ts_ms": finish,
                "clock_source": "validated_clock_report",
            },
        }
        for name, (path, begin, finish) in windows.items()
    }
    comparisons = []
    for left, right in combinations(arms, 2):
        matched = {}
        for name in (
            "cohorts", "cuda_graph_cohorts", "prefill_geometry",
            "prefill_graph_cohorts", "worker_interval_neighbors",
        ):
            if name == "prefill_graph_cohorts":
                before, after = (
                    arms[side]["prefill_geometry"].get("graph_cohorts", {})
                    for side in (left, right)
                )
            else:
                before, after = arms[left][name], arms[right][name]
            if name in ("prefill_geometry", "worker_interval_neighbors"):
                before, after = before.get("cohorts", {}), after.get("cohorts", {})
            matched[name] = {
                key: {
                    "left": before[key], "right": after[key],
                    "left_to_right_worker_mean_ratio": before[key]["mean_ms"] / after[key]["mean_ms"],
                }
                for key in sorted(before.keys() & after.keys())
                if before[key]["batches"] >= minimum_batches and after[key]["batches"] >= minimum_batches
                and after[key]["mean_ms"] > 0
            }
        comparisons.append({
            "left": left, "right": right, "matched": matched,
            "balanced_prefill": {
                population: [
                    balanced_prefill(
                        arms[left]["prefill_geometry"].get("graph_cohorts", {}),
                        arms[right]["prefill_geometry"].get("graph_cohorts", {}),
                        minimum_batches=support, short_only=short,
                    ) for support in (1, 2, 4, 8, 16, 32)
                ]
                for population, short in (("all_prefill", False), ("short_single_request", True))
            },
        })
    return {"arms": arms, "comparisons": comparisons}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-report", type=Path, required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--prefill-work-bin", type=int, default=512)
    parser.add_argument("--minimum-batches", type=int, default=32)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing evidence; choose a fresh output")
    raw = args.clock_report.read_bytes()
    result = audit(
        json.loads(raw), args.start_seconds, args.end_seconds,
        prefill_work_bin=args.prefill_work_bin, minimum_batches=args.minimum_batches,
    )
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "start_seconds": args.start_seconds, "end_seconds": args.end_seconds,
        "prefill_work_bin": args.prefill_work_bin,
        "minimum_batches": args.minimum_batches,
        "clock_source": {
            "path": str(args.clock_report.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
        },
        **result,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/analyze_busy_context_control.py",
                ROOT / "scripts/audit_native_system_costs.py",
            )
        },
        "semantics": [
            f"All arms and geometry bins remain. Paired descriptive bins require at least {args.minimum_batches} batches per side; this is not identical work or a causal cohort.",
            "Service geometry uses completed worker intervals, not CUDA kernel timing. Each source records the exact consumed raw prefix and hash.",
            "Balanced summaries weight both means by the same min(left_count,right_count) per common known-graph cohort, with all support thresholds retained.",
            "Short single-request balance includes only bins entirely below 512 actual extend tokens. Its population is separate from all-prefill balance.",
            "Writer processing samples use their own snapshot clocks. Sampled CPU/wall time is not extrapolated to all records, GPU stalls or removable JCT.",
            "This observer only reads existing files; it changes no live application, engine, telemetry, tasks or policy.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {name: {
            "decode_batches": arm["decode_batches"],
            "mean_context_tokens": arm["sequence_tokens_per_request_mean"],
            "mean_worker_ms": arm["worker_decode_duration_mean_ms"],
            "writer_sampled_cpu_ms": arm["background_writer_timing"].get("sampled_cpu_ms"),
        } for name, arm in result["arms"].items()},
    }, indent=2))


if __name__ == "__main__":
    main()
