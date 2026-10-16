#!/usr/bin/env python3
"""Audit frontend spans and hybrid prefix boundaries from sealed event prefixes."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics


STAGES = {
    "receipt_to_conversion_start": (
        "api_received_ts_ms", "chat_conversion_started_ts_ms",
    ),
    "chat_conversion_including_template_tokenization": (
        "chat_conversion_started_ts_ms", "chat_conversion_finished_ts_ms",
    ),
    "post_conversion_to_tokenized": (
        "chat_conversion_finished_ts_ms", "api_tokenized_ts_ms",
    ),
    "tokenized_to_dispatch": (
        "api_tokenized_ts_ms", "api_dispatch_started_ts_ms",
    ),
    "dispatch_to_scheduler": (
        "api_dispatch_started_ts_ms", "scheduler_received_ts_ms",
    ),
    "scheduler_to_queue_registration": (
        "scheduler_received_ts_ms", "native_queue_registered_ts_ms",
    ),
    "receipt_to_queue_registration": (
        "api_received_ts_ms", "native_queue_registered_ts_ms",
    ),
}


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}

    def percentile(fraction):
        position = (len(values) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    return {
        "count": len(values), "sum": sum(values), "min": values[0],
        "p50": statistics.median(values), "p90": percentile(.9),
        "p99": percentile(.99), "max": values[-1],
    }


def finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def union_ms(intervals):
    total, end = 0., -math.inf
    for left, right in sorted(intervals):
        total += max(0., right - max(left, end))
        end = max(end, right)
    return total


def read_prefix(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        data = stream.read(size)
    complete = data[:data.rfind(b"\n") + 1]
    return [
        json.loads(line) for line in complete.splitlines() if line.strip()
    ], {
        "path": str(path.resolve()), "observed_size_bytes": size,
        "analyzed_bytes": len(complete),
        "sha256": hashlib.sha256(complete).hexdigest(),
        "discarded_partial_tail_bytes": len(data) - len(complete),
    }


def prompt_bucket(tokens):
    if tokens < 8192:
        return "lt_8192"
    if tokens < 16384:
        return "8192_to_16383"
    if tokens < 32768:
        return "16384_to_32767"
    return "ge_32768"


def summarize_rows(rows):
    stage_values, stage_intervals = defaultdict(list), defaultdict(list)
    for row in rows:
        for name, (left, right) in row["stage_intervals_ms"].items():
            stage_values[name].append(right - left)
            stage_intervals[name].append((left, right))
    valid_prefix = [row for row in rows if row["cache_status"] == "observed"]
    positive = [
        row for row in valid_prefix
        if row["matched_full_beyond_effective_prefix_tokens"] > 0
    ]
    branch = [
        row for row in positive if row["mamba_branching_seqlen"] is not None
    ]
    uncached = sum(row["uncached_prompt_tokens"] for row in valid_prefix)
    delta = sum(
        row["matched_full_beyond_effective_prefix_tokens"] for row in valid_prefix
    )
    return {
        "requests": len(rows),
        "stages_ms": {
            name: {
                **distribution(stage_values[name]),
                "interval_union_ms": union_ms(stage_intervals[name]),
                "missing_or_invalid_requests": len(rows) - len(stage_values[name]),
            }
            for name in STAGES
        },
        "prompt_tokens": distribution(row["prompt_tokens"] for row in rows),
        "effective_cache_hit_tokens": distribution(
            row["cache_hit_tokens"] for row in valid_prefix
        ),
        "uncached_prompt_tokens": distribution(
            row["uncached_prompt_tokens"] for row in valid_prefix
        ),
        "hybrid_prefix": {
            "observed_requests": len(valid_prefix),
            "cache_status_counts": dict(Counter(row["cache_status"] for row in rows)),
            "positive_full_match_gap_requests": len(positive),
            "positive_gap_with_branching_target_requests": len(branch),
            "positive_gap_tokens": distribution(
                row["matched_full_beyond_effective_prefix_tokens"] for row in positive
            ),
            "gap_fraction_of_observed_uncached_tokens": delta / uncached if uncached else None,
            "branching_target_gap_tokens": distribution(
                row["matched_full_beyond_effective_prefix_tokens"] for row in branch
            ),
            "gap_at_least_threshold": {
                str(threshold): {
                    "requests": sum(
                        row["matched_full_beyond_effective_prefix_tokens"] >= threshold
                        for row in positive
                    ),
                    "tokens": sum(
                        row["matched_full_beyond_effective_prefix_tokens"]
                        for row in positive
                        if row["matched_full_beyond_effective_prefix_tokens"] >= threshold
                    ),
                }
                for threshold in (64, 512, 4096)
            },
        },
    }


def analyze(events):
    rows, seen = [], set()
    ignored = Counter()
    for event in events:
        if event.get("kind") != "llm_submit":
            ignored[str(event.get("kind"))] += 1
            continue
        attrs = event.get("attributes") or {}
        request = attrs.get("request_id")
        if not request or request in seen:
            raise ValueError(f"missing or duplicate request identity: {request!r}")
        seen.add(request)
        prompt = attrs.get("prompt_tokens")
        if not isinstance(prompt, int) or isinstance(prompt, bool) or prompt < 0:
            raise ValueError(f"invalid prompt length for {request}")
        timing = attrs.get("native_ingress_timing_ms") or {}
        intervals = {}
        for name, (left_key, right_key) in STAGES.items():
            left, right = timing.get(left_key), timing.get(right_key)
            if finite_number(left) and finite_number(right) and left <= right:
                intervals[name] = (left, right)
        device, host = attrs.get("cached_tokens_device"), attrs.get("cached_tokens_host")
        full = attrs.get("native_full_kv_hit_length")
        known = all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (device, host, full)
        )
        status, gap, cached = "missing", None, None
        if known:
            cached = device + host
            if cached <= prompt and full <= prompt:
                status = "observed"
                gap = max(0, full - cached)
            else:
                status = "invalid_lengths"
        branch = attrs.get("native_mamba_branching_seqlen")
        row = {
            "request_id": request, "workflow_id": event.get("workflow_id"),
            "invocation_id": event.get("invocation_id"),
            "context_id": event.get("context_id"),
            "context_epoch": event.get("context_epoch"),
            "prompt_tokens": prompt, "stage_intervals_ms": intervals,
            "cache_status": status, "cache_hit_tokens": cached,
            "uncached_prompt_tokens": prompt - cached if status == "observed" else None,
            "full_kv_hit_length": full,
            "matched_full_beyond_effective_prefix_tokens": gap,
            "mamba_branching_seqlen": branch if finite_number(branch) else None,
            "mamba_host_hit_slots": attrs.get("mamba_host_hit_slots"),
        }
        rows.append(row)
    grouped = defaultdict(list)
    context_seen, first, continuation = set(), [], []
    for row in rows:
        context = (
            (row["workflow_id"], row["context_id"])
            if row["workflow_id"] is not None and row["context_id"] is not None
            else ("unknown", row["request_id"])
        )
        (continuation if context in context_seen else first).append(row)
        context_seen.add(context)
    for row in rows:
        grouped[prompt_bucket(row["prompt_tokens"])].append(row)
    conversion_key = "chat_conversion_including_template_tokenization"
    conversion_rows = [
        row for row in rows if conversion_key in row["stage_intervals_ms"]
    ]
    slowest = sorted(
        conversion_rows,
        key=lambda row: (
            row["stage_intervals_ms"][conversion_key][1]
            - row["stage_intervals_ms"][conversion_key][0]
        ),
        reverse=True,
    )[:20]
    largest_gap = sorted(
        (row for row in rows if row["cache_status"] == "observed"),
        key=lambda row: row["matched_full_beyond_effective_prefix_tokens"],
        reverse=True,
    )[:20]
    return {
        "all": summarize_rows(rows),
        "by_prompt_length": {
            key: summarize_rows(group) for key, group in sorted(grouped.items())
        },
        "by_observed_context_position": {
            "first": summarize_rows(first),
            "continuation": summarize_rows(continuation),
        },
        "ignored_event_counts": dict(ignored),
        "slowest_conversion_requests": slowest,
        "largest_full_match_gap_requests": largest_gap,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    events, source = read_prefix(args.events)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "auditor_path": str(Path(__file__).resolve()),
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source": source,
        "semantics": [
            "Only requests with a native first-service registration event are present.",
            "Frontend spans are wall time, include waits, and are not exclusive CPU.",
            "Chat conversion can already include chat-template tokenization.",
            "Post-conversion tokenization alone does not measure total tokenization.",
            "Summed or union frontend spans are not exposed GPU idle or JCT savings.",
            "FULL match depth can exceed the effective all-component reuse boundary.",
            "The positive gap is a candidate token-work opportunity, not removable work.",
            "A gap does not prove a missing Mamba checkpoint is the sole cause.",
            "Branch target observations do not prove resident or recoverable checkpoints.",
            "No task quality, speedup, baseline equivalence or GPU effect is established.",
        ],
        **analyze(events),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps({
        "output": str(args.output), "source": source, "all": report["all"],
    }, ensure_ascii=True))


if __name__ == "__main__":
    main()
