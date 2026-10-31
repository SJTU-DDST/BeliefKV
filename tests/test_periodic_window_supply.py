from copy import deepcopy

import pytest

from scripts.audit_periodic_admission_decisions import inspect, observation_phases
from tests.test_periodic_admission_decisions import phase_evidence, records


PARTITION = (
    "free_budget_without_application_ready_paused_controls",
    "free_budget_with_application_ready_but_none_fitting_controls",
    "free_budget_with_only_window_deferred_fitting_ready_controls",
    "free_budget_with_fitting_window_eligible_ready_controls",
)


def held(*, capacity=800):
    choices, _batches = records()
    choices[0]["resume_capacity_tokens"] = capacity
    choices[0]["resumed_program_ids"] = []
    for item in choices[0]["programs"]:
        item["resume_window_eligible"] = False
        item["active_after_pressure"] = False
    return choices


def test_logically_fitting_deferred_work_is_not_reported_as_absent():
    result = inspect(held(), [])
    counts = result["counts"]
    assert counts["free_budget_without_ready_paused_controls"] == 1
    assert counts["free_budget_without_application_ready_paused_controls"] == 0
    assert counts["free_budget_with_only_window_deferred_fitting_ready_controls"] == 1
    assert counts["window_deferred_fitting_ready_paused_observations"] == 3
    assert counts["cross_workflow_application_ready_set_competition_controls"] == 1
    assert counts["cross_workflow_logical_set_competition_controls"] == 0
    assert not result["competition_decisions"]
    assert result["scope"]["logical_fit_does_not_prove_physical_feasibility"]


def test_large_deferred_agents_do_not_imply_that_opening_the_window_would_fit_them():
    result = inspect(held(capacity=200), [])
    counts = result["counts"]
    assert counts["controls_with_window_deferred_ready_paused"] == 1
    assert counts["controls_with_window_deferred_fitting_ready_paused"] == 0
    assert counts["free_budget_with_only_window_deferred_fitting_ready_controls"] == 0
    assert counts["free_budget_with_application_ready_but_none_fitting_controls"] == 1
    assert counts["cross_workflow_application_ready_set_competition_controls"] == 0


@pytest.mark.parametrize("change", ("active", "no_pending", "blocked", "marked"))
def test_window_deferral_does_not_create_a_candidate_from_nonready_state(change):
    choices = held()
    for item in choices[0]["programs"]:
        field, value = {
            "active": ("active_before", True),
            "no_pending": ("pending_before", False),
            "blocked": ("application_ready", False),
            "marked": ("marked_before", True),
        }[change]
        item[field] = value
    counts = inspect(choices, [])["counts"]
    assert counts["free_budget_without_application_ready_paused_controls"] == 1
    assert counts["controls_with_window_deferred_fitting_ready_paused"] == 0
    assert sum(counts[name] for name in PARTITION) == 1


def test_open_candidates_and_closed_peers_remain_separate():
    choices = held()
    choices[0]["programs"][0]["resume_window_eligible"] = True
    result = inspect(choices, [])
    row = result["control_observations"][0]
    assert row["application_ready_paused_agents"] == 3
    assert row["ready_paused_agents"] == 1
    assert row["window_deferred_fitting_ready_paused_agents"] == 2
    assert row["cross_workflow_application_ready_set_competition"]
    assert not row["cross_workflow_logical_set_competition"]
    assert result["counts"]["free_budget_with_only_window_deferred_fitting_ready_controls"] == 0
    assert result["counts"]["free_budget_with_fitting_window_eligible_ready_controls"] == 1


def test_missing_old_window_fields_reconstruct_the_original_open_window_semantics():
    choices, batches = records()
    result = inspect(choices, batches)
    row = result["control_observations"][0]
    assert row["application_ready_paused_agents"] == row["ready_paused_agents"] == 3
    assert row["window_deferred_fitting_ready_paused_agents"] == 0
    assert result["counts"]["cross_workflow_logical_set_competition_controls"] == 1
    assert result["counts"]["cross_workflow_application_ready_set_competition_controls"] == 1


@pytest.mark.parametrize("capacity", (0, 100, -100))
def test_buffer_only_or_negative_budget_is_not_available_for_dispatch(capacity):
    counts = inspect(held(capacity=capacity), [])["counts"]
    assert counts["controls_with_available_resume_budget"] == 0
    assert sum(counts[name] for name in PARTITION) == 0


def test_ready_but_window_deferred_ownership_is_still_validated():
    choices = held()
    choices[0]["programs"][0]["key"]["invocation_id"] = "other-owner"
    with pytest.raises(ValueError, match="another invocation"):
        inspect(choices, [])


def test_supply_categories_partition_available_logical_budget_snapshots():
    no_ready = held()
    for item in no_ready[0]["programs"]:
        item["application_ready"] = False
    no_fit = held(capacity=200)
    window_hold = held()
    open_window = held()
    for item in open_window[0]["programs"]:
        item["resume_window_eligible"] = True
    choices = [case[0] for case in (no_ready, no_fit, window_hold, open_window)]
    for index, row in enumerate(choices, start=1):
        row["monotonic_s"] = float(index)
    counts = inspect(choices, [])["counts"]
    assert counts["controls_with_available_resume_budget"] == 4
    assert all(counts[name] == 1 for name in PARTITION)


def test_phase_diagnostics_keep_held_work_distinct_without_changing_actual_choices():
    result, arrivals, cohort, events = phase_evidence()
    first = result["control_observations"][0]
    first.update(
        ready_paused_agents=0, fitting_ready_paused_agents=0,
        window_deferred_ready_paused_agents=3, window_deferred_fitting_ready_paused_agents=3,
        cross_workflow_logical_set_competition=False,
    )
    phases = observation_phases(result, arrivals, cohort, events)["phases"]
    fixed = phases["fixed_common_throughput_window"]
    assert fixed["application_readiness_diagnostics_observed"]
    assert fixed["controls_with_predecision_ready_paused"] == 0
    assert fixed["controls_with_application_ready_paused"] == 1
    assert fixed["actual_cross_workflow_logical_set_competitions"] == 0
    assert fixed["application_ready_cross_workflow_logical_set_competitions"] == 1
    assert fixed["free_budget_with_only_window_deferred_fitting_ready_controls"] == 1
    assert phases["measured_execution_after_common_window"][
        "free_budget_with_only_window_deferred_fitting_ready_controls"
    ] == 0


def test_precomputed_old_phase_rows_without_readiness_detail_remain_unknown():
    values = deepcopy(phase_evidence())
    for row in values[0]["control_observations"]:
        row.pop("application_ready_paused_agents")
    fixed = observation_phases(*values)["phases"]["fixed_common_throughput_window"]
    assert not fixed["application_readiness_diagnostics_observed"]
    assert fixed["controls_with_application_ready_paused"] is None
    assert fixed["free_budget_without_application_ready_paused_controls"] is None
    assert fixed["actual_periodic_controls"] == 1
