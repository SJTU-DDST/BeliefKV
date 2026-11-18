from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.runtime.ready_wait_priority import ReadyWaitSettings
from scripts.audit_ready_wait_aging import compare
from tests.test_combination_choice_audit import evidence
from tests.test_workflow_shortest_frontier import fixture


def recorded_aging(*, required=(), deadline=None):
    row, _ = evidence()
    rows, resources, costs = fixture()
    bases = {"short": .9, "long-a": .31, "long-b": .35}
    ages = {"short": 0., "long-a": 150000., "long-b": 150000.}
    rows = [
        replace(value, weight=min(1., bases[value.workflow] * (1. + ages[value.workflow] / 30000.)))
        for value in rows
    ]
    ticks = -1

    def clock():
        nonlocal ticks
        ticks += 1
        return 500000 if deadline is not None and ticks >= deadline else 0

    decision = choose_maximal_admission(
        rows, ("long-a", "long-b"), resources, costs, required=required,
        clock=clock, selection_objective="benefit",
    )
    template = row["keys"]["a"]
    row.update(
        selection_policy="maximal_benefit", decision=decision,
        candidates=[asdict(value) for value in rows], resources=asdict(resources),
        keys={
            value.identity: {
                **template, "invocation_id": value.identity, "root_workflow_id": value.workflow,
            } for value in rows
        },
        predictions={
            value.workflow: {
                "effective_weight": bases[value.workflow], "evidence": "oracle",
                "remaining_work": 1. / bases[value.workflow] - 1.,
            } for value in rows
        },
        ready_wait_priority={"scale_ms": 30000, "workflow_age_ms": ages},
        resource_fallback_selection_enabled=True, device_fit_scope="planned_pool_geometry",
    )
    return row, costs


def test_fixed_state_replay_corrects_aged_long_selection_without_changing_the_trace():
    row, costs = recorded_aging()
    before = deepcopy(row)
    result = compare(row, costs, "frozen", ReadyWaitSettings(120000))
    assert result["observed_selected"] == ["long-a", "long-b"]
    assert result["alternative_selected"] == ["short"]
    assert result["workflow_pairs_inverted_by_observed_aging"] == 2
    assert result["workflow_pairs_corrected"] == 2
    assert all(pair["short_selected_after"] for pair in result["exposed_competitive_pairs"])
    assert row == before


def test_counterfactual_respects_the_same_required_join():
    row, costs = recorded_aging(required=("long-a",))
    result = compare(row, costs, "frozen", ReadyWaitSettings(120000))
    assert result["alternative_selected"] == ["long-a", "long-b"]
    assert not result["exposed_competitive_pairs"]


@pytest.mark.parametrize("deadline", (1, 2, 3, 4, 5))
def test_counterfactual_retains_the_actual_deadline_fallback(deadline):
    row, costs = recorded_aging(deadline=deadline)
    result = compare(row, costs, "frozen", ReadyWaitSettings(120000))
    if row["decision"]["search_complete"]:
        assert result["alternative_selected"] == ["short"]
    else:
        assert result["alternative_selected"] == ["long-a", "long-b"]


def test_a_prediction_that_cannot_reconstruct_the_observed_aging_is_rejected():
    row, costs = recorded_aging()
    row["predictions"]["short"]["effective_weight"] = .8
    with pytest.raises(ValueError, match="does not match"):
        compare(row, costs, "frozen", ReadyWaitSettings(120000))


def test_unknown_prediction_does_not_become_a_fictitious_short_workflow():
    row, costs = recorded_aging()
    row["predictions"]["short"] = None
    with pytest.raises(ValueError, match="supported work prediction"):
        compare(row, costs, "frozen", ReadyWaitSettings(120000))
