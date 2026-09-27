#!/usr/bin/env python3
"""Audit actual 100 ms child-tool timing observations without action claims."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median


def _events(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    return sorted(values)[int(.95 * (len(values) - 1))]


def _eligible(attrs: dict) -> bool:
    total = attrs.get("project_shape_survivor_100ms_total_median_ms")
    deviation = attrs.get("project_shape_survivor_100ms_deviation_p90_ms")
    support = attrs.get("project_shape_survivor_100ms_support")
    return (
        attrs.get("is_child") is True
        and attrs.get("tool_name") == "execute"
        and attrs.get("previous_same_input_status") != "success"
        and type(total) in (int, float)
        and math.isfinite(total) and total > 1100
        and type(deviation) in (int, float)
        and math.isfinite(deviation) and 0 <= deviation <= 1000
        and type(support) is int and support >= 4
    )


def audit(workflows: Path, *, manifest: Path | None = None) -> dict:
    expected: set[str] | None = None
    if manifest is not None:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        ids = [row["instance_id"] for row in payload["workloads"]]
        if len(ids) != len(set(ids)):
            raise ValueError("manifest has duplicate workflow IDs")
        expected = set(ids)
    paths = sorted(workflows.glob("*/runtime_events.deepagents.jsonl"))
    if not paths:
        raise ValueError(f"no workflow traces in {workflows}")
    observed = {path.parent.name for path in paths}
    if expected is not None and observed - expected:
        raise ValueError("unexpected workflow trace outside frozen manifest")

    counts: Counter[str] = Counter()
    delays: list[float] = []
    leads: list[float] = []
    for path in paths:
        events = _events(path)
        if any(
            row.get("kind") == "workflow_end"
            and (row.get("attributes") or {}).get("outcome") == "completed"
            for row in events
        ):
            counts["completed_workflows"] += 1
        starts: dict[tuple[str, str], dict] = {}
        ends: dict[tuple[str, str], dict] = {}
        signals: dict[tuple[str, str], dict] = {}
        for event in events:
            attrs = event.get("attributes") or {}
            call_id = attrs.get("tool_call_id")
            invocation_id = event.get("invocation_id")
            if not call_id or not invocation_id:
                continue
            key = str(invocation_id), str(call_id)
            if event.get("kind") == "tool_start":
                if key in starts:
                    raise ValueError(f"duplicate tool start: {path}: {key}")
                starts[key] = event
            elif event.get("kind") == "tool_end":
                if key in ends:
                    raise ValueError(f"duplicate tool end: {path}: {key}")
                ends[key] = event
            elif (
                event.get("kind") == "structured_action"
                and attrs.get("beliefkv_tool_wait_early_shadow") is True
            ):
                if key in signals:
                    raise ValueError(f"duplicate early observation: {path}: {key}")
                signals[key] = event
        for key, signal in signals.items():
            if key not in starts:
                raise ValueError(f"orphan early observation: {path}: {key}")
            start_attrs = starts[key].get("attributes") or {}
            if not (
                signal.get("attributes", {}).get("diagnostic_only") is True
                and _eligible(start_attrs)
            ):
                raise ValueError(f"unqualified early observation: {path}: {key}")
        for key, start in starts.items():
            attrs = start.get("attributes") or {}
            if not _eligible(attrs):
                continue
            counts["eligible_tool_starts"] += 1
            end = ends.get(key)
            signal = signals.get(key)
            if end is None:
                counts["no_tool_end"] += 1
            elif end["ts_ms"] - start["ts_ms"] <= 100:
                counts["returned_by_100ms"] += 1
            if signal is None:
                counts["no_observation"] += 1
                if end is not None and end["ts_ms"] - start["ts_ms"] > 100:
                    counts["missed_live_landmark"] += 1
                continue
            counts["observations"] += 1
            delay = float(signal["ts_ms"] - start["ts_ms"] - 100)
            if delay < 0:
                raise ValueError(f"early observation precedes 100 ms: {path}: {key}")
            delays.append(delay)
            if end is None:
                counts["observed_without_tool_end"] += 1
                continue
            lead = float(end["ts_ms"] - signal["ts_ms"])
            if lead < 0:
                raise ValueError(f"early observation after tool end: {path}: {key}")
            leads.append(lead)
            counts["observed_returned"] += 1
            if lead >= 500:
                counts["lead_at_least_500ms"] += 1
            if lead >= 1000:
                counts["lead_at_least_1000ms"] += 1
            if (end.get("attributes") or {}).get("status") != "success":
                counts["observed_failed_tools"] += 1
    return {
        "expected_workflows": len(expected) if expected is not None else None,
        "traced_workflows": len(paths),
        "missing_workflows": sorted(expected - observed) if expected is not None else [],
        "counts": dict(counts),
        "dispatch_lateness_p50_ms": median(delays) if delays else None,
        "dispatch_lateness_p95_ms": _p95(delays),
        "lead_p50_ms": median(leads) if leads else None,
        "lead_p95_ms": _p95(leads),
        "interpretation": "trace_only_not_physical_transfer_or_action_eligible",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--workload-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.workflows, manifest=args.workload_manifest)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
