#!/usr/bin/env python3
"""Measure throughput while complete native requests remain outstanding."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_native_system_costs import finite_number, read_prefix, union_ms


BOUNDARIES = {
    "api_received": "api_received_ts_ms",
    "scheduler_received": "scheduler_received_ts_ms",
    "queue_registered": "native_queue_registered_ts_ms",
}
IDENTITY = ("workflow_id", "invocation_id", "context_id", "context_epoch")


def request_identity(event):
    identity = tuple(event.get(name) for name in IDENTITY)
    if (
        not all(isinstance(value, str) and value for value in identity[:3])
        or type(identity[3]) is not int or identity[3] < 0
    ):
        raise ValueError("missing native request identity")
    return identity


def analyze(events, *, maximum_clock_offset_span_ms=5.0):
    if (
        not finite_number(maximum_clock_offset_span_ms)
        or maximum_clock_offset_span_ms < 0
    ):
        raise ValueError("invalid maximum clock offset span")
    indexed = {"llm_submit": {}, "llm_result": {}}
    for event in events:
        kind = event.get("kind")
        if kind not in indexed:
            continue
        attrs = event.get("attributes") or {}
        rid = attrs.get("request_id")
        if not isinstance(rid, str) or not rid or rid in indexed[kind]:
            raise ValueError("missing or duplicate native request event")
        indexed[kind][rid] = event
    submits, results = indexed["llm_submit"], indexed["llm_result"]
    if not submits or submits.keys() != results.keys():
        raise ValueError("incomplete native request population")

    intervals = {name: [] for name in BOUNDARIES}
    work, offsets, domains = {}, [], set()
    for rid, submit in submits.items():
        result = results[rid]
        identity = request_identity(submit)
        if identity != request_identity(result):
            raise ValueError("native request identity changed")
        start, end = submit.get("ts_ms"), result.get("ts_ms")
        attrs, terminal = submit["attributes"], result["attributes"]
        complete = terminal.get("complete_monotonic_ms")
        domain = terminal.get("monotonic_clock_domain")
        if (
            not all(finite_number(value) for value in (start, end, complete))
            or not 0 <= start <= end or complete < 0
            or not isinstance(domain, str) or not domain
        ):
            raise ValueError("invalid native completion clock or chronology")
        offsets.append(end - complete)
        domains.add(domain)
        timing = attrs.get("native_ingress_timing_ms") or {}
        arrivals = [timing.get(field) for field in BOUNDARIES.values()]
        if (
            not all(finite_number(value) and value >= 0 for value in arrivals)
            or arrivals != sorted(arrivals) or arrivals[-1] != start
        ):
            raise ValueError("missing or unordered native ingress boundaries")
        for name, arrival in zip(BOUNDARIES, arrivals):
            intervals[name].append((arrival, end))
        prompt, output = attrs.get("prompt_tokens"), terminal.get("output_tokens")
        if any(type(value) is not int or value < 0 for value in (prompt, output)):
            raise ValueError("invalid native token counts")
        work[rid] = (*identity, prompt, output)
    offset_span = max(offsets) - min(offsets)
    if len(domains) != 1 or offset_span > maximum_clock_offset_span_ms:
        raise ValueError("mixed native clock domains or unstable wall clock")

    output_tokens = sum(row[-1] for row in work.values())
    measurements = {}
    for name, spans in intervals.items():
        outstanding_ms = union_ms(spans)
        span_ms = max(right for _, right in spans) - min(left for left, _ in spans)
        if outstanding_ms <= 0:
            raise ValueError("native request population has no positive duration")
        measurements[name] = {
            "outstanding_union_ms": outstanding_ms,
            "first_arrival_to_last_completion_ms": span_ms,
            "no_outstanding_request_ms_within_span": span_ms - outstanding_ms,
            "outstanding_fraction_of_span": outstanding_ms / span_ms,
            "time_weighted_outstanding_requests": (
                sum(right - left for left, right in spans) / outstanding_ms
            ),
            "output_tokens_per_outstanding_second": output_tokens * 1000 / outstanding_ms,
        }
    signature = json.dumps(work, sort_keys=True, separators=(",", ":")).encode()
    return {
        "requests": len(work),
        "workflow_ids": sorted({row[0] for row in work.values()}),
        "prompt_tokens": sum(row[-2] for row in work.values()),
        "output_tokens": output_tokens,
        "request_identity_and_token_count_sha256": hashlib.sha256(signature).hexdigest(),
        "clock": {
            "monotonic_clock_domain": next(iter(domains)),
            "completion_wall_minus_monotonic_span_ms": offset_span,
            "maximum_allowed_clock_offset_span_ms": maximum_clock_offset_span_ms,
            "interval_clock": "native recorded wall timestamps",
        },
        "by_arrival_boundary": measurements,
    }


def audit_arm(arm):
    server = arm / "server"
    status_path = server / "native_telemetry_status.json"
    status = json.loads(status_path.read_text())
    if (
        any(status.get(name) != 0 for name in (
            "pending_request_count", "pending_batch_count", "dropped_records", "failed_records",
        ))
        or status.get("writer_error")
    ):
        raise ValueError(f"native telemetry is incomplete or unhealthy: {arm}")
    summary_path = arm / "replay/summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        if (
            summary.get("causal_execution_completed") is not True
            or summary.get("exact_tokens_verified") is not True
            or summary.get("original_native_stopping_verified") is not True
        ):
            raise ValueError(f"replay has no complete exact-work summary: {arm}")
        expected = summary["completed_requests"]
    else:
        summaries = list(arm.glob("client_*/summary.json"))
        if len(summaries) != 1:
            raise ValueError(f"expected one completed native client summary: {arm}")
        summary_path = summaries[0]
        summary = json.loads(summary_path.read_text())
        expected = summary["llm_request_count"]
    path = server / "runtime_events.sglang.jsonl"
    events, binding = read_prefix(path)
    if (
        binding["discarded_partial_tail_bytes"]
        or path.stat().st_size != binding["analyzed_bytes"]
        or status.get("record_counts", {}).get("events") != len(events)
    ):
        raise ValueError(f"native event capture is not complete and settled: {arm}")
    result = analyze(events)
    if result["requests"] != expected:
        raise ValueError(f"native event population differs from completed work: {arm}")
    return {
        **result,
        "arm": str(arm.resolve()),
        "events": binding,
        "status_sha256": hashlib.sha256(status_path.read_bytes()).hexdigest(),
        "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True, help="NAME=ARM_PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output already exists")
    arms = {}
    for value in args.arm:
        name, separator, path = value.partition("=")
        if not separator or not name or not path or name in arms:
            parser.error("--arm requires a unique NAME=ARM_PATH")
        arms[name] = audit_arm(Path(path))
    signatures = {row["request_identity_and_token_count_sha256"] for row in arms.values()}
    if len(signatures) != 1:
        raise ValueError("arms do not have identical native request identities and token counts")
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arms": arms,
        "same_native_request_identities_and_token_counts": True,
        "analysis_source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "scripts/audit_outstanding_request_throughput.py",
                "scripts/audit_native_system_costs.py",
            )
        },
        "semantics": (
            "All completed native requests are included. Each denominator is the union "
            "of arrival-to-native-completion intervals: queueing, admission, restoration "
            "and processing remain included. Only periods with zero outstanding native "
            "requests are excluded. This is demand-outstanding throughput, not CUDA "
            "kernel throughput or a GPU-utilization measurement. It does not remove "
            "normal workflow tails from JCT. Matching identities and token counts do "
            "not independently prove token-content parity, causal equivalence, task "
            "correctness or isolated-module benefit."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        name: {
            "requests": row["requests"],
            "api_received": row["by_arrival_boundary"]["api_received"],
        }
        for name, row in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
