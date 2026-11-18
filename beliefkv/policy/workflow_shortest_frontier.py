"""CPU candidate: retain a shortest feasible frontier before bounded JOIN packing."""

import math
import time

from beliefkv.policy.workflow_combination import _evaluate, choose_combination


def strict_remaining_work_enabled(raw):
    enabled = raw.get("strict_remaining_work_admission", False)
    if type(enabled) is not bool or enabled and (
        raw.get("shortest_work_first_admission") is not True
        or raw.get("remaining_work_ranking_enabled", True) is not True
        or raw.get("cost_aware_maximal_admission", False) is not False
        or raw.get("maximal_admission_objective", "benefit_per_cost") != "benefit_per_cost"
        or type(raw.get("ready_wait_priority_ms", 0)) is not int
        or raw.get("ready_wait_priority_ms", 0) != 0
        or any(name in raw for name in (
            "ready_wait_priority_grace_ms", "ready_wait_priority_max_boost",
            "tool_boundary_preemption", "pressure_work_order",
        ))
        or type(raw.get("ready_capacity_accumulation_ms", 0)) is not int
        or raw.get("ready_capacity_accumulation_ms", 0) != 0
    ):
        raise ValueError("strict remaining-work admission excludes aging, utility packing and capacity holds")
    return enabled


def choose_shortest_work_first(
    candidates, reference, resources, costs, *, required=(), budget_ns=500_000,
    clock=time.perf_counter_ns, strict_remaining_work=False, group_ready_siblings=True,
):
    """Fill by workflow priority, retaining JOIN rights and ready sibling adjacency."""
    started = clock()
    candidates, reference, required = tuple(candidates), tuple(reference), tuple(required)
    result = {
        "selected": reference, "reference": reference,
        "required_reference_members": required,
        "evaluated_candidates": 0, "search_complete": False,
        "reason": "shortest_work_reference_fallback",
    }
    if strict_remaining_work:
        result["strict_remaining_work_admission"] = True
    if group_ready_siblings:
        result.update(group_ready_siblings=True, deadline_checks=0)

    def expired():
        if group_ready_siblings:
            result["deadline_checks"] += 1
        return clock() - started >= budget_ns

    try:
        by_id = {row.identity: row for row in candidates}
        if (
            not 1 <= len(candidates) <= 8 or len(by_id) != len(candidates)
            or len(set(reference)) != len(reference) or len(set(required)) != len(required)
            or type(strict_remaining_work) is not bool
            or type(group_ready_siblings) is not bool
            or not strict_remaining_work and not set(reference) <= by_id.keys()
            or not set(required) <= set(reference) or not set(required) <= by_id.keys()
            or any(type(owner) is not str or not owner for owner in reference)
            or type(budget_ns) is not int or budget_ns <= 0
            or costs.selection_objective != "benefit" or costs.decode_cost_basis != "full_batch"
            or type(resources.cold_device_tokens) is not int or resources.cold_device_tokens < 0
            or resources.device_capacity_tokens is not None and (
                type(resources.device_capacity_tokens) is not int
                or resources.device_capacity_tokens <= 0
                or resources.device_capacity_tokens
                < resources.device_tokens + resources.cold_device_tokens
            )
        ):
            raise ValueError("invalid bounded short-work admission contract")
        weights = {}
        for row in candidates:
            if (
                row.logical_tokens <= 0 or row.category not in (0, 1, 2)
                or not math.isfinite(row.weight) or not 0 <= row.weight <= 1
                or row.input_tokens < 0 or row.context_tokens <= 0
                or any(type(tokens) is not int or tokens <= 0 for _, tokens in row.missing_extents)
                or row.workflow in weights and weights[row.workflow] != row.weight
            ):
                raise ValueError("invalid or inconsistent short-work candidate")
            weights[row.workflow] = row.weight
        complete_reference = set(reference) <= by_id.keys()
        baseline = (
            _evaluate([by_id[owner] for owner in reference], resources, costs)
            if complete_reference else None
        )
        result["reference_evaluation"] = baseline
        if strict_remaining_work:
            result["reference_metadata_complete"] = complete_reference
        selected = [by_id[owner] for owner in required]
        if selected and _evaluate(selected, resources, costs) is None:
            result["reason"] = "required_JOIN_resource_fallback"
            return result
        ordered = sorted(
            (row for row in candidates if row.identity not in required),
            key=lambda row: (-row.weight, row.category, row.logical_tokens, row.sequence),
        )
        groups = {}
        def group_key(row):
            return row.workflow, row.group, row.weight, None if group_ready_siblings else row.category

        for row in ordered:
            groups.setdefault(group_key(row), []).append(row)
        seen = set(required)
        for row in ordered:
            peers = [
                peer for peer in groups[group_key(row)] if peer.identity not in seen
            ]
            if not peers:
                continue
            if group_ready_siblings and len(peers) > 1:
                if expired():
                    result["reason"] = "shortest_work_soft_deadline_reference_fallback"
                    return result
                if _evaluate([*selected, *peers], resources, costs) is not None:
                    selected.extend(peers)
                    seen.update(peer.identity for peer in peers)
                    result["evaluated_candidates"] += len(peers)
                    continue
            for peer in peers:
                if peer.identity in seen:
                    continue
                if expired():
                    result["reason"] = "shortest_work_soft_deadline_reference_fallback"
                    return result
                seen.add(peer.identity)
                result["evaluated_candidates"] += 1
                if _evaluate([*selected, peer], resources, costs) is not None:
                    selected.append(peer)
        evaluated = _evaluate(selected, resources, costs)
        if evaluated is None:
            result["reason"] = "no_feasible_shortest_work_reference_fallback"
            return result
        result.update(
            selected=tuple(row.identity for row in selected),
            selected_evaluation=evaluated, search_complete=True,
            reason=(
                "shortest_work_set_changed"
                if {row.identity for row in selected} != set(reference)
                else "shortest_work_reference"
            ),
        )
        return result
    except (AttributeError, TypeError, ValueError, KeyError, OverflowError) as error:
        result.update(reason="unsupported_summary_reference_fallback", error=str(error))
        return result
    finally:
        result["wall_ns"] = clock() - started


def choose_shortest_frontier(
    candidates, reference, resources, costs, *, required=(), budget_ns=500_000,
    clock=time.perf_counter_ns,
):
    """Plan eligibility only; no native admission, eviction, or transfer authority."""
    started = clock()
    candidates, reference, required = tuple(candidates), tuple(reference), tuple(required)
    result = {
        "kind": "shortest_feasible_frontier_then_combination_cpu_candidate",
        "selected": reference, "original_reference": reference,
        "required_JOIN_members": required, "shortest_frontier_members": (),
        "candidate_checks": 0, "search_complete": False,
        "reason": "original_reference_fallback",
    }
    try:
        by_id = {candidate.identity: candidate for candidate in candidates}
        if (
            not 1 <= len(candidates) <= 8 or len(by_id) != len(candidates)
            or len(set(reference)) != len(reference) or len(set(required)) != len(required)
            or not set(reference) <= by_id.keys() or not set(required) <= set(reference)
            or type(budget_ns) is not int or budget_ns <= 0
            or costs.selection_objective != "benefit" or costs.decode_cost_basis != "full_batch"
        ):
            raise ValueError("require a bounded benefit profile and exact reference/JOIN identities")
        weights = {}
        for candidate in candidates:
            if (
                candidate.logical_tokens <= 0 or candidate.category not in (0, 1, 2)
                or not math.isfinite(candidate.weight) or not 0 <= candidate.weight <= 1
                or candidate.input_tokens < 0 or candidate.context_tokens <= 0
                or any(type(tokens) is not int or tokens <= 0 for _, tokens in candidate.missing_extents)
                or candidate.workflow in weights and weights[candidate.workflow] != candidate.weight
            ):
                raise ValueError("invalid or inconsistent bounded candidate")
            weights[candidate.workflow] = candidate.weight
        protected = {by_id[owner].workflow for owner in required}
        forced = [by_id[owner] for owner in required]
        if forced and _evaluate(forced, resources, costs) is None:
            result["reason"] = "required_JOIN_resource_fallback"
            return result
        ordinary = sorted(
            (candidate for candidate in candidates if candidate.workflow not in protected),
            key=lambda candidate: (
                -candidate.weight, candidate.category, candidate.logical_tokens, candidate.sequence,
            ),
        )
        frontier = ()
        for candidate in ordinary:
            if clock() - started >= budget_ns:
                result["reason"] = "frontier_soft_deadline_reference_fallback"
                return result
            result["candidate_checks"] += 1
            if _evaluate([*forced, candidate], resources, costs) is None:
                continue
            peers = tuple(
                peer for peer in ordinary
                if (peer.workflow, peer.group, peer.category)
                == (candidate.workflow, candidate.group, candidate.category)
            )
            frontier = (
                peers if _evaluate([*forced, *peers], resources, costs) is not None
                else (candidate,)
            )
            break
        if not frontier:
            remaining = budget_ns - (clock() - started)
            if remaining <= 0:
                result["reason"] = "frontier_soft_deadline_reference_fallback"
                return result
            choice = choose_combination(
                candidates, reference, resources, costs, required=required, budget_ns=remaining,
                clock=clock, allow_feasible_resource_alternatives=True,
            )
        else:
            result["shortest_frontier_members"] = tuple(candidate.identity for candidate in frontier)
            result["shortest_frontier_workflow"] = frontier[0].workflow
            seed = list(forced) + list(frontier)
            seed_ids = {candidate.identity for candidate in seed}
            # A feasible reference containing the frontier keeps the existing
            # bounded search's JOIN contract and deadline fallback meaningful.
            for owner in reference:
                if owner not in seed_ids and _evaluate([*seed, by_id[owner]], resources, costs) is not None:
                    seed.append(by_id[owner])
                    seed_ids.add(owner)
            remaining = budget_ns - (clock() - started)
            if remaining <= 0:
                result["reason"] = "frontier_soft_deadline_reference_fallback"
                return result
            choice = choose_combination(
                candidates, [candidate.identity for candidate in seed], resources, costs,
                required=(*required, *result["shortest_frontier_members"]), budget_ns=remaining,
                clock=clock, allow_feasible_resource_alternatives=True,
            )
        if choice["reason"] == "unsupported_summary_reference_fallback":
            raise ValueError(choice.get("error", "unsupported native planning summary"))
        selected = choice["selected"]
        if (
            not set(required) <= set(selected)
            or frontier and not set(result["shortest_frontier_members"]) <= set(selected)
            or selected and _evaluate([by_id[owner] for owner in selected], resources, costs) is None
        ):
            raise ValueError("frontier selection violated a required right or resource budget")
        result.update(
            selected=selected, reason="shortest_frontier_then_combination" if frontier else "ordinary_combination",
            search_complete=choice["search_complete"], combination=choice,
        )
        return result
    except (AttributeError, TypeError, ValueError, KeyError, OverflowError) as error:
        result.update(reason="unsupported_summary_reference_fallback", error=str(error))
        return result
    finally:
        result["wall_ns"] = clock() - started
