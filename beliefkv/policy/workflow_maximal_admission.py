"""Bounded cost-aware packing with no optional capacity left unfilled."""

import math
import time

from beliefkv.policy.workflow_combination import _evaluate


_SUBSET_STEPS = tuple(
    tuple(
        ((mask & -mask).bit_length() - 1, mask ^ (mask & -mask), mask.bit_count())
        for mask in range(1, 1 << count)
    )
    for count in range(9)
)


def choose_maximal_admission(
    candidates, reference, resources, costs, *, required=(), budget_ns=500_000,
    clock=time.perf_counter_ns, selection_objective="benefit_per_cost",
):
    started = clock()
    candidates, reference, required = tuple(candidates), tuple(reference), tuple(required)
    result = {
        "selected": reference, "reference": reference,
        "required_reference_members": required, "evaluated_subsets": 0,
        "resource_subsets": 0, "deadline_checks": 0, "search_complete": False,
        "selection_objective": (
            "maximal_benefit" if selection_objective == "benefit"
            else "maximal_benefit_per_cost"
        ),
        "reason": "maximal_reference_fallback",
    }

    def expired():
        result["deadline_checks"] += 1
        return clock() - started >= budget_ns

    try:
        by_id = {row.identity: row for row in candidates}
        if (
            not 1 <= len(candidates) <= 8 or len(by_id) != len(candidates)
            or selection_objective not in ("benefit_per_cost", "benefit")
            or len(set(reference)) != len(reference) or len(set(required)) != len(required)
            or not set(reference) <= by_id.keys() or not set(required) <= set(reference)
            or type(budget_ns) is not int or budget_ns <= 0
            or costs.selection_objective != "benefit" or costs.decode_cost_basis != "full_batch"
            or type(resources.chunked_input) is not bool
            or any(type(getattr(resources, name)) is not int or getattr(resources, name) < 0
                   for name in (
                       "logical_tokens", "device_tokens", "input_tokens", "slots", "growth_tokens",
                       "active_width", "active_context_tokens", "cold_device_tokens",
                   ))
            or type(resources.input_page_tokens) is not int or resources.input_page_tokens <= 0
            or resources.device_capacity_tokens is not None and (
                type(resources.device_capacity_tokens) is not int
                or resources.device_capacity_tokens <= 0
                or resources.device_capacity_tokens
                < resources.device_tokens + resources.cold_device_tokens
            )
        ):
            raise ValueError("invalid bounded maximal admission contract")
        weights, groups, extent_index, extent_tokens, extent_masks = {}, {}, {}, [], []
        for index, row in enumerate(candidates):
            if (
                any(type(value) is not str or not value for value in (row.identity, row.workflow, row.group))
                or type(row.logical_tokens) is not int or row.logical_tokens <= 0
                or row.category not in (0, 1, 2)
                or not math.isfinite(row.weight) or not 0 <= row.weight <= 1
                or type(row.input_tokens) is not int or not 0 <= row.input_tokens <= row.context_tokens
                or type(row.context_tokens) is not int or row.context_tokens <= 0
                or type(row.unfinished_members) is not int or row.unfinished_members <= 0
                or type(row.active_ready_members) is not int
                or not 0 <= row.active_ready_members < row.unfinished_members
                or row.workflow in weights and weights[row.workflow] != row.weight
            ):
                raise ValueError("invalid or inconsistent maximal candidate")
            weights[row.workflow] = row.weight
            group_key = row.workflow, row.group
            signature = row.unfinished_members, row.active_ready_members
            group = groups.setdefault(group_key, [signature, 0])
            if group[0] != signature:
                raise ValueError("inconsistent JOIN snapshot")
            group[1] |= 1 << index
            extent_mask = 0
            for identity, tokens in row.missing_extents:
                if type(tokens) is not int or tokens <= 0:
                    raise ValueError("invalid native extent")
                if identity not in extent_index:
                    extent_index[identity] = len(extent_tokens)
                    extent_tokens.append(tokens)
                extent = extent_index[identity]
                if extent_tokens[extent] != tokens:
                    raise ValueError("inconsistent shared native extent")
                extent_mask |= 1 << extent
            extent_masks.append(extent_mask)
        if expired():
            result["reason"] = "soft_deadline_maximal_reference_fallback"
            return result
        active_cost = costs.decode_cost(resources.active_width, resources.active_context_tokens)
        prefill_costs = {
            row.identity: costs.prefill_cost(row.input_tokens, row.context_tokens) for row in candidates
        }

        def evaluate(mask):
            return _evaluate(
                [row for index, row in enumerate(candidates) if mask & (1 << index)],
                resources, costs, active_decode_cost=active_cost, prefill_costs=prefill_costs,
            )

        reference_mask = sum(1 << index for index, row in enumerate(candidates) if row.identity in reference)
        required_mask = sum(1 << index for index, row in enumerate(candidates) if row.identity in required)
        result["reference_evaluation"] = evaluate(reference_mask)
        full = (1 << len(candidates)) - 1
        whole = evaluate(full)
        if whole is not None:
            result.update(
                selected=tuple(row.identity for row in candidates), selected_evaluation=whole,
                evaluated_subsets=1, resource_subsets=1, search_complete=True,
                reason="all_candidates_fit_maximal",
            )
            return result
        size = full + 1
        logical, inputs, pages, extents, missing = ([0] * size for _ in range(5))
        feasible = [False] * size
        resource_feasible = [False] * size
        device = (
            resources.device_capacity_tokens if resources.device_capacity_tokens is not None
            else resources.device_tokens + resources.cold_device_tokens
        )
        resource_feasible[0] = resources.growth_tokens <= device
        group_limits = [None] * len(candidates)
        for (total, active), mask in groups.values():
            if mask.bit_count() > total - active:
                for index in range(len(candidates)):
                    if mask & (1 << index):
                        group_limits[index] = mask, total - active
        # Resource feasibility is monotone. Single-member extensions suffice to
        # prove that a feasible set cannot accept any additional candidate.
        for mask, (index, previous, width) in enumerate(_SUBSET_STEPS[len(candidates)], 1):
            if (mask - 1) % 8 == 0 and expired():
                result["reason"] = "soft_deadline_maximal_reference_fallback"
                return result
            result["resource_subsets"] += 1
            # Required membership is separate: a resource-feasible prefix may
            # still acquire its required member in a later extension.
            if not resource_feasible[previous]:
                continue
            row = candidates[index]
            logical[mask] = logical[previous] + row.logical_tokens
            if width > resources.slots or logical[mask] > resources.logical_tokens:
                continue
            inputs[mask] = inputs[previous] + row.input_tokens
            pages[mask] = pages[previous] + min(row.input_tokens, resources.input_page_tokens)
            staged = min(inputs[mask], resources.input_tokens) if resources.chunked_input else inputs[mask]
            if staged > resources.input_tokens or (
                resources.chunked_input and staged < pages[mask]
            ):
                continue
            group = group_limits[index]
            if group is not None and (mask & group[0]).bit_count() > group[1]:
                continue
            extents[mask] = extents[previous] | extent_masks[index]
            added = extents[mask] ^ extents[previous]
            missing[mask] = missing[previous]
            while added:
                extent = added & -added
                missing[mask] += extent_tokens[extent.bit_length() - 1]
                added ^= extent
            if missing[mask] + staged + resources.growth_tokens > device:
                continue
            resource_feasible[mask] = True
            feasible[mask] = mask & required_mask == required_mask
        maximal = [
            mask for mask in range(1, size) if feasible[mask] and not any(
                feasible[mask | (1 << index)] for index in range(len(candidates))
                if not mask & (1 << index)
            )
        ]
        if not maximal:
            result.update(reason="no_feasible_maximal_reference_fallback", search_complete=True)
            return result
        best_mask = reference_mask if reference_mask in maximal else maximal[0]
        best = evaluate(best_mask)
        if best is None:
            raise ValueError("resource packing disagrees with native summary evaluation")
        score_field = "benefit" if selection_objective == "benefit" else "cost_normalized_score"
        for mask in maximal:
            if expired():
                result["reason"] = "soft_deadline_maximal_reference_fallback"
                return result
            value = best if mask == best_mask else evaluate(mask)
            if value is None:
                raise ValueError("resource packing disagrees with native summary evaluation")
            result["evaluated_subsets"] += 1
            if value[score_field] > best[score_field] * (1. + costs.relative_tie):
                best_mask, best = mask, value
        selected = tuple(row.identity for index, row in enumerate(candidates) if best_mask & (1 << index))
        result.update(
            selected=selected, selected_evaluation=best, search_complete=True,
            reason=(
                ("maximal_benefit_set_changed" if selection_objective == "benefit" else "maximal_cost_set_changed")
                if set(selected) != set(reference) else "maximal_reference_best_or_tied"
            ),
        )
        return result
    except (AttributeError, TypeError, ValueError, KeyError, OverflowError) as error:
        result.update(reason="unsupported_maximal_summary_reference_fallback", error=str(error))
        return result
    finally:
        result["wall_ns"] = clock() - started
