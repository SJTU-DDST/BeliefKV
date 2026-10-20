#!/usr/bin/env python3
"""Audit a collecting arm using independently sealed complete JSONL prefixes."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_prefetch_lifecycle as lifecycle


class SealedRecords:
    def __init__(self):
        self.prefixes = {}

    def __call__(self, path):
        path = Path(path)
        if not path.exists():
            return
        previous = self.prefixes.get(str(path))
        limit = (
            previous["complete_prefix_bytes"] if previous else path.stat().st_size
        )
        consumed, digest = 0, hashlib.sha256()
        with path.open("rb") as stream:
            while consumed < limit:
                line = stream.readline(limit - consumed)
                if not line:
                    raise ValueError(f"short sealed prefix: {path}")
                if not line.endswith(b"\n"):
                    if previous:
                        raise ValueError(f"incomplete sealed prefix: {path}")
                    break
                consumed += len(line)
                digest.update(line)
                if line.strip():
                    yield json.loads(line)
        evidence = {
            "complete_prefix_bytes": consumed,
            "complete_prefix_sha256": digest.hexdigest(),
            "first_read_limit_bytes": (
                previous["first_read_limit_bytes"] if previous else limit
            ),
        }
        if previous and evidence != previous:
            raise ValueError(f"changed sealed prefix: {path}")
        self.prefixes[str(path)] = evidence


def dispatch_funnel(groups: dict, plans: dict) -> dict:
    sources = {}
    for source in ("join_ticket", "tool_wait"):
        counts, reasons, details = Counter(), Counter(), Counter()
        for identity, wait in groups.items():
            if identity[0] != source:
                continue
            completion = wait.get("completion_ts_ms")
            counts["observed_events"] += 1
            if completion is None:
                counts["completion_unknown"] += 1
                continue
            counts["completion_observed"] += 1
            before = [
                row for row in plans.get(identity, ()) if row["ts_ms"] < completion
            ]
            starts = [row for row in before if row["reason"] == "start_window"]
            submitted = any(
                row["reason"] in ("submitted", "submitted_partial") for row in before
            )
            counts["positive_full_plan_before_completion"] += any(
                (row.get("planned_full_tokens") or 0) > 0 for row in before
            )
            counts["start_before_completion"] += bool(starts)
            counts["submitted_before_completion"] += submitted
            counts["events_with_submitted_reason_before_completion"] += any(
                row["reason"] == "submitted" for row in before
            )
            counts["events_with_submitted_partial_reason_before_completion"] += any(
                row["reason"] == "submitted_partial" for row in before
            )
            counts["start_without_submit_before_completion"] += bool(
                starts and not submitted
            )
            reasons.update({row["reason"] for row in before})
            details.update({
                row["no_step_reason"] for row in before if row.get("no_step_reason")
            })
        sources[source] = {
            "event_counts": dict(counts),
            "events_with_observed_reason_before_completion": dict(reasons),
            "events_with_observed_no_step_reason_before_completion": dict(details),
        }
    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--collection-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.suffixes[-2:] != [".json", ".gz"]:
        parser.error("--output must end in .json.gz")
    if args.output.exists():
        parser.error("--output already exists; use a new observation filename")
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    sealed = SealedRecords()
    with patch.object(lifecycle, "records", sealed):
        report = lifecycle.audit(args.arm)
    groups = {
        lifecycle.wait_event_identity(row): row
        for row in report["wait_event_attribution"]["rows"]
    }
    plans = defaultdict(list)
    reclaimed, state = [], None
    for row in sealed(args.arm / "opportunities/admission_opportunities.jsonl"):
        if row["event"] == "admission_runtime_state":
            state = row
        elif row["event"] == "wait_prefetch_capacity_reclaimed":
            reclaimed.append(row)
        elif row["event"] == "wait_prefetch_plan":
            plans[lifecycle.wait_event_identity(row)].append(row)
    sources = dispatch_funnel(groups, plans)
    report["sealed_dispatch_funnel"] = {
        "sources": sources,
        "reclaim_records": reclaimed,
        "semantics": (
            "Independent wait episodes with recorded completion boundaries; reasons "
            "can overlap. Pending/unobserved boundaries are unknown, not misses. "
            "Submitted includes submitted and submitted_partial; their reason "
            "counts can overlap within one event. A submitted burst is not proof "
            "of a complete checkpoint or next-request reuse. Positive plans and "
            "cumulative checks do not prove available capacity."
        ),
    }
    report["runtime_state_at_recorded_prefix_end"] = state
    report["input_jsonl_prefixes"] = sealed.prefixes
    report["collection_code_commit"] = args.collection_commit
    report["audit_started_at_local"] = started
    report["audit_finished_at_local"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    report["helper_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["prefix_semantics"] = (
        "Each JSONL is capped at its first-read stat size and last complete line. "
        "Repeated reads must match length and SHA256; this is not an atomic "
        "cross-file snapshot. Demand handoff remains separate from early prefetch."
    )
    payload = json.dumps(report, indent=2, allow_nan=False).encode() + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(gzip.compress(payload, mtime=0))
    if gzip.decompress(args.output.read_bytes()) != payload:
        raise ValueError("compressed output differs from generated report")
    summary = {
        key: report[key] for key in (
            "summary", "input_jsonl_prefixes", "collection_code_commit",
            "audit_started_at_local", "audit_finished_at_local", "helper_sha256",
            "prefix_semantics",
        )
    }
    summary["sealed_dispatch_funnel"] = {
        "sources": sources, "reclaim_record_count": len(reclaimed),
    }
    summary["detail_file"] = args.output.name
    summary["detail_uncompressed_sha256"] = hashlib.sha256(payload).hexdigest()
    summary_path = args.output.with_name(
        args.output.name.removesuffix(".json.gz") + "_summary.json"
    )
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "summary": str(summary_path), "funnel": summary["sealed_dispatch_funnel"],
        "wait_events": summary["summary"]["wait_events"],
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
