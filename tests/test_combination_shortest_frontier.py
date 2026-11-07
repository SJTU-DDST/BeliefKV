from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from beliefkv.policy.workflow_combination import choose_combination
from scripts.audit_combination_shortest_frontier import greedy_frontier, inspect, totals
from tests.test_combination_device_planning import audit_row, resource, rows
from tests.test_combination_full_batch_costs import costs


def fixture():
    row = audit_row()
    candidates = [replace(value, weight=.9 if value.identity == "long" else .3) for value in rows()]
    profile = replace(costs(), selection_objective="benefit")
    row["candidates"] = [asdict(value) for value in candidates]
    row["resources"] = asdict(resource())
    row["source"] = "periodic_decay_resume"
    row["decision"] = choose_combination(
        candidates, ("long",), resource(), profile, clock=lambda: 0,
        allow_feasible_resource_alternatives=True,
    )
    evidence = {
        "monotonic_s": row["monotonic_s"], "source": row["source"],
        "planning_capacity_competition": True,
        "ordinary_workflows": [{
            "workflow": value.workflow, "remaining_model_work": 500. if value.identity == "long" else 10.,
            "weights": {"actual_cached": value.weight},
        } for value in candidates],
        "selections": {
            "actual_cached": {"selected": list(row["decision"]["selected"])},
            "oracle_continuous": {"selected": ["short"]},
        },
    }
    return json.loads(json.dumps(row)), evidence, profile


def test_predicted_and_true_greedy_are_compared_on_the_same_resources_without_mutation():
    row, evidence, profile = fixture()
    original = deepcopy((row, evidence))
    result = inspect(row, evidence, profile, {"identity": row["cost_identity"]}, {"short"})
    assert (row, evidence) == original
    assert result["choices"]["predicted_greedy"]["selected"] == ["long"]
    assert result["choices"]["truth_greedy"]["selected"] == ["short"]
    assert result["choices"]["truth_greedy"]["includes_true_shortest_ordinary_workflow"]
    assert totals([result])["ordinary_capacity_competitions"]["truth_greedy_set_changes"] == 1


def test_required_join_survives_even_when_an_ordinary_workflow_has_a_lower_rank():
    candidates = rows()
    chosen = greedy_frontier(
        candidates, ("long",), resource(), costs(), {"long": 100., "short": 1.},
    )
    assert chosen == ["long"]


def test_unsupported_required_join_set_remains_unknown():
    assert greedy_frontier(
        rows(), ("long",), replace(resource(), logical_tokens=1), costs(),
        {"long": 100., "short": 1.},
    ) is None


def test_ready_siblings_remain_adjacent_without_waiting_for_unobserved_members():
    base = replace(rows()[0], missing_extents=(), input_tokens=0, weight=.5)
    candidates = [
        replace(
            base, identity="a1", workflow="a", group="join-a", logical_tokens=40,
            context_tokens=40, unfinished_members=2,
        ),
        replace(
            base, identity="b", workflow="b", group="b", logical_tokens=60,
            context_tokens=60, unfinished_members=1,
        ),
        replace(
            base, identity="a2", workflow="a", group="join-a", logical_tokens=100,
            context_tokens=100, unfinished_members=2,
        ),
    ]
    assert greedy_frontier(
        candidates, (), replace(resource(), logical_tokens=140, slots=3), costs(),
        {"a1": 1., "a2": 1., "b": 1.},
    ) == ["a1", "a2"]
    assert greedy_frontier(
        candidates[:2], (), replace(resource(), logical_tokens=140, slots=3), costs(),
        {"a1": 1., "b": 1.},
    ) == ["a1", "b"]


@pytest.mark.parametrize("ranks", ({"short": 1.}, {"short": 1., "long": float("nan")}))
def test_incomplete_or_invalid_ranks_are_rejected(ranks):
    with pytest.raises(ValueError):
        greedy_frontier(rows(), (), resource(), costs(), ranks)


def test_changed_boundary_or_prediction_cannot_be_relabelled_as_an_algorithm_gap():
    row, evidence, profile = fixture()
    evidence["source"] = "another-source"
    with pytest.raises(ValueError, match="different actual boundary"):
        inspect(row, evidence, profile, {"identity": row["cost_identity"]}, set())
    row, evidence, profile = fixture()
    evidence["ordinary_workflows"][0]["weights"]["actual_cached"] = .1
    with pytest.raises(ValueError, match="cached prediction"):
        inspect(row, evidence, profile, {"identity": row["cost_identity"]}, set())
