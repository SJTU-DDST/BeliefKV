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


def audit(basis, start, end):
    if not (
        finite_number(start) and finite_number(end) and 0 <= start < end
        and isinstance(basis.get("runs"), dict) and basis["runs"]
    ):
        raise ValueError("nonempty arms and a finite increasing window are required")
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
            **decode_geometry(path, begin, finish),
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
        for name in ("cohorts", "cuda_graph_cohorts", "prefill_geometry", "worker_interval_neighbors"):
            before, after = arms[left][name], arms[right][name]
            if name in ("prefill_geometry", "worker_interval_neighbors"):
                before, after = before.get("cohorts", {}), after.get("cohorts", {})
            matched[name] = {
                key: {
                    "left": before[key], "right": after[key],
                    "left_to_right_worker_mean_ratio": before[key]["mean_ms"] / after[key]["mean_ms"],
                }
                for key in sorted(before.keys() & after.keys())
                if before[key]["batches"] >= 32 and after[key]["batches"] >= 32
                and after[key]["mean_ms"] > 0
            }
        comparisons.append({"left": left, "right": right, "matched": matched})
    return {"arms": arms, "comparisons": comparisons}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-report", type=Path, required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve existing evidence; choose a fresh output")
    raw = args.clock_report.read_bytes()
    result = audit(json.loads(raw), args.start_seconds, args.end_seconds)
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "start_seconds": args.start_seconds, "end_seconds": args.end_seconds,
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
            "All arms and geometry bins remain. Paired descriptive bins require at least 32 batches per side, matching the existing analyzer; this is not identical work or a causal cohort.",
            "Service geometry uses completed worker intervals, not CUDA kernel timing. Each source records the exact consumed raw prefix and hash.",
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
