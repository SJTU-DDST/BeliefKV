"""Inventory admission entries without double-counting replacement markers."""

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_free_dispatch_service import identity, validate_grant
from scripts.audit_oracle_rank_objectives import bound_original_inputs
from scripts.audit_periodic_admission_decisions import (
    EXTRA_GRANTS, PublishedGrantInterventions, inspect as inspect_periodic,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def entries(records, measured):
    counts, grants, initial = Counter(), [], Counter()
    by_entry, observed_candidates = defaultdict(Counter), Counter()
    tracker, changes = PublishedGrantInterventions(), defaultdict(list)
    for sequence, row in enumerate(records):
        tracker.observe(row, sequence, changes)
        event = row.get("event")
        if event is not None and event.startswith(("agent_", "workflow_")):
            counts[event] += 1
        if event == "agent_initial_registration_decision":
            if type(row.get("active_after_arrival")) is not bool:
                raise ValueError("initial registration lacks observed grant state")
            initial["registrations"] += 1
            initial["active_after_arrival"] += row["active_after_arrival"]
        elif event in EXTRA_GRANTS:
            members = [identity(item["key"], grant=True) for item in row["programs"]]
            by_entry[event]["grant_records"] += 1
            by_entry[event]["member_episodes"] += len(members)
            by_entry[event]["measured_member_episodes"] += sum(key[1] in measured for key in members)
            if event == "agent_dispatch_free_capacity_admitted":
                validated = validate_grant(row)
                grants.append((sequence, [item[0] for item in validated]))
        elif event == "agent_free_capacity_choice":
            for member in row["candidates"]:
                observed_candidates["members"] += 1
                observed_candidates["with_resume_category_and_queue_sequence"] += all(
                    field in member for field in ("resume_category", "queue_sequence")
                )
    linked = {item["grant_journal_sequence"] for item in tracker.links}
    if len(linked) != len(tracker.links):
        raise ValueError("a replacement marker reused an earlier grant")
    free_sequences = {sequence for sequence, _members in grants}
    if not linked <= free_sequences:
        raise ValueError("replacement links point outside the actual free grants")
    return {
        "event_records": dict(counts), "initial_registration": dict(initial),
        "published_extra_grants_by_entry": {name: dict(value) for name, value in by_entry.items()},
        "free_dispatch_grants": {
            "published_grant_records": len(grants),
            "linked_tool_replacement_grant_records": len(linked),
            "remaining_grant_records": len(grants) - len(linked),
            "unique_request_attempts": len({key for _sequence, members in grants for key in members}),
            "linked_marker_is_another_admission": False,
        },
        "published_grant_interventions": tracker.summary(),
        "free_choice_candidate_metadata": dict(observed_candidates),
        "scope": {
            "grant_records_and_member_episodes_are_not_interchangeable": True,
            "periodic_and_event_grants_are_not_independent_competition_opportunities": True,
            "JOIN_reservation_is_not_proof_of_application_ready_request": True,
            "missing_category_or_sequence_prevents_exact_nonranking_free_set_replay": True,
            "selected_native_physical_fit_or_JCT_gain_proven": False,
        },
    }


def compare_progress(previous, current):
    if (
        previous["registration"] != current["registration"]
        or previous["input_jsonl_prefixes"] != current["input_jsonl_prefixes"]
        or previous["observations"] != current["observations"]
    ):
        raise ValueError("progress repair changed execution, choices or sealed input evidence")
    before = {
        (tuple(row["key"]), row["selection_ms"]): row
        for row in previous["native_progress"]["ready_grant_episodes"]
    }
    after = {
        (tuple(row["key"]), row["selection_ms"]): row
        for row in current["native_progress"]["ready_grant_episodes"]
    }
    if before.keys() != after.keys() or any(
        before[key]["following_native_service_ms"] != row["following_native_service_ms"]
        for key, row in after.items()
    ):
        raise ValueError("repair changed exact grant population or following service evidence")
    return {
        "same_original_grants_choices_and_following_exact_service": True,
        "ready_grant_episodes": len(after),
        "observation_windows_changed": sum(
            before[key]["observation_window_end_ms"] != row["observation_window_end_ms"]
            for key, row in after.items()
        ),
        "bounded_useful_completions_before": sum(
            row["new_completion_before_next_control_or_intervention"] is True for row in before.values()
        ),
        "bounded_useful_completions_after": sum(
            row["new_completion_before_next_control_or_intervention"] is True for row in after.values()
        ),
        "scope": "Repaired attribution on one unchanged execution; no new service or performance.",
    }


def compare_periodic_progress(original, current):
    indexed = {
        (tuple(row["key"]), row["selection_ms"]): row
        for row in current["ready_grant_episodes"]
    }
    if len(indexed) != len(current["ready_grant_episodes"]):
        raise ValueError("duplicate exact periodic grant episode")
    previous = [
        member["episode"] for decision in original["decisions"]
        for member in decision["periodic_service"] if member["episode"] is not None
    ]
    canonical = lambda row: json.dumps(row, sort_keys=True, allow_nan=False)
    matched = sum(
        canonical(indexed.get((tuple(row["key"]), row["selection_ms"]))) == canonical(row)
        for row in previous
    )
    return {
        "all_periodic_progress_counts_unchanged": current["counts"] == original["periodic_progress_counts"],
        "original_newly_ranked_agent_episodes": len(previous),
        "original_newly_ranked_episodes_exactly_reproduced": matched,
        "all_original_newly_ranked_episodes_reproduced": matched == len(previous),
        "comparison_scope": "Every episode field after JSON tuple/list normalization.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("periodic-service-audit", "free-choice-service", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--previous-free-choice-service", type=Path)
    parser.add_argument("--revalidate-periodic-progress", action="store_true")
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve earlier evidence and use nice19 hidden-CUDA analysis")
    periodic, free, cohort = [
        json.loads(path.read_bytes())
        for path in (args.periodic_service_audit, args.free_choice_service, args.cohort)
    ]
    if (
        periodic.get("complete_measured_execution_verified") is not True
        or free.get("verified_execution_protocol") != "all_fixed_measured_workflows_completed"
        or periodic["registration"] != free["registration"]
    ):
        raise ValueError("require matched completed-measured execution audits")
    plan = json.loads(Path(periodic["registration"]).read_bytes())
    original_inputs = bound_original_inputs(periodic, Path(plan["execution_checkout"]))
    if str(args.cohort.resolve()) not in original_inputs:
        raise ValueError("cohort was not bound by the original exact service audit")
    path = Path(plan["run_root"]) / "full/opportunities/admission_opportunities.jsonl"
    prefix = periodic["input_jsonl_prefixes"][str(path)]
    if free["input_jsonl_prefixes"].get(str(path)) != prefix:
        raise ValueError("free and periodic audits used different original journal prefixes")
    reader = SealedRecords()
    reader.prefixes[str(path)] = prefix
    result = entries(reader(path), set(cohort["measured_workflow_ids"]))
    if args.revalidate_periodic_progress:
        batch_path = Path(plan["run_root"]) / "full/server/runtime_audit.jsonl"
        reader.prefixes[str(batch_path)] = periodic["input_jsonl_prefixes"][str(batch_path)]
        result["periodic_progress_revalidation"] = compare_periodic_progress(
            periodic, inspect_periodic(reader(path), reader(batch_path)),
        )
    result.update(
        registration=periodic["registration"], original_inputs_verified=original_inputs,
        original_periodic_set_change_counts=periodic["counts"],
        free_conflict_choices={
            name: free[name] for name in (
                "instrumentation", "observed_conflict_grant_records",
                "deduplicated_conflict_decisions", "deduplicated_ready_unprotected_decisions",
                "counts", "conflict_grants_by_entry", "observation_phases",
            )
        },
        free_conflict_service={
            name: free["native_progress"][name] for name in (
                "counts", "deduplicated_decisions_with_bounded_useful_completion",
                "deduplicated_unprotected_decisions_with_bounded_useful_completion",
            )
        },
        input_jsonl_prefixes=reader.prefixes, GPU_action=False, performance_accepted=False,
    )
    paths = [args.periodic_service_audit, args.free_choice_service, args.cohort, Path(__file__)]
    if args.previous_free_choice_service is not None:
        result["progress_attribution_repair"] = compare_progress(
            json.loads(args.previous_free_choice_service.read_bytes()), free,
        )
        paths.append(args.previous_free_choice_service)
    result["inputs_sha256"] = {str(path.resolve()): sha256_file(path) for path in paths}
    result["analysis_dependencies_sha256"] = {
        name: sha256_file(ROOT / name) for name in (
            "scripts/audit_periodic_admission_decisions.py",
            "scripts/audit_free_dispatch_service.py", "scripts/audit_oracle_rank_objectives.py",
        )
    }
    write(args.output, result)
    revalidation = result.get("periodic_progress_revalidation")
    if revalidation is not None and (
        not revalidation["all_periodic_progress_counts_unchanged"]
        or not revalidation["all_original_newly_ranked_episodes_reproduced"]
    ):
        raise ValueError("periodic service revalidation changed the original evidence")
    print(json.dumps({
        "free_dispatch_grants": result["free_dispatch_grants"],
        "initial_registration": result["initial_registration"],
        "progress_attribution_repair": result.get("progress_attribution_repair"),
        "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
