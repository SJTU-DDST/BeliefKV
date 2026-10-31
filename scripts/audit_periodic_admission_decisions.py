"""Inspect periodic grants and native progress before the next observed decision."""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


EVENT = "agent_periodic_admission_decision"
EXTRA_GRANTS = frozenset((
    "agent_continuation_free_capacity_admitted", "agent_dispatch_free_capacity_admitted",
    "agent_join_free_capacity_admitted", "agent_join_siblings_coadmitted",
    "agent_join_blocked_capacity_admitted",
))


def intervention(record, sequence, interventions):
    name = record.get("event")
    if name not in EXTRA_GRANTS and name != "agent_initial_registration_decision":
        return
    when = record.get("monotonic_s")
    if not finite_number(when) or when < 0:
        raise ValueError("admission intervention needs an observed monotonic clock")
    members = (
        [{"key": record["key"]}] if name == "agent_initial_registration_decision"
        else [*record["programs"], *record.get("donors", ())]
    )
    for member in members:
        key = identity(member["key"], grant=True)
        interventions[key[2]].append((when * 1000., sequence))


def useful_work(member, phase):
    output, tokens = member.get("output_token_delta"), member.get("token_delta")
    if output is None or tokens is None:
        return None
    if any(type(value) is not int or value < 0 for value in (output, tokens)):
        raise ValueError("native progress needs nonnegative input and output deltas")
    return output > 0 or phase == "prefill" and tokens > 0


def inspect(records, batches):
    counts, decisions, grants = Counter(), [], []
    controls, interventions = [], defaultdict(list)
    previous = -1.
    for sequence, record in enumerate(records):
        intervention(record, sequence, interventions)
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
        controls.append((now * 1000., sequence))
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
            if item["key"] is not None:
                interventions[owner].append((now * 1000., sequence))
            if owner in owners:
                grants.append({
                    "key": owners[owner], "selection_ms": now * 1000.,
                    "journal_sequence": sequence,
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
    services, launches = defaultdict(list), defaultdict(list)
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
                launch = batch.get("launch_monotonic_ms")
                if launch is None:
                    counts["target_service_samples_without_launch_clock"] += 1
                    continue
                if not finite_number(launch) or not 0 <= launch <= when:
                    raise ValueError("native launch must precede observed service in the same clock")
                complete = batch.get("complete_monotonic_ms")
                if complete is None:
                    counts["target_service_samples_without_completion_clock"] += 1
                    continue
                if not finite_number(complete) or complete < when:
                    raise ValueError("native completion precedes observed service")
                progress = useful_work(member, member.get("phase", batch.get("phase")))
                if progress is None:
                    counts["target_service_samples_without_progress_deltas"] += 1
                elif progress:
                    launches[key].append((launch, when, complete))
                else:
                    counts["target_zero_progress_service_samples"] += 1
    if len(domains) > 1:
        raise ValueError("native services cross monotonic clock domains")
    for values in services.values():
        values.sort()
    for values in launches.values():
        values.sort()
    for values in interventions.values():
        values.sort()
    latencies, launch_latencies, bounded_service_latencies, completion_latencies = [], [], [], []
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
        # A later control or regrant must not validate an earlier grant's window.
        position = row["selection_ms"], row["journal_sequence"]
        next_control = bisect_right(controls, position)
        changes = interventions[row["key"][2]]
        next_change = bisect_right(changes, position)
        boundaries = []
        if next_control < len(controls):
            boundaries.append((controls[next_control][0], "next_control"))
        if next_change < len(changes):
            boundaries.append((changes[next_change][0], "same_agent_intervention"))
        end, reason = min(boundaries) if boundaries else (None, None)
        candidates = launches[row["key"]]
        index = bisect_left(candidates, (row["selection_ms"], -1., -1.))
        selected = (
            candidates[index]
            if row["active_after_pressure"] and index < len(candidates) else None
        )
        launched = selected is not None and end is not None and selected[0] < end
        served = launched and selected[1] < end
        completed = launched and selected[2] < end
        row.update(
            observation_window_end_ms=end, observation_window_end_reason=reason,
            first_new_useful_launch_ms=selected[0] - row["selection_ms"] if selected else None,
            first_new_useful_service_ms=selected[1] - row["selection_ms"] if selected else None,
            first_new_useful_completion_ms=selected[2] - row["selection_ms"] if selected else None,
            new_launch_before_next_control_or_intervention=launched if end is not None else None,
            new_service_before_next_control_or_intervention=served if end is not None else None,
            new_completion_before_next_control_or_intervention=completed if end is not None else None,
        )
        counts["ready_grants_with_observed_new_useful_launch"] += selected is not None
        counts["ready_grants_with_open_observation_window"] += end is None
        counts["ready_grants_launched_within_observation_window"] += launched
        counts["ready_grants_served_within_observation_window"] += served
        counts["ready_grants_completed_useful_batch_within_observation_window"] += completed
        if launched:
            launch_latencies.append(row["first_new_useful_launch_ms"])
        if served:
            bounded_service_latencies.append(row["first_new_useful_service_ms"])
        if completed:
            completion_latencies.append(row["first_new_useful_completion_ms"])
    return {
        "counts": dict(counts), "competition_decisions": decisions,
        "ready_grant_episodes": grants, "following_service_ms": distribution(latencies),
        "within_window_new_launch_ms": distribution(launch_latencies),
        "within_window_new_service_ms": distribution(bounded_service_latencies),
        "within_window_new_completion_ms": distribution(completion_latencies),
        "scope": {
            "exact_periodic_resume_budget_and_before_after_state": True,
            "cross_category_competition_is_diagnostic_not_original_permission": True,
            "logical_fit_does_not_prove_physical_feasibility": True,
            "earlier_and_foreign_attempt_service_excluded": True,
            "intervening_grants_or_exclusive_causal_benefit_proven": False,
            "bounded_progress_ends_at_next_control_or_recorded_same_agent_intervention": True,
            "bounded_progress_requires_new_launch_and_positive_model_progress": True,
            "launch_clock_is_scheduler_run_batch_entry_not_CUDA_submission": True,
            "service_start_clock_is_worker_interval_not_kernel_start": True,
            "completion_clock_has_observed_positive_native_progress": True,
            "open_prefix_windows_are_unknown_not_negative_results": True,
            "unlogged_interventions_and_exclusive_causal_benefit_not_proven": True,
            "other_admission_entries_require_their_separate_audits": True,
            "independently_sealed_prefixes_not_atomic": True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior decision observations")
    registration = json.loads(args.registration.read_bytes())
    if args.complete and complete_work(args.registration) is None:
        raise ValueError("complete decision audit requires full work, fresh drain and actual owned exit")
    arm = Path(registration["run_root"]) / "full"
    reader = SealedRecords()
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    if args.complete and any(
        Path(path).stat().st_size != prefix["complete_prefix_bytes"]
        or sha256_file(Path(path)) != prefix["complete_prefix_sha256"]
        for path, prefix in reader.prefixes.items()
    ):
        raise ValueError("completed decision evidence changed during analysis")
    result.update(
        registration=str(args.registration.resolve()),
        registration_sha256=sha256_file(args.registration),
        input_jsonl_prefixes=reader.prefixes, GPU_action=False, performance_accepted=False,
        analysis_sha256=sha256_file(Path(__file__)),
        complete_work_and_owned_exit_verified=args.complete,
    )
    write(args.output, result)
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
