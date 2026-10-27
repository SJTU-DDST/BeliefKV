#!/usr/bin/env python3
"""Partition completed request waits by same-snapshot full logical capacity."""

import argparse
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords


def validate_states(states):
    previous = -math.inf
    for state in states:
        when = state.get("ts_ms")
        if not finite_number(when) or when <= previous:
            raise ValueError("capacity snapshots require increasing finite clocks")
        previous = when
        if state.get("physical_disabled") is not False:
            raise ValueError("native physical control is unhealthy")
        admission = state.get("agent_admission") or {}
        active = admission.get("active_invocation_ids")
        marks = admission.get("marked_for_pause_invocation_ids")
        capacity, used = admission.get("capacity_tokens"), admission.get("logical_active_tokens")
        if (
            admission.get("strategy") != "thunderagent_reference"
            or admission.get("logical_capacity_semantics") != "reference_undiscounted_contexts_plus_buffers"
            or type(capacity) is not int or capacity <= 0
            or type(used) is not int or used < 0
            or type(active) is not list or len(set(active)) != len(active)
            or type(marks) is not list or len(set(marks)) != len(marks)
            or not set(marks).issubset(active)
        ):
            raise ValueError("unsupported same-snapshot reference budget evidence")


def partition(begin, end, invocation, required_tokens, states, *, maximum_age_ms=1500.):
    if (
        not finite_number(begin) or not finite_number(end) or not 0 <= begin <= end
        or type(required_tokens) is not int or required_tokens <= 0
        or not finite_number(maximum_age_ms) or maximum_age_ms <= 0
    ):
        raise ValueError("invalid capacity wait interval or complete input demand")
    validate_states(states)
    times = [row["ts_ms"] for row in states]
    parts = Counter(
        active_ms=0., inactive_full_logical_fit_ms=0.,
        inactive_full_logical_shortfall_ms=0., unknown_ms=0.,
    )
    cursor, run, longest, first_fit = begin, 0., 0., None
    samples, fitting_controls = 0, set()
    for index in range(max(0, bisect_right(times, begin) - 1), len(states)):
        state = states[index]
        when = state["ts_ms"]
        if when >= end:
            break
        right = min(end, when + maximum_age_ms, times[index + 1] if index + 1 < len(times) else end)
        left = max(cursor, when)
        if right <= left:
            continue
        if left > cursor:
            parts["unknown_ms"] += left - cursor
            run = 0.
        admission = state["agent_admission"]
        available = admission["capacity_tokens"] - admission["logical_active_tokens"]
        category = (
            "active_ms" if invocation in admission["active_invocation_ids"] else
            "inactive_full_logical_fit_ms" if available >= required_tokens else
            "inactive_full_logical_shortfall_ms"
        )
        duration = right - left
        parts[category] += duration
        if category == "inactive_full_logical_fit_ms":
            if first_fit is None:
                first_fit = left
            samples += 1
            tick = admission.get("counts", {}).get("control_ticks")
            if type(tick) is int:
                fitting_controls.add(tick)
            run += duration
            longest = max(longest, run)
        else:
            run = 0.
        cursor = right
    parts["unknown_ms"] += end - cursor
    if not math.isclose(sum(parts.values()), end - begin, abs_tol=.01):
        raise ValueError("capacity observations did not partition the complete wait")
    return {
        **parts, "fitting_snapshot_intervals": samples,
        "first_sampled_fit_ts_ms": first_fit,
        "maximum_consecutive_sampled_fit_ms": longest,
        "distinct_observed_control_ticks_during_fit": len(fitting_controls),
    }


def audit(arm, reentries, label, *, minimum_wait_ms):
    if not finite_number(minimum_wait_ms) or minimum_wait_ms < 0:
        raise ValueError("invalid minimum diagnostic wait")
    data = json.loads(reentries.read_bytes())
    selected = data["arms"][label]
    if Path(selected["arm"]).resolve() != arm.resolve():
        raise ValueError("tool reentry evidence belongs to another arm")
    sealed = SealedRecords()
    prompts = {}
    for row in sealed(arm / "server/runtime_events.sglang.jsonl"):
        if row.get("kind") != "llm_submit":
            continue
        attrs = row["attributes"]
        request, tokens = attrs["request_id"], attrs["prompt_tokens"]
        if request in prompts or type(tokens) is not int or tokens <= 0:
            raise ValueError("native prompt population is not exact and unique")
        prompts[request] = tokens
    states = [
        row for row in sealed(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    if not states:
        raise ValueError("missing reference capacity observations")
    validate_states(states)
    # This reference declaration uses a fixed, undiscounted 100-token buffer.
    # Physical free-page telemetry has no observation age and grants no authority.
    buffer = 100
    rows = []
    for episode in selected["episodes"]:
        if (
            episode.get("status") != "first_service_observed"
            or episode["native_queue_to_service_ms"] < minimum_wait_ms
        ):
            continue
        request = episode["request_id"]
        if request not in prompts:
            raise ValueError("a completed successor lacks its exact native prompt")
        required = prompts[request] + buffer
        parts = partition(
            episode["native_queue_ts_ms"], episode["service_start_ts_ms"],
            episode["invocation_id"], required, states,
        )
        rows.append({
            "workflow_id": episode["workflow_id"], "invocation_id": episode["invocation_id"],
            "request_id": request, "required_full_logical_tokens": required,
            "native_queue_to_service_ms": episode["native_queue_to_service_ms"], **parts,
        })
    marks = Counter(snapshots=len(states), snapshots_with_marks=0, marks_while_raw_budget_fits=0,
                    all_active_programs_marked=0)
    for state in states:
        admission = state["agent_admission"]
        pending_marks = admission["marked_for_pause_invocation_ids"]
        if pending_marks:
            marks["snapshots_with_marks"] += 1
            marks["marks_while_raw_budget_fits"] += (
                admission["logical_active_tokens"] <= admission["capacity_tokens"]
            )
            marks["all_active_programs_marked"] += (
                len(pending_marks) == len(admission["active_invocation_ids"])
            )
    fields = (
        "native_queue_to_service_ms", "active_ms", "inactive_full_logical_fit_ms",
        "inactive_full_logical_shortfall_ms", "unknown_ms",
    )
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arm": str(arm.resolve()), "reentries": str(reentries.resolve()),
        "reentries_sha256": sha256_file(reentries),
        "minimum_diagnostic_wait_ms": minimum_wait_ms, "maximum_sample_age_ms": 1500.,
        "reference_growth_buffer_tokens": buffer,
        "summary": {
            "selected_waits": len(rows), **{
                field: sum(row[field] for row in rows) for field in fields
            },
            "waits_with_sampled_complete_logical_fit": sum(
                row["inactive_full_logical_fit_ms"] > 0 for row in rows
            ),
        },
        "snapshot_mark_counts": dict(marks),
        "rows": sorted(rows, key=lambda row: -row["inactive_full_logical_fit_ms"]),
        "input_jsonl_prefixes": sealed.prefixes,
        "auditor_sha256": sha256_file(Path(__file__)), "performance_accepted": False,
        "physical_fit_verified": False,
        "semantics": [
            "Every selected completed wait is retained; no workflow tail or request work is removed.",
            "Fit means the exact submitted input plus the declared 100-token buffer fits the same snapshot's full undiscounted logical capacity.",
            "Snapshot values are held at most 1500ms; gaps remain unknown, not free capacity.",
            "Concurrent request intervals can overlap and their sum is not removable JCT.",
            "A fitting inactive snapshot is a diagnostic opportunity, not proof that earlier admission or transfer would improve performance.",
            "Cached physical headroom has no observation age here; native growth, allocation, locks and DMA remain unproven and authoritative.",
            "Observed marks after a deficit clears are not proof of a resulting pause or migration.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--reentries", type=Path, required=True)
    parser.add_argument("--label", default="Full")
    parser.add_argument("--minimum-wait-ms", type=float, default=1000.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier capacity diagnostics")
    report = audit(args.arm, args.reentries, args.label, minimum_wait_ms=args.minimum_wait_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "summary": report["summary"], "snapshot_mark_counts": report["snapshot_mark_counts"],
        "largest_fitting_waits": report["rows"][:3],
    }, indent=2))


if __name__ == "__main__":
    main()
