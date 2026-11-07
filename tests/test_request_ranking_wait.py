from copy import deepcopy
from dataclasses import asdict

import pytest

from beliefkv.policy import workflow_combination as policy
from scripts import audit_learned_combination_prediction as work
from scripts import audit_workflow_combination_choices as reader
from scripts.audit_request_ranking_wait import choice_details


def fixture():
    candidates = [
        policy.CombinationCandidate("big", "big-wf", 100, 0, 0, .5, "big",
                                    input_tokens=1, context_tokens=10),
        *[policy.CombinationCandidate(name, name, 30, 0, i + 1, .9, name,
                                     input_tokens=1, context_tokens=10)
          for i, name in enumerate(("a", "b", "c"))],
    ]
    resources = policy.CombinationResources(100, 1000, 10, 4, device_capacity_tokens=1000)
    costs = policy.CombinationCosts(.1, .1, (1., 2., 3., 4.), 1000.,
                                    decode_cost_basis="full_batch", selection_objective="benefit")
    result = policy.choose_combination(
        candidates, ["a", "b", "c"], resources, costs, clock=lambda: 0,
        allow_feasible_resource_alternatives=True,
    )
    row = {
        "event": reader.EVENT, "source": "periodic_decay_resume", "monotonic_s": 1.,
        "physical_capacity_credit": False, "cost_identity": "cost",
        "metadata_and_selection_cpu_ns": 1, "metadata_and_selection_wall_ns": 1,
        "resource_fallback_selection_enabled": True, "device_fit_scope": "planned_pool_geometry",
        "decision": result, "resources": asdict(resources),
        "candidates": [asdict(candidate) for candidate in candidates],
        "keys": {candidate.identity: {
            "request_id": candidate.identity, "root_workflow_id": candidate.workflow,
            "invocation_id": candidate.identity, "context_id": candidate.identity,
            "context_epoch": 0, "attempt_id": 0, "session_id": candidate.identity, "session_generation": 1,
        } for candidate in candidates},
        "predictions": {"big-wf": None},
    }
    observation = {"monotonic_s": 1., "candidates": [{
        "key": row["keys"]["big"], "features": [1.],
        "predictions": {"synchronous": {"support": "observed_progress_outside_training_support"}},
    }]}
    return row, observation, costs


def test_individual_planning_fit_does_not_mean_selected_group_has_room():
    row, observation, costs = fixture()
    result = choice_details([row], [observation], "big", costs, {"identity": "cost"},
                            (policy, reader, work))
    decision = result["decisions"][0]
    assert decision["state"] == "planning_fit_not_selected"
    assert decision["planning_target_with_required_JOIN"] is not None
    assert decision["planning_selected_plus_target"] is None
    assert decision["max_target_weight_one_selection"] == ("a", "b", "c")
    assert result["counts"]["cached_prediction_absent"] == 1
    assert result["counts"]["max_weight_one_would_select_target"] == 0


def test_changed_forecast_cannot_bypass_required_join_capacity():
    row, observation, costs = fixture()
    candidates = work.combination_candidates(row)
    row["decision"] = policy.choose_combination(
        candidates, ["a", "b", "c"], policy.CombinationResources(**row["resources"]), costs,
        required=["a"], clock=lambda: 0, allow_feasible_resource_alternatives=True,
    )
    result = choice_details([row], [observation], "big", costs, {"identity": "cost"},
                            (policy, reader, work))
    assert result["decisions"][0]["state"] == "does_not_fit_with_required_JOIN"
    assert "a" in result["decisions"][0]["max_target_weight_one_selection"]


@pytest.mark.parametrize("change", ("different_request", "different_owner", "missing_features",
                                   "changed_search", "different_boundary"))
def test_request_diagnosis_rejects_mismatched_identity_or_state(change):
    row, observation, costs = fixture()
    if change == "different_request":
        row["keys"]["big"]["request_id"] = "other"
    elif change == "different_owner":
        observation = deepcopy(observation)
        observation["candidates"][0]["key"]["context_epoch"] += 1
    elif change == "missing_features":
        observation["candidates"][0].pop("features")
    elif change == "different_boundary":
        observation["monotonic_s"] += 1.
    else:
        row["decision"]["selected"] = ["big"]
    with pytest.raises(ValueError):
        choice_details([row], [observation], "big", costs, {"identity": "cost"},
                       (policy, reader, work))


def test_actual_deadline_does_not_receive_an_unbounded_weight_counterfactual():
    row, observation, costs = fixture()
    ticks = iter((0, 500_000))
    row["decision"] = policy.choose_combination(
        work.combination_candidates(row), ["a", "b", "c"],
        policy.CombinationResources(**row["resources"]), costs,
        clock=lambda: next(ticks, 500_000), allow_feasible_resource_alternatives=True,
    )
    assert not row["decision"]["search_complete"]
    result = choice_details([row], [observation], "big", costs, {"identity": "cost"},
                            (policy, reader, work))
    assert result["counts"]["complete_searches"] == 0
    assert result["decisions"][0]["max_target_weight_one_selection"] is None
