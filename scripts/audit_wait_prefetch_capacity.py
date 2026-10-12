#!/usr/bin/env python3
"""Audit recorded wait-prefetch capacity refusals without inventing a live census."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_sealed_wait_prefetch import SealedRecords


def identity(row):
    return (
        row["source"], row["context_id"], row["context_epoch"],
        row.get("join_id"), tuple(row.get("active_tool_ids") or ()),
    )


def audit(path):
    sealed = SealedRecords()
    counts = defaultdict(Counter)
    events = defaultdict(lambda: defaultdict(set))
    examples = defaultdict(list)
    reclaimed, demoted = defaultdict(Counter), defaultdict(Counter)
    split = defaultdict(Counter)
    last_reclaim = {}
    range_ms = [None, None]
    for row in sealed(path):
        ts = row.get("ts_ms")
        if ts is not None:
            range_ms[0] = ts if range_ms[0] is None else min(range_ms[0], ts)
            range_ms[1] = ts if range_ms[1] is None else max(range_ms[1], ts)
        kind, source = row["event"], row.get("source", "unknown")
        if kind == "wait_prefetch_full_extent_split":
            split[source]["records"] += 1
            split[source]["fragment_full_tokens"] += row["fragment_full_tokens"]
            continue
        if kind in ("wait_prefetch_capacity_reclaimed", "parent_pressure_demoted"):
            target = reclaimed if kind == "wait_prefetch_capacity_reclaimed" else demoted
            target[source]["records"] += 1
            units = row.get("freed_units") or {}
            target[source]["full_tokens"] += units.get("0", units.get(0, 0))
            target[source]["mamba_slots"] += units.get("2", units.get(2, 0))
            if kind == "wait_prefetch_capacity_reclaimed":
                last_reclaim[(source, row["context_id"], row["context_epoch"])] = row
            continue
        if kind != "wait_prefetch_plan":
            continue
        key = identity(row)
        counts[source]["plan_records"] += 1
        budget = row.get("capacity_budget") or {}
        categories = []
        if row.get("capacity_reclaim_probe"):
            categories.append("planned_backed_cold_capacity_probe")
        if row.get("partial_full_tokens", 0):
            categories.append("planned_partial_extent")
        if row.get("restore_batch_scope") == "partial_observed_host_backed_full":
            categories.append("planned_burst_with_observed_full_remainder")
            if row.get("reason") in ("submitted", "submitted_partial"):
                categories.append("submitted_burst_with_observed_full_remainder")
        if row.get("no_step_reason") == "prefetch_capacity_budget":
            categories.append("capacity_rejected")
            detail = row.get("blocked_detail")
            categories.append(detail or "capacity_reason_unknown")
            full_limit = budget.get("full_limit_tokens")
            if detail == "prefetch_full_capacity_budget" and type(full_limit) is int:
                categories.append(
                    "first_extent_too_large_with_positive_full_budget"
                    if full_limit > 0 else "no_full_budget_after_reserve"
                )
                if full_limit > 0:
                    remaining = row.get("predicted_remaining_ms")
                    window = row.get("start_window_ms")
                    if (
                        isinstance(remaining, (int, float))
                        and isinstance(window, (int, float))
                        and remaining <= window
                    ):
                        categories.append("first_extent_refused_inside_recorded_start_window")
                    categories.append(
                        "positive_budget_has_current_free_pages"
                        if budget.get("device_full_free_tokens", 0)
                        > budget.get("reserve_full_tokens", 0)
                        else "positive_budget_requires_eviction"
                    )
            recent = last_reclaim.get((source, row["context_id"], row["context_epoch"]))
            if recent is not None and 0 <= ts - recent["ts_ms"] <= 100:
                categories.append("refusal_with_same_context_reclaim_within_100ms")
            diagnosis = row.get("capacity_reclaim")
            if not isinstance(diagnosis, dict):
                categories.append("no_same_action_cold_candidate_census")
            else:
                if diagnosis.get("native_full_scan_truncated"):
                    categories.append("same_action_native_scan_truncated")
                if diagnosis.get("full_target_satisfied") is False:
                    categories.append("same_action_bounded_reclaim_insufficient")
                for check in diagnosis.get("waiting_candidate_checks", ()):
                    if check.get("invocation_state") == "wait_tool":
                        categories.append("same_action_tool_wait_candidate_checked")
                        break
        for category in set(categories):
            counts[source][category] += 1
            events[source][category].add(key)
            if len(examples[(source, category)]) < 5 and not any(
                identity(example) == key for example in examples[(source, category)]
            ):
                examples[(source, category)].append(row)
    return {
        "sources": {
            source: {
                "record_counts": dict(counter),
                "distinct_recorded_wait_episodes": {
                    name: len(keys) for name, keys in events[source].items()
                },
                "examples": {
                    name: examples[(source, name)] for name in events[source]
                },
            }
            for source, counter in counts.items()
        },
        "actual_reclaim_records": {source: dict(value) for source, value in reclaimed.items()},
        "actual_waiting_agent_demotions": {source: dict(value) for source, value in demoted.items()},
        "actual_full_extent_splits": {source: dict(value) for source, value in split.items()},
        "observed_timestamp_range_ms": range_ms,
        "input_jsonl_prefixes": sealed.prefixes,
        "interpretation": [
            "Categories overlap; records are not independent events or a performance benefit.",
            "A zero-step FULL capacity rejection with a positive FULL limit proves the "
            "first complete extent did not fit the recorded budget. It does not prove "
            "that timing, DMA and next-request reuse would all have succeeded.",
            "A positive limit that includes evictable counts is not actual allocator capacity.",
            "A refusal within 100ms of the same context's reclaim is a temporal association, "
            "not a global census or unique reclaim-attempt identifier.",
            "Absent action-local diagnostics, sampled sessions from other timestamps cannot "
            "prove that no safe migratable KV existed when the action was rejected.",
            "Actual waiting-agent demotions also occur for demand admission; keep them "
            "separate from predictive wait-prefetch reclaim.",
            "A native extent split changes the tree only; it is not DMA, complete "
            "checkpoint readiness, admission, or next-request reuse.",
            "Observed FULL remainders describe the captured closure, which may include "
            "output beyond the next reusable input checkpoint. A burst covering all "
            "observed Host-backed FULL still does not prove required state is resident.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collection-commit", required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve the previous audit; choose a new output")
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    report = audit(args.arm / "opportunities/admission_opportunities.jsonl")
    report.update(
        arm=str(args.arm.resolve()), collection_commit=args.collection_commit,
        audit_started_at_local=started,
        audit_finished_at_local=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "sources": {
            source: {key: value for key, value in data.items() if key != "examples"}
            for source, data in report["sources"].items()
        },
        "actual_reclaim_records": report["actual_reclaim_records"],
        "actual_waiting_agent_demotions": report["actual_waiting_agent_demotions"],
        "actual_full_extent_splits": report["actual_full_extent_splits"],
    }, indent=2))


if __name__ == "__main__":
    main()
