import pytest

from scripts.audit_workflow_competition import inspect_competition
from scripts.audit_workflow_size_selection import inspect_choice
from tests.test_workflow_resume_oracle import choice


def population():
    return [
        {"workflow_id": name, "project": name, "eligible": True, "size_tier": tier}
        for name, tier in (("a", "short"), ("b", "long"))
    ]


def competition():
    row = choice()
    row["capacity_tokens"] = 700
    row["ordered"] = [["a", 0, 0, 400, 1, None], ["b", 0, 2, 400, 2, None]]
    row["selected_tokens"] = 500
    row["selected_in_priority_order"] = ["a"]
    row["logical_ready_paused_before_resume"] = {"a": True, "b": True}
    for item in row["candidates"]:
        item["active_after_pressure"] = item["key"]["invocation_id"] == "a"
    return row


def test_counts_only_observed_ready_short_long_competition_in_same_category():
    row = competition()
    result = inspect_competition([row], population())
    assert result["counts"]["controls_with_ready_short_long_capacity_competition"] == 1
    assert result["episodes"][0]["aggregate_tokens"] == 1000
    assert result["episodes"][0]["selected_after_pressure"] == ["a"]
    assert result["readiness_coverage_complete"]
    row["logical_ready_paused_before_resume"]["b"] = False
    assert inspect_competition([row], population())["counts"]["controls_with_ready_short_long_capacity_competition"] == 0


def test_missing_readiness_is_reported_as_missing_not_assumed_ready():
    row = competition()
    del row["logical_ready_paused_before_resume"]
    result = inspect_competition([row], population())
    assert not result["readiness_coverage_complete"]
    assert result["counts"]["controls_without_readiness_snapshot"] == 1
    row["logical_ready_paused_before_resume"] = {"a": True}
    with pytest.raises(ValueError):
        inspect_competition([row], population())


def test_continuous_oracle_rank_requires_its_explicit_kind_and_replays_same_choice():
    row = competition()
    row["ordered"][0][2] = .9
    row["ordered"][1][2] = 15.3
    with pytest.raises(ValueError):
        inspect_choice(row)
    row["rank_kind"] = "oracle_remaining_model_tool_critical_path_seconds"
    assert not inspect_choice(row)["selected_set_changed"]
    row["ordered"][1][2] = float("nan")
    with pytest.raises(ValueError):
        inspect_choice(row)


def test_different_categories_or_all_fitting_do_not_count_as_size_competition():
    row = competition()
    row["ordered"][1][1] = 1
    assert inspect_competition([row], population())["counts"]["controls_with_ready_short_long_capacity_competition"] == 0
    row = competition()
    row.update(capacity_tokens=1000, selected_tokens=1000, selected_in_priority_order=["a", "b"])
    assert inspect_competition([row], population())["counts"]["controls_with_ready_short_long_capacity_competition"] == 0
