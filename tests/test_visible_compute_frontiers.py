from copy import deepcopy

import pytest

from scripts.audit_visible_compute_frontiers import project_case, visible_cases
from tests.test_compute_frontier_projection import model
from tests.test_resident_decode_prefill_frontiers import member


def fixture():
    before = {
        "phase": "decode", "sample_id": "d0", "complete_monotonic_ms": 100.,
        "service_elapsed_ms": 2., "request_samples": [member()],
    }
    victim = member(request_id="prefill", workflow_id="victim", phase="prefill")
    prefills = [{
        "phase": "prefill", "sample_id": f"p{index}", "launch_monotonic_ms": 99. + index * 10.,
        "service_start_monotonic_ms": 101. + index * 10.,
        "complete_monotonic_ms": 111. + index * 10., "request_samples": [victim],
    } for index in range(2)]
    after = {
        "phase": "decode", "sample_id": "d1", "request_samples": [
            member(output_tokens_before=10), {**victim, "phase": "decode"},
        ],
    }
    requests = {
        "request": {
            "output_tokens": 11, "native_complete_monotonic_ms": 125.,
            "native_receipt": {"output_token_ids": [0] * 9 + [2, 3]},
        },
        "prefill": {"native_complete_monotonic_ms": 140.},
    }
    return before, prefills, after, requests


def cases(before, prefills, after, requests, *, closed=("workflow", "victim")):
    return visible_cases(
        (before, *prefills, after), requests, set(closed), {"close": 2, "eos": 3}, {}, {},
    )


def test_visible_signal_cannot_replace_a_prefill_that_was_already_submitted():
    before, prefills, after, requests = fixture()
    rows, counts = cases(before, prefills, after, requests)
    assert counts["bursts_with_already_submitted_prefill_prefix"] == 1
    assert rows[0]["sample_id"] == "p1"
    assert rows[0]["begin_ms"] == 111.
    assert rows[0]["whole_previous_batch_worker_cost_ms"] == 2.


def test_no_action_when_every_prefill_is_submitted_before_the_signal_is_visible():
    before, prefills, after, requests = fixture()
    before["complete_monotonic_ms"] = 110.
    rows, counts = cases(before, prefills, after, requests)
    assert not rows
    assert counts["signal_arrived_after_all_prefills_were_submitted"] == 1


def test_future_close_token_is_not_a_visible_trigger():
    before, prefills, after, requests = fixture()
    requests["request"]["native_receipt"]["output_token_ids"] = [0] * 10 + [2]
    rows, counts = cases(before, prefills, after, requests)
    assert not rows
    assert not counts["bursts_with_visible_tool_close"]


def test_emitted_eos_does_not_trigger_another_decode():
    before, prefills, after, requests = fixture()
    requests["request"]["native_receipt"]["output_token_ids"][8] = 3
    assert not cases(before, prefills, after, requests)[0]


def test_finished_previous_batch_member_is_not_runnable():
    before, prefills, after, requests = fixture()
    requests["request"]["native_complete_monotonic_ms"] = 100.
    assert not cases(before, prefills, after, requests)[0]


def test_signal_with_more_than_one_remaining_output_is_not_filtered_by_future_length():
    before, prefills, after, requests = fixture()
    requests["request"]["output_tokens"] = 12
    requests["request"]["native_receipt"]["output_token_ids"].insert(-1, 0)
    rows, _counts = cases(before, prefills, after, requests)
    assert rows[0]["remaining_outputs_for_signal_offline"] == {"request": 2}
    assert rows[0]["immediately_completing_requests_offline"] == []
    assert rows[0]["full_state_identity_observed"] is True


@pytest.mark.parametrize("change,counter", (
    ("censored", "censored_direct_participants"),
    ("missing_receipt", "prefill_request_completion_unobserved"),
    ("missing_member", "incomplete_movement_evidence"),
    ("identity_changed", "incomplete_movement_evidence"),
    ("unknown_previous_member", "incomplete_movement_evidence"),
))
def test_unobserved_movement_and_direct_participant_costs_are_not_treated_as_free(change, counter):
    before, prefills, after, requests = fixture()
    closed = ("workflow", "victim")
    if change == "censored":
        closed = ("workflow",)
    elif change == "missing_receipt":
        requests.pop("prefill")
    elif change == "missing_member":
        after["request_samples"] = after["request_samples"][1:]
    elif change == "identity_changed":
        after["request_samples"][0]["session_generation"] += 1
    else:
        before["request_samples"].append(member(request_id="unknown"))
    rows, counts = cases(before, prefills, after, requests, closed=closed)
    assert not rows
    assert counts[counter] == 1


def test_missing_extended_identity_remains_unknown_instead_of_borrowing_the_receipt():
    before, prefills, after, requests = fixture()
    before["request_samples"][0].pop("session_id")
    rows, _counts = cases(before, prefills, after, requests)
    assert rows[0]["full_state_identity_observed"] is False


def projection_case():
    return {
        "begin_ms": 1., "whole_previous_batch_worker_cost_ms": .5,
        "immediately_completing_requests_offline": ["decode"],
        "victim_requests": ["prefill"], "unused_batch_member_count": 5,
    }


def test_one_step_pays_the_whole_batch_and_delays_prefill_without_fractional_member_discount():
    case = projection_case()
    result = project_case(
        case, model(sibling_return=7.),
        {"decode": {"native_complete_monotonic_ms": 4.}}, {"a", "b"},
    )["cost_sensitivity"]["1.0"]
    assert result["whole_batch_decode_worker_cost_ms"] == .5
    assert result["affected_workflow_JCT_change_s"] == {"a": -.001, "b": .0005}


def test_signal_false_positive_gets_no_completion_credit_but_still_pays_cost():
    case = deepcopy(projection_case())
    case["immediately_completing_requests_offline"] = []
    result = project_case(case, model(), {}, {"a", "b"})["cost_sensitivity"]["1.0"]
    assert result["all_completed_workflow_sum_JCT_reduction_s"] == -.0005
    assert result["measured_workflow_mean_JCT_reduction_s"] == -.00025


def test_decode_finishing_after_observed_completion_is_not_credited_as_an_advance():
    case = projection_case()
    case["begin_ms"] = 4.
    result = project_case(
        case, model(), {"decode": {"native_complete_monotonic_ms": 4.}}, {"a", "b"},
    )
    assert not any(value["applicable"] for value in result["cost_sensitivity"].values())
