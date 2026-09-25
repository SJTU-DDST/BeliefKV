#!/usr/bin/env python3
"""Audit observable child-state cues against subsequent RETURN timestamps."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values.sort()
    return values[math.ceil(q * len(values)) - 1]


def audit(dataset_dir: Path) -> dict:
    returns: dict[str, tuple[float, float | None]] = {}
    join_count = 0
    with (dataset_dir / "reentries.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if (
                row.get("reentry_kind") != "join"
                or row.get("terminal_status") != "satisfied"
                or row.get("training_eligible") is not True
            ):
                continue
            join_count += 1
            for member in row.get("member_outcomes") or ():
                child = str(member.get("invocation_id") or "")
                if child and member.get("return_ts_ms") is not None:
                    returns[child] = (
                        float(member["return_ts_ms"]),
                        float(member["start_ts_ms"])
                        if member.get("start_ts_ms") is not None else None,
                    )
    first_by_cue: dict[str, dict[str, float]] = defaultdict(dict)
    last_by_cue: dict[str, dict[str, float]] = defaultdict(dict)
    snapshots: dict[str, list[float]] = defaultdict(list)
    with (dataset_dir / "frontier_decision_points.jsonl").open(
        encoding="utf-8"
    ) as stream:
        for line in stream:
            row = json.loads(line)
            ts = float(row.get("timestamp_ms") or 0)
            for invocation in row.get("invocations") or ():
                child = str(invocation.get("invocation_id") or "")
                target = returns.get(child)
                if target is None or ts >= target[0] or (
                    target[1] is not None and ts < target[1]
                ):
                    continue
                state = str(invocation.get("state") or "unknown")
                history = invocation.get("boundary_history") or ()
                last = str(history[-1]) if history else "none"
                cue = f"{state}|{last}"
                remaining = target[0] - ts
                snapshots[cue].append(remaining)
                first_by_cue[cue].setdefault(child, remaining)
                last_by_cue[cue][child] = remaining
    return {
        "join_count": join_count,
        "completed_child_count": len(returns),
        "qualification": (
            "Only completed, training-eligible JOIN members; this does not "
            "measure false positives on canceled children or online delivery."
        ),
        "cues": {
            cue: {
                "snapshots": len(samples),
                "children": len(first_by_cue[cue]),
                "first_p50_ms": _quantile(list(first_by_cue[cue].values()), 0.5),
                "first_p90_ms": _quantile(list(first_by_cue[cue].values()), 0.9),
                "last_p50_ms": _quantile(list(last_by_cue[cue].values()), 0.5),
                "first_within_500ms": sum(
                    value <= 500 for value in first_by_cue[cue].values()
                ),
                "first_over_2000ms": sum(
                    value > 2_000 for value in first_by_cue[cue].values()
                ),
            }
            for cue, samples in sorted(snapshots.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.dataset_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
