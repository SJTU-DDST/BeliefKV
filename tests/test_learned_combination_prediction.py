from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from beliefkv.policy.workflow_combination import choose_combination
from scripts.audit_learned_combination_prediction import inspect, pair_quality, planning_costs
from tests.test_combination_device_planning import audit_row, resource, rows
from tests.test_combination_full_batch_costs import costs


@pytest.mark.parametrize(("kind", "objective"), (
    ("r5_frozen_batch_cost_proxy_capacity_v6", "benefit_per_cost"),
    ("r5_frozen_batch_cost_proxy_capacity_v7", "benefit"),
))
def test_planning_profile_preserves_its_recorded_objective(kind, objective):
    expected = replace(costs(), selection_objective=objective)
    assert planning_costs({"kind": kind, "costs": asdict(expected)}) == expected


@pytest.mark.parametrize(("kind", "objective", "basis"), (
    ("r5_frozen_batch_cost_proxy_capacity_v5", "benefit_per_cost", "full_batch"),
    ("r5_frozen_batch_cost_proxy_capacity_v6", "benefit", "full_batch"),
    ("r5_frozen_batch_cost_proxy_capacity_v7", "benefit_per_cost", "full_batch"),
    ("r5_frozen_batch_cost_proxy_capacity_v6", "benefit_per_cost", "incremental"),
    ("r5_frozen_batch_cost_proxy_capacity_v7", "benefit", "incremental"),
))
def test_planning_profile_rejects_unsupported_or_mismatched_costs(kind, objective, basis):
    value = replace(costs(), selection_objective=objective, decode_cost_basis=basis)
    with pytest.raises(ValueError, match="planning profile and objective"):
        planning_costs({"kind": kind, "costs": asdict(value)})


def evidence(*, required=(), deadline=False, all_fit=False):
    row = audit_row()
    candidates = rows()
    candidates = [replace(candidate, weight=.9 if candidate.identity == "long" else .3)
                  for candidate in candidates]
    resources = replace(resource(), logical_tokens=400) if all_fit else resource()
    row["candidates"] = [asdict(value) for value in candidates]
    row["resources"] = asdict(resources)
    clock = iter((0, 500_000, 600_000)) if deadline else None
    row["decision"] = choose_combination(
        candidates, ("long",), resources, costs(), required=required,
        clock=(lambda: next(clock)) if deadline else lambda: 0,
        allow_feasible_resource_alternatives=True,
    )
    row = json.loads(json.dumps(row))
    observation = {
        "monotonic_s": row["monotonic_s"],
        "candidates": [{
            "key": deepcopy(row["keys"][value.identity]),
            "observed_remaining_model_work": 500. if value.identity == "long" else 10.,
            "predictions": {"synchronous": {
                "effective_weight": .3 if value.identity == "long" else .9,
            }},
        } for value in candidates],
    }
    return row, observation


def analyse(row, observed):
    return inspect(
        [row], [observed], costs(), {"identity": row["cost_identity"]}, (100., 300.),
        {value["workflow"] for value in row["candidates"]},
    )


def test_cached_model_and_offline_truth_are_separated_without_changing_resources():
    row, observed = evidence()
    original = deepcopy((row, observed))
    result = analyse(row, observed)
    assert (row, observed) == original
    assert result["counts"]["complete_searches_with_labels"] == 1
    decision = result["decisions"][0]
    assert decision["selections"]["actual_cached"]["selected"] == ("long",)
    for name in ("synchronous", "oracle_three_bins", "oracle_continuous"):
        assert decision["selections"][name]["selected"] == ("short",)
    assert result["ranking"]["actual_cached"]["both_development_workflows"][
        "boundary_equal_weight_accuracy"
    ] == 0.
    assert result["ranking"]["synchronous"]["both_development_workflows"][
        "boundary_equal_weight_accuracy"
    ] == 1.


def test_required_join_is_preserved_and_is_not_an_ordinary_competitor():
    row, observed = evidence(required=("long",))
    result = analyse(row, observed)
    assert result["counts"]["planning_feasible_ordinary_cross_workflow_capacity_competitions"] == 0
    assert all(
        "long" in value["selected"]
        for value in result["decisions"][0]["selections"].values()
    )
    assert result["ranking"]["actual_cached"]["all_complete_planning_competitions"]["boundaries"] == 0


def test_all_fit_and_deadline_cases_cannot_be_counted_as_comparable_competition():
    row, observed = evidence(all_fit=True)
    assert analyse(row, observed)["counts"][
        "planning_feasible_ordinary_cross_workflow_capacity_competitions"
    ] == 0
    row, observed = evidence(deadline=True)
    result = analyse(row, observed)
    assert result["counts"]["incomplete_search_excluded_from_weight_counterfactual"] == 1
    assert not result["decisions"]


def test_missing_or_changed_observed_attempt_stays_unknown():
    row, observed = evidence()
    observed["candidates"][0]["key"]["attempt_id"] += 1
    result = analyse(row, observed)
    assert result["counts"]["missing_exact_observed_inputs"] == 1
    assert not result["decisions"]


def test_changed_recorded_selection_and_nonfinite_labels_are_rejected():
    row, observed = evidence()
    row["decision"]["selected"] = ["short"]
    with pytest.raises(ValueError, match="reproduce"):
        analyse(row, observed)
    row, observed = evidence()
    observed["candidates"][0]["observed_remaining_model_work"] = float("nan")
    with pytest.raises(ValueError, match="label"):
        analyse(row, observed)


def test_ordinal_oracle_ties_do_not_claim_continuous_size_accuracy():
    decision = {
        "ordinary_workflows": [
            {"workflow": owner, "remaining_model_work": work, "weights": {"bins": .25}}
            for owner, work in (("a", 100.), ("b", 200.))
        ],
    }
    result = pair_quality([decision], "bins")
    assert result["boundary_equal_weight_accuracy"] == .5
    assert result["pair_observations"] == 1
    assert pair_quality([decision], "bins", {"a"})["boundaries"] == 0
