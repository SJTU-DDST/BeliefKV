from copy import deepcopy

import pytest

from scripts.summarize_measured_admission_competition import summarize


def evidence():
    cohort = {"measured_workflow_ids": ["m1", "m2"], "throughput_window_offset_seconds": [1., 3.]}
    observations = []
    for when, ready, fitting, workflows, competing in (
        (10., 4, 3, 3, True), (11., 6, 3, 2, True), (12., 1, 1, 1, False),
        (13., 5, 4, 3, True), (14., 1, 0, 0, False), (15., 0, 0, 0, False),
    ):
        observations.append({
            "monotonic_s": when, "ready_paused_agents": ready,
            "fitting_ready_paused_agents": fitting, "fitting_workflows": workflows,
            "cross_workflow_logical_set_competition": competing,
        })
    decisions = []
    for when, owners in (
        (10., [("m1", "m1a"), ("m2", "m2a"), ("b", "b0")]),
        (11., [("m1", "m1a"), ("m1", "m1b"), ("b", "b0")]),
        (13., [("m1", "m1a"), ("m2", "m2a"), ("b", "b0"), ("b", "b1")]),
    ):
        decisions.append({
            "monotonic_s": when, "fitting_candidates": [
                {"root_workflow_id": workflow, "invocation_id": owner} for workflow, owner in owners
            ],
        })
    phases = {}
    for name, left, right, count in (
        ("fixed_common_throughput_window", 1., 3., 2),
        ("measured_execution_observation_span", 1., None, 5),
        ("measured_execution_after_common_window", 3., None, 3),
    ):
        phases[name] = {
            "start_s": left, "end_s": right, "actual_periodic_controls": count,
            "observed_through_offset_s": min(5., right) if right is not None else 5.,
            "complete_phase_observed": right is not None,
        }
    return {
        "control_observations": observations, "competition_decisions": decisions,
        "observation_phases": {
            "origin_monotonic_ms": 10000., "all_measured_completed": False, "phases": phases,
        },
    }, cohort


def test_competing_only_average_does_not_use_all_ready_or_all_controls():
    report, cohort = evidence()
    phase = summarize(report, cohort)["phases"]["fixed_common_throughput_window"]
    assert phase["controls"] == 2
    assert phase["mean_ready_paused_per_control"] == 3.5
    assert phase["mean_individually_fitting_per_control"] == 2.
    assert phase["competition_fraction"] == .5
    assert phase["mean_candidates_when_competing"] == 3.


def test_two_siblings_do_not_become_two_measured_workflows():
    report, cohort = evidence()
    phases = summarize(report, cohort)["phases"]
    common = phases["fixed_common_throughput_window"]
    assert common["competition_controls_with_two_measured_agents"] == 1
    assert common["competition_controls_with_two_measured_workflows"] == 0
    assert common["competition_controls_per_measured_workflow"] == {"m1": 1, "m2": 0}
    full = phases["measured_execution_observation_span"]
    assert full["competition_controls_with_two_measured_workflows"] == 1
    assert full["competition_controls_per_measured_workflow"] == {"m1": 2, "m2": 1}


def test_phase_right_boundary_and_warmup_exclusion_preserve_natural_tail():
    report, cohort = evidence()
    phases = summarize(report, cohort)["phases"]
    assert phases["fixed_common_throughput_window"]["competition_controls"] == 1
    assert phases["measured_execution_observation_span"]["competition_controls"] == 2
    tail = phases["measured_execution_after_common_window"]
    assert tail["controls"] == 3 and tail["competition_controls"] == 1
    assert tail["end_s"] is None and tail["complete_phase_observed"] is False


def test_unstarted_phase_is_unknown_rather_than_zero_mean_supply():
    report, cohort = evidence()
    phases = report["observation_phases"]["phases"]
    phases["future"] = {
        "start_s": 9., "end_s": None, "actual_periodic_controls": 0,
        "observed_through_offset_s": 5., "complete_phase_observed": False,
    }
    phase = summarize(report, cohort)["phases"]["future"]
    assert phase["controls"] == 0
    assert phase["mean_ready_paused_per_control"] is None
    assert phase["competition_fraction"] is None
    assert phase["competition_controls_per_measured_workflow"] == {"m1": 0, "m2": 0}


@pytest.mark.parametrize("damage", ("missing", "duplicate_owner", "foreign_boundary", "false_flag"))
def test_missing_or_ambiguous_candidate_evidence_is_rejected(damage):
    report, cohort = evidence()
    if damage == "missing":
        report["competition_decisions"].pop()
    elif damage == "duplicate_owner":
        report["competition_decisions"][1]["fitting_candidates"][1] = deepcopy(
            report["competition_decisions"][1]["fitting_candidates"][0]
        )
    elif damage == "foreign_boundary":
        report["competition_decisions"][1]["monotonic_s"] = 11.5
    else:
        report["control_observations"][1]["cross_workflow_logical_set_competition"] = False
    with pytest.raises(ValueError):
        summarize(report, cohort)


@pytest.mark.parametrize("damage", ("duplicate_control", "wrong_count", "wrong_cohort", "overcount_fit"))
def test_cross_population_and_denominator_errors_are_rejected(damage):
    report, cohort = evidence()
    if damage == "duplicate_control":
        report["control_observations"].append(deepcopy(report["control_observations"][0]))
    elif damage == "wrong_count":
        report["observation_phases"]["phases"]["fixed_common_throughput_window"]["actual_periodic_controls"] = 3
    elif damage == "wrong_cohort":
        cohort["throughput_window_offset_seconds"] = [0., 3.]
    else:
        report["control_observations"][2]["fitting_ready_paused_agents"] = 2
    with pytest.raises(ValueError):
        summarize(report, cohort)
