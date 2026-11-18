from dataclasses import asdict, replace

from scripts.audit_r5_completion_path import inspect, totals
from tests.test_workflow_shortest_frontier import fixture


def row(candidates, resources, selected, required=()):
    return {
        "monotonic_s": 100., "source": "periodic_decay_resume",
        "candidates": [asdict(value) for value in candidates],
        "resources": asdict(resources),
        "decision": {
            "selected": selected, "reference": selected, "required_reference_members": required,
            "reason": "shortest_work_reference",
        },
    }


def mappings(rows, actual):
    return {
        "current_weight": {value.workflow: value.weight for value in rows},
        "point_model_work": actual,
        "reforecast_point_model_work": actual,
        "true_model_path": actual,
        "true_model_tool_path": actual,
        "predicted_model_tool_path": actual,
    }


def test_audit_counts_feasible_misranking_without_mistaking_all_fit_for_competition():
    rows, resources, costs = fixture()
    old = [replace(value, weight=.1 if value.identity == "short" else .9) for value in rows]
    truth = {value.workflow: value.weight for value in rows}
    result = inspect(row(old, resources, ("long-a", "long-b")), costs, mappings(old, truth))
    assert result["capacity_competition"]
    assert len(result["feasible_true_path_priority_inversions"]) == 2
    assert result["choices"]["true_model_tool_path"]["selected"] == ["short"]
    counts = totals([result], {rows[0].workflow})["counts"]
    assert counts["measured_in_capacity_competition"] == 1
    assert counts["feasible_true_path_priority_inversion_boundaries"] == 1
    all_fit = inspect(
        row(rows, replace(resources, logical_tokens=500), ("short", "long-a", "long-b")),
        costs, mappings(rows, truth),
    )
    assert all_fit["all_fit"] and not all_fit["capacity_competition"]
    assert not all_fit["feasible_true_path_priority_inversions"]


def test_audit_separates_capacity_refusal_and_required_join_from_misranking():
    rows, resources, costs = fixture()
    truth = {value.workflow: value.weight for value in rows}
    required = inspect(
        row(rows, resources, ("long-a", "long-b"), required=("long-a",)),
        costs, mappings(rows, truth),
    )
    assert not required["feasible_true_path_priority_inversions"]
    too_large = [replace(rows[0], logical_tokens=1000), *rows[1:]]
    result = inspect(
        row(too_large, resources, ("long-a", "long-b")), costs, mappings(too_large, truth),
    )
    assert not result["feasible_true_path_priority_inversions"]
    assert rows[0].workflow not in result["feasible_workflows"]
    unknown = inspect(row(rows[:1], resources, ("missing",)), costs, mappings(rows[:1], truth))
    assert not unknown["metadata_complete"]
