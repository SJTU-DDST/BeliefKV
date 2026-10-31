from copy import deepcopy

import pytest

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan
from beliefkv.experiments.workflow_cohort import declare_cohort
from scripts.audit_periodic_admission_decisions import inspect, observation_phases
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


def phase_evidence():
    arrivals = poisson_arrival_plan(
        ["w0", "w1", "w2", "w3"], rate_per_second=.1, seed=31, initial_workflows=1,
    )
    start, end = (row["offset_seconds"] for row in arrivals["arrival_schedule"][1:3])
    cohort = declare_cohort(
        arrivals, warmup=1, measured=2, throughput_start_s=start,
        throughput_end_s=end, stop_after_measured=True,
    )
    origin = 100.
    events = [{
        "kind": "workflow_start", "workflow_id": row["workflow_id"],
        "ts_ms": (origin + row["offset_seconds"]) * 1000.,
    } for row in arrivals["arrival_schedule"]]
    events += [
        {"kind": "workflow_end", "workflow_id": "w1", "ts_ms": (origin + end + 2.) * 1000.},
        {"kind": "workflow_end", "workflow_id": "w2", "ts_ms": (origin + end + 4.) * 1000.},
        {"kind": "llm_result", "workflow_id": "w1", "ts_ms": (origin + start) * 1000.,
         "attributes": {"request_id": "left"}},
        {"kind": "llm_result", "workflow_id": "w2", "ts_ms": (origin + end) * 1000.,
         "attributes": {"request_id": "right"}},
    ]
    choices, batches = records()
    controls = []
    for offset in (start, end, end + 1., end + 5.):
        row = deepcopy(choices[0])
        row["monotonic_s"] = origin + offset
        controls.append(row)
    return inspect(controls, batches), arrivals, cohort, events


def test_phase_audit_preserves_fixed_half_open_window_and_complete_measured_span():
    report = observation_phases(*phase_evidence())
    fixed = report["phases"]["fixed_common_throughput_window"]
    tail = report["phases"]["measured_execution_after_common_window"]
    measured = report["phases"]["measured_execution_observation_span"]
    assert fixed["actual_periodic_controls"] == fixed["completed_measured_model_requests"] == 1
    assert tail["actual_periodic_controls"] == 2
    assert tail["completed_measured_model_requests"] == 1
    assert measured["actual_periodic_controls"] == 3
    assert measured["actual_cross_workflow_logical_set_competitions"] == 3
    assert report["all_measured_completed"]
    assert not report["scope"]["workload_acceptance_gate_changed"]
    assert report["scope"]["execution_span_depends_on_policy_completion_diagnostic_only"]


def test_phase_audit_never_treats_partial_measured_completions_as_a_complete_span():
    report, arrivals, cohort, events = phase_evidence()
    events = [row for row in events if not (
        row["kind"] == "workflow_end" and row["workflow_id"] == "w2"
    )]
    result = observation_phases(report, arrivals, cohort, events)
    assert not result["all_measured_completed"]
    assert result["observed_measured_completions"] == 1
    assert result["last_measured_completion_offset_s"] is None
    assert result["phases"]["measured_execution_observation_span"]["end_s"] is None
    assert not result["phases"]["measured_execution_observation_span"]["complete_phase_observed"]


def test_phase_audit_keeps_an_unstarted_window_unknown():
    report, arrivals, cohort, events = phase_evidence()
    report["control_observations"] = [
        {**report["control_observations"][0], "monotonic_s": 100.}
    ]
    phases = observation_phases(report, arrivals, cohort, events)["phases"]
    assert not phases["fixed_common_throughput_window"]["phase_started"]
    assert phases["fixed_common_throughput_window"]["predecision_ready_fraction"] is None
    assert phases["fixed_common_throughput_window"]["actual_periodic_controls"] == 0


@pytest.mark.parametrize("change", ("cohort", "arrival", "duplicate", "foreign", "end"))
def test_phase_audit_rejects_changed_membership_clocks_and_duplicate_boundaries(change):
    report, arrivals, cohort, events = phase_evidence()
    if change == "cohort":
        cohort["measured_workflow_ids"].reverse()
    elif change == "arrival":
        events[0]["ts_ms"] += 1.
    elif change == "duplicate":
        events.append(deepcopy(events[0]))
    elif change == "foreign":
        events[0]["workflow_id"] = "foreign"
    else:
        next(row for row in events if row["kind"] == "workflow_end")["ts_ms"] = 1.
    with pytest.raises(ValueError):
        observation_phases(report, arrivals, cohort, events)
