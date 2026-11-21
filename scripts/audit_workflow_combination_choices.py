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
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.policy.workflow_capacity_hold import CapacityHoldCandidate, choose_capacity_hold
from beliefkv.runtime.ready_wait_priority import ReadyWaitSettings
from beliefkv.runtime.workflow_capacity_accumulation import ReadyCapacityAccumulation
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
UNSEARCHED_REFERENCE_REASONS = frozenset((
    "metadata_unavailable_reference_fallback",
    "remaining_work_ranking_disabled_reference",
    "initial_materialization_reference_preserved",
    "no_competing_choice",
    "native_resources_unavailable_reference_fallback",
    "reference_candidate_summary_unavailable",
    "predictions_unavailable_reference_fallback",
    "invalid_metadata_reference_fallback",
    "strict_reference_exceeds_prefix_budget",
))


def validate_selection_policy(
    records, *, shortest_work_first=False, cost_aware_maximal=False,
    maximal_objective="benefit_per_cost", ready_wait_priority_ms=0,
    ready_capacity_accumulation_ms=0,
    ready_wait_priority_grace_ms=None, ready_wait_priority_max_boost=None,
    strict_remaining_work=False,
):
    if type(shortest_work_first) is not bool or type(cost_aware_maximal) is not bool or (
        cost_aware_maximal and not shortest_work_first
    ):
        raise ValueError("selection policy declaration must be boolean")
    if type(strict_remaining_work) is not bool or strict_remaining_work and (
        not shortest_work_first or cost_aware_maximal or ready_wait_priority_ms
        or ready_capacity_accumulation_ms
    ):
        raise ValueError("strict remaining-work policy cannot use utility packing or wait priority")
    if maximal_objective not in ("benefit_per_cost", "benefit") or (
        maximal_objective != "benefit_per_cost" and not cost_aware_maximal
    ):
        raise ValueError("maximal objective requires its explicitly declared selector")
    settings = ReadyWaitSettings(
        ready_wait_priority_ms,
        60000 if ready_wait_priority_grace_ms is None else ready_wait_priority_grace_ms,
        1.25 if ready_wait_priority_max_boost is None else ready_wait_priority_max_boost,
    )
    explicit_tuning = (
        ready_wait_priority_grace_ms is not None or ready_wait_priority_max_boost is not None
    )
    if (
        settings.scale_ms and not cost_aware_maximal
        or explicit_tuning and not settings.scale_ms
    ):
        raise ValueError("ready wait priority requires its explicit maximal selector")
    if type(ready_capacity_accumulation_ms) is not int or ready_capacity_accumulation_ms not in (
        0, 1000, 2000,
    ) or ready_capacity_accumulation_ms and (
        not ready_wait_priority_ms or not cost_aware_maximal or maximal_objective != "benefit"
    ):
        raise ValueError("capacity accumulation requires its registered bounded wait policy")
    expected = (
        "strict_remaining_work" if strict_remaining_work else
        ("maximal_benefit" if maximal_objective == "benefit" else "maximal_cost_aware")
        if cost_aware_maximal else
        "shortest_work_first" if shortest_work_first else None
    )
    for row in records:
        if row.get("event") == EVENT and row.get("selection_policy") != expected:
            raise ValueError("recorded selection policy differs from the registered artifact")
        if row.get("event") == EVENT:
            priority = row.get("ready_wait_priority", {})
            if not isinstance(priority, dict) or priority.get("scale_ms", 0) != ready_wait_priority_ms:
                raise ValueError("recorded ready wait priority differs from the registered artifact")
            if "kind" in priority:
                observed = ReadyWaitSettings(
                    priority.get("scale_ms"), priority.get("grace_ms"), priority.get("max_boost"),
                )
                if (
                    not settings.scale_ms or observed != settings
                    or priority["kind"] != settings.record()["kind"]
                ):
                    raise ValueError("recorded ready wait priority tuning differs from the registered artifact")
            elif explicit_tuning:
                raise ValueError("legacy ready wait priority cannot attest bounded tuning")
            if row.get("ready_capacity_accumulation_ms", 0) != ready_capacity_accumulation_ms:
                raise ValueError("recorded capacity accumulation differs from the registered artifact")


def replay_capacity_hold(row):
    hold = row.get("capacity_accumulation")
    priority = row.get("ready_wait_priority", {})
    minimum_wait = (
        ReadyCapacityAccumulation.minimum_wait_ms if priority.get("kind") == "bounded_relative_ready_wait_v2"
        else priority.get("scale_ms")
    )
    cooldown = (
        ReadyCapacityAccumulation.cooldown_ms if priority.get("kind") == "bounded_relative_ready_wait_v2"
        else minimum_wait
    )
    if not isinstance(hold, dict) or (
        hold.get("max_wait_ms") not in (1000, 2000)
        or row.get("ready_capacity_accumulation_ms") != hold["max_wait_ms"]
        or hold.get("physical_capacity_credit") is not False
        or hold.get("minimum_ready_wait_ms") != minimum_wait
        or hold.get("cooldown_ms") != cooldown
        or not all(finite_number(hold.get(name)) for name in (
            "decision_monotonic_s", "started_monotonic_s", "deadline_monotonic_s",
        ))
        or not hold["started_monotonic_s"] <= hold["decision_monotonic_s"] < hold["deadline_monotonic_s"]
        or abs(
            hold["deadline_monotonic_s"] - hold["started_monotonic_s"]
            - hold["max_wait_ms"] / 1000.
        ) > 1e-9
        or hold["decision_monotonic_s"] > row["monotonic_s"]
    ):
        raise ValueError("invalid exact bounded capacity hold")
    decision = hold["decision"]
    if not decision["continuing_existing_hold"] and row["source"] != "periodic_decay_resume":
        raise ValueError("only a periodic boundary may start capacity accumulation")
    candidates = [CapacityHoldCandidate(**value) for value in hold["candidates"]]
    for value in candidates:
        key = identity(hold["keys"][value.identity], grant=True)
        if key[2] != value.identity or key[1] != value.workflow:
            raise ValueError("capacity hold changed an exact candidate owner")
    replayed = choose_capacity_hold(
        candidates, hold["reference"], capacity=hold["logical_capacity_tokens"],
        pool_capacity=hold["pool_capacity_tokens"],
        minimum_wait_ms=hold["minimum_ready_wait_ms"], running_width=hold["running_width"],
        relative_tie=hold["relative_tie"],
        focus=decision["focus"] if decision["continuing_existing_hold"] else None,
    )
    if replayed is None or canonical(replayed) != canonical(decision) or (
        list(row["decision"]["selected"]) != list(replayed["selected"])
        or list(row["decision"]["reference"]) != list(hold["reference"])
        or row["logical_reference_capacity_tokens"] != hold["logical_capacity_tokens"]
    ):
        raise ValueError("capacity hold does not reproduce its exact new-grant deferral")
    return replayed


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
    strategy = row.get("selection_policy")
    if strategy not in (
        None, "shortest_work_first", "maximal_cost_aware", "maximal_benefit", "strict_remaining_work",
    ):
        raise ValueError("unknown explicitly recorded selection policy")
    maximal_strategy = strategy in ("maximal_cost_aware", "maximal_benefit")
    shortest = strategy in ("shortest_work_first", "strict_remaining_work") and "evaluated_candidates" in observed
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
    if observed["reason"] == "ready_capacity_accumulation":
        if not maximal_strategy or strategy != "maximal_benefit":
            raise ValueError("capacity hold requires its explicit maximal benefit selector")
        hold = replay_capacity_hold(row)
        summary.update(
            reference=list(observed["reference"]), selected_set_changed=bool(hold["held_reference"]),
            capacity_hold_replayed=True, capacity_hold_focus=hold["focus"],
            capacity_hold_removed_members=list(hold["held_reference"]),
        )
        return summary
    if "evaluated_subsets" not in observed and not shortest:
        if any(name in observed for name in (
            "search_complete", "required_reference_members",
            "reference_evaluation", "selected_evaluation",
        )):
            raise ValueError("combination search evidence lacks its enumeration count")
        if "reference" in observed:
            reference = observed["reference"]
            if (
                observed["reason"] not in UNSEARCHED_REFERENCE_REASONS
                or not isinstance(reference, (list, tuple))
                or any(type(owner) is not str or not owner for owner in reference)
                or len(set(reference)) != len(reference)
                or list(selected) != list(reference)
            ):
                raise ValueError("unsearched fallback must preserve its exact reference")
            # The producer records its unchanged reference before search metadata exists.
            summary.update(
                fallback_reference=list(reference), fallback_reference_preserved=True,
            )
        return summary
    count_field = "evaluated_candidates" if shortest else "evaluated_subsets"
    if type(observed.get("search_complete")) is not bool or (
        type(observed[count_field]) is not int or observed[count_field] < 0
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
    if strategy == "strict_remaining_work":
        for invocation in reference:
            if identity(keys[invocation], grant=True)[2] != invocation:
                raise ValueError("strict fallback changed its exact request identity")
    expected = canonical(observed)

    def run(clock):
        if maximal_strategy:
            return choose_maximal_admission(
                candidates, reference, resources, costs, required=required,
                budget_ns=500_000, clock=clock,
                selection_objective="benefit" if strategy == "maximal_benefit" else "benefit_per_cost",
            )
        if shortest:
            return choose_shortest_work_first(
                candidates, reference, resources, costs, required=required,
                budget_ns=500_000, clock=clock,
                group_ready_siblings=observed.get("group_ready_siblings", False),
                **({"strict_remaining_work": True} if strategy == "strict_remaining_work" else {}),
            )
        return choose_combination(
            candidates, reference, resources, costs, required=required,
            budget_ns=500_000, clock=clock,
            allow_feasible_resource_alternatives=allow_feasible_resource_alternatives,
        )

    actual = run(lambda: 0)
    if canonical(actual) != expected and maximal_strategy and (
        observed["reason"] == "soft_deadline_maximal_reference_fallback"
    ):
        calls = -1

        def maximal_clock():
            nonlocal calls
            calls += 1
            return 500_000 if calls >= observed["deadline_checks"] else 0

        actual = run(maximal_clock)
    if canonical(actual) != expected and shortest and (
        observed["reason"] == "shortest_work_soft_deadline_reference_fallback"
    ):
        calls = -1

        def clock():
            nonlocal calls
            calls += 1
            if observed.get("group_ready_siblings", False):
                return 500_000 if calls >= observed["deadline_checks"] else 0
            return 500_000 if calls > observed["evaluated_candidates"] else 0

        actual = run(clock)
    if canonical(actual) != expected and not shortest and not maximal_strategy and (
        observed["reason"].startswith("soft_deadline_")
    ):
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
        **{count_field: observed[count_field]},
    )
    return summary


def periodic_service_fit(choice, selection, control, members, guard):
    """Bind the independent ready-service fit grant to this periodic decision."""
    actual = set(control["resumed_program_ids"])
    ordinary = set(choice["selected"])
    capacity, tokens = guard.get("logical_capacity_tokens"), guard.get("selected_tokens")
    if (
        guard.get("physical_capacity_credit") is not False
        or guard.get("monotonic_s") != control["monotonic_s"]
        or "displaced" not in guard
        or any(type(value) is not int or value <= 0 for value in (capacity, tokens))
        or not tokens <= capacity <= control["resume_capacity_tokens"]
        or tokens != sum(members[owner]["complete_budget_tokens"] for owner in actual)
    ):
        raise ValueError("invalid service fit boundary or logical budget")

    added = identity(guard["key"], grant=True)
    owner = added[2]
    if (
        owner in ordinary or owner not in actual
        or identity(members[owner]["key"], grant=True) != added
    ):
        raise ValueError("service fit changed the added request attempt")

    displaced = None
    if guard["displaced"] is not None:
        displaced = identity(guard["displaced"], grant=True)
        dropped = displaced[2]
        selected_key = selection.get("keys", {}).get(dropped)
        if (
            not choice["selected"] or dropped != choice["selected"][-1]
            or dropped not in members
            or identity(members[dropped]["key"], grant=True) != displaced
            or selected_key is not None and identity(selected_key, grant=True) != displaced
        ):
            raise ValueError("service fit changed the displaced request attempt")
        ordinary.remove(dropped)
    if ordinary | {owner} != actual:
        raise ValueError("service fit does not explain the actual periodic grant")
    return added, displaced


def inspect(
    records, batches, costs, cost_identity, *, allow_feasible_resource_alternatives=False,
    plan_device_capacity=False, include_all_selected_service=False,
):
    """Associate choices only with exact grants in their actual dispatch boundaries."""
    if type(include_all_selected_service) is not bool:
        raise ValueError("selected-service coverage must be explicitly enabled")
    progress = periodic_inspect(records, batches, include_free_grants=True)
    episodes = {}
    for episode in progress["ready_grant_episodes"]:
        key = tuple(episode["key"]), episode["journal_sequence"]
        if key in episodes:
            raise ValueError("duplicate actual periodic service episode")
        episodes[key] = episode
    choices, pending, fit_pending, free_pending, last_free_associations = [], None, None, None, {}
    for sequence, row in enumerate(records):
        if row.get("event") == EVENT:
            last_free_associations = {}
            choice = replay_choice(
                row, costs, cost_identity,
                allow_feasible_resource_alternatives=allow_feasible_resource_alternatives,
                plan_device_capacity=plan_device_capacity,
            )
            choice["journal_sequence"] = sequence
            if include_all_selected_service:
                known = set(row.get("keys") or {}) & {
                    item["identity"] for item in row.get("candidates") or ()
                }
                choice.update(
                    all_periodic_service=[], all_free_service=[],
                    all_selected_metadata_complete=(
                        choice["search_replayed"] and set(choice["selected"]) <= known
                    ),
                )
            choices.append(choice)
            if row["source"] == "periodic_decay_resume":
                if fit_pending is not None:
                    raise ValueError("service fit is missing its periodic grant boundary")
                pending = choice, row
                free_pending = None
            else:
                free_pending = (choice, row) if choice["search_replayed"] else None
                targets = (
                    choice["selected"] if include_all_selected_service
                    and choice["all_selected_metadata_complete"]
                    else choice["newly_selected_invocations"]
                )
                service_rows = [{
                    "invocation_id": owner, "key": identity(row["keys"][owner], grant=True),
                    "actual_grant_verified": False, "separate_tool_replacement": False, "episode": None,
                } for owner in targets]
                choice["free_service"] = [
                    service for service in service_rows
                    if service["invocation_id"] in choice["newly_selected_invocations"]
                ]
                if include_all_selected_service:
                    choice["all_free_service"] = service_rows
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
                for service in choice[
                    "all_free_service" if include_all_selected_service else "free_service"
                ]:
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
        elif row.get("event") == "ready_service_fit_granted":
            if pending is None or fit_pending is not None:
                raise ValueError("service fit needs one pending periodic selection")
            fit_pending = row
        elif row.get("event") == "agent_periodic_admission_decision":
            free_pending = None
            last_free_associations = {}
            if pending is None:
                continue
            choice, selection = pending
            pending = None
            members = {item["invocation_id"]: item for item in row["programs"]}
            granted = set(row["resumed_program_ids"])
            if fit_pending is not None:
                added, displaced = periodic_service_fit(choice, selection, row, members, fit_pending)
                choice["periodic_ready_service_fit"] = {
                    "key": added, "displaced": displaced,
                    "episode": episodes.get((added, sequence)),
                }
                fit_pending = None
            elif granted != set(choice["selected"]):
                raise ValueError("combination and following actual periodic grant disagree")
            targets = (
                choice["selected"] if include_all_selected_service
                and choice["all_selected_metadata_complete"]
                else choice["newly_selected_invocations"]
            )
            for owner in targets:
                if owner not in granted:
                    continue  # The independent fit grant displaced this member.
                member = members[owner]
                selected_key = identity(selection["keys"][owner], grant=True)
                if identity(member["key"], grant=True) != selected_key:
                    raise ValueError("periodic grant changed the selected request attempt")
                service = {
                    "invocation_id": owner, "key": selected_key,
                    "episode": episodes.get((selected_key, sequence)),
                }
                if owner in choice["newly_selected_invocations"]:
                    choice["periodic_service"].append(service)
                if include_all_selected_service:
                    choice["all_periodic_service"].append(service)
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
            "periodic_ready_service_fit_grants",
            "periodic_ready_service_fit_followed_by_exact_service",
            "periodic_ready_service_fit_displacements",
            "newly_selected_periodic_displaced_by_ready_service_fit",
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
        if (fit := choice.get("periodic_ready_service_fit")) is not None:
            counts["periodic_ready_service_fit_grants"] += 1
            counts["periodic_ready_service_fit_followed_by_exact_service"] += (
                fit["episode"] is not None and fit["episode"]["following_native_service_ms"] is not None
            )
            if fit["displaced"] is not None:
                counts["periodic_ready_service_fit_displacements"] += 1
                counts["newly_selected_periodic_displaced_by_ready_service_fit"] += (
                    fit["displaced"][2] in choice["newly_selected_invocations"]
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
            "independent_ready_service_fit_grants_are_attributed_separately": True,
            "free_dispatch_selection_replayed_but_service_not_associated": not any(
                service["actual_grant_verified"] and not service["separate_tool_replacement"]
                for choice in choices for service in choice[
                    "all_free_service" if include_all_selected_service else "free_service"
                ]
            ),
            "free_dispatch_exact_grant_association_enabled": True,
            "free_dispatch_boundary_closes_at_next_selection_or_coalesced_dispatch_record": True,
            "free_grant_observed_active_at_publication_not_assumed_after_pressure": True,
            "separate_tool_replacement_grants_excluded_from_free_selection_service_counts": True,
            "A_to_B_is_separate_single_donor_order_not_combination_search": True,
            "missing_grant_or_service_evidence_is_unknown": True,
            "exclusive_causal_JCT_or_throughput_gain_proven": False,
            **({
                "all_selected_service_includes_retained_reference_members": True,
                "reference_outsider_counts_keep_their_original_population": True,
            }
               if include_all_selected_service else {}),
        },
    }


def verify_model_work_pair(control, oracle, learned):
    """Bind Oracle analysis through its declared learned R5 reference."""
    from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
    from scripts.finish_model_work_oracle import verify_same_objective
    from scripts.prepare_r5_path_comparison import ORACLE_PATH_TARGET, verify_shared_path_comparison
    from scripts.review_learned_work_ranking import verify_learned_combination_pair

    if oracle["comparison_contract"].get("workflow_work_prediction") == ORACLE_PATH_TARGET:
        return verify_shared_path_comparison(control, learned, oracle)
    paths = verify_learned_combination_pair(control, learned)
    private = control.get("private_analysis_cohort")
    if not isinstance(private, dict) or private.get("delivered_to_scheduler") is not False:
        raise ValueError("model-work analysis requires its private fixed measurement cohort")
    for name in (
        "source_registration", "workflow_arrivals", "tool_timing_overlay",
        "stream_response_metrics", "workload_acceptance", "private_analysis_cohort",
    ):
        if any(spec.get(name) != control.get(name) for spec in (learned, oracle)):
            raise ValueError("model-work comparison changed traffic or its fixed private cohort")
    environment = oracle["common_environment"]
    path = Path(environment["WORKFLOW_WORK_MODEL"])
    truth = WorkflowWorkOracleArtifact.load(
        path, environment["WORKFLOW_WORK_MODEL_SHA256"],
    ).raw
    verify_same_objective(learned, oracle, causal.load(paths[1]), truth)
    for name in (
        "minimum_promising_measured_mean_JCT_reduction",
        "common_window_output_throughput_noninferiority_fraction",
    ):
        if oracle["comparison_contract"].get(name) != control["comparison_contract"].get(name):
            raise ValueError("model-work comparison changed its performance thresholds")
    return [*paths, path]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "control", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("--policy-kind", choices=("oracle", "learned"), default="oracle")
    parser.add_argument(
        "--model-work-reference", type=Path,
        help="learned R5 configuration binding the Oracle's model-work or model-tool path objective",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier combination observations")
    plan, control = (causal.load(path) for path in (args.registration, args.control))
    for item in (plan, control):
        causal.comparison.verify_sources(item)
    candidate_path, control_path = (Path(item["experiment_configuration"]) for item in (plan, control))
    candidate = causal.load(candidate_path)
    validation_paths = []
    exact_path = False
    if args.model_work_reference is not None:
        if args.policy_kind != "oracle":
            raise ValueError("an exact work reference applies only to Oracle analysis")
        from scripts.prepare_r5_path_comparison import ORACLE_PATH_TARGET
        from scripts.review_predictive_thunder_cohort import matching_serving_traffic

        exact_path = candidate["comparison_contract"].get("workflow_work_prediction") == ORACLE_PATH_TARGET
        matching_serving_traffic(control, plan)
        validation_paths = [
            args.model_work_reference,
            *verify_model_work_pair(
                causal.load(control_path), candidate, causal.load(args.model_work_reference),
            ),
            ROOT / "scripts/finish_model_work_oracle.py",
            ROOT / "scripts/prepare_r5_path_comparison.py",
            ROOT / "scripts/review_learned_work_ranking.py",
            ROOT / "beliefkv/runtime/workflow_work_oracle.py",
        ]
        private = candidate["private_analysis_cohort"]
        if private.get("path") != str(args.cohort.resolve()) or (
            private.get("sha256") != sha256_file(args.cohort)
        ):
            raise ValueError("model-work analysis uses another private cohort")
    elif args.policy_kind == "learned":
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
    artifact = causal.load(artifact_path)
    profile = artifact["admission_cost_profile"]
    shortest = artifact.get("shortest_work_first_admission", False)
    maximal = artifact.get("cost_aware_maximal_admission", False)
    if shortest:
        name = "beliefkv/policy/workflow_shortest_frontier.py"
        if sha256_file(ROOT / name) != plan["source_sha256"].get(name):
            raise ValueError("short-work policy differs from the actual serving source")
        validation_paths.append(ROOT / name)
    if maximal:
        name = "beliefkv/policy/workflow_maximal_admission.py"
        if sha256_file(ROOT / name) != plan["source_sha256"].get(name):
            raise ValueError("maximal packing differs from the actual serving source")
        validation_paths.append(ROOT / name)
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    records = list(reader(arm / "opportunities/admission_opportunities.jsonl"))
    validate_selection_policy(
        records, shortest_work_first=shortest, cost_aware_maximal=maximal,
        maximal_objective=artifact.get("maximal_admission_objective", "benefit_per_cost"),
        ready_wait_priority_ms=artifact.get("ready_wait_priority_ms", 0),
        ready_capacity_accumulation_ms=artifact.get("ready_capacity_accumulation_ms", 0),
        ready_wait_priority_grace_ms=artifact.get("ready_wait_priority_grace_ms"),
        ready_wait_priority_max_boost=artifact.get("ready_wait_priority_max_boost"),
        strict_remaining_work=artifact.get("strict_remaining_work_admission", False),
    )
    result = inspect(
        records,
        reader(arm / "server/runtime_audit.jsonl"),
        CombinationCosts(**profile["costs"]), profile["identity"],
        allow_feasible_resource_alternatives=(
            profile["kind"] in (
                "r5_frozen_batch_cost_proxy_capacity_v4", "r5_frozen_batch_cost_proxy_capacity_v5",
                "r5_frozen_batch_cost_proxy_capacity_v6",
                "r5_frozen_batch_cost_proxy_capacity_v7",
            )
        ),
        plan_device_capacity=profile["kind"] in (
            "r5_frozen_batch_cost_proxy_capacity_v6", "r5_frozen_batch_cost_proxy_capacity_v7",
        ),
    )
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        analysis_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        registration=str(args.registration), complete_measured_execution_verified=args.complete,
        policy_kind=args.policy_kind,
        exact_model_work_reference_verified=args.model_work_reference is not None and not exact_path,
        exact_model_tool_path_reference_verified=exact_path,
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.registration, args.control, args.cohort, candidate_path, control_path,
            artifact_path, Path(__file__), ROOT / "beliefkv/policy/workflow_combination.py",
            ROOT / "scripts/audit_periodic_admission_decisions.py",
            ROOT / "scripts/audit_free_dispatch_service.py",
            *validation_paths,
        )},
        formal_workload_accepted=False, GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print({"counts": result["counts"], "reasons": result["reasons"]})


if __name__ == "__main__":
    main()
