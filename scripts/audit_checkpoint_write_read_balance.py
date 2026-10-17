#!/usr/bin/env python3
"""Bound completed-restoration-backed writes from physical per-pool copy units."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_native_system_costs import finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def copy_balance(records, begin, end, bytes_per_unit):
    if not (
        finite_number(begin) and finite_number(end) and begin < end
        and bytes_per_unit.keys() == {"kv", "mamba"}
        and all(type(value) is int and value > 0 for value in bytes_per_unit.values())
    ):
        raise ValueError("invalid cohort or unsupported pool sizing")
    units = {
        direction: defaultdict(int) for direction in (
            "cohort_d2h", "all_observed_d2h", "all_observed_h2d",
        )
    }
    batch_counts = {key: 0 for key in units}
    actual_bytes = {key: 0 for key in units}
    stream_ms = {key: 0.0 for key in units}
    unknown_stream = {key: 0 for key in units}
    seen, ignored, observed_through = set(), 0, None
    for row in records:
        if row.get("status") != "completed":
            ignored += 1
            continue
        direction = row.get("direction")
        identity = row.get("command_id")
        if direction not in ("d2h", "h2d"):
            raise ValueError("unknown transfer direction")
        if (
            type(identity) is not str or not identity
            or (direction, identity) in seen
        ):
            raise ValueError("missing or duplicate physical receipt identity")
        seen.add((direction, identity))
        start, complete = row.get("submit_ts_ms"), row.get("complete_ts_ms")
        if (
            not finite_number(start) or not finite_number(complete)
            or complete < start
        ):
            raise ValueError("invalid transfer timestamps")
        observed_through = max(complete, observed_through) if observed_through is not None else complete
        counts = row.get("num_tokens_by_pool")
        if not isinstance(counts, dict) or counts.keys() - bytes_per_unit.keys():
            raise ValueError("unsupported or missing pool counters")
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError("invalid physical copy units")
        size = row.get("actual_bytes")
        if (
            type(size) is not int
            or size != sum(count * bytes_per_unit[pool] for pool, count in counts.items())
        ):
            raise ValueError("physical bytes disagree with per-pool units")
        selected = [f"all_observed_{direction}"]
        if direction == "d2h" and begin <= start < end:
            selected.append("cohort_d2h")
        elapsed = row.get("transfer_stream_elapsed_ms")
        measured = finite_number(elapsed) and elapsed >= 0
        for key in selected:
            batch_counts[key] += 1
            actual_bytes[key] += size
            for pool, count in counts.items():
                units[key][pool] += count
            if measured:
                stream_ms[key] += elapsed
            else:
                unknown_stream[key] += 1
    if observed_through is None or observed_through < end:
        raise ValueError("receipt prefix does not cover the write cohort")
    totals = {
        key: {
            "batches": batch_counts[key],
            "actual_bytes": actual_bytes[key],
            "copy_units": dict(units[key]),
            "bytes_by_pool": {
                pool: units[key][pool] * size for pool, size in bytes_per_unit.items()
            },
            "measured_stream_ms": stream_ms[key],
            "unmeasured_stream_batches": unknown_stream[key],
        }
        for key in units
    }
    bounds = {}
    for pool in bytes_per_unit:
        writes, reads = units["cohort_d2h"][pool], units["all_observed_h2d"][pool]
        bounds[pool] = {
            "cohort_completed_write_copy_units": writes,
            "all_observed_completed_read_copy_units": reads,
            "maximum_write_copy_units_that_can_back_completed_reads": min(writes, reads),
            "maximum_fraction_that_can_back_completed_reads": min(1.0, reads / writes) if writes else None,
            "minimum_write_copy_units_not_needed_as_distinct_sources_of_completed_reads": max(0, writes - reads),
            "read_copy_units_per_cohort_write_copy_unit": reads / writes if writes else None,
        }
    return {
        "cohort_submit_window_ms": [begin, end],
        "receipt_observed_through_ts_ms": observed_through,
        "non_completed_records_not_credited": ignored,
        "totals": totals, "completed_read_source_bounds": bounds,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-report", type=Path, required=True)
    parser.add_argument("--arm", action="append", required=True)
    parser.add_argument("--write-start-seconds", type=float, default=0.0)
    parser.add_argument("--write-end-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or len(set(args.arm)) != len(args.arm):
        parser.error("fresh output and distinct arm names are required")
    raw = args.clock_report.read_bytes()
    basis, arms = json.loads(raw), {}
    for name in args.arm:
        run = basis["runs"][name]
        alignment = run["clock_alignment"]
        if alignment.get("native_client_clock_validated") is not True:
            raise ValueError("unverified source clocks")
        sizing = [row["pool_bytes_per_unit"] for row in run["windows"]]
        if not sizing or any(value != sizing[0] for value in sizing):
            raise ValueError("unknown or changing physical pool sizing")
        anchor = alignment["anchor_unix_ms"]
        sealed = SealedRecords()
        result = copy_balance(
            sealed(Path(run["run"]) / "server/transfer_telemetry.jsonl"),
            anchor + args.write_start_seconds * 1000,
            anchor + args.write_end_seconds * 1000,
            sizing[0],
        )
        arms[name] = {
            **result, "run": run["run"], "pool_bytes_per_unit": sizing[0],
            "input_jsonl_prefixes": sealed.prefixes,
        }
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "clock_source": {
            "path": str(args.clock_report), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "write_start_seconds": args.write_start_seconds,
        "write_end_seconds": args.write_end_seconds, "arms": arms,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/audit_native_system_costs.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "bound_assumptions": [
            "Each reported D2H unit writes a complete immutable KV cell or checkpoint state copy; a later Host read uses one version of a source cell, not several overwritten copies at once.",
            "H2D copy units include every reported destination copy, including duplicate source state copies for cache and request CoW. They overcount distinct read sources, making the source bound conservative.",
            "All H2D receipts in the observed prefix are allowed as potential reads, even those preceding or outside the write cohort. No node-ID or physical-generation association is assumed.",
        ],
        "semantics": [
            "An upper bound on distinct completed D2H copies needed as sources of observed completed restorations, not a measured cache-hit rate, exact reuse identity or online-safe discard set.",
            "Uncompleted receipts, ongoing partial-layer consumption and future restores after the observed prefix are not credited. The bound is expressly limited to completed-read sources.",
            "A low bound does not prove a write lacked capacity-release or protection value, nor that an online policy can safely predict which writes to decline.",
            "Bytes and transfer-stream sums do not identify exposed GPU stalls or removable JCT. Runtime quality, recomputation and end-to-end effects need a separate matched experiment.",
            "One stat-bounded complete-line transfer prefix per arm; cross-arm observation horizons differ and this is not a causal performance comparison.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {
            name: {
                "totals": arm["totals"],
                "completed_read_source_bounds": arm["completed_read_source_bounds"],
            }
            for name, arm in arms.items()
        },
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
