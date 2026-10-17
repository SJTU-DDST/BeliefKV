#!/usr/bin/env python3
"""Relate sampled selection intervals to recorded physical transfer receipts."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def bound_json(path, expected=None):
    path = Path(path)
    raw = path.read_bytes()
    binding = {
        "path": str(path), "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if expected is not None and binding["sha256"] != expected:
        raise ValueError(f"input digest changed: {path}")
    return json.loads(raw), binding


def selection_transfers(samples, classifications, transfers, offset):
    if not finite_number(offset):
        raise ValueError("unverified native clock offset")
    indexed, seen = defaultdict(list), set()
    for transfer in transfers:
        if transfer.get("status") != "completed":
            continue
        direction = transfer.get("direction")
        if direction not in ("h2d", "d2h"):
            continue
        identity = transfer.get("command_id")
        if type(identity) is not str or not identity or (direction, identity) in seen:
            raise ValueError("missing or duplicate physical transfer identity")
        seen.add((direction, identity))
        start, finish = transfer.get("submit_ts_ms"), transfer.get("complete_ts_ms")
        if (
            not finite_number(start) or not finite_number(finish)
            or finish < start
            or type(transfer.get("actual_bytes")) is not int
            or transfer["actual_bytes"] < 0
        ):
            raise ValueError("invalid physical transfer interval or size")
        indexed[direction].append(transfer)
    for rows in indexed.values():
        rows.sort(key=lambda row: row["submit_ts_ms"])
    starts = {
        direction: [row["submit_ts_ms"] for row in rows]
        for direction, rows in indexed.items()
    }
    joined = []
    for sample in samples:
        ids = sample.get("request_ids")
        if sample["batch_size"] != 1 or not 0 <= sample["extend_tokens"] < 512:
            continue
        if not isinstance(ids, (tuple, list)) or len(ids) != 1:
            raise ValueError("invalid short-prefill identity")
        phase = sample["phases"].get("selection")
        if phase is None:
            joined.append({"request_id": ids[0], "status": "selection_unobserved"})
            continue
        begin = phase["start_monotonic_ms"] + offset
        end = phase["end_monotonic_ms"] + offset
        if not finite_number(begin) or not finite_number(end) or end < begin:
            raise ValueError("invalid selection interval")
        directions = {}
        for direction in ("d2h", "h2d"):
            rows = indexed.get(direction, [])
            positions = starts.get(direction, [])
            selected = rows[bisect_left(positions, begin):bisect_left(positions, end)]
            directions[direction] = {
                "submitted_batches": len(selected),
                "completed_by_selection_end": sum(
                    row["complete_ts_ms"] <= end for row in selected
                ),
                "actual_bytes": sum(row["actual_bytes"] for row in selected),
                "recorded_node_count": sum(len(row.get("node_ids") or ()) for row in selected),
                "multi_node_batches": sum(len(row.get("node_ids") or ()) > 1 for row in selected),
                "measured_stream_ms": sum(
                    row["transfer_stream_elapsed_ms"] for row in selected
                    if finite_number(row.get("transfer_stream_elapsed_ms"))
                ),
                "unmeasured_stream_batches": sum(
                    not finite_number(row.get("transfer_stream_elapsed_ms")) for row in selected
                ),
                "command_ids": [row["command_id"] for row in selected],
            }
        joined.append({
            "request_id": ids[0],
            "forward_iter": sample["forward_iter"],
            "status": classifications.get(ids[0], "host_counters_unobserved"),
            "selection_wall_ms": phase["wall_ms"],
            "selection_thread_cpu_ms": phase["thread_cpu_ms"],
            "selection_wall_interval_ms": [begin, end],
            "transfers_submitted_during_selection": directions,
        })
    grouped = defaultdict(list)
    for row in joined:
        grouped[row["status"]].append(row)
    summaries = {}
    for status, rows in grouped.items():
        observed = [row for row in rows if "transfers_submitted_during_selection" in row]
        summary = {
            "samples": len(rows), "selection_observed_samples": len(observed),
            "selection_wall_ms": distribution(row["selection_wall_ms"] for row in observed),
            "selection_thread_cpu_ms": distribution(
                row["selection_thread_cpu_ms"] for row in observed
            ),
        }
        for direction in ("d2h", "h2d"):
            values = [
                row["transfers_submitted_during_selection"][direction] for row in observed
            ]
            summary[direction] = {
                key: sum(value[key] for value in values)
                for key in (
                    "submitted_batches", "completed_by_selection_end", "actual_bytes",
                    "recorded_node_count", "multi_node_batches",
                    "measured_stream_ms", "unmeasured_stream_batches",
                )
            }
            summary[direction]["samples_with_submission"] = sum(
                value["submitted_batches"] > 0 for value in values
            )
            summary[direction]["batches_per_sample"] = distribution(
                value["submitted_batches"] for value in values
            )
        summaries[status] = summary
    return {"by_host_counter": summaries, "rows": joined}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output exists; choose a fresh report ID")
    phases, phase_binding = bound_json(args.phase_report)
    source = phases["pressure_source"]
    pressure, pressure_binding = bound_json(source["path"], source["sha256"])
    source = pressure["window_source"]
    windows, window_binding = bound_json(source["path"], source["sha256"])
    arms = {}
    for name, arm in phases["arms"].items():
        alignment = windows["runs"][name]["clock_alignment"]
        if alignment.get("native_client_clock_validated") is not True:
            raise ValueError("unverified native/client clock")
        sealed = SealedRecords()
        classifications = {
            row["request_id"]: row["status"]
            for row in arm["request_restore_association"]["joined_rows"]
        }
        result = selection_transfers(
            arm["phases"]["samples"], classifications,
            sealed(Path(pressure["arms"][name]["run"]) / "server/transfer_telemetry.jsonl"),
            alignment["native_wall_minus_monotonic_ms"],
        )
        arms[name] = {**result, "transfer_prefixes": sealed.prefixes}
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [phase_binding, pressure_binding, window_binding], "arms": arms,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/audit_sealed_wait_prefetch.py",
                ROOT / "scripts/audit_native_system_costs.py",
            )
        },
        "semantics": [
            "Existing sampled native selection intervals are converted with verified native wall-minus-monotonic offsets; no cross-domain nearest-event fallback.",
            "Physical receipts are uniquely identified by direction and command ID. Submission membership is half-open; completion by interval end is reported separately.",
            "Temporal overlap does not prove which eviction or request caused a transfer, blocking call duration, or removable JCT.",
            "Stream intervals, selection wall and thread CPU overlap. They are not additive, and samples are not extrapolated to all requests.",
            "Only completed receipts in a stat-bounded complete-line prefix are credited. Unknown durations stay explicitly unmeasured; cross-file reads are not atomic.",
            "Native H2D may be submitted after selection ends. No submission during selection is not evidence that the request needs no H2D.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {name: arm["by_host_counter"] for name, arm in arms.items()},
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
