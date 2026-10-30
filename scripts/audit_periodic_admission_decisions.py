"""Inspect exact periodic admission boundaries and same-attempt following service."""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


EVENT = "agent_periodic_admission_decision"


def inspect(records, batches):
    counts, decisions, grants = Counter(), [], []
    previous = -1.
    for record in records:
        if record.get("event") != EVENT:
            continue
        now, capacity = record.get("monotonic_s"), record.get("resume_capacity_tokens")
        programs = record.get("programs")
        resumed = record.get("resumed_program_ids")
        if (
            not finite_number(now) or now < previous or type(capacity) is not int
            or not isinstance(programs, list) or not isinstance(resumed, list)
            or len(set(resumed)) != len(resumed)
            or record.get("physical_capacity_credit") is not False
        ):
            raise ValueError("invalid actual periodic boundary")
        previous = now
        metadata = {}
        for item in programs:
            owner = item["invocation_id"]
            if owner in metadata or any(type(item.get(name)) is not bool for name in (
                "active_before", "pending_before", "acting_before", "marked_before",
                "application_ready", "active_after_pressure", "marked_after_pressure",
            )) or type(item.get("complete_budget_tokens")) is not int or item["complete_budget_tokens"] <= 0:
                raise ValueError("invalid periodic program state or budget")
            metadata[owner] = item
        if not set(resumed) <= metadata.keys() or sum(
            metadata[owner]["complete_budget_tokens"] for owner in resumed
        ) > max(0, capacity):
            raise ValueError("periodic selection exceeds its recorded resume budget")
        ready = [
            item for item in programs
            if item["application_ready"] and item["pending_before"]
            and not item["active_before"] and not item["marked_before"]
        ]
        fitting = [item for item in ready if item["complete_budget_tokens"] <= capacity]
        owners = {}
        for item in ready:
            key = identity(item["key"], grant=True)
            if key[2] != item["invocation_id"]:
                raise ValueError("periodic request has another invocation owner")
            owners[item["invocation_id"]] = key
        competition = (
            len({owners[item["invocation_id"]][1] for item in fitting}) > 1
            and sum(item["complete_budget_tokens"] for item in fitting) > capacity
        )
        counts["controls"] += 1
        counts["controls_with_ready_paused"] += bool(ready)
        counts["cross_workflow_logical_set_competition_controls"] += competition
        counts["free_budget_without_ready_paused_controls"] += capacity > 100 and not ready
        for owner in resumed:
            item = metadata[owner]
            counts["resumed_program_episodes"] += 1
            counts["resumed_revoked_by_pressure"] += not item["active_after_pressure"]
            if owner in owners:
                grants.append({
                    "key": owners[owner], "selection_ms": now * 1000.,
                    "active_after_pressure": item["active_after_pressure"],
                    "marked_after_pressure": item["marked_after_pressure"],
                })
        if competition:
            decisions.append({
                "monotonic_s": now, "resume_capacity_tokens": capacity,
                "fitting_candidates": [item["key"] for item in fitting],
                "selected": resumed,
                "selected_after_pressure": [
                    owner for owner in resumed if metadata[owner]["active_after_pressure"]
                ],
            })
    target = {row["key"] for row in grants}
    services = defaultdict(list)
    domains = set()
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        when, domain = batch.get("service_start_monotonic_ms"), batch.get("monotonic_clock_domain")
        if not finite_number(when) or when < 0 or not domain:
            raise ValueError("unobserved native service clock")
        domains.add(domain)
        for member in batch.get("request_samples", ()):
            if any(member.get(name) is None for name in ("attempt_id", "session_id", "session_generation")):
                continue
            key = identity(member)
            if key in target:
                services[key].append(when)
    if len(domains) > 1:
        raise ValueError("native services cross monotonic clock domains")
    for values in services.values():
        values.sort()
    latencies = []
    for row in grants:
        values = services[row["key"]]
        index = bisect_left(values, row["selection_ms"])
        row["following_native_service_ms"] = (
            values[index] - row["selection_ms"]
            if row["active_after_pressure"] and index < len(values) else None
        )
        if row["following_native_service_ms"] is not None:
            latencies.append(row["following_native_service_ms"])
            counts["ready_grants_with_following_exact_service"] += 1
    return {
        "counts": dict(counts), "competition_decisions": decisions,
        "ready_grant_episodes": grants, "following_service_ms": distribution(latencies),
        "scope": {
            "exact_periodic_resume_budget_and_before_after_state": True,
            "cross_category_competition_is_diagnostic_not_original_permission": True,
            "logical_fit_does_not_prove_physical_feasibility": True,
            "earlier_and_foreign_attempt_service_excluded": True,
            "intervening_grants_or_exclusive_causal_benefit_proven": False,
            "other_admission_entries_require_their_separate_audits": True,
            "independently_sealed_prefixes_not_atomic": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior decision observations")
    registration = json.loads(args.registration.read_bytes())
    arm = Path(registration["run_root"]) / "full"
    reader = SealedRecords()
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    result.update(
        registration=str(args.registration.resolve()),
        registration_sha256=sha256_file(args.registration),
        input_jsonl_prefixes=reader.prefixes, GPU_action=False, performance_accepted=False,
        analysis_sha256=sha256_file(Path(__file__)),
    )
    write(args.output, result)
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
