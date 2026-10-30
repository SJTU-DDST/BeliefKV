from copy import deepcopy

import pytest

from experiments.reports.workflow_resume_oracle_20261029.analyze import select


def choice():
    return {
        "event": "workflow_size_resume_selection", "monotonic_s": 1.,
        "capacity_tokens": 700, "selected_tokens": 700,
        "ordered": [
            ["a", 0, 0, 600, 1, None], ["b", 0, 1, 400, 2, None],
        ],
        "selected_in_priority_order": ["a"],
        "candidates": [
            {"key": {"invocation_id": owner, "root_workflow_id": owner}}
            for owner in ("a", "b")
        ],
    }


def test_oracle_uses_whole_workflow_remaining_and_respects_complete_budget():
    row = choice()
    remaining = {"a": 100., "b": 10.}
    assert select(row, remaining, (20., 80.), continuous=False) == ["b"]
    assert select(row, remaining, (20., 80.), continuous=True) == ["b"]


def test_oracle_continuous_can_disambiguate_one_true_class():
    row = choice()
    remaining = {"a": 15., "b": 16.}
    assert select(row, remaining, (20., 80.), continuous=False) == ["b"]
    assert select(row, remaining, (20., 80.), continuous=True) == ["a"]


def test_oracle_keeps_original_categories_and_skips_oversized_candidates():
    row = choice()
    row["ordered"][1][1] = 1
    assert select(row, {"a": 100., "b": 1.}, (20., 80.), continuous=True) == ["a"]
    row = choice()
    row["capacity_tokens"] = 500
    row["selected_tokens"] = 500
    row["selected_in_priority_order"] = ["b"]
    assert select(row, {"a": 1., "b": 100.}, (20., 80.), continuous=True) == ["b"]


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -1., True))
def test_oracle_refuses_invalid_future_work_labels(value):
    with pytest.raises(ValueError):
        select(choice(), {"a": value, "b": 10.}, (20., 80.), continuous=True)


def test_oracle_refuses_a_changed_recorded_policy_or_duplicate_identity():
    row = choice()
    row["selected_in_priority_order"] = ["b"]
    with pytest.raises(ValueError):
        select(row, {"a": 100., "b": 10.}, (20., 80.), continuous=True)
    row = choice()
    row["candidates"].append(deepcopy(row["candidates"][0]))
    with pytest.raises(ValueError):
        select(row, {"a": 100., "b": 10.}, (20., 80.), continuous=True)
