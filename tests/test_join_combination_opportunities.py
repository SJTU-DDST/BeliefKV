from dataclasses import replace

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationCandidate, CombinationCosts, CombinationResources,
)
from scripts.audit_join_combination_opportunities import feasible_frontier


def candidate(identity, logical=5):
    return CombinationCandidate(
        identity, "workflow", logical, 0, 1, .5, "join",
        unfinished_members=2, input_tokens=1, context_tokens=logical,
    )


def costs():
    return CombinationCosts(
        .01, .01, (1., 2., 3.), 100.,
        selection_objective="benefit",
    )


def resources(logical=10):
    return CombinationResources(logical, 100, 10, 3)


def test_maximal_sets_keep_both_members_when_all_fit():
    feasible, maximal = feasible_frontier(
        [candidate("a"), candidate("b")], resources(), costs(),
    )
    assert len(feasible) == 3
    assert maximal == [frozenset(("a", "b"))]


def test_cap_conflict_exposes_both_branch_choices_without_relaxing_resources():
    feasible, maximal = feasible_frontier(
        [candidate("a", 6), candidate("b", 6)], resources(), costs(),
    )
    assert set(feasible) == set(maximal) == {frozenset(("a",)), frozenset(("b",))}


def test_required_join_right_is_present_in_every_alternative():
    feasible, maximal = feasible_frontier(
        [candidate("a", 6), candidate("b", 6)], resources(), costs(), required=("a",),
    )
    assert feasible == maximal == [frozenset(("a",))]


def test_maximal_sets_can_have_different_member_counts():
    rows = [
        replace(row, unfinished_members=3)
        for row in (candidate("a", 8), candidate("b", 4), candidate("c", 4))
    ]
    _, maximal = feasible_frontier(rows, resources(), costs())
    assert set(maximal) == {frozenset(("a",)), frozenset(("b", "c"))}


def test_device_and_growth_constraints_still_reject_join_members():
    rows = [replace(candidate("a"), missing_extents=(("page", 8),))]
    constrained = replace(resources(), device_tokens=10, growth_tokens=3)
    assert feasible_frontier(rows, constrained, costs()) == ([], [])


def test_unknown_required_member_never_silently_drops_protection():
    with pytest.raises(ValueError, match="required"):
        feasible_frontier([candidate("a")], resources(), costs(), required=("missing",))
