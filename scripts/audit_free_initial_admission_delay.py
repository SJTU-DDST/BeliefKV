"""Measure initial deferred grants with free logical capacity and worker coverage."""

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_decode_transfer_envelopes import EnvelopeIndex
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_periodic_admission_decisions import EXTRA_GRANTS
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def merged_duration(spans):
    return sum(right - left for left, right in EnvelopeIndex(spans).spans)


def first_following(times, begin):
    slot = bisect_left(times, begin)
    return times[slot] if slot < len(times) else None


def inspect(registration, cohort_path):
    plan = json.loads(registration.read_bytes())
    cohort = json.loads(cohort_path.read_bytes())
    measured = set(cohort["measured_workflow_ids"])
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    selected = [
        row for row in requests.values() if row["native_receipt"]["identity"][0] in measured
    ]
    if len(selected) != 2819 or sum(row["output_tokens"] for row in selected) != 362072:
        raise ValueError("the retained completed measurement population changed")
    starts, workers, exact_services = {}, [], {}
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        workers.append((row["service_start_monotonic_ms"], row["complete_monotonic_ms"]))
        for member in row["request_samples"]:
            rid = member["request_id"]
            if rid not in starts:
                starts[rid] = row["launch_monotonic_ms"]
                exact_services[rid] = identity(member)
    worker = EnvelopeIndex(workers)
    grants, arrivals, snapshots = defaultdict(list), [], []
    counts = Counter()
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        name = row.get("event")
        if name == "agent_initial_registration_decision":
            counts["initial_registrations"] += 1
            if (
                not row["active_after_arrival"] and 0 < row["complete_budget_tokens"]
                <= row["remaining_after_tokens"]
            ):
                arrivals.append(row)
        elif name in EXTRA_GRANTS and row.get("native_capacity_authority_preserved"):
            for member in row["programs"]:
                grants[identity(member["key"], grant=True)].append(row["monotonic_s"] * 1000.)
        elif name == "agent_periodic_admission_decision":
            for member in row["programs"]:
                if member["key"] is not None and member["invocation_id"] in row["resumed_program_ids"]:
                    grants[identity(member["key"], grant=True)].append(row["monotonic_s"] * 1000.)
        elif name == "admission_runtime_state":
            admission = row.get("agent_admission") or {}
            snapshots.append({
                "when": row["monotonic_ms"],
                "window_ms": admission.get("workflow_oracle_coalescing_window_ms"),
                "ranking": admission.get("workflow_oracle_future_work_used_for_decisions"),
            })
    for times in grants.values():
        times.sort()
    if not snapshots or any(row["window_ms"] != 2000 or row["ranking"] is not False for row in snapshots):
        raise ValueError("this diagnostic requires the frozen nonranking two-second-window control")
    rows = []
    for registration_row in arrivals:
        key = identity(registration_row["key"], grant=True)
        begin = registration_row["monotonic_s"] * 1000.
        grant = first_following(grants.get(key, ()), begin)
        rid = key[0]
        if grant is None or rid not in starts:
            counts["initial_free_logical_registration_without_following_grant_or_service"] += 1
            continue
        if key != exact_services[rid] or starts[rid] < grant:
            raise ValueError("the initial deferred grant differs from its actual native attempt")
        exposure = max(0., grant - begin - worker.overlap_ms(begin, grant))
        rows.append({
            "key": registration_row["key"],
            "registration_monotonic_ms": begin, "first_grant_monotonic_ms": grant,
            "first_native_service_ms": starts[rid],
            "registration_to_grant_ms": grant - begin,
            "not_covered_by_recorded_worker_ms": exposure,
            "complete_logical_budget_tokens": registration_row["complete_budget_tokens"],
            "remaining_logical_tokens_at_registration": registration_row["remaining_after_tokens"],
            "measured": key[1] in measured,
            "physical_capacity_at_registration": "unknown",
            "delay_exclusively_caused_by_window": False,
        })
    all_intervals = [
        (row["registration_monotonic_ms"], row["first_grant_monotonic_ms"]) for row in rows
    ]
    united = EnvelopeIndex(all_intervals)
    whole_coverage = sum(worker.overlap_ms(left, right) for left, right in united.spans)
    measured_intervals = defaultdict(list)
    for row in rows:
        if row["measured"]:
            measured_intervals[row["key"]["root_workflow_id"]].append((
                row["registration_monotonic_ms"], row["first_grant_monotonic_ms"],
            ))
    workflow_union = {workflow: merged_duration(measured_intervals[workflow]) for workflow in measured}
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed initial-grant inputs changed")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "initial_registrations_deferred_despite_fitting_logical_budget": len(arrivals),
        "with_exact_following_grant_and_first_service": len(rows),
        "measured_request_episodes": sum(row["measured"] for row in rows),
        "registration_to_grant_ms": distribution(row["registration_to_grant_ms"] for row in rows),
        "not_covered_by_recorded_worker_ms": distribution(
            row["not_covered_by_recorded_worker_ms"] for row in rows
        ),
        "union_of_registration_to_grant_intervals_ms": merged_duration(all_intervals),
        "union_not_covered_by_recorded_worker_ms": max(
            0., merged_duration(all_intervals) - whole_coverage,
        ),
        "measured_workflow_equal_weight_initial_wait_union_s": statistics.mean(workflow_union.values()) / 1000.,
        "measured_initial_wait_union_ms_by_workflow": workflow_union,
        "rows": rows,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                registration, cohort_path, Path(__file__),
                ROOT / "scripts/audit_decode_transfer_envelopes.py",
                ROOT / "scripts/audit_free_dispatch_service.py",
                ROOT / "scripts/audit_periodic_admission_decisions.py",
            )
        },
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "Only initial agent registrations with a fitting complete logical budget are audited.",
            "Full native attempt/session identities connect deferred registration, grant and first actual service.",
            "Logical fit at registration does not prove physical pages, request rows or later collective fit.",
            "Delay can include competing grants, locks, pressure and planning, not only the timer.",
            "Worker coverage is neither true GPU utilization nor proof of removable GPU idle.",
            "Per-workflow unions include noncritical siblings and are not JCT gains or a global scheduling bound.",
            "Ordinary offloaded continuations are outside this initial-registration-only diagnostic.",
            "No serving policy, coalescing window, future-work ranking, workload or GPU job is changed.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve evidence and use nice19 CPU-only analysis")
    result = inspect(args.registration, args.cohort)
    write(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key not in (
        "rows", "inputs_sha256", "input_jsonl_prefixes", "scope",
        "measured_initial_wait_union_ms_by_workflow",
    )}))


if __name__ == "__main__":
    main()
