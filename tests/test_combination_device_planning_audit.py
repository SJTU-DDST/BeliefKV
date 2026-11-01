from dataclasses import asdict, replace
import json

import pytest

from beliefkv.policy.workflow_combination import choose_combination
from scripts.audit_combination_device_planning import compare_choice, competition, summarize
from tests.test_combination_device_planning import audit_row, resource, rows
from tests.test_workflow_combination import cost


def legacy_row(*, budget=300, deadline=False):
    row = audit_row()
    resources = replace(resource(), logical_tokens=budget, device_capacity_tokens=None)
    row["resources"] = asdict(resources)
    row.pop("device_fit_scope")
    clock = iter((0, 500_000, 600_000)) if deadline else None
    row["decision"] = choose_combination(
        rows(), ("long",), resources, cost(), allow_feasible_resource_alternatives=True,
        clock=(lambda: next(clock)) if deadline else lambda: 0,
    )
    return json.loads(json.dumps(row))


def test_differential_retains_all_costs_and_exposes_planning_competition():
    report = compare_choice(legacy_row(), cost(), "planned", 1000)
    assert report["strict"]["selected"] == ("long",)
    assert report["planned"]["selected"] == ("short",)
    assert not report["strict_competition"]["any_feasible_subset"]
    assert report["planned_competition"]["cannot_cover_all_feasible_workflows"]
    assert summarize([report])["variants"]["planned"]["selected_evaluation_requires_native_capacity_wait"] == 1


def test_multiple_feasible_workflows_are_not_competition_when_they_can_all_be_covered():
    feasible = replace(resource(), logical_tokens=400)
    contest = competition(rows(), (), feasible, cost())
    assert contest["multiple_workflows_each_have_a_feasible_selection"]
    assert not contest["cannot_cover_all_feasible_workflows"]


def test_protected_workflow_does_not_create_an_extra_short_long_competitor():
    contest = competition(
        rows(), ("long",), replace(resource(), logical_tokens=400), cost(),
    )
    assert contest["feasible_unprotected_workflows"] == 1
    assert not contest["cannot_cover_all_feasible_workflows"]


def test_actual_deadline_is_preserved_and_excluded_from_complete_enumeration():
    report = compare_choice(legacy_row(deadline=True), cost(), "planned", 1000)
    assert report["kind"] == "actual_deadline"
    assert "planned" not in report


def test_inconsistent_physical_census_is_not_a_new_planning_opportunity():
    with pytest.raises(ValueError, match="capacity census"):
        compare_choice(legacy_row(), cost(), "planned", 0)


def test_differential_does_not_accept_a_changed_original_decision():
    row = legacy_row()
    row["decision"]["selected"] = ["short"]
    with pytest.raises(ValueError, match="reproduce"):
        compare_choice(row, cost(), "planned", 1000)
