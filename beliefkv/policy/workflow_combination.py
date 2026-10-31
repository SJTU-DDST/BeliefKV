"""Bounded workflow/JOIN set selection; no native mutation or future inputs."""

from collections import defaultdict
from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class CombinationCandidate:
    identity: str
    workflow: str
    logical_tokens: int
    category: int
    sequence: int
    weight: float
    group: str
    unfinished_members: int = 1
    active_ready_members: int = 0
    missing_extents: tuple = ()
    input_tokens: int = 0
    context_tokens: int = 0


@dataclass(frozen=True)
class CombinationResources:
    logical_tokens: int
    device_tokens: int
    input_tokens: int
    slots: int
    growth_tokens: int = 0
    active_width: int = 0
    active_context_tokens: int = 0
    chunked_input: bool = False
    input_page_tokens: int = 1
    cold_device_tokens: int = 0


@dataclass(frozen=True)
class CombinationCosts:
    """Frozen, explicitly supplied batch-cost proxy, never a JCT guarantee."""

    prefill_ms_per_token: float
    h2d_ms_per_token: float
    decode_ms_by_width: tuple
    reference_context_tokens: float
    decode_steps: int = 16
    epsilon_ms: float = .1
    relative_tie: float = .01
    prefill_ms_per_token_by_context: tuple = ()

    def __post_init__(self):
        if (
            not self.decode_ms_by_width
            or any(not math.isfinite(value) or value <= 0 for value in (
                self.prefill_ms_per_token, self.h2d_ms_per_token,
                self.reference_context_tokens, self.epsilon_ms, *self.decode_ms_by_width,
            ))
            or any(right < left for left, right in zip(
                self.decode_ms_by_width, self.decode_ms_by_width[1:],
            ))
            or type(self.decode_steps) is not int or not 1 <= self.decode_steps <= 128
            or not math.isfinite(self.relative_tie) or not 0 <= self.relative_tie < 1
        ):
            raise ValueError("invalid frozen combination cost profile")
        previous_bound, previous_rate = 0, 0.
        for bound, rate in self.prefill_ms_per_token_by_context:
            if (
                type(bound) is not int or bound <= previous_bound
                or type(rate) not in (int, float) or not math.isfinite(rate)
                or rate <= 0 or rate < previous_rate
            ):
                raise ValueError("invalid context prefill cost curve")
            previous_bound, previous_rate = bound, rate

    def decode_cost(self, width, contexts):
        if width == 0:
            return 0.
        if width > len(self.decode_ms_by_width):
            raise ValueError("batch width outside calibrated cost support")
        context_ratio = max(1., contexts / width / self.reference_context_tokens)
        return self.decode_steps * self.decode_ms_by_width[width - 1] * context_ratio

    def prefill_cost(self, tokens, context):
        if not self.prefill_ms_per_token_by_context:
            return tokens * self.prefill_ms_per_token
        if (
            type(tokens) is not int or type(context) is not int
            or not 0 <= tokens <= context
            or context > self.prefill_ms_per_token_by_context[-1][0]
        ):
            raise ValueError("prefill context outside frozen cost support")
        start, total = context - tokens, 0.
        for bound, rate in self.prefill_ms_per_token_by_context:
            end = min(context, bound)
            total += max(0, end - start) * rate
            start = max(start, end)
            if start >= context:
                break
        return total


def probability_weight(probabilities):
    if (
        len(probabilities) != 3
        or any(type(value) not in (int, float) or not math.isfinite(value)
               or not 0 <= value <= 1 for value in probabilities)
        or not math.isclose(sum(probabilities), 1., abs_tol=1e-6)
    ):
        raise ValueError("expected a supported three-class conditional distribution")
    return sum(p * factor for p, factor in zip(probabilities, (1., .5, .25)))


def _evaluate(rows, resources, costs, *, active_decode_cost=None, prefill_costs=None):
    if (
        not rows or len(rows) > resources.slots
        or sum(row.logical_tokens for row in rows) > resources.logical_tokens
    ):
        return None
    extents = {}
    inputs = sum(row.input_tokens for row in rows)
    for row in rows:
        for identity, tokens in row.missing_extents:
            if identity in extents and extents[identity] != tokens:
                raise ValueError("inconsistent shared native extent")
            extents[identity] = tokens
    missing = sum(extents.values())
    staged_inputs = inputs
    if resources.chunked_input:
        staged_inputs = min(inputs, resources.input_tokens)
        if staged_inputs < sum(min(row.input_tokens, resources.input_page_tokens) for row in rows):
            return None
    if (
        staged_inputs > resources.input_tokens
        or missing + staged_inputs + resources.growth_tokens
        > resources.device_tokens + resources.cold_device_tokens
    ):
        return None
    groups, weights = {}, {}
    for row in rows:
        value = groups.setdefault(
            (row.workflow, row.group),
            [row.unfinished_members, row.active_ready_members, 0],
        )
        if value[:2] != [row.unfinished_members, row.active_ready_members]:
            raise ValueError("JOIN snapshot changed inside a combination")
        value[2] += 1
        if row.workflow in weights and weights[row.workflow] != row.weight:
            raise ValueError("one workflow must share one prediction weight")
        weights[row.workflow] = row.weight
    coverage = defaultdict(float)
    for (workflow, _group), (total, active, selected) in groups.items():
        if not 0 <= active < active + selected <= total:
            raise ValueError("invalid unfinished ALL-JOIN coverage")
        coverage[workflow] += ((active + selected) ** 2 - active ** 2) / total ** 2
    benefit = sum(weights[workflow] * min(1., fraction) for workflow, fraction in coverage.items())
    before = (
        costs.decode_cost(resources.active_width, resources.active_context_tokens)
        if active_decode_cost is None else active_decode_cost
    )
    after = costs.decode_cost(
        resources.active_width + len(rows),
        resources.active_context_tokens + sum(row.context_tokens for row in rows),
    )
    prefill = (
        sum(
            prefill_costs[row.identity] if prefill_costs is not None
            else costs.prefill_cost(row.input_tokens, row.context_tokens)
            for row in rows
        )
        if costs.prefill_ms_per_token_by_context else inputs * costs.prefill_ms_per_token
    )
    incremental = (
        missing * costs.h2d_ms_per_token + prefill
        + max(0., after - before)
    )
    return {
        "score": benefit / (costs.epsilon_ms + incremental),
        "benefit": benefit, "incremental_cost_ms": incremental,
        "missing_unique_tokens": missing, "input_tokens": inputs,
        "next_chunk_input_tokens": staged_inputs,
    }


def choose_combination(
    candidates, reference, resources, costs, *, required=(), budget_ns=500_000,
    clock=time.perf_counter_ns, allow_feasible_resource_alternatives=False,
):
    """Preserve required rights and S0 unless a supported alternative is found."""
    started = clock()
    reference = tuple(reference)
    required = frozenset(required)
    by_id = {row.identity: row for row in candidates}
    result = {
        "selected": reference, "reference": reference, "reason": "reference",
        "evaluated_subsets": 0, "search_complete": False,
        "required_reference_members": tuple(identity for identity in reference if identity in required),
    }
    try:
        if type(allow_feasible_resource_alternatives) is not bool:
            raise ValueError("resource fallback selection must be explicit")
        if type(resources.cold_device_tokens) is not int or resources.cold_device_tokens < 0:
            raise ValueError("invalid safely reclaimable device capacity")
        if len(by_id) != len(candidates) or len(set(reference)) != len(reference):
            raise ValueError("duplicate combination identity")
        if not required <= set(reference):
            raise ValueError("required rights must belong to the reference set")
        if not 1 <= len(candidates) <= 8 or any(identity not in by_id for identity in reference):
            result["reason"] = "reference_outside_bounded_window"
            return result
        if len(candidates) == len(reference) or resources.slots <= 0:
            result["reason"] = "no_competing_choice"
            return result
        for row in candidates:
            if (
                row.logical_tokens <= 0 or row.category not in (0, 1, 2)
                or not math.isfinite(row.weight) or not 0 <= row.weight <= 1
                or row.input_tokens < 0 or row.context_tokens <= 0
                or any(type(tokens) is not int or tokens <= 0 for _key, tokens in row.missing_extents)
            ):
                raise ValueError("invalid combination candidate")
        active_decode_cost = costs.decode_cost(
            resources.active_width, resources.active_context_tokens,
        )
        prefill_costs = (
            {row.identity: costs.prefill_cost(row.input_tokens, row.context_tokens) for row in candidates}
            if costs.prefill_ms_per_token_by_context else None
        )
        baseline = _evaluate(
            [by_id[identity] for identity in reference], resources, costs,
            active_decode_cost=active_decode_cost, prefill_costs=prefill_costs,
        )
        if baseline is None and not allow_feasible_resource_alternatives:
            result["reason"] = "reference_resource_summary_unsupported"
            return result
        best_score = baseline["score"] if baseline is not None else 0.
        selected, best_value = reference, baseline
        result["reference_evaluation"] = baseline
        required_mask = sum(
            1 << index for index, row in enumerate(candidates) if row.identity in required
        )
        logical_by_mask = (
            [0] * (1 << len(candidates))
            if all(type(row.logical_tokens) is int for row in candidates) else None
        )
        deadline = False
        for mask in range(1, 1 << len(candidates)):
            if (mask - 1) % 8 == 0 and clock() - started >= budget_ns:
                deadline = True
                break
            if logical_by_mask is not None:
                bit = mask & -mask
                logical_by_mask[mask] = (
                    logical_by_mask[mask ^ bit] + candidates[bit.bit_length() - 1].logical_tokens
                )
            if mask & required_mask != required_mask:
                continue
            if mask.bit_count() > resources.slots or (
                logical_by_mask is not None and logical_by_mask[mask] > resources.logical_tokens
            ):
                result["evaluated_subsets"] += 1
                continue
            rows = [row for index, row in enumerate(candidates) if mask & (1 << index)]
            value = _evaluate(
                rows, resources, costs, active_decode_cost=active_decode_cost,
                prefill_costs=prefill_costs,
            )
            result["evaluated_subsets"] += 1
            if value is not None and (
                best_value is None or value["score"] > best_score * (1. + costs.relative_tie)
            ):
                best_score, selected, best_value = (
                    value["score"], tuple(row.identity for row in rows), value,
                )
        changed = set(selected) != set(reference)
        reason = "combination_changed" if changed else "reference_best_or_tied"
        if deadline:
            reason = "soft_deadline_best_feasible" if changed else "soft_deadline_reference_fallback"
        if baseline is None:
            reason = (
                "feasible_resource_alternative" if best_value is not None
                else "resource_fallback_no_feasible_subset"
            )
            if deadline:
                reason = (
                    "soft_deadline_feasible_resource_alternative" if best_value is not None
                    else "soft_deadline_resource_fallback"
                )
        result.update(selected=selected, reason=reason, search_complete=not deadline,
                      selected_evaluation=best_value)
        return result
    except (TypeError, ValueError, KeyError, OverflowError) as error:
        result.update(reason="unsupported_summary_reference_fallback", error=str(error))
        return result
    finally:
        result["wall_ns"] = clock() - started
