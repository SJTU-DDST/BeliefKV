#!/usr/bin/env python3
"""Count recorded runtime lifecycle actions in a verified complete service window."""

from __future__ import annotations

import argparse
from collections import Counter
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


def lifecycle_window(rows, begin, end):
    if not (finite_number(begin) and finite_number(end) and begin < end):
        raise ValueError("invalid lifecycle window")
    events, sources, freed = Counter(), Counter(), Counter()
    lower = upper = None
    through = None
    for row in rows:
        timestamp = row.get("ts_ms")
        if not finite_number(timestamp) or not isinstance(row.get("event"), str):
            raise ValueError("invalid lifecycle event identity or clock")
        if row["event"] == "admission_runtime_state":
            counts = row.get("counts")
            if not isinstance(counts, dict) or any(
                type(value) is not int or value < 0 for value in counts.values()
            ):
                raise ValueError("invalid cumulative runtime counters")
            through = timestamp if through is None else max(through, timestamp)
            if timestamp <= begin and (lower is None or timestamp > lower["ts_ms"]):
                lower = row
            if timestamp <= end and (upper is None or timestamp > upper["ts_ms"]):
                upper = row
        if not begin <= timestamp < end:
            continue
        events[row["event"]] += 1
        if row["event"] == "parent_pressure_demoted":
            units, source = row.get("freed_units"), row.get("source")
            if not isinstance(units, dict) or source not in ("join_wait", "tool_wait"):
                raise ValueError("invalid waiting demotion")
            for pool, count in units.items():
                if str(pool) not in ("0", "1", "2") or type(count) is not int or count < 0:
                    raise ValueError("invalid demotion component units")
                freed[str(pool)] += count
            sources[source] += 1
    if lower is None or upper is None or through is None or through < end:
        raise ValueError("runtime state does not cover the requested window")
    names = lower["counts"].keys() | upper["counts"].keys()
    deltas = {
        name: upper["counts"].get(name, 0) - lower["counts"].get(name, 0)
        for name in sorted(names)
    }
    if any(value < 0 for value in deltas.values()):
        raise ValueError("runtime counters reset in the sampled interval")
    return {
        "event_counts": dict(sorted(events.items())),
        "recorded_waiting_demotion_count": events["parent_pressure_demoted"],
        "recorded_waiting_demotion_sources": dict(sources),
        "recorded_waiting_demotion_freed_component_units": dict(freed),
        "runtime_counter_delta": deltas,
        "runtime_counter_boundary_rows": [lower, upper],
        "runtime_state_observed_through_ts_ms": through,
        "semantics": [
            "Event counts use the exact half-open wall-time window.",
            "Counter deltas use the latest preceding state at each boundary; absent Counter keys mean zero under this runtime's emitted Counter contract.",
            "No recorded waiting demotion excludes this observed direct path in this window, not earlier actions, unobserved paths, indirect effects or other cache eviction.",
            "Freed component units are event accounting, not uniquely identified pages, physical transfer bytes, useful reuse or removable JCT.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-report", type=Path, required=True)
    parser.add_argument("--arm", action="append", required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output exists; choose a fresh report ID")
    if len(args.arm) != len(set(args.arm)):
        parser.error("--arm names must be unique")
    raw = args.window_report.read_bytes()
    basis = json.loads(raw)
    arms = {}
    for name in args.arm:
        run = basis["runs"][name]
        windows = [
            row for row in run["windows"]
            if row["start_seconds"] == args.start_seconds
            and row["end_seconds"] == args.end_seconds
        ]
        if len(windows) != 1 or windows[0].get("service_window_observed_complete") is not True:
            raise ValueError("requested complete service window is absent")
        alignment = run["clock_alignment"]
        if alignment.get("native_client_clock_validated") is not True:
            raise ValueError("native/client clock is unverified")
        anchor = alignment["anchor_unix_ms"]
        if not finite_number(anchor):
            raise ValueError("invalid wall-time anchor")
        sealed = SealedRecords()
        result = lifecycle_window(
            sealed(Path(run["run"]) / "opportunities/admission_opportunities.jsonl"),
            anchor + 1000 * args.start_seconds, anchor + 1000 * args.end_seconds,
        )
        arms[name] = {
            "run": run["run"], **result, "input_jsonl_prefixes": sealed.prefixes,
        }
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "start_seconds": args.start_seconds, "end_seconds": args.end_seconds,
        "window_source": {
            "path": str(args.window_report), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "arms": arms,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), ROOT / "scripts/audit_sealed_wait_prefetch.py")
        },
        "scope": "Recorded runtime lifecycle actions only; no causal performance or novelty claim.",
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {
            name: {
                "recorded_waiting_demotions": result["recorded_waiting_demotion_count"],
                "recorded_freed_units": result["recorded_waiting_demotion_freed_component_units"],
            }
            for name, result in arms.items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
