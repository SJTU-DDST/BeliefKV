#!/usr/bin/env python3
"""Measure observed context competition between generation completion and reentry."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
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


def competition_band(value):
    for lower, upper in ((0, 16), (16, 32), (32, 48), (48, 64), (64, 96), (96, 128)):
        if lower <= value < upper:
            return f"{lower}_to_{upper - 1}"
    return "128_plus"


def cohort_summary(rows):
    known = [row for row in rows if row["reported_host_full_tokens"] is not None]
    positive = [row for row in known if row["reported_host_full_tokens"] > 0]
    return {
        "requests": len(rows),
        "known_full_host_counter_requests": len(known),
        "positive_full_host_requests": len(positive),
        "positive_full_host_fraction_of_known": len(positive) / len(known) if known else None,
        "reported_host_full_tokens": sum(row["reported_host_full_tokens"] for row in known),
        "distinct_known_other_contexts": distribution(
            row["distinct_known_other_contexts"] for row in rows
            if row["distinct_known_other_contexts"] is not None
        ),
        "other_request_starts": distribution(
            row["other_request_starts"] for row in rows if row["other_request_starts"] is not None
        ),
        "completion_to_queue_ms": distribution(
            row["completion_to_queue_ms"] for row in rows if row["completion_to_queue_ms"] is not None
        ),
    }


def service_competition(events, begin, end):
    if not (finite_number(begin) and finite_number(end) and begin < end):
        raise ValueError("invalid competition window")
    latest, active, seen_requests = {}, {}, set()
    last_service, sorted_last, unknown_starts = {}, [], []
    ordinal, completed_through = 0, None
    rows = []
    for event in events:
        kind = event.get("kind")
        if kind not in ("llm_submit", "llm_result"):
            continue
        timestamp = event.get("ts_ms")
        attrs = event.get("attributes") or {}
        request = attrs.get("request_id")
        if not finite_number(timestamp) or type(request) is not str or not request:
            raise ValueError("invalid native request identity or clock")
        if kind == "llm_result":
            prior = active.pop(request, None)
            if prior is None:
                raise ValueError("native completion has no unique observed first service")
            prior["completion_ts_ms"] = timestamp
            prior["completion_service_ordinal"] = ordinal
            prior["output_tokens"] = attrs.get("output_tokens")
            completed_through = timestamp if completed_through is None else max(completed_through, timestamp)
            continue
        if request in seen_requests:
            raise ValueError("duplicate native first-service request")
        seen_requests.add(request)
        ordinal += 1
        identity = tuple(event.get(key) for key in ("workflow_id", "invocation_id", "context_id"))
        scope = identity if all(type(value) is str and value for value in identity) else None
        previous = latest.get(scope) if scope is not None else None
        completion = previous.get("completion_ts_ms") if previous is not None else None
        status = (
            "context_identity_unknown" if scope is None else
            "previous_request_unobserved" if previous is None else
            "previous_completion_unobserved" if completion is None else
            "completion_after_queue_registration" if completion > timestamp else
            "previous_completed_request"
        )
        distance = starts = unknown = gap = None
        if status == "previous_completed_request":
            cutoff = previous["completion_service_ordinal"]
            distance = len(sorted_last) - bisect_right(sorted_last, cutoff)
            if last_service.get(scope, 0) > cutoff:
                distance -= 1
            starts = ordinal - cutoff - 1
            unknown = len(unknown_starts) - bisect_right(unknown_starts, cutoff)
            gap = timestamp - completion
        host = attrs.get("cached_tokens_host")
        prompt = attrs.get("prompt_tokens")
        for value in (host, prompt):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("invalid native token accounting")
        epoch = event.get("context_epoch")
        previous_epoch = previous["context_epoch"] if previous is not None else None
        same_epoch = (
            epoch == previous_epoch
            if type(epoch) is int and type(previous_epoch) is int else None
        )
        epoch_transition = (
            "unknown" if same_epoch is None else
            "same" if same_epoch else
            "advanced" if epoch > previous_epoch else "regressed"
        )
        if begin <= timestamp < end:
            rows.append({
                "request_id": request,
                **{key: event.get(key) for key in ("workflow_id", "invocation_id", "context_id", "context_epoch")},
                "native_queue_ts_ms": timestamp,
                "first_service_observation_ordinal": ordinal,
                "previous_request_id": previous["request_id"] if previous is not None else None,
                "previous_context_epoch": previous_epoch,
                "preceding_status": status,
                "same_context_epoch": same_epoch,
                "context_epoch_transition": epoch_transition,
                "previous_output_tokens": previous.get("output_tokens") if previous is not None else None,
                "completion_to_queue_ms": gap,
                "distinct_known_other_contexts": distance,
                "other_request_starts": starts,
                "unknown_context_request_starts": unknown,
                "prompt_tokens": prompt,
                "reported_host_full_tokens": host,
                "reported_device_full_tokens": attrs.get("cached_tokens_device"),
            })
        current = {
            "request_id": request, "context_epoch": epoch,
        }
        active[request] = current
        if scope is None:
            unknown_starts.append(ordinal)
        else:
            latest[scope] = current
            if scope in last_service:
                sorted_last.pop(bisect_left(sorted_last, last_service[scope]))
            last_service[scope] = ordinal
            sorted_last.append(ordinal)
    if completed_through is None or completed_through < end:
        raise ValueError("native completion observations do not cover the requested window")
    rapid = [
        row for row in rows
        if row["preceding_status"] == "previous_completed_request"
        and row["completion_to_queue_ms"] <= 1000
    ]
    known_competition = [row for row in rapid if row["unknown_context_request_starts"] == 0]
    bands, matched = defaultdict(list), defaultdict(list)
    for row in known_competition:
        band = competition_band(row["distinct_known_other_contexts"])
        bands[band].append(row)
        if row["prompt_tokens"] is not None:
            lower = row["prompt_tokens"] // 4096 * 4096
            matched[f"{lower}_to_{lower + 4095}/{band}"].append(row)
    return {
        "summary": cohort_summary(rows),
        "preceding_status_counts": dict(Counter(row["preceding_status"] for row in rows)),
        "context_epoch_transition_counts": dict(
            Counter(row["context_epoch_transition"] for row in rows)
        ),
        "rapid_same_context": cohort_summary(rapid),
        "rapid_same_epoch": cohort_summary(
            [row for row in rapid if row["same_context_epoch"] is True]
        ),
        "rapid_same_context_known_competition": cohort_summary(known_competition),
        "rapid_same_context_by_competition": {
            key: cohort_summary(value) for key, value in sorted(bands.items())
        },
        "rapid_same_context_by_prompt_and_competition": {
            key: cohort_summary(value) for key, value in sorted(matched.items())
        },
        "native_completion_observed_through_ts_ms": completed_through,
        "pending_observed_requests_at_prefix_end": len(active),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-report", type=Path, required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output exists; choose a fresh report ID")
    raw = args.window_report.read_bytes()
    basis = json.loads(raw)
    arms = {}
    for name, run in basis["runs"].items():
        windows = [
            row for row in run["windows"]
            if row["start_seconds"] == args.start_seconds and row["end_seconds"] == args.end_seconds
        ]
        if len(windows) != 1 or windows[0].get("service_window_observed_complete") is not True:
            raise ValueError("requested complete service window is absent")
        alignment = run["clock_alignment"]
        if alignment.get("native_client_clock_validated") is not True:
            raise ValueError("native/client clock is unverified")
        anchor = alignment["anchor_unix_ms"]
        reader = SealedRecords()
        result = service_competition(
            reader(Path(run["run"]) / "server/runtime_events.sglang.jsonl"),
            anchor + 1000 * args.start_seconds, anchor + 1000 * args.end_seconds,
        )
        arms[name] = {**result, "run": run["run"], "input_jsonl_prefixes": reader.prefixes}
    report = {
        "schema_version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
        "start_seconds": args.start_seconds, "end_seconds": args.end_seconds,
        "window_source": {
            "path": str(args.window_report), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "arms": arms,
        "auditor_sources": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__), ROOT / "scripts/audit_native_system_costs.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "semantics": [
            "Native llm_submit is emitted at first service registration but its timestamp is the earlier queue time. Intervening starts are counted by serialized native-event order, never by that backdated timestamp.",
            "Window cohorts include observed served requests queued in the window. Still-unserved requests are absent and service can occur after the queue window.",
            "Context competition counts distinct known workflow/invocation/context identities started after the immediately preceding observed generation completed and before this first-service observation.",
            "This is not physical-page LRU reuse distance, allocated HBM, a deduplicated working-set size, per-layer forward reuse or exposed restoration latency. Decode accesses, eviction and shared prefixes are unobserved.",
            "Same context/epoch does not establish the same native session, namespace or physical copy generation. Epoch changes are not automatically compaction.",
            "Rapid continuations include all observed immediately preceding completions of the same logical context within 1000 ms of queue registration. Equal epochs are a separate diagnostic subset, never a condition for including a logical continuation.",
            "Unknown competing contexts are counted separately and excluded from known-competition subcohorts, not treated as zero. Unfinished predecessors remain unknown.",
            "Conditional Host-hit fractions across historical single runs are associations; requests are clustered within workflows. They do not establish cache-policy causality or performance gains.",
        ],
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "arms": {name: {
            "rapid_same_context": arm["rapid_same_context"],
            "by_competition": arm["rapid_same_context_by_competition"],
        } for name, arm in arms.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
