from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from beliefkv.policy.workflow_combination import (
    CombinationResources, _evaluate, choose_combination,
)
from scripts.audit_combination_cost_denominator import (
    NativeEvidence, benefit_only, inspect, summarize_reversals,
)
from scripts.audit_cost_denominator_service_coverage import inspect as inspect_coverage
from tests.test_combination_device_planning import audit_row
from tests.test_combination_full_batch_costs import costs
from tests.test_workflow_combination import candidate


def fixture(*, required=(), deadline=False):
    candidates = [
        replace(candidate("long", 200, .4), context_tokens=10000),
        replace(candidate("short", 200, .9), input_tokens=10000, context_tokens=10000,
                missing_extents=((("old", 1.), 10000),)),
    ]
    resources = CombinationResources(
        300, 10000, 20000, 2, device_capacity_tokens=30000,
    )
    profile = replace(costs(), h2d_ms_per_token=1., prefill_ms_per_token=.01)
    row = audit_row()
    row["candidates"] = [asdict(item) for item in candidates]
    row["resources"] = asdict(resources)
    ticks = iter((0, 500000, 600000))
    row["decision"] = choose_combination(
        candidates, ("long",), resources, profile, required=required,
        clock=(lambda: next(ticks)) if deadline else lambda: 0,
        allow_feasible_resource_alternatives=True,
    )
    row = json.loads(json.dumps(row))
    observation = {
        "monotonic_s": row["monotonic_s"], "candidates": [
            {"key": deepcopy(row["keys"][item.identity]),
             "observed_remaining_model_work": 500. if item.identity == "long" else 10.}
            for item in candidates
        ],
    }
    labels = {
        row["keys"][item.identity]["request_id"]: {
            "workflow_id": item.workflow, "invocation_id": item.identity,
            "context_id": row["keys"][item.identity]["context_id"],
            "output_tokens": 100 if item.identity == "short" else 10,
        } for item in candidates
    }
    return row, observation, labels, profile


def analyze(row, observation, labels, profile):
    return inspect(
        [row], [3], [observation], profile, {"identity": row["cost_identity"]},
        (100., 300.), {"long", "short"}, labels, NativeEvidence(set()),
    )


def test_cost_can_reverse_an_accurate_short_workflow_preference():
    row, observation, labels, profile = fixture()
    before = deepcopy((row, observation))
    result = analyze(row, observation, labels, profile)
    assert (row, observation) == before
    comparison = result["decisions"][0]["selections"]["cached"]
    assert comparison["ratio_selected"] == ("long",)
    assert comparison["benefit_only_selected"] == ("short",)
    reversals = result["strict_cost_reversals"]
    assert reversals["unique_exact_request_pairs"] == 1
    assert reversals["unique_request_pairs_short_request_has_more_remaining_output"] == 1
    assert reversals["unique_request_pair_examples"][0]["selected_long_served_first"] is None


def test_required_JOIN_and_real_resource_limits_remain_authoritative():
    row, observation, labels, profile = fixture(required=("long",))
    result = analyze(row, observation, labels, profile)
    assert result["strict_cost_reversals"]["unique_exact_request_pairs"] == 0
    for value in result["decisions"][0]["selections"].values():
        assert value["benefit_only_selected"] == ("long",)
    candidates = [candidate("a", 200, 1.), candidate("b", 200, .5)]
    resources = CombinationResources(300, 10, 1, 1, growth_tokens=100)
    result = benefit_only(candidates, ("b",), resources, profile)
    assert result["selected"] == ("b",)
    assert result["selected_evaluation"] is None


def test_removing_cost_can_fill_unused_budget_without_manufacturing_competition():
    row, observation, labels, profile = fixture()
    row["resources"]["logical_tokens"] = 500
    row["resources"]["device_capacity_tokens"] = 50000
    candidates = [
        replace(candidate("long", 200, .4), context_tokens=10000),
        replace(candidate("short", 200, .9), input_tokens=10000, context_tokens=10000,
                missing_extents=((("old", 1.), 10000),)),
    ]
    row["decision"] = choose_combination(
        candidates, ("long",), CombinationResources(**row["resources"]), profile,
        clock=lambda: 0, allow_feasible_resource_alternatives=True,
    )
    result = analyze(row, observation, labels, profile)
    assert result["counts"]["planning_ordinary_cross_workflow_capacity_competitions"] == 0
    assert set(result["decisions"][0]["selections"]["cached"]["benefit_only_selected"]) == {
        "long", "short",
    }


def test_incomplete_search_or_mutated_request_identity_cannot_enter_the_comparison():
    row, observation, labels, profile = fixture(deadline=True)
    result = analyze(row, observation, labels, profile)
    assert result["counts"]["incomplete_searches_excluded"] == 1
    assert not result["decisions"]
    row, observation, labels, profile = fixture()
    observation["candidates"][0]["key"]["attempt_id"] += 1
    result = analyze(row, observation, labels, profile)
    assert result["counts"]["missing_exact_observed_inputs"] == 1
    assert not result["decisions"]


def test_benefit_ties_preserve_reference_without_reintroducing_cost():
    candidates = [candidate("first", 100, .5), candidate("second", 100, .5)]
    resources = CombinationResources(100, 10000, 2048, 1)
    result = benefit_only(candidates, ("second",), resources, costs())
    assert result["selected"] == ("second",)
    assert _evaluate([candidates[0]], resources, costs()) is not None


def test_progress_requires_new_positive_launch_and_full_attempt_identity():
    native = NativeEvidence({"r"})
    member = {
        "request_id": "r", "workflow_id": "w", "invocation_id": "a", "context_id": "c",
        "context_epoch": 0, "attempt_id": 1, "session_id": "s", "session_generation": 1,
        "phase": "decode", "output_token_delta": 1, "token_delta": 1, "output_tokens_after": 1,
    }
    batch = {
        "event": "gpu_service_sample", "monotonic_clock_domain": "clock",
        "launch_monotonic_ms": 9., "service_start_monotonic_ms": 11.,
        "complete_monotonic_ms": 12., "phase": "decode", "request_samples": [member],
    }
    list(native.collect([batch]))
    from scripts.audit_free_dispatch_service import identity
    key = identity(member)
    assert native.after(key, 10.) is None
    assert native.after(key, 8.)["new_service_wait_ms"] == 3.
    assert native.output_before(key, 11.) == 0
    assert native.output_before(key, 12.) == 1
    assert native.after((*key[:5], 2, *key[6:]), 8.) is None
    with pytest.raises(ValueError, match="clocks"):
        list(NativeEvidence({"r"}).collect([{**batch, "launch_monotonic_ms": 20.}]))


def test_earlier_partial_identity_sample_keeps_actual_service_order_uncertain():
    row, observation, labels, profile = fixture()
    result = analyze(row, observation, labels, profile)
    pair = result["strict_cost_reversals"]["unique_request_pair_examples"][0]
    pair.update(
        both_followed_by_exact_new_progress=True, selected_long_served_first=True,
        selected_long_request_actual_direct_grant=True, selected_long_request_retained_at_grant=True,
        actual_short_minus_long_service_gap_ms=10.,
    )
    for name, wait in (
        ("shorter_workflow_expensive_request", 20.), ("longer_workflow_cheaper_request", 10.),
    ):
        pair[name]["following_exact_native_progress"] = {"new_service_wait_ms": wait}
    baseline = inspect_coverage(result, [])
    assert baseline["counts"][
        "direct_retained_long_served_first_without_earlier_ambiguous_samples"
    ] == 1
    key = pair["shorter_workflow_expensive_request"]["key"]
    sample = {
        "request_id": key["request_id"], "workflow_id": key["root_workflow_id"],
        "invocation_id": key["invocation_id"], "context_id": key["context_id"],
        "phase": "decode", "output_token_delta": 1, "token_delta": 1,
    }
    partial = {
        "event": "gpu_service_sample", "phase": "decode",
        "launch_monotonic_ms": pair["monotonic_s"] * 1000. + 1.,
        "service_start_monotonic_ms": pair["monotonic_s"] * 1000. + 2.,
        "request_samples": [sample],
    }
    covered = inspect_coverage(result, [partial])
    assert covered["counts"]["pairs_with_ambiguous_earlier_positive_samples"] == 1
    assert covered["unambiguous_observed_long_first_pair_gaps_ms"]["count"] == 0
    partial["request_samples"][0]["invocation_id"] = "other"
    assert inspect_coverage(result, [partial])["counts"][
        "pairs_with_ambiguous_earlier_positive_samples"
    ] == 0


def test_repeated_boundary_observations_are_not_independent_request_pairs():
    row, observation, labels, profile = fixture()
    decision = analyze(row, observation, labels, profile)["decisions"][0]
    later = deepcopy(decision)
    later["monotonic_s"] += 1.
    later["raw_journal_sequence"] += 1
    result = summarize_reversals([decision, later])
    assert result["boundary_pair_observations"] == 2
    assert result["boundaries"] == 2
    assert result["unique_exact_request_pairs"] == 1
    assert result["unique_workflow_pairs"] == 1
