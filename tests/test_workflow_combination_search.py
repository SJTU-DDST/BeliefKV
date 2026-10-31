from dataclasses import replace
from itertools import combinations
import random

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationCandidate as Candidate, CombinationCosts as Costs,
    CombinationResources as Resources, _evaluate, choose_combination,
)


def literal_selection(rows, reference, resources, costs, required):
    masks = sorted(
        sum(1 << index for index in indices)
        for width in range(1, len(rows) + 1)
        for indices in combinations(range(len(rows)), width)
    )
    baseline = _evaluate(
        [next(row for row in rows if row.identity == identity) for identity in reference],
        resources, costs,
    )
    selected, value, count = tuple(reference), baseline, 0
    for mask in masks:
        subset = [row for index, row in enumerate(rows) if mask & (1 << index)]
        if not set(required) <= {row.identity for row in subset}:
            continue
        candidate = _evaluate(subset, resources, costs)
        count += 1
        if candidate is not None and candidate["score"] > value["score"] * (1. + costs.relative_tie):
            selected, value = tuple(row.identity for row in subset), candidate
    return selected, value, count


@pytest.mark.parametrize("width", (6, 8))
def test_incremental_search_matches_literal_subsets_with_required_join_rights(width):
    rng = random.Random(71 + width)
    costs = Costs(.083, .004, tuple(5. + number for number in range(24)), 40000.)
    for case in range(120):
        rows = []
        for index in range(width):
            group = index // 2
            tokens = rng.randrange(1000, 70000)
            rows.append(Candidate(
                str(index), str(group), tokens + 100, index % 3, index,
                (group + 1) / 8., str(group), 3 if case % 2 else 2,
                1 if case % 2 else 0, (((group, 1.), 256),),
                rng.choice((1, 64, 4096)), tokens,
            ))
        resources = Resources(
            rng.randrange(75000, 180000), 65536, 2048, rng.randrange(1, 5),
            growth_tokens=16, active_width=4, active_context_tokens=160000,
            chunked_input=True,
        )
        reference, used = [], 0
        for row in rows:
            if used + row.logical_tokens <= resources.logical_tokens and len(reference) < resources.slots:
                reference.append(row.identity)
                used += row.logical_tokens
        required = reference[:1] if case % 3 else []
        expected, value, count = literal_selection(rows, reference, resources, costs, required)
        actual = choose_combination(
            rows, reference, resources, costs, required=required, clock=lambda: 0,
        )
        assert actual["search_complete"]
        assert actual["selected"] == expected
        assert actual["selected_evaluation"] == value
        assert actual["evaluated_subsets"] == count
        assert set(required) <= set(actual["selected"])


def test_noninteger_legacy_budget_input_keeps_literal_float_summation():
    costs = Costs(.01, .001, (1., 2., 3., 4.), 40000.)
    rows = [
        Candidate(str(i), str(i), 10. + i / 10., 0, i, .5, str(i), input_tokens=1, context_tokens=100)
        for i in range(3)
    ]
    resources = Resources(20.2, 10000, 2048, 4)
    expected, value, count = literal_selection(rows, ["0"], resources, costs, ())
    actual = choose_combination(rows, ["0"], resources, costs, clock=lambda: 0)
    assert actual["selected"] == expected
    assert actual["selected_evaluation"] == value
    assert actual["evaluated_subsets"] == count


def test_active_decode_cost_cache_does_not_change_evaluation_or_resource_rejection():
    costs = Costs(.01, .001, (1., 2., 3., 4.), 40000.)
    row = Candidate("a", "a", 100, 0, 0, .5, "a", input_tokens=64, context_tokens=100)
    resources = Resources(200, 10000, 2048, 4, active_width=1, active_context_tokens=100)
    before = costs.decode_cost(resources.active_width, resources.active_context_tokens)
    assert _evaluate([row], resources, costs) == _evaluate(
        [row], resources, costs, active_decode_cost=before,
    )
    assert _evaluate([row], replace(resources, device_tokens=0), costs, active_decode_cost=before) is None
