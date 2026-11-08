from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from beliefkv.policy.workflow_combination import choose_combination
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_frontier
from beliefkv.predictor.workflow_continuous_work import remaining_work_weight
from scripts.audit_shortest_frontier_candidate import continuous_oracle_evidence, omitted_service_exposure
from scripts.audit_workflow_combination_choices import replay_choice
from tests.test_combination_device_planning import audit_row
from tests.test_workflow_shortest_frontier import fixture
from tests.test_workflow_size_selection_audit import key


def evidence(*, required=(), capacity=None):
    candidates, resources, costs = fixture()
    work = {"short": 190., "long-a": 450., "long-b": 370.}
    reference = 200.
    candidates = [
        replace(candidate, weight=remaining_work_weight(work[candidate.workflow], reference))
        for candidate in candidates
    ]
    if capacity is not None:
        resources = replace(resources, logical_tokens=capacity)
    row = audit_row()
    row.update(
        candidates=[asdict(candidate) for candidate in candidates], resources=asdict(resources),
        keys={
            candidate.identity: {**key(candidate.identity), "root_workflow_id": candidate.workflow}
            for candidate in candidates
        },
        predictions={
            workflow: {
                "evidence": "oracle", "remaining_work": value, "remaining_work_units": int(value * 100),
                "reference_work": reference, "effective_weight": remaining_work_weight(value, reference),
                "remaining_path_s": 0., "model_only_path_s": 0., "tool_and_fixed_delay_path_s": 0.,
            } for workflow, value in work.items()
        },
        decision=choose_combination(
            candidates, ("long-a", "long-b"), resources, costs, required=required,
            clock=lambda: 0, allow_feasible_resource_alternatives=True,
        ),
    )
    return json.loads(json.dumps(row)), candidates, resources, costs, reference


def test_truth_alone_can_leave_short_work_outvoted_and_frontier_changes_the_same_state():
    row, candidates, resources, costs, reference = evidence()
    original = deepcopy(row)
    replay = replay_choice(
        row, costs, row["cost_identity"], allow_feasible_resource_alternatives=True,
        plan_device_capacity=True,
    )
    observed = continuous_oracle_evidence(row, costs, reference)
    choice = choose_shortest_frontier(
        candidates, row["decision"]["reference"], resources, costs, clock=lambda: 0,
    )
    assert set(replay["selected"]) == {"long-a", "long-b"}
    assert observed["planning_capacity_competition"]
    assert choice["selected"] == ("short",)
    assert choice["shortest_frontier_workflow"] == "short"
    assert row == original


def test_required_join_budget_and_workflow_are_excluded_from_ordinary_competition():
    row, candidates, resources, costs, reference = evidence(required=("long-a",))
    observed = continuous_oracle_evidence(row, costs, reference)
    assert not observed["planning_capacity_competition"]
    assert {item["workflow"] for item in observed["ordinary_workflows"]} == {"long-b"}
    choice = choose_shortest_frontier(
        candidates, row["decision"]["reference"], resources, costs,
        required=("long-a",), clock=lambda: 0,
    )
    assert "long-a" in choice["selected"] and "short" not in choice["selected"]


def test_all_candidates_fitting_is_not_counted_as_a_capacity_competition():
    row, _candidates, _resources, costs, reference = evidence(capacity=400)
    assert not continuous_oracle_evidence(row, costs, reference)["planning_capacity_competition"]


@pytest.mark.parametrize(("field", "value"), (
    ("remaining_work", float("nan")),
    ("remaining_work", True),
    ("remaining_work_units", 1),
    ("remaining_work_units", True),
    ("effective_weight", .1),
    ("reference_work", 100.),
    ("tool_and_fixed_delay_path_s", 10.),
    ("evidence", "learned"),
))
def test_malformed_or_different_objective_truth_cannot_supply_frontier_labels(field, value):
    row, _candidates, _resources, costs, reference = evidence()
    row["predictions"]["short"][field] = value
    with pytest.raises(ValueError, match="bound readout"):
        continuous_oracle_evidence(row, costs, reference)


def test_missing_prediction_is_unknown_and_cannot_be_neutral_truth():
    row, _candidates, _resources, costs, reference = evidence()
    row["predictions"]["short"] = None
    assert continuous_oracle_evidence(row, costs, reference) is None


def test_changed_actual_weight_and_incomplete_search_are_rejected():
    row, _candidates, _resources, costs, reference = evidence()
    row["candidates"][0]["weight"] = .1
    with pytest.raises(ValueError, match="bound readout"):
        continuous_oracle_evidence(row, costs, reference)
    row, _candidates, _resources, costs, reference = evidence()
    row["decision"]["search_complete"] = False
    with pytest.raises(ValueError, match="complete actual search"):
        continuous_oracle_evidence(row, costs, reference)


def service_fixture():
    query = {"monotonic_s": 10., "source": "periodic_decay_resume", "newly_selected_keys": [key("short")]}
    member = key("short")
    member["workflow_id"] = member.pop("root_workflow_id")
    member.update(phase="decode", output_token_delta=1, token_delta=1)
    batch = {
        "event": "gpu_service_sample", "phase": "decode", "monotonic_clock_domain": "same-host",
        "launch_monotonic_ms": 14990., "service_start_monotonic_ms": 15000.,
        "complete_monotonic_ms": 15010., "request_samples": [member],
    }
    return query, batch


def test_repeated_alternative_waits_are_unioned_without_fabricating_a_grant():
    query, batch = service_fixture()
    later = {**deepcopy(query), "monotonic_s": 12.}
    result = omitted_service_exposure([query, later], [batch], {"workflow-short"})
    assert result["measured"]["alternative_request_episodes"] == 2
    assert result["measured"]["distinct_exact_request_attempts"] == 1
    assert result["measured"]["historical_useful_service_delay_ms"]["sum"] == 8000.
    assert result["measured"]["within_workflow_useful_wait_union_sum_ms"] == 5000.
    assert result["semantics"]["no_actual_grant_to_the_counterfactual_choice_is_claimed"]
    assert result["semantics"]["avoidable_JCT_or_whole_system_speedup_established"] is False


@pytest.mark.parametrize("change", ("attempt", "session", "unknown", "zero", "prior_launch"))
def test_foreign_unknown_zero_or_already_launched_service_cannot_validate_an_alternative(change):
    query, batch = service_fixture()
    member = batch["request_samples"][0]
    if change == "attempt":
        member["attempt_id"] = 1
    elif change == "session":
        member["session_id"] = "other"
    elif change == "unknown":
        member.pop("session_generation")
    elif change == "zero":
        member.update(token_delta=0, output_token_delta=0)
    else:
        batch["launch_monotonic_ms"] = 9990.
    result = omitted_service_exposure([query], [batch], {"workflow-short"})
    assert result["all"]["with_following_new_useful_service"] == 0
    assert result["all"]["within_workflow_useful_wait_union_sum_ms"] == 0.


def test_earlier_exact_service_does_not_hide_later_service_or_change_query_identity():
    query, batch = service_fixture()
    earlier = {
        **deepcopy(batch), "launch_monotonic_ms": 8990.,
        "service_start_monotonic_ms": 9000., "complete_monotonic_ms": 9010.,
    }
    result = omitted_service_exposure([query], [earlier, batch], set())
    assert result["episodes"][0]["earlier_exact_services"] == 1
    assert result["all"]["historical_useful_service_delay_ms"]["p50"] == 5000.
    with pytest.raises(ValueError, match="duplicate"):
        omitted_service_exposure([query, deepcopy(query)], [batch], set())


def test_cross_clock_domains_and_impossible_native_clocks_are_rejected():
    query, batch = service_fixture()
    foreign = {**deepcopy(batch), "monotonic_clock_domain": "foreign"}
    with pytest.raises(ValueError, match="clock domains"):
        omitted_service_exposure([query], [batch, foreign], set())
    batch["launch_monotonic_ms"] = 16000.
    with pytest.raises(ValueError, match="invalid native clock"):
        omitted_service_exposure([query], [batch], set())
