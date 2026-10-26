#!/usr/bin/env python3
"""Partition native request waiting by independently observed agent eligibility."""

import argparse
from bisect import bisect_right
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords


def partition(begin, end, invocation, states, *, maximum_sample_age_ms=1500.):
    if (
        not all(type(value) in (int, float) and math.isfinite(value)
                for value in (begin, end, maximum_sample_age_ms))
        or not 0 <= begin <= end or maximum_sample_age_ms <= 0
    ):
        raise ValueError("invalid request wait interval")
    times = [row["ts_ms"] for row in states]
    if (
        any(type(value) not in (int, float) or not math.isfinite(value) for value in times)
        or any(left >= right for left, right in zip(times, times[1:]))
    ):
        raise ValueError("agent snapshots require finite increasing times")
    parts = Counter(active_ms=0., inactive_ms=0., unknown_ms=0.)
    start = max(0, bisect_right(times, begin) - 1)
    cursor = begin
    for index in range(start, len(states)):
        state = states[index]
        when = state["ts_ms"]
        if when >= end:
            break
        next_when = times[index + 1] if index + 1 < len(times) else end
        left = max(cursor, when)
        right = min(end, next_when, when + maximum_sample_age_ms)
        if right <= left:
            continue
        parts["unknown_ms"] += left - cursor
        admission = state.get("agent_admission")
        active = admission.get("active_invocation_ids") if type(admission) is dict else None
        category = (
            "unknown_ms" if type(active) is not list
            else "active_ms" if invocation in active else "inactive_ms"
        )
        parts[category] += right - left
        cursor = right
    parts["unknown_ms"] += end - cursor
    if not math.isclose(sum(parts.values()), end - begin, abs_tol=.01):
        raise ValueError("eligibility observations did not partition the wait")
    return dict(parts)


def audit(arm, report_path, label, *, minimum_wait_ms):
    if not math.isfinite(minimum_wait_ms) or minimum_wait_ms < 0:
        raise ValueError("invalid minimum diagnostic wait")
    reentries = json.loads(report_path.read_bytes())
    selected = reentries["arms"][label]
    if Path(selected["arm"]).resolve() != arm.resolve():
        raise ValueError("reentry report describes another arm")
    reader = SealedRecords()
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    if not states or any(row.get("physical_disabled") is not False for row in states):
        raise ValueError("no healthy agent eligibility observations")
    rows = []
    for episode in selected["episodes"]:
        if (
            episode.get("status") != "first_service_observed"
            or episode["native_queue_to_service_ms"] < minimum_wait_ms
        ):
            continue
        invocation = episode.get("invocation_id") or episode["parent_invocation_id"]
        begin, end = episode["native_queue_ts_ms"], episode["service_start_ts_ms"]
        parts = partition(begin, end, invocation, states)
        rows.append({
            "workflow_id": episode["workflow_id"], "invocation_id": invocation,
            "request_id": episode["request_id"],
            "native_queue_to_service_ms": end - begin,
            "tool_round_duration_ms": episode.get("tool_round_duration_ms"),
            "cached_tokens_host": episode.get("cached_tokens_host"),
            **parts,
        })
    return {
        "arm": str(arm.resolve()), "report": str(report_path.resolve()),
        "report_sha256": sha256_file(report_path),
        "minimum_diagnostic_wait_ms": minimum_wait_ms,
        "maximum_sample_age_ms": 1500.,
        "summary": {
            "selected_waits": len(rows),
            **{key: sum(row[key] for row in rows) for key in (
                "native_queue_to_service_ms", "active_ms", "inactive_ms", "unknown_ms",
            )},
        },
        "rows": sorted(rows, key=lambda row: -row["native_queue_to_service_ms"]),
        "input_jsonl_prefixes": reader.prefixes,
        "auditor_sha256": sha256_file(Path(__file__)),
        "semantics": [
            "Native queue-to-first-service boundaries and runtime-state samples use observed server wall clocks.",
            "A snapshot is held only until the next snapshot or 1500ms age; gaps and unsupported identity fields remain unknown.",
            "Inactive means no observed logical active-agent eligibility; it does not prove an exclusive pause cause or a removable JCT.",
            "Active time can include native capacity, restore, scheduling and CPU waiting; it is not a GPU wait measurement.",
            "Diagnostic filtering preserves every selected interval and does not exclude any workflow from complete JCT.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--reentries", type=Path, required=True)
    parser.add_argument("--label", default="full")
    parser.add_argument("--minimum-wait-ms", type=float, default=5000.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous eligibility-wait evidence")
    result = audit(args.arm, args.reentries, args.label, minimum_wait_ms=args.minimum_wait_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"summary": result["summary"], "largest_waits": result["rows"][:4]}, indent=2))


if __name__ == "__main__":
    main()
