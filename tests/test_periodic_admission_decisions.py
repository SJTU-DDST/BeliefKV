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


def window_evidence():
    choices, batches = records()
    final = deepcopy(choices[0])
    final["monotonic_s"] = 2.
    final["resumed_program_ids"] = []
    for member in final["programs"]:
        member["active_before"] = member["active_after_pressure"]
    choices.append(final)
    batches[0]["launch_monotonic_ms"] = 1010.
    batches[0]["request_samples"][0].update(
        token_delta=200, output_token_delta=0, phase="prefill",
    )
    return choices, batches


def test_positive_prefill_progress_requires_a_new_launch_within_the_control_window():
    report = inspect(*window_evidence())
    assert report["counts"]["ready_grants_launched_within_observation_window"] == 1
    assert report["counts"]["ready_grants_served_within_observation_window"] == 1
    assert report["counts"]["ready_grants_completed_useful_batch_within_observation_window"] == 1
    assert report["within_window_new_launch_ms"]["p50"] == 10.
    assert report["within_window_new_service_ms"]["p50"] == 15.
    assert report["within_window_new_completion_ms"]["p50"] == 100.
    row = report["ready_grant_episodes"][0]
    assert row["observation_window_end_ms"] == 2000.
    assert row["observation_window_end_reason"] == "next_control"


def test_a_pregrant_launch_with_late_service_does_not_prove_new_grant_progress():
    choices, batches = window_evidence()
    batches[0]["launch_monotonic_ms"] = 999.
    report = inspect(choices, batches)
    assert report["counts"]["ready_grants_with_following_exact_service"] == 1
    assert report["counts"]["ready_grants_with_observed_new_useful_launch"] == 0
    assert report["counts"]["ready_grants_served_within_observation_window"] == 0


def test_later_control_service_is_retained_but_not_counted_as_prompt_consumption():
    choices, batches = window_evidence()
    batches[0].update(
        launch_monotonic_ms=2010., service_start_monotonic_ms=2020.,
        complete_monotonic_ms=2100.,
    )
    report = inspect(choices, batches)
    assert report["counts"]["ready_grants_with_following_exact_service"] == 1
    assert report["counts"]["ready_grants_with_observed_new_useful_launch"] == 1
    assert report["counts"]["ready_grants_launched_within_observation_window"] == 0


def test_same_agent_free_grant_or_donor_pause_closes_the_earlier_observation_window():
    for donor in (False, True):
        choices, batches = window_evidence()
        key = deepcopy(choices[0]["programs"][0]["key"])
        member = {"key": key}
        changed = deepcopy(member)
        changed["key"].update(invocation_id="parent", request_id="parent")
        choices.insert(1, {
            "event": "agent_join_blocked_capacity_admitted" if donor else
                     "agent_dispatch_free_capacity_admitted",
            "monotonic_s": 1.005,
            "programs": [changed] if donor else [member],
            **({"donors": [member]} if donor else {}),
        })
        report = inspect(choices, batches)
        assert report["counts"]["ready_grants_with_following_exact_service"] == 1
        assert report["counts"]["ready_grants_served_within_observation_window"] == 0
        assert report["ready_grant_episodes"][0]["observation_window_end_ms"] == pytest.approx(1005.)


def test_empty_decode_tail_is_not_positive_progress_after_a_grant():
    choices, batches = window_evidence()
    batches[0]["request_samples"][0].update(phase="decode", token_delta=0)
    report = inspect(choices, batches)
    assert report["counts"]["ready_grants_with_following_exact_service"] == 1
    assert report["counts"]["target_zero_progress_service_samples"] == 1
    assert report["counts"]["ready_grants_with_observed_new_useful_launch"] == 0


def test_live_last_window_without_a_later_control_remains_unknown():
    choices, batches = window_evidence()
    choices.pop()
    report = inspect(choices, batches)
    assert report["counts"]["ready_grants_with_observed_new_useful_launch"] == 1
    assert report["counts"]["ready_grants_with_open_observation_window"] == 1
    assert report["ready_grant_episodes"][0]["new_service_before_next_control_or_intervention"] is None


def test_new_launch_and_service_clocks_are_checked_independently():
    choices, batches = window_evidence()
    batches[0].update(
        launch_monotonic_ms=1990., service_start_monotonic_ms=2010.,
        complete_monotonic_ms=2100.,
    )
    report = inspect(choices, batches)
    assert report["counts"]["ready_grants_launched_within_observation_window"] == 1
    assert report["counts"]["ready_grants_served_within_observation_window"] == 0
    batches[0]["launch_monotonic_ms"] = 2020.
    with pytest.raises(ValueError, match="launch"):
        inspect(choices, batches)


def test_worker_start_does_not_substitute_for_useful_batch_completion():
    choices, batches = window_evidence()
    batches[0]["complete_monotonic_ms"] = 2010.
    report = inspect(choices, batches)
    assert report["counts"]["ready_grants_served_within_observation_window"] == 1
    assert report["counts"]["ready_grants_completed_useful_batch_within_observation_window"] == 0
    assert not report["ready_grant_episodes"][0]["new_completion_before_next_control_or_intervention"]
