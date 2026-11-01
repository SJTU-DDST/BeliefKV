from copy import deepcopy

import pytest

from scripts.audit_control_oracle_service_order import inspect
from tests.test_control_oracle_opportunities import control
from tests.test_workflow_size_selection_audit import key


def evidence(*, capacity=2000, protected=False):
    choice = control(capacity=capacity, protected=protected)
    if capacity == 2000:
        choice.update(selected_tokens=1100, selected_in_priority_order=["small", "short"])
        choice["candidates"][1]["active_after_pressure"] = True
    owners = choice["selected_in_priority_order"]
    members = [
        {
            "invocation_id": owner, "key": key(owner),
            "active_before": False, "pending_before": True, "acting_before": False,
            "marked_before": False, "application_ready": True,
            "active_after_pressure": owner in owners, "marked_after_pressure": False,
            "complete_budget_tokens": 400 if owner == "small" else 700,
        }
        for owner in ("small", "short")
    ]
    grant = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 10.,
        "resume_capacity_tokens": capacity, "programs": members,
        "resumed_program_ids": owners, "physical_capacity_credit": False,
    }
    next_control = deepcopy(grant)
    next_control.update(monotonic_s=12., resumed_program_ids=[])
    for member in next_control["programs"]:
        member["active_before"] = member["active_after_pressure"]
    batches = []
    for owner, when in (("small", 10010.), ("short", 10100.)):
        member = key(owner)
        member["workflow_id"] = member.pop("root_workflow_id")
        member.update(token_delta=10, output_token_delta=1, phase="decode")
        batches.append({
            "event": "gpu_service_sample", "launch_monotonic_ms": when - 1,
            "service_start_monotonic_ms": when, "complete_monotonic_ms": when + 5,
            "monotonic_clock_domain": "same-host", "request_samples": [member],
        })
    return [choice, grant, next_control], batches


def test_same_set_reordering_can_have_a_later_shortest_workflow_service():
    report = inspect(*evidence())
    assert report["counts"]["cross_workflow_same_set_selected_order_changes"] == 1
    assert report["counts"]["shortest_workflow_served_after_another_workflow"] == 1
    assert report["observed_workflow_first_service_spread_ms"]["p50"] == 90.
    assert report["observed_shortest_workflow_delay_after_earliest_ms"]["p50"] == 90.
    assert report["scope"]["observed_service_spread_is_avoidable_JCT_or_oracle_gain"] is False


def test_service_already_follows_short_work_preference():
    records, batches = evidence()
    batches[0].update(
        launch_monotonic_ms=10199., service_start_monotonic_ms=10200.,
        complete_monotonic_ms=10205.,
    )
    report = inspect(records, batches)
    assert report["counts"]["shortest_workflow_already_in_first_service_wave"] == 1
    assert report["observed_shortest_workflow_delay_after_earliest_ms"]["p50"] == 0.


def test_members_in_one_native_service_wave_do_not_create_a_delay():
    records, batches = evidence()
    batches[1].update(
        launch_monotonic_ms=10009., service_start_monotonic_ms=10010.,
        complete_monotonic_ms=10015.,
    )
    report = inspect(records, batches)
    assert report["counts"]["all_workflows_same_first_service_time"] == 1
    assert report["observed_workflow_first_service_spread_ms"]["max"] == 0.


@pytest.mark.parametrize("change", ("attempt", "session", "out_of_window", "pregrant_launch", "zero_work"))
def test_nonmatching_or_unbounded_service_stays_unknown(change):
    records, batches = evidence()
    if change == "attempt":
        batches[1]["request_samples"][0]["attempt_id"] += 1
    elif change == "session":
        batches[1]["request_samples"][0]["session_generation"] += 1
    elif change == "out_of_window":
        batches[1].update(
            launch_monotonic_ms=12001., service_start_monotonic_ms=12002.,
            complete_monotonic_ms=12005.,
        )
    elif change == "pregrant_launch":
        batches[1]["launch_monotonic_ms"] = 9999.
    else:
        batches[1]["request_samples"][0].update(token_delta=0, output_token_delta=0)
    report = inspect(records, batches)
    assert report["counts"]["cross_workflow_order_changes_with_incomplete_bounded_service"] == 1
    assert report["observed_workflow_first_service_spread_ms"]["count"] == 0
    assert report["decisions"][0]["shortest_workflow_in_first_service_wave"] is None


def test_set_changes_are_not_reported_as_same_set_order_opportunities():
    report = inspect(*evidence(capacity=700))
    assert report["decisions"][0]["selected_set_changed"]
    assert report["counts"]["cross_workflow_same_set_selected_order_changes"] == 0
    assert report["observed_workflow_first_service_spread_ms"]["count"] == 0


def test_protected_join_is_not_compared_against_short_work():
    report = inspect(*evidence(protected=True))
    assert report["counts"]["cross_workflow_same_set_selected_order_changes"] == 0
    assert report["observed_workflow_first_service_spread_ms"]["count"] == 0


def test_duplicate_selection_boundary_is_rejected():
    records, batches = evidence()
    records.append(deepcopy(records[0]))
    with pytest.raises(ValueError, match="duplicate matched-control"):
        inspect(records, batches)
