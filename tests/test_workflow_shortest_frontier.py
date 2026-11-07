from copy import deepcopy
from dataclasses import replace

import pytest

from beliefkv.policy.workflow_combination import _evaluate, choose_combination
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_frontier
from tests.test_combination_device_planning import resource, rows
from tests.test_combination_full_batch_costs import costs


def fixture():
    base = replace(rows()[0], missing_extents=(), input_tokens=0)
    candidates = [
        replace(
            base, identity=owner, workflow=owner, group=owner, logical_tokens=tokens,
            context_tokens=tokens, weight=weight,
        ) for owner, tokens, weight in (("short", 210, .51), ("long-a", 14, .31), ("long-b", 115, .35))
    ]
    resources = replace(resource(), logical_tokens=216, slots=3)
    return candidates, resources, replace(costs(), selection_objective="benefit")


def select(candidates, resources, profile, reference=("long-a", "long-b"), **kwargs):
    return choose_shortest_frontier(
        candidates, reference, resources, profile, clock=lambda: 0, **kwargs,
    )


def test_short_frontier_is_not_outvoted_by_multiple_long_workflow_weights():
    candidates, resources, profile = fixture()
    ordinary = choose_combination(
        candidates, ("long-a", "long-b"), resources, profile,
        clock=lambda: 0, allow_feasible_resource_alternatives=True,
    )
    before = deepcopy((candidates, resources, profile))
    result = select(candidates, resources, profile)
    assert ordinary["selected"] == ("long-a", "long-b")
    assert result["selected"] == ("short",)
    assert result["shortest_frontier_workflow"] == "short"
    assert result["search_complete"]
    assert _evaluate([candidates[0]], resources, profile) is not None
    assert (candidates, resources, profile) == before


def test_required_join_and_its_resource_cost_survive_short_workflow_priority():
    candidates, resources, profile = fixture()
    result = select(candidates, resources, profile, required=("long-a",))
    assert "long-a" in result["selected"]
    assert "short" not in result["selected"]
    assert set(result["selected"]) == {"long-a", "long-b"}


def test_reference_and_short_frontier_fill_remaining_capacity_together():
    candidates, resources, profile = fixture()
    result = select(candidates, replace(resources, logical_tokens=400), profile)
    assert set(result["selected"]) == {"short", "long-a", "long-b"}


def test_all_observed_ready_siblings_are_retained_when_the_group_fits():
    candidates, resources, profile = fixture()
    first = replace(
        candidates[0], identity="s1", workflow="short", group="join", logical_tokens=50,
        unfinished_members=2,
    )
    second = replace(first, identity="s2")
    result = select([first, second, *candidates[1:]], resources, profile)
    assert set(result["shortest_frontier_members"]) == {"s1", "s2"}
    assert {"s1", "s2"} <= set(result["selected"])


def test_unready_sibling_is_not_required_and_an_oversized_group_can_make_partial_progress():
    candidates, resources, profile = fixture()
    first = replace(candidates[0], identity="s1", group="join", unfinished_members=3)
    second = replace(first, identity="s2")
    result = select([first, second, *candidates[1:]], resources, profile)
    assert result["shortest_frontier_members"] == ("s1",)
    assert result["selected"] == ("s1",)


def test_unfitting_short_candidate_is_skipped_without_claiming_capacity():
    candidates, resources, profile = fixture()
    result = select(candidates, replace(resources, logical_tokens=200), profile)
    assert result["shortest_frontier_workflow"] == "long-b"
    assert set(result["selected"]) == {"long-a", "long-b"}


@pytest.mark.parametrize("change", (
    {"logical_tokens": 1}, {"slots": 0}, {"device_capacity_tokens": 1, "growth_tokens": 2},
))
def test_no_new_feasible_frontier_keeps_the_original_reference(change):
    candidates, resources, profile = fixture()
    result = select(candidates, replace(resources, **change), profile)
    assert result["selected"] == ("long-a", "long-b")


def test_deadline_before_frontier_selection_preserves_the_original_reference():
    candidates, resources, profile = fixture()
    ticks = iter((0, 500_000, 500_001))
    result = choose_shortest_frontier(
        candidates, ("long-a", "long-b"), resources, profile, clock=lambda: next(ticks),
    )
    assert result["selected"] == ("long-a", "long-b")
    assert result["reason"] == "frontier_soft_deadline_reference_fallback"


@pytest.mark.parametrize("change", ("foreign_reference", "foreign_join", "cost_objective", "weights"))
def test_unsupported_or_changed_contracts_fall_back_without_granting_new_rights(change):
    candidates, resources, profile = fixture()
    reference, required = ("long-a", "long-b"), ()
    if change == "foreign_reference":
        reference = ("foreign",)
    elif change == "foreign_join":
        required = ("short",)
    elif change == "cost_objective":
        profile = replace(profile, selection_objective="benefit_per_cost")
    else:
        candidates[0] = replace(candidates[0], weight=float("nan"))
    result = select(candidates, resources, profile, reference=reference, required=required)
    assert result["selected"] == reference
    assert result["reason"] == "unsupported_summary_reference_fallback"
