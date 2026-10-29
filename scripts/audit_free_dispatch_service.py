#!/usr/bin/env python3
"""Associate free-budget grants with exact native service and completed receipts."""

from collections import Counter
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_collecting_agent_pipeline import validate_receipts
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def identity(row, *, grant=False):
    fields = (
        "request_id", "root_workflow_id" if grant else "workflow_id",
        "invocation_id", "context_id", "context_epoch", "attempt_id",
        "session_id", "session_generation",
    )
    key = tuple(row.get(name) for name in fields)
    if (
        any(type(key[index]) is not str or not key[index] for index in (0, 1, 2, 3, 6))
        or any(type(key[index]) is not int or key[index] < 0 for index in (4, 5, 7))
    ):
        raise ValueError("grant/service requires exact request, context, attempt and session ownership")
    return key


def associate(grants, submissions, batches, receipts, *, complete=False):
    expanded, first, offsets, domains = [], {}, [], set()
    counts = Counter()
    for grant in grants:
        members = grant.get("programs")
        before, after, required = (
            grant.get("remaining_before_tokens"), grant.get("remaining_after_tokens"),
            grant.get("required_tokens"),
        )
        if (
            type(members) is not list or not 1 <= len(members) <= 4
            or grant.get("native_capacity_authority_preserved") is not True
            or not finite_number(grant.get("ts_ms"))
            or not finite_number(grant.get("monotonic_s"))
            or any(type(value) is not int or value < 0 for value in (before, after, required))
            or before - after != required
        ):
            raise ValueError("invalid free-budget grant authority, clock or complete capacity")
        total = 0
        for member in members:
            key = identity(member["key"], grant=True)
            observed, budget = member.get("observed_context_tokens"), member.get("complete_budget_tokens")
            if type(observed) is not int or observed <= 0 or budget != observed + 100:
                raise ValueError("grant lacks the complete observed context and declared buffer")
            total += budget
            expanded.append((key, grant, observed, budget))
        if total != required:
            raise ValueError("grant member budgets do not reconcile")
    target_ids = {key[0] for key, *_ in expanded}
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        for member in batch.get("request_samples", ()):
            if member.get("request_id") not in target_ids:
                continue
            if any(member.get(name) is None for name in (
                "attempt_id", "session_id", "session_generation",
            )):
                counts["service_samples_without_full_attempt_session_identity"] += 1
                continue
            key = identity(member)
            timestamp = batch.get("service_start_ts_ms")
            wall, monotonic = batch.get("complete_ts_ms"), batch.get("complete_monotonic_ms")
            domain = batch.get("monotonic_clock_domain")
            if not all(finite_number(value) for value in (timestamp, wall, monotonic)) or not domain:
                raise ValueError("native service clock is unobserved")
            offsets.append(wall - monotonic)
            domains.add(domain)
            if key not in first or timestamp < first[key]["ts_ms"]:
                first[key] = {"ts_ms": timestamp, "phase": member["phase"]}
    if offsets and (len(domains) != 1 or max(offsets) - min(offsets) > 5.):
        raise ValueError("native service clocks are inconsistent")
    offset = statistics.median(offsets) if offsets else None
    indexed_submits = {}
    for submission in submissions:
        if submission.get("kind") != "llm_submit":
            continue
        rid = submission.get("attributes", {}).get("request_id")
        if rid in target_ids:
            if rid in indexed_submits:
                raise ValueError("duplicate native grant-recipient submission")
            indexed_submits[rid] = submission
    indexed_receipts = {row["request_id"]: row for row in receipts}
    rows = []
    for key, grant, observed, budget in expanded:
        rid, workflow, invocation, context, epoch, attempt, session, generation = key
        row = {
            "request_id": rid, "workflow_id": workflow, "invocation_id": invocation,
            "context_id": context, "context_epoch": epoch, "attempt_id": attempt,
            "session_id": session, "session_generation": generation,
            "grant_ts_ms": grant["ts_ms"], "complete_budget_tokens": budget,
            "first_service_observed": key in first, "completion_receipt_observed": False,
        }
        counts["grant_episodes"] += 1
        submission = indexed_submits.get(rid)
        if submission is not None:
            if (
                tuple(submission.get(name) for name in ("workflow_id", "invocation_id", "context_id", "context_epoch"))
                != (workflow, invocation, context, epoch)
                or submission["attributes"]["prompt_tokens"] != observed
            ):
                raise ValueError("logical grant differs from actual native input ownership or size")
            row["queue_to_grant_ms"] = grant["ts_ms"] - submission["ts_ms"]
            if row["queue_to_grant_ms"] < -5.:
                raise ValueError("grant precedes native request registration")
            counts["native_input_verified"] += 1
        if key in first:
            if abs(grant["ts_ms"] - grant["monotonic_s"] * 1000. - offset) > 5.:
                raise ValueError("grant and native service clock alignment changed")
            latency = first[key]["ts_ms"] - grant["ts_ms"]
            if latency < -5.:
                raise ValueError("native service preceded its same-attempt grant")
            row.update(grant_to_first_service_ms=max(0., latency), first_service_phase=first[key]["phase"])
            counts["first_native_service_verified"] += 1
        receipt = indexed_receipts.get(rid)
        if receipt is not None:
            native = receipt["native_receipt"]
            if (
                tuple(native["identity"]) != (workflow, invocation, context, epoch)
                or native["session_id"] != session or native["session_generation"] != generation
            ):
                raise ValueError("completed native receipt differs from the grant owner")
            row["completion_receipt_observed"] = True
            counts["completed_receipt_verified"] += 1
        row["status"] = (
            "service_and_completion_verified"
            if row["first_service_observed"] and row["completion_receipt_observed"] and submission is not None
            else "open_or_cross_stream_coverage_unknown"
        )
        rows.append(row)
    unique = {key for key, *_ in expanded}
    counts["unique_granted_request_attempts"] = len(unique)
    counts["repeated_grants_to_same_attempt"] = len(expanded) - len(unique)
    counts["open_or_cross_stream_coverage_unknown"] = sum(row["status"] != "service_and_completion_verified" for row in rows)
    if complete and counts["open_or_cross_stream_coverage_unknown"]:
        raise ValueError("complete-work audit has an unverified grant recipient")
    return {
        "counts": dict(counts),
        "grant_to_first_native_service_ms": distribution([
            row["grant_to_first_service_ms"] for row in rows if row["first_service_observed"]
        ]),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--expected-receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior grant/service audits")
    plan = causal.load(args.registration)
    causal.comparison.verify_sources(plan)
    state = causal.load(args.registration.with_suffix(".state.json"))
    if args.complete:
        if complete_work(args.registration) is None:
            raise ValueError("require exact complete work, fresh drain and actual owned exit")
    elif state.get("status") != "running_full" or not all(
        causal.reservation.alive(state.get(name)) for name in ("watcher", "server", "driver")
    ):
        raise ValueError("live audit requires actual owned running handles")
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    grants = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "agent_dispatch_free_capacity_admitted"
    ]
    expected = causal.receipt_work(args.expected_receipts, sampling_mode="position")
    receipts = list(reader(arm / "replay/native_requests.jsonl"))
    verified = validate_receipts(receipts, expected)
    submissions = list(reader(arm / "server/runtime_events.sglang.jsonl"))
    result = associate(
        grants, submissions, reader(arm / "server/runtime_audit.jsonl"), receipts,
        complete=args.complete,
    )
    if args.complete:
        if verified != plan["source_plan"]["requests"]:
            raise ValueError("not every expected request has completed")
        if any(Path(path).stat().st_size != seal["complete_prefix_bytes"] for path, seal in reader.prefixes.items()):
            raise ValueError("complete native evidence is still changing")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration), complete_work_verified=args.complete,
        expected_requests=len(expected), exact_completed_receipts=verified,
        input_jsonl_prefixes=reader.prefixes, operator_sha256=sha256_file(Path(__file__)),
        expected_receipts_sha256=sha256_file(args.expected_receipts),
        performance_accepted=False,
        semantics=[
            "Grants are logical authority only; exact same-attempt/session native service is checked separately.",
            "Receipts retain original exact tokens and stopping; they identify context/session, not a per-attempt CUDA completion.",
            "Live streams are sealed independently, not atomically. Open observations remain unknown, not failures or absent future service.",
            "Native service is the worker interval boundary, not kernel start; sampled waits and grant latencies are not removable JCT or isolated benefit.",
            "Every grant is retained, including repeated grants. No tail or request is removed.",
        ],
    )
    causal.reservation.write_json(args.output, result)
    print(json.dumps({key: result[key] for key in (
        "counts", "grant_to_first_native_service_ms", "exact_completed_receipts", "complete_work_verified",
    )}, indent=2))


if __name__ == "__main__":
    main()
