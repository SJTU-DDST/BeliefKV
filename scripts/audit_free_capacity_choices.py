"""Validate bounded pregrant alternatives without treating missing telemetry as zero."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import finite_number
from scripts.audit_periodic_admission_decisions import (
    EVENT as PERIODIC_EVENT, EXTRA_GRANTS, associate_progress,
    inspect as inspect_periodic, intervention, observation_phases,
)
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


EVENT = "agent_free_capacity_choice"


def grant_identity(event, when, keys):
    return event, when, tuple(sorted(keys))


def choice_identity(row):
    if row["candidate_scope"] == "actual_dispatch_window":
        return row["candidate_scope"], row["dispatch_sequence"]
    return row["candidate_scope"], row["journal_sequence"]


def inspect(records):
    counts, sources, grants, consumed = Counter(), Counter(), {}, set()
    decisions, ready_decisions, direct_decisions, snapshots = set(), set(), set(), []
    observations = []
    for sequence, row in enumerate(records):
        event = row.get("event")
        if event == "admission_runtime_state":
            admission = row.get("agent_admission") or {}
            if admission.get("free_capacity_choice_observation_version") == 1:
                if admission.get("free_capacity_choice_observation_enabled") is not True:
                    raise ValueError("choice observation was disabled inside its journal")
                snapshots.append((sequence, admission.get("counts", {}), row.get("final") is True))
        elif event in EXTRA_GRANTS and row.get("native_capacity_authority_preserved") is True:
            key = grant_identity(
                event, row["monotonic_s"],
                (identity(item["key"], grant=True) for item in row["programs"]),
            )
            if key in grants:
                raise ValueError("duplicate original logical grant")
            grants[key] = row
        elif event == EVENT:
            if (
                row.get("schema_version") != 1 or row.get("grant_published") is not True
                or row.get("logical_capacity_only") is not True
                or row.get("physical_feasibility_proven") is not False
                or not finite_number(row.get("monotonic_s"))
                or not finite_number(row.get("grant_monotonic_s"))
                or not 0 <= row["monotonic_s"] <= row["grant_monotonic_s"]
                or not finite_number(row.get("ts_ms"))
            ):
                raise ValueError("invalid choice authority or clock")
            remaining = row.get("remaining_before_tokens")
            candidates, members = row.get("candidates"), {}
            if type(remaining) is not int or remaining <= 0 or not isinstance(candidates, list):
                raise ValueError("invalid choice capacity or candidates")
            for member in candidates:
                owner = identity(member["key"], grant=True)
                budget = member.get("complete_budget_tokens")
                if (
                    owner[2] in members or type(budget) is not int or budget <= 0
                    or any(type(member.get(field)) is not bool for field in (
                        "selected", "application_ready", "protected_join",
                    ))
                    or not member["selected"] and not member["application_ready"]
                ):
                    raise ValueError("invalid, duplicate or nonready alternative")
                members[owner[2]] = owner, member
            selected = {owner: member for owner, member in members.values() if member["selected"]}
            key = grant_identity(row.get("grant_event"), row["grant_monotonic_s"], selected)
            grant = grants.get(key)
            if grant is None or key in consumed:
                raise ValueError("choice lacks a unique preceding published grant")
            consumed.add(key)
            selected_tokens = sum(member["complete_budget_tokens"] for member in selected.values())
            if (
                not selected or selected_tokens != row.get("selected_tokens")
                or grant.get("remaining_before_tokens") != remaining
                or grant.get("required_tokens") != selected_tokens
                or grant.get("remaining_after_tokens") != remaining - selected_tokens
                or grant.get("join_id") != row.get("join_id")
                or grant["ts_ms"] < row["ts_ms"]
                or selected_tokens > remaining
                or any(
                    selected[identity(member["key"], grant=True)]["complete_budget_tokens"]
                    != member.get("complete_budget_tokens")
                    for member in grant["programs"]
                )
            ):
                raise ValueError("choice differs from its published grant budget or members")
            fitting = [
                (owner, member) for owner, member in members.values()
                if member["complete_budget_tokens"] <= remaining
            ]
            if (
                len({owner[1] for owner, _ in fitting}) < 2
                or sum(member["complete_budget_tokens"] for _, member in fitting) <= remaining
            ):
                raise ValueError("choice has no cross-workflow fitting-set conflict")
            ready = all(member["application_ready"] for member in selected.values())
            protected = any(member["protected_join"] for _, member in members.values())
            rankable = ready and not protected
            if (
                row.get("all_selected_application_ready") is not ready
                or row.get("protected_join_present") is not protected
                or row.get("ready_unprotected_competition") is not rankable
            ):
                raise ValueError("choice readiness or protected JOIN classification changed")
            source, dispatch = row.get("candidate_scope"), row.get("dispatch_sequence")
            if source == "actual_dispatch_window":
                if type(dispatch) is not int or dispatch <= 0:
                    raise ValueError("actual dispatch observation needs its dispatch sequence")
                decision = source, dispatch
            elif source == "bounded_pending_index" and dispatch is None:
                decision = source, sequence
                direct_decisions.add(decision)
            else:
                raise ValueError("unknown bounded candidate observation scope")
            decisions.add(decision)
            if rankable:
                ready_decisions.add(decision)
            counts["conflict_grant_records"] += 1
            counts["ready_unprotected_conflict_grant_records"] += rankable
            counts["protected_join_conflict_grant_records"] += protected
            counts["warming_target_conflict_grant_records"] += not ready
            sources[row["grant_event"]] += 1
            observations.append({**row, "journal_sequence": sequence})
    final = next((item for item in reversed(snapshots) if item[2]), None)
    if final is not None:
        last_sequence, cumulative, _ = final
        if (
            any(row["journal_sequence"] > last_sequence for row in observations)
            or cumulative.get("free_choice_logical_competitions", 0) != len(observations)
            or cumulative.get("free_choice_observed_grants", 0) < len(observations)
        ):
            raise ValueError("final observer counters do not reconcile with its journal")
    instrumented = bool(snapshots or observations)
    return {
        "instrumentation": (
            "final_counters_reconciled" if final is not None else
            "observed_live_prefix" if instrumented else "unobserved"
        ),
        "observed_conflict_grant_records": len(observations) if instrumented else None,
        "deduplicated_conflict_decisions": len(decisions) if instrumented else None,
        "deduplicated_ready_unprotected_decisions": len(ready_decisions) if instrumented else None,
        "bounded_index_direct_decisions": len(direct_decisions) if instrumented else None,
        "counts": dict(counts), "conflict_grants_by_entry": dict(sources),
        "last_observer_counters": snapshots[-1][1] if snapshots else None,
        "observations": observations,
        "scope": {
            "same_dispatch_grants_deduplicated": True,
            "direct_grants_cannot_be_deduplicated_as_one_native_boundary": True,
            "all_paused_candidates_observed": False,
            "nonfree_donor_handoffs_fully_covered": False,
            "logical_conflicts_prove_physical_feasibility": False,
            "service_or_JCT_gain_proven": False,
            "missing_instrumentation_is_unknown_not_zero": True,
            "workload_acceptance_gate_changed": False,
        },
    }


def service_progress(result, records, batches):
    if result["instrumentation"] == "unobserved":
        return {"instrumentation": "unobserved", "counts": None, "ready_grant_episodes": []}
    choices = {
        grant_identity(row["grant_event"], row["grant_monotonic_s"], (
            identity(member["key"], grant=True)
            for member in row["candidates"] if member["selected"]
        )): row for row in result["observations"]
    }
    controls, changes, grants, found = [], defaultdict(list), [], set()
    counts = Counter()
    for sequence, row in enumerate(records):
        intervention(row, sequence, changes)
        if row.get("event") == PERIODIC_EVENT:
            now = row.get("monotonic_s")
            if not finite_number(now) or now < 0:
                raise ValueError("service boundary needs an observed periodic clock")
            controls.append((now * 1000., sequence))
        if row.get("event") not in EXTRA_GRANTS:
            continue
        key = grant_identity(row["event"], row["monotonic_s"], (
            identity(member["key"], grant=True) for member in row["programs"]
        ))
        choice = choices.get(key)
        if choice is None:
            continue
        if key in found:
            raise ValueError("progress journal repeats an observed conflict grant")
        found.add(key)
        for member in choice["candidates"]:
            if not member["selected"]:
                continue
            if not member["application_ready"]:
                counts["warming_selected_members_excluded"] += 1
                continue
            grants.append({
                "key": identity(member["key"], grant=True),
                "selection_ms": row["monotonic_s"] * 1000.,
                "journal_sequence": sequence, "selection_retained": True,
                "choice_identity": choice_identity(choice),
                "ready_unprotected_competition": choice["ready_unprotected_competition"],
                "grant_event": row["event"],
            })
    if found != choices.keys():
        raise ValueError("service journal does not contain every audited conflict grant")
    controls.sort()
    counts["selected_ready_grant_episodes"] = len(grants)
    progress = associate_progress(grants, controls, changes, batches, counts) if grants else {
        "ready_grant_episodes": [],
    }
    completed = {
        row["choice_identity"] for row in grants
        if row["new_completion_before_next_control_or_intervention"] is True
    }
    unprotected = {
        row["choice_identity"] for row in grants
        if row["ready_unprotected_competition"]
        and row["new_completion_before_next_control_or_intervention"] is True
    }
    return {
        "instrumentation": result["instrumentation"],
        "counts": dict(counts), **progress,
        "deduplicated_decisions_with_bounded_useful_completion": len(completed),
        "deduplicated_unprotected_decisions_with_bounded_useful_completion": len(unprotected),
        "scope": {
            "exact_request_context_attempt_session_required": True,
            "warming_targets_excluded_from_ready_service": True,
            "bounded_by_next_periodic_control_or_recorded_same_agent_intervention": True,
            "requires_new_launch_and_positive_native_progress": True,
            "launch_is_scheduler_entry_and_service_is_worker_interval": True,
            "open_windows_unknown_not_failures": True,
            "unlogged_interventions_or_exclusive_causal_gain_proven": False,
        },
    }


def choice_phases(result, phases):
    origin = phases["origin_monotonic_ms"] / 1000.
    rows, episodes = result["observations"], result.get(
        "native_progress", {},
    ).get("ready_grant_episodes", [])
    instrumented = result["instrumentation"] != "unobserved"
    summaries = {}
    for name, phase in phases["phases"].items():
        left, right = phase["start_s"], phase["end_s"]
        selected = [
            row for row in rows if row["grant_monotonic_s"] - origin >= left
            and (right is None or row["grant_monotonic_s"] - origin < right)
        ]
        completed = [
            row for row in episodes if row["selection_ms"] / 1000. - origin >= left
            and (right is None or row["selection_ms"] / 1000. - origin < right)
            and row["new_completion_before_next_control_or_intervention"] is True
        ]
        summaries[name] = {
            "start_s": left, "end_s": right,
            "periodic_boundary_coverage_complete": phase["complete_phase_observed"],
            "conflict_grant_records": len(selected) if instrumented else None,
            "deduplicated_conflict_decisions": len({
                choice_identity(row) for row in selected
            }) if instrumented else None,
            "deduplicated_ready_unprotected_decisions": len({
                choice_identity(row) for row in selected if row["ready_unprotected_competition"]
            }) if instrumented else None,
            "deduplicated_decisions_with_bounded_useful_completion": len({
                tuple(row["choice_identity"]) for row in completed
            }) if result.get("native_progress", {}).get("counts") is not None else None,
            "deduplicated_unprotected_decisions_with_bounded_useful_completion": len({
                tuple(row["choice_identity"]) for row in completed
                if row["ready_unprotected_competition"]
            }) if result.get("native_progress", {}).get("counts") is not None else None,
        }
    return {
        "origin_monotonic_ms": phases["origin_monotonic_ms"],
        "all_measured_completed": phases["all_measured_completed"],
        "last_measured_completion_offset_s": phases["last_measured_completion_offset_s"],
        "phases": summaries,
        "scope": {
            "fixed_half_open_throughput_window_preserved": True,
            "completion_dependent_execution_span_diagnostic_only": True,
            "overlapping_phases_must_not_be_added": True,
            "deduplicated_dispatches_still_not_independent_randomized_opportunities": True,
            "workload_acceptance_gate_changed": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("--with-service", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous choice observations")
    plan = json.loads(args.registration.read_bytes())
    execution = verify_execution(args.registration, args.cohort) if args.complete else None
    path = Path(plan["run_root"]) / "full/opportunities/admission_opportunities.jsonl"
    reader = SealedRecords()
    result = inspect(reader(path))
    arm = path.parents[1]
    if args.with_service:
        result["native_progress"] = service_progress(
            result, reader(path), reader(arm / "server/runtime_audit.jsonl"),
        )
    if args.cohort is not None:
        arrivals_path = Path(plan["workflow_arrivals_path"])
        arrivals = json.loads(arrivals_path.read_bytes())
        if arrivals != plan["workflow_arrivals"]:
            raise ValueError("choice phases used a changed arrival table")
        phases = observation_phases(
            inspect_periodic(reader(path), ()), arrivals, json.loads(args.cohort.read_bytes()),
            reader(arm / "replay/runtime_events.replay.jsonl"),
        )
        result["observation_phases"] = choice_phases(result, phases)
        result["phase_inputs_sha256"] = {
            str(name.resolve()): sha256_file(name) for name in (args.cohort, arrivals_path)
        }
    if args.complete and any(
        Path(name).stat().st_size != prefix["complete_prefix_bytes"]
        or sha256_file(Path(name)) != prefix["complete_prefix_sha256"]
        for name, prefix in reader.prefixes.items()
    ):
        raise ValueError("completed choice journal changed during analysis")
    result.update(
        registration=str(args.registration.resolve()),
        registration_sha256=sha256_file(args.registration),
        analysis_sha256=sha256_file(Path(__file__)), input_jsonl_prefixes=reader.prefixes,
        verified_execution_protocol=execution["protocol"] if execution else None,
        GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps({name: result[name] for name in (
        "instrumentation", "observed_conflict_grant_records", "deduplicated_conflict_decisions",
    )}))


if __name__ == "__main__":
    main()
