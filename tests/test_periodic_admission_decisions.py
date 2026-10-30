from copy import deepcopy

import pytest

from scripts.audit_periodic_admission_decisions import inspect
from tests.test_free_dispatch_service_audit import evidence


def records():
    grant, _submits, batches, _receipts = evidence()
    key = grant[0]["programs"][0]["key"]
    members = []
    for index in range(3):
        owner = str(index)
        members.append({
            "invocation_id": owner,
            "key": {**key, "invocation_id": owner, "root_workflow_id": owner, "request_id": owner},
            "active_before": False, "pending_before": True, "acting_before": False,
            "marked_before": False, "application_ready": True,
            "active_after_pressure": index == 0, "marked_after_pressure": False,
            "complete_budget_tokens": 300,
        })
    batch = batches[0]
    batch["request_samples"][0].update(workflow_id="0", invocation_id="0", request_id="0")
    return [{
        "event": "agent_periodic_admission_decision", "monotonic_s": 1.,
        "resume_capacity_tokens": 800, "programs": members,
        "resumed_program_ids": ["0"], "physical_capacity_credit": False,
    }], [batch]


def test_three_way_competition_and_following_exact_service():
    report = inspect(*records())
    assert report["counts"]["cross_workflow_logical_set_competition_controls"] == 1
    assert report["counts"]["ready_grants_with_following_exact_service"] == 1
    assert report["following_service_ms"]["p50"] == 15.
    assert len(report["competition_decisions"][0]["fitting_candidates"]) == 3


@pytest.mark.parametrize("field", ("application_ready", "pending_before"))
def test_nonready_members_do_not_create_a_choice(field):
    choices, batches = records()
    choices[0]["programs"][2][field] = False
    assert inspect(choices, batches)["counts"]["cross_workflow_logical_set_competition_controls"] == 0


@pytest.mark.parametrize("change", ("attempt", "earlier", "revoked"))
def test_foreign_earlier_or_revoked_service_is_not_consumption(change):
    choices, batches = records()
    if change == "attempt":
        batches[0]["request_samples"][0]["attempt_id"] += 1
    elif change == "earlier":
        batches[0]["service_start_monotonic_ms"] = 999.
    else:
        choices[0]["programs"][0]["active_after_pressure"] = False
    report = inspect(choices, batches)
    assert report["following_service_ms"]["count"] == 0


def test_full_width_is_not_mistaken_for_a_four_member_free_dispatch():
    choices, batches = records()
    template = choices[0]["programs"][0]
    for number in range(3, 8):
        member = deepcopy(template)
        member["invocation_id"] = str(number)
        member["key"].update(invocation_id=str(number), request_id=str(number), root_workflow_id=str(number))
        choices[0]["programs"].append(member)
    choices[0]["resume_capacity_tokens"] = 2400
    choices[0]["resumed_program_ids"] = [str(number) for number in range(8)]
    assert inspect(choices, batches)["counts"]["resumed_program_episodes"] == 8


def test_overbudget_or_duplicate_grants_are_rejected():
    choices, batches = records()
    choices[0]["resumed_program_ids"] = ["0", "1", "2"]
    with pytest.raises(ValueError, match="resume budget"):
        inspect(choices, batches)
    choices[0]["resumed_program_ids"] = ["0", "0"]
    with pytest.raises(ValueError, match="boundary"):
        inspect(choices, batches)
