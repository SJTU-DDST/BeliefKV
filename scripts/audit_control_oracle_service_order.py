"""Associate matched-control oracle order opportunities with bounded native service."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_control_oracle_opportunities import opportunity
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution
from scripts.audit_periodic_admission_decisions import inspect as periodic_inspect
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_size_selection import EVENT
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def inspect(records, batches):
    records = list(records)
    progress = periodic_inspect(records, batches)
    episodes = {}
    for episode in progress["ready_grant_episodes"]:
        key = episode["selection_ms"], tuple(episode["key"])
        if key in episodes:
            raise ValueError("duplicate exact periodic grant episode")
        episodes[key] = episode
    counts = Counter({name: 0 for name in (
        "control_decisions", "oracle_candidate_order_changes",
        "candidate_order_changes_with_fewer_than_two_grants",
        "same_selected_set_order_changes", "cross_workflow_same_set_selected_order_changes",
        "cross_workflow_order_changes_with_all_workflows_bounded_served",
        "cross_workflow_order_changes_with_incomplete_bounded_service",
        "shortest_workflow_already_in_first_service_wave",
        "shortest_workflow_served_after_another_workflow",
        "all_workflows_same_first_service_time",
    )})
    decisions, seen = [], set()
    for row in records:
        if row.get("event") != EVENT:
            continue
        if row["monotonic_s"] in seen:
            raise ValueError("duplicate matched-control selection timestamp")
        seen.add(row["monotonic_s"])
        choice = opportunity(row)
        counts["control_decisions"] += 1
        if not choice["order_changed"]:
            continue
        counts["oracle_candidate_order_changes"] += 1
        selected = choice["control_selected"]
        selected_set = set(selected)
        oracle_order = [
            owner for owner in choice["oracle_selected"] if owner in selected_set
        ]
        same_set = not choice["selected_set_changed"]
        changed_selected_order = same_set and oracle_order != selected
        counts["candidate_order_changes_with_fewer_than_two_grants"] += len(selected) < 2
        counts["same_selected_set_order_changes"] += changed_selected_order
        keys = {
            item["key"]["invocation_id"]: identity(item["key"], grant=True)
            for item in row["candidates"]
        }
        metadata = {item["invocation_id"]: item for item in choice["candidates"]}
        members = []
        for owner in selected:
            key = keys[owner]
            episode = episodes.get((row["monotonic_s"] * 1000., key))
            member = {
                **metadata[owner], "key": key, "exact_grant_observed": episode is not None,
                "active_after_pressure": None, "bounded_service_ms": None,
                "bounded_completion_ms": None, "observation_window_end_ms": None,
            }
            if episode is not None:
                member.update(
                    active_after_pressure=episode["active_after_pressure"],
                    observation_window_end_ms=episode["observation_window_end_ms"],
                )
                if episode["new_service_before_next_control_or_intervention"] is True:
                    member["bounded_service_ms"] = episode["first_new_useful_service_ms"]
                if episode["new_completion_before_next_control_or_intervention"] is True:
                    member["bounded_completion_ms"] = episode["first_new_useful_completion_ms"]
            members.append(member)
        unprotected = [member for member in members if not member["protected_join"]]
        grouped = defaultdict(list)
        for member in unprotected:
            grouped[member["workflow_id"]].append(member)
        workflows = []
        for workflow, peers in grouped.items():
            work = {peer["oracle_remaining_path_s"] for peer in peers}
            if len(work) != 1:
                raise ValueError("one workflow has conflicting remaining work")
            service = [peer["bounded_service_ms"] for peer in peers if peer["bounded_service_ms"] is not None]
            workflows.append({
                "workflow_id": workflow, "remaining_path_s": next(iter(work)),
                "selected_agents": len(peers), "bounded_served_agents": len(service),
                "first_bounded_service_ms": min(service) if service else None,
            })
        eligible = same_set and changed_selected_order and len(workflows) >= 2
        observed = eligible and all(
            workflow["first_bounded_service_ms"] is not None for workflow in workflows
        )
        spread, shortest_delay, first_is_shortest = None, None, None
        if eligible:
            counts["cross_workflow_same_set_selected_order_changes"] += 1
        if observed:
            counts["cross_workflow_order_changes_with_all_workflows_bounded_served"] += 1
            earliest = min(workflow["first_bounded_service_ms"] for workflow in workflows)
            latest = max(workflow["first_bounded_service_ms"] for workflow in workflows)
            shortest = min(workflow["remaining_path_s"] for workflow in workflows)
            shortest_service = min(
                workflow["first_bounded_service_ms"] for workflow in workflows
                if workflow["remaining_path_s"] == shortest
            )
            spread, shortest_delay = latest - earliest, shortest_service - earliest
            first_is_shortest = shortest_delay == 0.
            counts["shortest_workflow_already_in_first_service_wave"] += first_is_shortest
            counts["shortest_workflow_served_after_another_workflow"] += not first_is_shortest
            counts["all_workflows_same_first_service_time"] += spread == 0.
        elif eligible:
            counts["cross_workflow_order_changes_with_incomplete_bounded_service"] += 1
        decisions.append({
            **choice, "same_selected_set": same_set,
            "selected_members_order_changed": changed_selected_order,
            "selected_members": members, "unprotected_workflows": workflows,
            "all_unprotected_workflows_bounded_served": observed,
            "first_service_spread_ms": spread,
            "shortest_workflow_delay_after_earliest_ms": shortest_delay,
            "shortest_workflow_in_first_service_wave": first_is_shortest,
        })
    return {
        "counts": dict(counts), "decisions": decisions,
        "observed_workflow_first_service_spread_ms": distribution(
            row["first_service_spread_ms"] for row in decisions
            if row["first_service_spread_ms"] is not None
        ),
        "observed_shortest_workflow_delay_after_earliest_ms": distribution(
            row["shortest_workflow_delay_after_earliest_ms"] for row in decisions
            if row["shortest_workflow_delay_after_earliest_ms"] is not None
        ),
        "actual_periodic_progress_counts": progress["counts"],
        "scope": {
            "same_sealed_control_journal_for_choices_grants_and_interventions": True,
            "exact_request_context_attempt_session_identity_required": True,
            "new_positive_native_progress_before_next_control_or_recorded_intervention": True,
            "each_selected_workflow_counted_once_per_decision": True,
            "protected_ready_join_excluded_from_shortest_workflow_comparison": True,
            "missing_or_out_of_window_service_is_unknown_not_a_long_delay": True,
            "worker_interval_start_not_CUDA_kernel_or_streaming_TTFT": True,
            "candidate_order_change_is_not_selected_order_or_set_change": True,
            "native_priority_or_physical_fit_counterfactual_reconstructed": False,
            "observed_service_spread_is_avoidable_JCT_or_oracle_gain": False,
            "future_states_or_arrivals_simulated": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve prior service-order observations")
    if args.complete:
        verify_execution(args.registration, args.cohort)
    plan = causal.load(args.registration)
    causal.comparison.verify_sources(plan)
    policy_sources = (
        ROOT / "beliefkv/policy/workflow_size_resume.py",
        ROOT / "beliefkv/policy/paper_baselines.py",
        ROOT / "beliefkv/runtime/workflow_oracle_admission.py",
    )
    for path in policy_sources:
        if sha256_file(path) != plan["source_sha256"].get(str(path.relative_to(ROOT))):
            raise ValueError("local order replay differs from the executed policy")
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    result = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        registration=str(args.registration.resolve()),
        registration_sha256=sha256_file(args.registration),
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, Path(__file__), *policy_sources,
            ROOT / "scripts/audit_control_oracle_opportunities.py",
            ROOT / "scripts/audit_periodic_admission_decisions.py",
            ROOT / "scripts/audit_free_dispatch_service.py",
            ROOT / "scripts/audit_sealed_wait_prefetch.py",
        )},
        complete_measured_execution_verified=args.complete,
        GPU_action=False, serving_process_changed=False, performance_accepted=False,
    )
    write(args.output, result)
    print(result["counts"])


if __name__ == "__main__":
    main()
