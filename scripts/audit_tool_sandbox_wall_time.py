#!/usr/bin/env python3
"""Read-only, conservative timing match of execute calls to sandbox audits."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import orjson

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_repeated_tool_timing import _quantile, _read_workflow


def _audits(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("rb") as stream:
        return sorted(
            (
                orjson.loads(line)
                for line in stream if line.strip()
                if b'"event": "sandbox_execute"' in line
            ),
            key=lambda row: float(row["ts_ms"]),
        )


def match(workflow: Path, *, max_callback_gap_ms: float = 200) -> tuple[list[dict], Counter]:
    tool_calls = sorted(
        _read_workflow(workflow / "runtime_events.deepagents.jsonl"),
        key=lambda row: row["terminal_ts_ms"],
    )
    audits = _audits(workflow / "sandbox_audit.jsonl")
    timestamps = [float(item["ts_ms"]) for item in audits]
    used = set()
    counts = Counter()
    pairs = []
    for row in tool_calls:
        counts["completed_execute"] += 1
        begin = bisect_left(timestamps, row["start_ts_ms"])
        end = bisect_right(timestamps, row["terminal_ts_ms"])
        candidates = [
            (i, audits[i])
            for i in range(begin, end) if i not in used
            and row["terminal_ts_ms"] - timestamps[i] <= max_callback_gap_ms
            and float(audits[i].get("duration_ms") or 0) <= row["duration_ms"] + 2
        ]
        if len(candidates) != 1:
            counts["ambiguous" if candidates else "unmatched"] += 1
            continue
        index, audit = candidates[0]
        used.add(index)
        counts["matched"] += 1
        gap = row["terminal_ts_ms"] - float(audit["ts_ms"])
        pairs.append({
            **row,
            "sandbox_duration_ms": float(audit["duration_ms"]),
            "callback_gap_ms": gap,
            "outside_sandbox_ms": row["duration_ms"] - float(audit["duration_ms"]),
        })
    return pairs, counts


def _stats(rows: list[dict]) -> dict:
    return {
        "count": len(rows),
        "tool_p50_ms": _quantile([r["duration_ms"] for r in rows], .5),
        "sandbox_p50_ms": _quantile([r["sandbox_duration_ms"] for r in rows], .5),
        "sandbox_p90_ms": _quantile([r["sandbox_duration_ms"] for r in rows], .9),
        "outside_sandbox_p50_ms": _quantile([r["outside_sandbox_ms"] for r in rows], .5),
        "outside_sandbox_p90_ms": _quantile([r["outside_sandbox_ms"] for r in rows], .9),
        "callback_gap_p95_ms": _quantile([r["callback_gap_ms"] for r in rows], .95),
        "workflow_count": len({r["workflow"] for r in rows}),
        "by_project": {
            project: {
                "count": sum(r["project"] == project for r in rows),
                "sandbox_p50_ms": _quantile([
                    r["sandbox_duration_ms"] for r in rows if r["project"] == project
                ], .5),
            } for project in sorted({r["project"] for r in rows})
        },
    }


def audit(workflows: Path) -> dict:
    pairs = []
    counts = Counter()
    for path in sorted(workflows.iterdir()):
        if not path.is_dir() or not (path / "runtime_events.deepagents.jsonl").exists():
            continue
        matched, local = match(path)
        pairs.extend(matched)
        counts.update(local)
    buckets = defaultdict(list)
    for row in pairs:
        buckets["all_matched"].append(row)
        if row["is_child"] is True:
            buckets["child"].append(row)
            if row["duration_ms"] >= 2_000:
                buckets["long_child"].append(row)
                if row["previous"] is None or row["previous"][2] != "success":
                    buckets["cold_long_child"].append(row)
    return {
        "status": "conservative_clock_match_not_call_identity_proof",
        "counts": dict(counts),
        "metrics": {name: _stats(group) for name, group in sorted(buckets.items())},
        "note": (
            "Sandbox duration includes internal lock wait. The current raw trace "
            "has no separate lock_wait_ms or execute_elapsed_ms; ambiguous matches "
            "were excluded, not assigned by nearest timestamp."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.workflows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
