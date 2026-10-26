#!/usr/bin/env python3
"""Check sampled agent loan relationships in independently sealed runtime records."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords


COUNTERS = (
    "backed_wait_admitted", "backed_wait_free_admitted", "backed_wait_probes",
    "capacity_switch:backed_wait", "join_loan_recall_admitted",
    "capacity_switch:join_loan_return", "join_loan_recall_deferred",
)


def valid_identity(value):
    return type(value) is str and bool(value)


def inspect(states):
    previous_time = -math.inf
    maximum, counts = Counter(), Counter()
    lenders, borrowers = set(), set()
    last = None
    for row in states:
        if row.get("event") != "admission_runtime_state":
            continue
        timestamp, admission = row.get("ts_ms"), row.get("agent_admission")
        if (
            type(timestamp) not in (int, float) or not math.isfinite(timestamp)
            or timestamp <= previous_time or row.get("physical_disabled") is not False
        ):
            raise ValueError("runtime samples have invalid chronology or disabled physical control")
        if (
            type(admission) is not dict
            or admission.get("strategy") != "thunderagent_retained"
            or admission.get("blocked_lending") != "backed_demand_recall"
            or admission.get("join_loan_recall") is not True
            or admission.get("native_capacity_authority_preserved") is not True
            or admission.get("join_loan_scope") != "logical_admissions_during_open_blocked_lending_period"
            or admission.get("join_loan_recall_boundary") != "submitted_join_successor;nonrunning_borrower"
        ):
            raise ValueError("runtime does not observe the declared JOIN loan policy")
        active, loans, policy = (
            admission.get("active_invocation_ids"),
            admission.get("join_loan_borrowers"), admission.get("policy_counts"),
        )
        if (
            type(active) is not list or not all(valid_identity(value) for value in active)
            or len(set(active)) != len(active)
            or type(loans) is not dict or type(policy) is not dict
            or admission.get("active_agents") != len(active)
        ):
            raise ValueError("runtime has malformed active or loan identities")
        active = set(active)
        observed_borrowers, edges = set(), 0
        for lender, members in loans.items():
            if (
                not valid_identity(lender) or lender in active
                or type(members) is not list or not all(valid_identity(value) for value in members)
                or len(set(members)) != len(members) or not set(members) <= active
            ):
                raise ValueError("an open loan has an active lender or inactive/duplicate borrower")
            lenders.add(lender)
            observed_borrowers.update(members)
            edges += len(members)
            counts["sampled_open_periods_without_current_borrower"] += not members
        borrowers.update(observed_borrowers)
        for name in COUNTERS:
            value = policy.get(name, 0)
            if type(value) is not int or value < 0 or value < maximum[name]:
                raise ValueError("loan policy counters regressed or are invalid")
            maximum[name] = value
        counts["snapshots"] += 1
        counts["snapshots_with_open_loans"] += bool(loans)
        counts["snapshots_with_borrowers"] += bool(observed_borrowers)
        counts["maximum_open_lenders"] = max(counts["maximum_open_lenders"], len(loans))
        counts["maximum_distinct_borrowers"] = max(
            counts["maximum_distinct_borrowers"], len(observed_borrowers),
        )
        counts["maximum_relationships"] = max(counts["maximum_relationships"], edges)
        if last is not None and timestamp - previous_time > 1500.:
            counts["snapshot_gaps_over_1500ms"] += 1
        previous_time, last = timestamp, row
    if last is None:
        raise ValueError("no observed JOIN loan policy samples")
    final = last["agent_admission"]
    return {
        "counts": dict(counts), "maximum_policy_counters": dict(maximum),
        "distinct_observed_lenders": len(lenders),
        "distinct_observed_borrowers": len(borrowers),
        "last_observation": {
            "ts_ms": last["ts_ms"], "active_agents": final["active_agents"],
            "open_loans": final["join_loan_borrowers"],
            "agent_counts": final.get("counts", {}),
        },
        "sampled_relationship_invariants_passed": True,
    }


def audit(arm):
    status_path = arm / "server/native_telemetry_status.json"
    status = json.loads(status_path.read_bytes())
    if (
        status.get("writer_error") is not None
        or status.get("dropped_records") != 0 or status.get("failed_records") != 0
    ):
        raise ValueError("native telemetry is unhealthy")
    reader = SealedRecords()
    result = inspect(reader(arm / "opportunities/admission_opportunities.jsonl"))
    summary_path = arm / "replay/summary.json"
    complete = False
    if summary_path.exists():
        summary = json.loads(summary_path.read_bytes())
        if any(summary.get(name) is not True for name in (
            "causal_execution_completed", "exact_tokens_verified",
            "original_native_stopping_verified",
        )):
            raise ValueError("replay summary does not establish complete work")
        if any(status.get(name) != 0 for name in (
            "pending_request_count", "pending_batch_count", "writer_queue_depth",
        )):
            raise ValueError("complete replay telemetry has not drained")
        if result["last_observation"]["open_loans"]:
            raise ValueError("completed replay still has open borrowing periods")
        complete = True
    return {
        **result, "arm": str(arm.resolve()), "complete_exact_work_replay": complete,
        "input_jsonl_prefixes": reader.prefixes,
        "native_status_sha256": sha256_file(status_path),
        "summary_sha256": sha256_file(summary_path) if complete else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve prior loan observations")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(), **audit(args.arm),
        "auditor_sha256": sha256_file(Path(__file__)),
        "scope": [
            "Sampled logical relationships only. A state can miss transitions between snapshots.",
            "Counters identify observed executed policy branches, not exact per-agent recall timestamps.",
            "No native page ownership, nonrunning status at each actual recall, DMA safety or GPU numerical equivalence is inferred from snapshots.",
            "Independent sealed prefixes and native status are not an atomic cross-process snapshot.",
            "Collecting prefixes are not whole-workload performance or completion evidence.",
        ],
        "performance_accepted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "counts", "maximum_policy_counters", "last_observation",
            "complete_exact_work_replay", "sampled_relationship_invariants_passed",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
