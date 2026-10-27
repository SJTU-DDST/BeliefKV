#!/usr/bin/env python3
"""Bind one completed request's native launch to sealed eligibility observations."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_agent_admission_wait import partition
from scripts.audit_join_reentry_service import identity
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_tool_reentry_service import tool_reentries


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def one(rows, description):
    if len(rows) != 1:
        raise ValueError(f"expected one {description}, observed {len(rows)}")
    return rows[0]


def first_launch(rows, receipt):
    selected = None
    count, domain = 0, None
    expected_identity = tuple(receipt["native_receipt"]["identity"])
    for row in rows:
        if row.get("event") != "gpu_service_sample":
            continue
        count += 1
        start, end = row.get("launch_monotonic_ms"), row.get("complete_monotonic_ms")
        clock = row.get("monotonic_clock_domain")
        if (
            not finite(start) or not finite(end) or not 0 <= start <= end
            or not isinstance(clock, str) or not clock
            or domain is not None and clock != domain
            or row.get("sample_id") != f"native-service-{count:09d}"
        ):
            raise ValueError("invalid or incomplete native service prefix")
        domain = clock
        matches = [
            sample for sample in row.get("request_samples", ())
            if sample.get("request_id") == receipt["request_id"]
        ]
        if len(matches) > 1:
            raise ValueError("duplicate request within a service sample")
        if not matches:
            continue
        if (
            identity(matches[0]) != expected_identity
            or clock != receipt["native_receipt"]["clock_domain"]
        ):
            raise ValueError("native launch changed request identity or clock")
        if selected is None or start < selected["launch_monotonic_ms"]:
            selected = row
    if selected is None:
        raise ValueError("no observed first native launch")
    return selected, count


def snapshot_rows(states, invocation, *, begin, end, lookback_ms):
    rows, previous = [], None
    for state in states:
        when = state["ts_ms"]
        if not begin - lookback_ms <= when <= end + 1500.:
            continue
        admission = state.get("agent_admission") or {}
        active = admission.get("active_invocation_ids")
        pending = admission.get("paused_pending_invocation_ids")
        marked = admission.get("marked_for_pause_invocation_ids")
        observed = (
            invocation in active if type(active) is list else None,
            invocation in pending if type(pending) is list else None,
            invocation in marked if type(marked) is list else None,
        )
        active_set = set(active) if type(active) is list else None
        rows.append({
            "ts_ms": when, "from_native_queue_ms": when - begin,
            "active": observed[0], "paused_pending": observed[1],
            "marked_for_pause": observed[2],
            "active_added_since_previous_sample": (
                sorted(active_set - previous)
                if active_set is not None and previous is not None else None
            ),
            "active_removed_since_previous_sample": (
                sorted(previous - active_set)
                if active_set is not None and previous is not None else None
            ),
            "logical_active_tokens": admission.get("logical_active_tokens"),
            "control_capacity_snapshot": admission.get("control_capacity_snapshot"),
            "control_counts": admission.get("counts"),
            "native_no_token_count": (state.get("counts") or {}).get("native_NO_TOKEN"),
        })
        previous = active_set
    return rows


def audit(arm, request_id, *, lookback_ms=10000.):
    if not finite(lookback_ms) or lookback_ms < 0:
        raise ValueError("invalid diagnostic lookback")
    reader = SealedRecords()
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    receipt = one(
        [row for row in receipts if row.get("request_id") == request_id],
        "completed request receipt",
    )
    if receipt.get("exact_tokens_verified") is not True:
        raise ValueError("request lacks exact replay work verification")
    native = list(reader(arm / "server/runtime_events.sglang.jsonl"))
    events = [
        row for row in native
        if (row.get("attributes") or {}).get("request_id") == request_id
    ]
    submit = one([row for row in events if row.get("kind") == "llm_submit"], "native submit")
    result = one([row for row in events if row.get("kind") == "llm_result"], "native result")
    expected_identity = tuple(receipt["native_receipt"]["identity"])
    if identity(submit) != expected_identity or identity(result) != expected_identity:
        raise ValueError("native request lifecycle changed identity")
    launch, service_count = first_launch(reader(arm / "server/runtime_audit.jsonl"), receipt)
    begin = (submit.get("attributes") or {}).get("native_ingress_timing_ms", {}).get(
        "native_queue_registered_ts_ms",
    )
    complete = receipt.get("native_complete_monotonic_ms")
    submitted = receipt.get("submitted_monotonic_ms")
    wall_complete = result.get("ts_ms")
    if (
        not all(finite(value) for value in (begin, complete, submitted, wall_complete))
        or not submitted <= launch["launch_monotonic_ms"] <= complete
        or receipt["native_receipt"].get("complete_monotonic_ms") != complete
    ):
        raise ValueError("invalid completed request or ingress chronology")
    # Translate only this native process's two clocks, using its own result.
    native_wall_offset = wall_complete - complete
    end = launch["launch_monotonic_ms"] + native_wall_offset
    if not begin <= end <= wall_complete:
        raise ValueError("native ingress and launch clocks disagree")
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    if not states or any(row.get("physical_disabled") is not False for row in states):
        raise ValueError("no healthy native eligibility prefix")
    samples = snapshot_rows(
        states, submit["invocation_id"], begin=begin, end=end, lookback_ms=lookback_ms,
    )
    previous = [
        row for row in native if row.get("kind") == "llm_result"
        and identity(row)[:3] == expected_identity[:3]
        and row.get("context_epoch") == submit["context_epoch"] - 1
    ]
    if len(previous) > 1:
        raise ValueError("ambiguous preceding same-context completion")
    tools, _, _ = tool_reentries(reader(arm / "replay/runtime_events.replay.jsonl"))
    predecessor = [row for row in tools if row.get("request_id") == request_id]
    if len(predecessor) > 1:
        raise ValueError("ambiguous preceding tool round")
    attrs = submit["attributes"]
    return {
        "schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "one completed request in independently sealed collecting prefixes",
        "arm": str(arm.resolve()), "request_id": request_id,
        "workflow_id": submit["workflow_id"], "invocation_id": submit["invocation_id"],
        "context_id": submit["context_id"], "context_epoch": submit["context_epoch"],
        "prompt_tokens": receipt["prompt_tokens"], "output_tokens": receipt["output_tokens"],
        "native_queue_wall_ms": begin, "first_launch_wall_ms": end,
        "submit_to_first_launch_ms": launch["launch_monotonic_ms"] - submitted,
        "native_queue_to_first_launch_ms": end - begin,
        "first_launch_to_native_complete_ms": complete - launch["launch_monotonic_ms"],
        "native_service_prefix_records": service_count,
        "first_launch_sample_id": launch["sample_id"],
        "previous_native_complete_wall_ms": previous[0]["ts_ms"] if previous else None,
        "preceding_tool_round": predecessor[0] if predecessor else None,
        "service_host_hit_tokens": attrs.get("cached_tokens_host"),
        "service_device_hit_tokens": attrs.get("cached_tokens_device"),
        "service_uncached_tokens": attrs.get("uncached_prompt_tokens"),
        "maximum_sample_age_ms": 1500.,
        "sample_hold_partition": partition(begin, end, submit["invocation_id"], states),
        "eligibility_samples": samples,
        "input_jsonl_prefixes": reader.prefixes,
        "auditor_sha256": sha256_file(Path(__file__)),
        "semantics": [
            "First launch is scheduler.run_batch entry, not CUDA kernel start.",
            "The sample-hold partition is an observation convention, not exact state-transition timing.",
            "An eligibility transition can occur between snapshots, including before the first launch.",
            "Inactive observations identify a logical admission restriction; active time is not pure GPU transfer waiting.",
            "Predecessor tool duration remains in the client clock; no client tool timestamp is subtracted from a server timestamp.",
            "Service cache hits do not establish residency at arrival or the exclusive cause of eviction.",
            "Concurrent collecting prefixes are separately sealed and are not an atomic full-run comparison.",
        ],
        "complete_run_verified": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--lookback-ms", type=float, default=10000.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous request evidence")
    result = audit(args.arm, args.request_id, lookback_ms=args.lookback_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "request_id", "native_queue_to_first_launch_ms", "sample_hold_partition",
            "previous_native_complete_wall_ms", "first_launch_sample_id",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
