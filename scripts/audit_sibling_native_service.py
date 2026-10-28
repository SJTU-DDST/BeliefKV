#!/usr/bin/env python3
"""Check sibling service journals against actual workers and explicit JOINs."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_join_reentry_service import identity
from scripts.audit_sealed_wait_prefetch import SealedRecords


EVENT = "agent_join_siblings_native_service"
CONFIDENCE = frozenset(("declared_runtime", "observed_runtime"))


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def key_identity(key):
    return [
        key["root_workflow_id"], key["invocation_id"],
        key["context_id"], key["context_epoch"],
    ]


def validate_journal(row):
    ids, keys = row.get("request_ids"), row.get("keys")
    selected, returned = row.get("ready_to_selected_ms"), row.get("ready_to_forward_return_ms")
    if (
        row.get("event") != EVENT
        or not all(finite(row.get(name)) and row[name] >= 0 for name in ("ts_ms", "monotonic_s"))
        or type(row.get("join_id")) is not str or not row["join_id"]
        or type(ids) is not list or not 1 <= len(ids) <= 24
        or any(type(rid) is not str or not rid for rid in ids) or len(set(ids)) != len(ids)
        or any(type(values) is not list or len(values) != len(ids)
               for values in (keys, selected, returned))
    ):
        raise ValueError("invalid sibling native-service journal")
    owners = set()
    for rid, key, before, after in zip(ids, keys, selected, returned, strict=True):
        if (
            type(key) is not dict or key.get("request_id") != rid
            or any(type(key.get(name)) is not str or not key[name] for name in (
                "root_workflow_id", "invocation_id", "context_id",
            ))
            or any(type(key.get(name)) is not int or key[name] < 0
                   for name in ("context_epoch", "attempt_id"))
            or not all(finite(value) and value >= 0 for value in (before, after))
            or before > after
        ):
            raise ValueError("invalid sibling native-service identity or ready timing")
        session, generation = key.get("session_id"), key.get("session_generation")
        if (session is None) != (generation is None) or session is not None and (
            type(session) is not str or not session
            or type(generation) is not int or generation < 0
        ):
            raise ValueError("invalid sibling native session identity")
        owner = key["root_workflow_id"], key["invocation_id"]
        if owner in owners:
            raise ValueError("sibling journal repeats an agent")
        owners.add(owner)
    if len({owner[0] for owner in owners}) != 1:
        raise ValueError("sibling journal mixes workflows")


def inspect(opportunities, replay_events, receipts, services):
    journals = []
    seen = set()
    for row in opportunities:
        if row.get("event") != EVENT:
            continue
        validate_journal(row)
        for key in row["keys"]:
            token = key["request_id"], key["attempt_id"]
            if token in seen:
                raise ValueError("duplicate sibling service for one request attempt")
            seen.add(token)
        journals.append(row)
    joins, waits = {}, {}
    for event in replay_events:
        if event.get("kind") not in ("join_create", "join_wait", "join_satisfied"):
            continue
        workflow, join_id = event.get("workflow_id"), event.get("join_id")
        if (
            type(workflow) is not str or not workflow
            or type(join_id) is not str or not join_id
            or not finite(event.get("ts_ms"))
        ):
            raise ValueError("invalid observed JOIN event")
        token = workflow, join_id
        if event["kind"] == "join_create":
            if token in joins:
                raise ValueError("ambiguous repeated JOIN creation")
            joins[token] = event
        else:
            waits.setdefault(token, []).append(event)
    completions = {}
    for row in receipts:
        rid = row.get("request_id")
        if type(rid) is not str or not rid or rid in completions:
            raise ValueError("invalid or duplicate completed receipt")
        if row.get("exact_tokens_verified") is not True:
            raise ValueError("receipt does not verify exact work")
        completions[rid] = row
    ends, workers, clock, first, forwards = [], [], None, {}, set()
    for row in services:
        if row.get("event") != "gpu_service_sample":
            continue
        end, start, launch = (
            row.get("complete_monotonic_ms"), row.get("service_start_monotonic_ms"),
            row.get("launch_monotonic_ms"),
        )
        members, domain = row.get("request_samples"), row.get("monotonic_clock_domain")
        if (
            not all(finite(value) for value in (launch, start, end))
            or not 0 <= launch <= start <= end
            or ends and start < ends[-1] - .001
            or type(domain) is not str or not domain or clock is not None and domain != clock
            or row.get("sample_id") != f"native-service-{len(workers) + 1:09d}"
            or type(row.get("forward_iter")) is not int or row["forward_iter"] in forwards
            or row.get("phase") not in ("prefill", "decode")
            or not finite(row.get("complete_ts_ms"))
            or type(members) is not list or not members
            or len(members) != row.get("batch_size")
            or len({member.get("request_id") for member in members}) != len(members)
        ):
            raise ValueError("invalid native worker sequence, clock or membership")
        clock = domain
        forwards.add(row["forward_iter"])
        for member in members:
            rid = member.get("request_id")
            first[rid] = min(first.get(rid, math.inf), launch)
        workers.append(row)
        ends.append(end)
    result = []
    for ordinal, journal in enumerate(journals, 1):
        now = journal["monotonic_s"] * 1000.
        selected_times = [
            now - returned + selected for returned, selected in zip(
                journal["ready_to_forward_return_ms"], journal["ready_to_selected_ms"],
                strict=True,
            )
        ]
        row = {
            "journal_number": ordinal, "join_id": journal["join_id"],
            "request_ids": journal["request_ids"], "members": len(journal["keys"]),
            "recorded_attempt_ids": [key["attempt_id"] for key in journal["keys"]],
            "attempt_identity_independently_verified": False,
            "status": "native_worker_not_in_sealed_prefix",
            "ready_to_selected_ms": journal["ready_to_selected_ms"],
            "ready_to_forward_return_ms": journal["ready_to_forward_return_ms"],
        }
        result.append(row)
        index = bisect_left(ends, now)
        if index == len(workers):
            continue
        worker = workers[index]
        if worker["service_start_monotonic_ms"] > now + .001:
            row["status"] = "journal_outside_recorded_worker_interval"
            continue
        row["worker_sample_id"] = worker["sample_id"]
        row["forward_iter"] = worker["forward_iter"]
        row["phase"] = worker["phase"]
        samples = {member["request_id"]: member for member in worker["request_samples"]}
        if any(
            key["request_id"] not in samples
            or identity(samples[key["request_id"]]) != tuple(key_identity(key))
            for key in journal["keys"]
        ):
            row["status"] = "native_worker_membership_or_identity_mismatch"
            continue
        if any(when > worker["launch_monotonic_ms"] + .01 for when in selected_times):
            row["status"] = "selection_after_native_launch"
            continue
        offset = journal["ts_ms"] - now
        worker_offset = worker["complete_ts_ms"] - worker["complete_monotonic_ms"]
        if abs(offset - worker_offset) > 5.:
            row["status"] = "journal_native_clock_alignment_unproven"
            continue
        workflow = journal["keys"][0]["root_workflow_id"]
        token = workflow, journal["join_id"]
        join = joins.get(token)
        if join is None:
            row["status"] = "explicit_join_not_in_sealed_prefix"
            continue
        members = join.get("member_invocation_ids")
        if (
            join.get("confidence") not in CONFIDENCE
            or (join.get("attributes") or {}).get("mode") != "all"
            or type(members) is not list or len(set(members)) < 2
            or any(key["invocation_id"] not in members for key in journal["keys"])
        ):
            row["status"] = "explicit_parallel_all_join_membership_unproven"
            continue
        events = sorted(waits.get(token, ()), key=lambda event: event["ts_ms"])
        valid = True
        for selected in selected_times:
            prior = [event for event in events if event["ts_ms"] <= selected + .001]
            active = bool(
                prior and prior[-1]["kind"] == "join_wait"
                and prior[-1].get("confidence") in CONFIDENCE
                and join["ts_ms"] <= selected
            )
            valid &= active
        if not valid:
            row["status"] = "actual_parent_join_wait_at_selection_unproven"
            continue
        row["same_native_batch_and_explicit_blocking_join_verified"] = True
        row["request_had_earlier_native_launch"] = [
            first[key["request_id"]] < worker["launch_monotonic_ms"] - .001
            for key in journal["keys"]
        ]
        row["status"] = "completion_or_session_not_in_sealed_prefix"
        bound = []
        for key in journal["keys"]:
            receipt = completions.get(key["request_id"])
            native = (receipt or {}).get("native_receipt") or {}
            bound.append(bool(
                native.get("request_id") == key["request_id"]
                and native.get("identity") == key_identity(key)
                and native.get("clock_domain") == clock
                and key.get("session_id") is not None
                and type(native.get("session_generation")) is int
                and (native.get("session_id"), native.get("session_generation"))
                == (key.get("session_id"), key.get("session_generation"))
                and finite(receipt.get("submitted_monotonic_ms"))
                and finite(receipt.get("native_complete_monotonic_ms"))
                and receipt["submitted_monotonic_ms"] <= worker["launch_monotonic_ms"]
                <= receipt["native_complete_monotonic_ms"]
                and now <= receipt["native_complete_monotonic_ms"] + .01
            ))
        row["completed_session_bindings_verified"] = bound
        if all(bound):
            row["status"] = "native_batch_join_and_completed_sessions_verified"
    verified = [row for row in result if row["status"] == "native_batch_join_and_completed_sessions_verified"]
    return {
        "summary": {
            "journal_records": len(result),
            "journal_request_attempts": sum(row["members"] for row in result),
            "status_counts": dict(Counter(row["status"] for row in result)),
            "verified_records": len(verified),
            "verified_request_attempts": sum(row["members"] for row in verified),
            "verified_multi_member_batches": sum(row["members"] >= 2 for row in verified),
            "verified_prefill_records": sum(row["phase"] == "prefill" for row in verified),
            "native_worker_samples": len(workers),
            "all_journal_records_verified": len(result) == len(verified),
            "positive_service_evidence": bool(verified),
        },
        "records": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sealed-audit", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier audit evidence")
    reader = SealedRecords()
    arm = args.arm.resolve()
    prior = None
    if args.sealed_audit is not None:
        basis = json.loads(args.sealed_audit.read_bytes())
        expected_paths = {
            str(arm / name) for name in (
                "opportunities/admission_opportunities.jsonl",
                "replay/runtime_events.replay.jsonl", "replay/native_requests.jsonl",
                "server/runtime_audit.jsonl",
            )
        }
        if basis.get("arm") != str(arm) or set(basis.get("input_jsonl_prefixes", ())) != expected_paths:
            parser.error("sealed source must describe this exact arm and all four inputs")
        reader.prefixes.update(basis["input_jsonl_prefixes"])
        prior = {"path": str(args.sealed_audit.resolve()), "sha256": sha256_file(args.sealed_audit)}
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "replay/runtime_events.replay.jsonl"),
        reader(arm / "replay/native_requests.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "independently_sealed_service_evidence_not_complete_performance",
        "arm": str(arm), **result, "input_jsonl_prefixes": reader.prefixes,
        "prior_sealed_audit": prior,
        "operator_sha256": sha256_file(Path(__file__)),
        "helper_sources_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_sealed_wait_prefetch.py", "scripts/audit_join_reentry_service.py",
            )
        },
        "gpu_experiment_started": False, "performance_accepted": False,
        "limitations": [
            "Missing journal, worker, JOIN or completion evidence remains unknown.",
            "Zero records are not positive evidence or a count of all service opportunities.",
            "Journals name first forward after a request admission, not the agent's first-ever service.",
            "Request attempt IDs are recorded but not independently present in native worker samples.",
            "Ready-to-selected and ready-to-forward-return are distinct from kernel timing.",
            "Shared native batch and blocking JOIN evidence do not prove a JCT benefit.",
            "Repeated planning opportunities are not an independent denominator for service coverage.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
