from copy import deepcopy

import pytest

from experiments.reports.workflow_resume_oracle_20261029.review_choices import RANK, inspect
from tests.test_workflow_resume_oracle import choice


def oracle_choice():
    row = choice()
    row["rank_kind"] = RANK
    row["physical_capacity_credit"] = False
    row["ordered"][0][2] = 10.
    row["ordered"][1][2] = 100.
    for item, score, model in zip(row["candidates"], (10., 100.), (10., 1.)):
        item["prediction"] = {
            "evidence": "oracle", "remaining_path_s": score, "model_only_path_s": model,
            "tool_and_fixed_delay_path_s": score - model,
            "remaining_work": model * 10, "remaining_work_units": int(model * 1000),
        }
    return row


def test_bound_oracle_decision_replays_reference_and_external_delay_counterfactual():
    result = inspect(oracle_choice())
    assert result == {"actual": ["a"], "reference": ["b"], "without_external_delays": ["b"]}


@pytest.mark.parametrize("capacity", (-100, 0, 100))
def test_empty_budget_preserves_the_original_fast_path(capacity):
    row = oracle_choice()
    row.update(
        capacity_tokens=capacity, selected_tokens=0, selected_in_priority_order=[],
        ordered=[], candidates=[],
    )
    assert inspect(row) == {"actual": [], "reference": [], "without_external_delays": []}


@pytest.mark.parametrize("mutation", (
    lambda row: row.update(rank_kind="learned_work_class"),
    lambda row: row.update(physical_capacity_credit=True),
    lambda row: row["ordered"][0].__setitem__(2, float("nan")),
    lambda row: row["candidates"][0]["prediction"].update(remaining_path_s=11.),
    lambda row: row["candidates"][0]["prediction"].update(evidence="model"),
    lambda row: row["candidates"].append(deepcopy(row["candidates"][0])),
    lambda row: row.update(selected_in_priority_order=["b"]),
))
def test_unbound_or_non_oracle_recorded_decision_is_rejected(mutation):
    row = oracle_choice()
    mutation(row)
    with pytest.raises(ValueError):
        inspect(row)
