"""Replay recorded JOIN combinations and audit their periodic service consumption."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources, choose_combination,
)
from scripts import run_causal_comparison as causal
from scripts.audit_free_dispatch_service import identity
from scripts.audit_native_system_costs import distribution, finite_number
from scripts.audit_periodic_admission_decisions import inspect as periodic_inspect
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.review_matched_work_ranking import verify_calibrated_combination_pair
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution

EVENT = "workflow_combination_selection"
SOURCES = frozenset(("periodic_decay_resume", "undiscounted_free_dispatch"))


def canonical(decision):
    return json.loads(json.dumps({
        name: value for name, value in decision.items() if name != "wall_ns"
    }, allow_nan=False))


def replay_choice(
    row, costs, cost_identity, *, allow_feasible_resource_alternatives=False,
    plan_device_capacity=False,
):
    """Reconstruct the recorded bounded search, including its cutoff mask."""
    if type(allow_feasible_resource_alternatives) is not bool or (
        row.get("resource_fallback_selection_enabled", False) is not allow_feasible_resource_alternatives
    ):
        raise ValueError("resource fallback selection differs from the frozen profile")
    if (
        row.get("event") != EVENT or row.get("source") not in SOURCES
        or row.get("physical_capacity_credit") is not False
        or row.get("cost_identity") != cost_identity
        or not finite_number(row.get("monotonic_s")) or row["monotonic_s"] < 0
        or any(
            type(row.get(name)) is not int or row[name] < 0
            for name in ("metadata_and_selection_cpu_ns", "metadata_and_selection_wall_ns")
        )
    ):
        raise ValueError("combination needs its exact source, clock, cost profile and measured overhead")
    observed = row["decision"]
    selected = observed.get("selected")
    if (
        not isinstance(selected, (list, tuple))
        or any(type(owner) is not str or not owner for owner in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("combination must record unique selected agents")
    summary = {
        "source": row["source"], "monotonic_s": row["monotonic_s"],
        "selected": list(selected), "reason": observed["reason"],
        "search_replayed": False, "selected_set_changed": None,
        "newly_selected_invocations": [], "periodic_service": [], "free_service": [],
        "metadata_and_selection_cpu_ns": row["metadata_and_selection_cpu_ns"],
        "metadata_and_selection_wall_ns": row["metadata_and_selection_wall_ns"],
    }
    if "evaluated_subsets" not in observed:
        if "reference" in observed:
            raise ValueError("unreplayed metadata fallback must not claim a search reference")
        return summary
    if type(observed.get("search_complete")) is not bool or (
        type(observed["evaluated_subsets"]) is not int or observed["evaluated_subsets"] < 0
    ):
        raise ValueError("combination search lacks its actual completion and enumeration count")
    candidates = []
    keys = row.get("keys", {})
    for raw in row["candidates"]:
        values = dict(raw)
        values["missing_extents"] = tuple(
            (tuple(key) if isinstance(key, list) else key, tokens)
            for key, tokens in values["missing_extents"]
        )
        candidate = CombinationCandidate(**values)
        owner = identity(keys[candidate.identity], grant=True)
        if owner[2] != candidate.identity or owner[1] != candidate.workflow:
            raise ValueError("combination candidate and exact request ownership differ")
        candidates.append(candidate)
    resources = CombinationResources(**row["resources"])
    scope = "planned_pool_geometry" if plan_device_capacity else "current_free_and_safe_cold"
    if (
        type(plan_device_capacity) is not bool
        or (resources.device_capacity_tokens is not None) != plan_device_capacity
        or row.get("device_fit_scope", "current_free_and_safe_cold") != scope
    ):
        raise ValueError("device fit scope differs from the frozen profile")
    reference = observed["reference"]
    required = observed["required_reference_members"]
    expected = canonical(observed)

    def run(clock):
        return choose_combination(
            candidates, reference, resources, costs, required=required,
            budget_ns=500_000, clock=clock,
            allow_feasible_resource_alternatives=allow_feasible_resource_alternatives,
        )

    actual = run(lambda: 0)
    if canonical(actual) != expected and observed["reason"].startswith("soft_deadline_"):
        required_mask = sum(
            1 << index for index, candidate in enumerate(candidates)
            if candidate.identity in required
        )
        for stop_mask in range(1, 1 << len(candidates), 8):
            evaluated = sum(
                mask & required_mask == required_mask for mask in range(1, stop_mask)
            )
            if evaluated != observed["evaluated_subsets"]:
                continue
            stop_check = (stop_mask - 1) // 8 + 1
            calls = -1

            def clock():
                nonlocal calls
                calls += 1
                return 500_000 if calls >= stop_check else 0

            actual = run(clock)
            if canonical(actual) == expected:
                break
    if canonical(actual) != expected:
        raise ValueError("recorded combination does not reproduce its frozen bounded search")
    summary.update(
        search_replayed=True, reference=list(reference),
        selected_set_changed=set(selected) != set(reference),
        newly_selected_invocations=[owner for owner in selected if owner not in reference],
        search_complete=observed["search_complete"],
        evaluated_subsets=observed["evaluated_subsets"],
    )
    return summary


def inspect(
    records, batches, costs, cost_identity, *, allow_feasible_resource_alternatives=False,
    plan_device_capacity=False,
):
    """Associate choices only with exact grants in their actual dispatch boundaries."""
    progress = periodic_inspect(records, batches, include_free_grants=True)
    episodes = {}
    for episode in progress["ready_grant_episodes"]:
        key = tuple(episode["key"]), episode["journal_sequence"]
        if key in episodes:
            raise ValueError("duplicate actual periodic service episode")
        episodes[key] = episode
    choices, pending, free_pending, last_free_associations = [], None, None, {}
    for sequence, row in enumerate(records):
        if row.get("event") == EVENT:
            last_free_associations = {}
            choice = replay_choice(
                row, costs, cost_identity,
                allow_feasible_resource_alternatives=allow_feasible_resource_alternatives,
                plan_device_capacity=plan_device_capacity,
            )
            choice["journal_sequence"] = sequence
            choices.append(choice)
            if row["source"] == "periodic_decay_resume":
                pending = choice, row
                free_pending = None
            else:
                free_pending = (choice, row) if choice["search_replayed"] else None
                choice["free_service"] = [{
                    "invocation_id": owner, "key": identity(row["keys"][owner], grant=True),
                    "actual_grant_verified": False, "separate_tool_replacement": False, "episode": None,
                } for owner in choice["newly_selected_invocations"]]
        elif row.get("event") == "workflow_oracle_coalesced_dispatch":
            free_pending = None
            last_free_associations = {}
        elif row.get("event") == "agent_dispatch_free_capacity_admitted":
            last_free_associations = {}
            if free_pending is None:
                continue
            choice, selection = free_pending
            for member in row["programs"]:
                key = identity(member["key"], grant=True)
                owner = key[2]
                if owner not in choice["selected"]:
                    free_pending = None
                    break
                if identity(selection["keys"][owner], grant=True) != key:
                    raise ValueError("free grant changed the selected request attempt")
                candidate = next(item for item in selection["candidates"] if item["identity"] == owner)
                if (
                    member["complete_budget_tokens"] != candidate["logical_tokens"]
                    or member["observed_context_tokens"] != candidate["context_tokens"]
                ):
                    raise ValueError("free grant changed the selected complete context budget")
                for service in choice["free_service"]:
                    if service["invocation_id"] != owner:
                        continue
                    if service["actual_grant_verified"]:
                        raise ValueError("duplicate free grant within the same combination dispatch")
                    service.update(
                        actual_grant_verified=True, journal_sequence=sequence,
                        episode=episodes.get((key, sequence)),
                    )
                    last_free_associations[key] = service
        elif row.get("event") == "agent_predicted_tool_replacement":
            key = identity(row["consumer"], grant=True)
            if key in last_free_associations:
                last_free_associations[key]["separate_tool_replacement"] = True
            free_pending, last_free_associations = None, {}
        elif row.get("event") == "agent_periodic_admission_decision":
            free_pending = None
            last_free_associations = {}
            if pending is None:
                continue
            choice, selection = pending
            pending = None
            if set(row["resumed_program_ids"]) != set(choice["selected"]):
                raise ValueError("combination and following actual periodic grant disagree")
            members = {item["invocation_id"]: item for item in row["programs"]}
            for owner in choice["newly_selected_invocations"]:
                member = members[owner]
                selected_key = identity(selection["keys"][owner], grant=True)
                if identity(member["key"], grant=True) != selected_key:
                    raise ValueError("periodic grant changed the selected request attempt")
                choice["periodic_service"].append({
                    "invocation_id": owner, "key": selected_key,
                    "episode": episodes.get((selected_key, sequence)),
                })
            choice["following_periodic_grant_verified"] = True
    counts, reasons, sources = Counter({
        name: 0 for name in (
            "newly_selected_free_episodes", "newly_selected_free_actual_grant_verified",
            "newly_selected_free_without_grant_evidence",
            "newly_selected_free_grant_without_progress_episode",
            "newly_selected_free_followed_by_exact_service",
            "newly_selected_free_with_bounded_useful_completion",
            "newly_selected_free_direct_dispatch_grant_verified",
            "newly_selected_free_granted_by_separate_tool_replacement",
        )
    }), Counter(), Counter()
    for choice in choices:
        counts["combination_passes"] += 1
        sources[choice["source"]] += 1
        reasons[choice["reason"]] += 1
        counts["searches_replayed"] += choice["search_replayed"]
        counts["selected_set_changes"] += choice["selected_set_changed"] is True
        counts["newly_selected_agent_episodes"] += len(choice["newly_selected_invocations"])
        counts["periodic_selections_with_actual_grant_boundary"] += choice.get(
            "following_periodic_grant_verified", False,
        )
        for service in choice["periodic_service"]:
            episode = service["episode"]
            counts["newly_selected_periodic_episodes"] += 1
            counts["newly_selected_periodic_without_ready_grant_evidence"] += episode is None
            if episode is not None:
                counts["newly_selected_periodic_retained_after_pressure"] += episode["active_after_pressure"]
                counts["newly_selected_periodic_followed_by_exact_service"] += (
                    episode["following_native_service_ms"] is not None
                )
                counts["newly_selected_periodic_with_bounded_useful_completion"] += (
                    episode["new_completion_before_next_control_or_intervention"] is True
                )
        for service in choice["free_service"]:
            episode = service["episode"]
            counts["newly_selected_free_episodes"] += 1
            counts["newly_selected_free_actual_grant_verified"] += service["actual_grant_verified"]
            counts["newly_selected_free_direct_dispatch_grant_verified"] += (
                service["actual_grant_verified"] and not service["separate_tool_replacement"]
            )
            counts["newly_selected_free_granted_by_separate_tool_replacement"] += service["separate_tool_replacement"]
            counts["newly_selected_free_without_grant_evidence"] += not service["actual_grant_verified"]
            counts["newly_selected_free_grant_without_progress_episode"] += (
                service["actual_grant_verified"] and episode is None
            )
            if episode is not None and not service["separate_tool_replacement"]:
                counts["newly_selected_free_followed_by_exact_service"] += (
                    episode["following_native_service_ms"] is not None
                )
                counts["newly_selected_free_with_bounded_useful_completion"] += (
                    episode["new_completion_before_next_control_or_intervention"] is True
                )
    return {
        "counts": dict(counts), "reasons": dict(reasons), "sources": dict(sources),
        "metadata_and_selection_cpu_us": distribution([
            row["metadata_and_selection_cpu_ns"] / 1000. for row in choices
        ]),
        "metadata_and_selection_wall_us": distribution([
            row["metadata_and_selection_wall_ns"] / 1000. for row in choices
        ]),
        "decisions": choices,
        "scope": {
            "search_replay_includes_actual_soft_deadline": True,
            "reference_is_local_S0_not_a_counterfactual_whole_run": True,
            "periodic_progress_uses_exact_attempt_and_post_pressure_eligibility": True,
            "periodic_progress_bounded_by_next_control_or_observed_intervention": True,
            "free_dispatch_selection_replayed_but_service_not_associated": not any(
                service["actual_grant_verified"] and not service["separate_tool_replacement"]
                for choice in choices for service in choice["free_service"]
            ),
            "free_dispatch_exact_grant_association_enabled": True,
            "free_dispatch_boundary_closes_at_next_selection_or_coalesced_dispatch_record": True,
            "free_grant_observed_active_at_publication_not_assumed_after_pressure": True,
            "separate_tool_replacement_grants_excluded_from_free_selection_service_counts": True,
            "A_to_B_is_separate_single_donor_order_not_combination_search": True,
            "missing_grant_or_service_evidence_is_unknown": True,
            "exclusive_causal_JCT_or_throughput_gain_proven": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "control", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("--policy-kind", choices=("oracle", "learned"), default="oracle")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier combination observations")
    plan, control = (causal.load(path) for path in (args.registration, args.control))
    for item in (plan, control):
        causal.comparison.verify_sources(item)
    candidate_path, control_path = (Path(item["experiment_configuration"]) for item in (plan, control))
    candidate = causal.load(candidate_path)
    if args.policy_kind == "learned":
        from scripts.review_learned_work_ranking import verify_learned_combination_pair

        verify_learned_combination_pair(causal.load(control_path), candidate)
    else:
        verify_calibrated_combination_pair(causal.load(control_path), candidate)
    for name in ("beliefkv/policy/workflow_combination.py",):
        if sha256_file(ROOT / name) != plan["source_sha256"].get(name):
            raise ValueError("analysis search differs from executed combination policy")
    if args.complete:
        executions = [verify_execution(path, args.cohort) for path in (args.registration, args.control)]
        if executions[0]["work"] != executions[1]["work"]:
            raise ValueError("complete measured native work changed")
    else:
        state = causal.load(args.registration.with_suffix(".state.json"))
        if state.get("status") != "running_full" or not all(
            causal.reservation.alive(state.get(name)) for name in ("watcher", "server", "driver")
        ):
            raise ValueError("live audit requires actual running owners")
    artifact_path = Path(candidate["common_environment"]["WORKFLOW_WORK_MODEL"])
    if sha256_file(artifact_path) != candidate["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("combination artifact changed")
    profile = causal.load(artifact_path)["admission_cost_profile"]
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    result = inspect(
        list(reader(arm / "opportunities/admission_opportunities.jsonl")),
        reader(arm / "server/runtime_audit.jsonl"),
        CombinationCosts(**profile["costs"]), profile["identity"],
        allow_feasible_resource_alternatives=(
            profile["kind"] in (
                "r5_frozen_batch_cost_proxy_capacity_v4", "r5_frozen_batch_cost_proxy_capacity_v5",
                "r5_frozen_batch_cost_proxy_capacity_v6",
            )
        ),
        plan_device_capacity=profile["kind"] == "r5_frozen_batch_cost_proxy_capacity_v6",
    )
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        analysis_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        registration=str(args.registration), complete_measured_execution_verified=args.complete,
        policy_kind=args.policy_kind,
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.control, args.cohort, candidate_path, control_path,
            artifact_path, Path(__file__), ROOT / "beliefkv/policy/workflow_combination.py",
            ROOT / "scripts/audit_periodic_admission_decisions.py",
            ROOT / "scripts/audit_free_dispatch_service.py",
        )},
        formal_workload_accepted=False, GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print({"counts": result["counts"], "reasons": result["reasons"]})


if __name__ == "__main__":
    main()
