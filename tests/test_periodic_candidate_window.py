from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from beliefkv.policy.workflow_combination import choose_combination
from scripts.audit_periodic_candidate_window import inspect, pair_periodic
from tests.test_combination_device_planning import audit_row, resource, rows
from tests.test_combination_full_batch_costs import costs
from tests.test_workflow_size_selection_audit import key


def history(*, required=(), outside_workflow="outside", capacity=300):
    combination = audit_row()
    candidates = [replace(item, context_tokens=100) for item in rows()]
    resources = replace(resource(), logical_tokens=capacity)
    combination.update(
        candidates=[asdict(item) for item in candidates],
        resources=asdict(resources), monotonic_s=10.001,
        decision=choose_combination(
            candidates, ("long",), resources, costs(), required=required,
            clock=lambda: 0, allow_feasible_resource_alternatives=True,
        ),
        logical_reference_capacity_tokens=capacity, unavailable_summaries=[],
    )
    keys = {
        **combination["keys"],
        "outside": {**key("outside"), "root_workflow_id": outside_workflow},
    }
    selected = list(combination["decision"]["selected"])
    control = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 10.,
        "resume_capacity_tokens": capacity, "resumed_program_ids": selected,
        "programs": [{
            "key": value, "invocation_id": owner, "complete_budget_tokens": 200,
            "active_before": False, "marked_before": False, "pending_before": True,
            "application_ready": True,
        } for owner, value in keys.items()],
    }
    resume = {
        "event": "workflow_size_resume_selection", "monotonic_s": 10.,
        "capacity_tokens": capacity, "selected_in_priority_order": selected,
        "ordered": [[owner, 0, 1, 100, index, None] for index, owner in enumerate(keys)],
        "logical_ready_paused_before_resume": {owner: True for owner in keys},
        "candidates": [{"key": value} for value in keys.values()],
    }
    return json.loads(json.dumps([combination, control, resume]))


def analyse(records):
    pairs, counts = pair_periodic(records)
    observations = [{
        "monotonic_s": pair["monotonic_s"],
        "candidates": [{
            "key": item["key"],
            "observed_remaining_model_work": {
                "long": 400., "short": 200., "outside": 50.,
            }[item["key"]["invocation_id"]],
        } for item in pair["ready"]],
    } for pair in pairs]
    return inspect(pairs, observations, costs(), "planned", measured={"outside"}), counts


def test_pair_uses_journal_boundaries_even_when_control_timestamp_precedes_search_end():
    records = history()
    pairs, counts = pair_periodic(records)
    assert counts["exact_periodic_windows_paired"] == 1
    assert pairs[0]["control_sequence"] == 1
    assert pairs[0]["monotonic_s"] == 10.001
    result, _ = analyse(records)
    assert result["counts"]["outside_other_workflow_shorter_than_every_fitting_window_workflow"] == 1
    assert result["counts"]["outside_shorter_by_at_least_two"] == 1
    assert result["counts"]["outside_shorter_workflow_is_measured"] == 1
    assert result["outside_shorter_cases"][0]["outside_minimum"]["remaining_model_work"] == 50.


@pytest.mark.parametrize("change", (
    "missing_control", "missing_resume", "new_periodic", "wrong_clock", "wrong_capacity",
    "wrong_selection", "changed_attempt", "changed_budget", "duplicate_program", "unknown_ready",
    "duplicate_selected", "window_budget_changed",
))
def test_changed_or_incomplete_periodic_association_is_rejected(change):
    records = history()
    combination, control, resume = records
    if change == "missing_control":
        records.pop(1)
    elif change == "missing_resume":
        records.pop()
    elif change == "new_periodic":
        records.insert(1, deepcopy(combination))
    elif change == "wrong_clock":
        resume["monotonic_s"] += .001
    elif change == "wrong_capacity":
        control["resume_capacity_tokens"] += 1
    elif change == "wrong_selection":
        resume["selected_in_priority_order"] = ["outside"]
    elif change == "changed_attempt":
        resume["candidates"][-1]["key"]["attempt_id"] += 1
    elif change == "changed_budget":
        control["programs"][-1]["complete_budget_tokens"] += 1
    elif change == "duplicate_program":
        control["programs"].append(deepcopy(control["programs"][-1]))
    elif change == "duplicate_selected":
        control["resumed_program_ids"].append(control["resumed_program_ids"][0])
    elif change == "window_budget_changed":
        combination["candidates"][0]["logical_tokens"] += 1
    else:
        resume["logical_ready_paused_before_resume"]["outside"] = None
    with pytest.raises(ValueError):
        pair_periodic(records)


def test_unknown_ready_map_cannot_supply_outside_candidates():
    records = history()
    records[-1].pop("logical_ready_paused_before_resume")
    pairs, counts = pair_periodic(records)
    assert not pairs
    assert counts["missing_exact_ready_map"] == 1


@pytest.mark.parametrize("change", ("active", "blocked", "not_pending", "marked"))
def test_outside_candidate_must_be_actual_ready_paused_before_control(change):
    records = history()
    outsider = records[1]["programs"][-1]
    field, value = {
        "active": ("active_before", True), "blocked": ("application_ready", False),
        "not_pending": ("pending_before", False), "marked": ("marked_before", True),
    }[change]
    outsider[field] = value
    result, counts = analyse(records)
    assert counts["ready_map_members_without_ready_paused_before_state"] == 1
    assert result["counts"]["searches_with_individually_logical_fitting_outside_candidates"] == 0


def test_required_JOIN_budget_and_workflow_are_not_ordinary_replacement_candidates():
    result, _ = analyse(history(required=("long",)))
    assert result["counts"]["searches_with_individually_logical_fitting_outside_candidates"] == 0
    assert not result["outside_shorter_cases"]


def test_already_covered_workflow_is_not_a_missing_short_workflow():
    result, _ = analyse(history(outside_workflow="short"))
    assert result["counts"]["searches_with_individually_logical_fitting_outside_candidates"] == 1
    assert not result["outside_shorter_cases"]


def test_protected_JOIN_keeps_its_budget_when_an_ordinary_outsider_can_also_fit():
    result, _ = analyse(history(required=("long",), capacity=600))
    case = result["outside_shorter_cases"][0]
    assert case["required_JOIN_members"] == ["long"]
    assert case["ordinary_capacity_after_protected_JOIN_tokens"] == 400
    assert case["inside_minimum"]["key"]["invocation_id"] == "short"


def test_unknown_or_nonfinite_progress_cannot_be_used_for_window_claims():
    pairs, _ = pair_periodic(history())
    observations = [{
        "monotonic_s": pairs[0]["monotonic_s"],
        "candidates": [{"key": item["key"]} for item in pairs[0]["ready"]],
    }]
    result = inspect(pairs, observations, costs(), "planned")
    assert result["counts"]["missing_observed_progress"] == 1
    observations[0]["candidates"][0]["observed_remaining_model_work"] = float("nan")
    with pytest.raises(ValueError, match="nonnegative"):
        inspect(pairs, observations, costs(), "planned")
