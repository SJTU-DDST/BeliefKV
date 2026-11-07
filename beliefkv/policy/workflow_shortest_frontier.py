"""CPU candidate: retain a shortest feasible frontier before bounded JOIN packing."""

import math
import time

from beliefkv.policy.workflow_combination import _evaluate, choose_combination


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
