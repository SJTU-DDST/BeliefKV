from dataclasses import replace

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationCandidate as Candidate, CombinationCosts as Costs,
    CombinationResources as Resources, choose_combination, probability_weight,
)


def cost():
    return Costs(.0001, .001, (1., 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7), 100000.)


def candidate(name, size, weight, *, workflow=None, **kwargs):
    return Candidate(
        name, workflow or name, size, 0, 0, weight,
        kwargs.pop("group", "single:" + name), input_tokens=1,
        context_tokens=10000, **kwargs,
    )


def select(rows, reference, resources=None, **kwargs):
    return choose_combination(
        rows, reference, resources or Resources(165000, 1000000, 2048, 4),
        cost(), budget_ns=100_000_000, **kwargs,
    )


def test_two_fitting_workflows_can_outweigh_one_shorter_individual():
    rows = [
        candidate("one", 110000, .9),
        candidate("two", 79000, .6), candidate("three", 83000, .6),
    ]
    result = select(rows, ("one",))
    assert set(result["selected"]) == {"two", "three"}
    assert result["search_complete"] and result["evaluated_subsets"] == 7
    assert result["selected_evaluation"]["benefit"] == 1.2


def test_original_category_is_no_longer_a_hard_partition():
    old = replace(candidate("old", 165000, .25), category=0)
    new = replace(candidate("new", 160000, 1.), category=1)
    assert select([old, new], ("old",))["selected"] == ("new",)


def test_real_all_join_coverage_is_non_linear_and_capped_per_workflow():
    rows = [
        candidate("a1", 80000, 1., workflow="a", group="join", unfinished_members=2),
        candidate("a2", 80000, 1., workflow="a", group="join", unfinished_members=2),
        candidate("b", 165000, .8),
    ]
    result = select(rows, ("b",))
    assert result["selected"] == ("a1", "a2")
    assert result["selected_evaluation"]["benefit"] == 1.
    rows[0] = replace(rows[0], active_ready_members=1)
    single = select(rows[::2], ("b",))
    assert single["selected_evaluation"]["benefit"] <= 1.


def test_shared_missing_extents_are_charged_once_and_conflicts_fall_back():
    rows = [
        replace(candidate(name, 80000, .6), missing_extents=((("shared", 1.), 200),))
        for name in ("a", "b")
    ]
    large = candidate("other", 165000, .7)
    result = select([large, *rows], ("other",), Resources(165000, 205, 2048, 4))
    assert result["selected"] == ("a", "b")
    assert result["selected_evaluation"]["missing_unique_tokens"] == 200
    rows[1] = replace(rows[1], missing_extents=((("shared", 1.), 201),))
    bad = select([large, *rows], ("other",), Resources(165000, 205, 2048, 4))
    assert bad["selected"] == ("other",)
    assert bad["reason"] == "unsupported_summary_reference_fallback"


@pytest.mark.parametrize("resources", (
    Resources(165000, 1000000, 2048, 1),
    Resources(165000, 1000000, 1, 4),
    Resources(165000, 2, 2048, 4, growth_tokens=1),
))
def test_slots_input_and_growth_reserve_cannot_be_overridden_by_prediction(resources):
    rows = [candidate("a", 110000, .9), candidate("b", 79000, .6), candidate("c", 83000, .6)]
    assert select(rows, ("a",), resources)["selected"] == ("a",)


def test_deadline_and_incomplete_reference_window_preserve_the_original_set():
    rows = [candidate("a", 110000, .9), candidate("b", 79000, .6), candidate("c", 83000, .6)]
    ticks = iter(range(0, 10_000_000_000, 1_000_000_000))
    result = select(rows, ("a",), clock=lambda: next(ticks))
    assert result["selected"] == ("a",) and not result["search_complete"]
    assert result["reason"] == "soft_deadline_reference_fallback"
    assert select(rows, ("missing",))["reason"] == "reference_outside_bounded_window"


def test_ties_preserve_reference_and_decode_batch_cost_is_not_per_request_sum():
    rows = [candidate("a", 165000, .5), candidate("b", 165000, .5)]
    assert select(rows, ("b",))["selected"] == ("b",)
    profile = cost()
    assert profile.decode_cost(2, 20000) < 2 * profile.decode_cost(1, 10000)
    assert probability_weight((.2, .3, .5)) == pytest.approx(.475)


def test_eligibility_can_span_native_chunks_without_double_reserving_all_input():
    rows = [
        replace(candidate("a", 110000, .4), input_tokens=10000),
        replace(candidate("b", 79000, .6), input_tokens=6000),
        replace(candidate("c", 83000, .6), input_tokens=6000),
    ]
    resources = Resources(165000, 2050, 2048, 4, chunked_input=True)
    result = select(rows, ("a",), resources)
    assert result["selected"] == ("b", "c")
    assert result["selected_evaluation"]["input_tokens"] == 12000
    assert result["selected_evaluation"]["next_chunk_input_tokens"] == 2048
    unchunked = select(rows, ("a",), replace(resources, chunked_input=False))
    assert unchunked["reason"] == "reference_resource_summary_unsupported"
