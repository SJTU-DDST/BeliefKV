#!/usr/bin/env python3
"""Separate measured client preparation from complete workflow elapsed time."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics


def summarize(events):
    starts, submissions, ends = {}, {}, {}
    for row in events:
        kind, workflow, stamp = row["kind"], row["workflow_id"], row["ts_ms"]
        if not math.isfinite(stamp):
            raise ValueError("nonfinite event clock")
        if kind in ("workflow_start", "workflow_end"):
            table = starts if kind == "workflow_start" else ends
            if workflow in table:
                raise ValueError("duplicate workflow boundary")
            table[workflow] = stamp
        elif kind == "llm_submit":
            submissions[workflow] = min(stamp, submissions.get(workflow, stamp))
    if not starts or starts.keys() != ends.keys() or starts.keys() != submissions.keys():
        raise ValueError("incomplete workflow population")
    rows = []
    for workflow, start in sorted(starts.items()):
        submit, end = submissions[workflow], ends[workflow]
        if not start <= submit <= end:
            raise ValueError("workflow boundaries are out of order")
        rows.append({
            "workflow_id": workflow,
            "startup_s": (submit - start) / 1000.,
            "end_to_end_jct_s": (end - start) / 1000.,
            "after_first_submit_s": (end - submit) / 1000.,
        })
    return {
        "workflows": len(rows), "rows": rows,
        "mean_startup_s": statistics.fmean(row["startup_s"] for row in rows),
        "min_startup_s": min(row["startup_s"] for row in rows),
        "max_startup_s": max(row["startup_s"] for row in rows),
        "mean_end_to_end_jct_s": statistics.fmean(row["end_to_end_jct_s"] for row in rows),
        "mean_after_first_submit_s": statistics.fmean(row["after_first_submit_s"] for row in rows),
        "workflow_event_makespan_s": (max(ends.values()) - min(starts.values())) / 1000.,
        "primary_metric_boundary": "workflow_start_to_workflow_end",
        "after_first_submit_is_diagnostic_only": True,
        "hypothetical_startup_removal_performance_measured": False,
    }


def audit(paths):
    sources, events = [], []
    for path in paths:
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError("unsealed event file")
        sources.append({
            "path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        })
        events.extend(json.loads(line) for line in data.splitlines() if line.strip())
    return {"sources": sources, **summarize(events)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-events", type=Path, nargs="+", required=True)
    parser.add_argument("--replay-events", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    source, replay = audit(args.source_events), audit(args.replay_events)
    indexed = {row["workflow_id"]: row for row in source["rows"]}
    if indexed.keys() != {row["workflow_id"] for row in replay["rows"]}:
        raise ValueError("source and replay populations differ")
    drift = [
        row["startup_s"] - indexed[row["workflow_id"]]["startup_s"]
        for row in replay["rows"]
    ]
    result = {
        "schema_version": 1, "scope": "complete_population_startup_measurement",
        "source": source, "replay": replay,
        "mean_paired_startup_drift_s": statistics.fmean(drift),
        "max_absolute_paired_startup_drift_s": max(map(abs, drift)),
        "replay_startup_fraction_of_mean_jct": (
            replay["mean_startup_s"] / replay["mean_end_to_end_jct_s"]
        ),
        "causal_performance_attribution": False,
        "historical_timer_changes": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "scope", "mean_paired_startup_drift_s",
        "max_absolute_paired_startup_drift_s", "replay_startup_fraction_of_mean_jct",
    )}, indent=2))


if __name__ == "__main__":
    main()
