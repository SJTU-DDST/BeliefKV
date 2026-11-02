from types import SimpleNamespace

import pytest

from scripts.audit_short_workflow_prefill_choices import (
    ObservedWork, observed_eligibility, rank_waiting, summarize, unchanged_cadence_return,
)


def artifact():
    requests = {
        "a": {
            "workflow_id": "long", "input_increment": 100, "output_tokens": 20,
        },
        "b": {
            "workflow_id": "short", "input_increment": 0, "output_tokens": 5,
        },
    }
    graphs = {
        workflow: [
            {"node_id": request, "request_id": request, "event_id": None,
             "dependencies": [], "delay_s": 0.},
            {"node_id": workflow + "-tool", "request_id": None, "event_id": workflow + "-end",
             "dependencies": [0], "delay_s": delay},
        ] for workflow, request, delay in (("long", "a", 0.), ("short", "b", 30.))
    }
    return SimpleNamespace(
        requests=requests, graphs=graphs,
        workflows={
            "long": {"total_work_units": 2100}, "short": {"total_work_units": 500},
        },
        model_seconds_per_work_token=1.,
        model_prefill_seconds_per_token=None,
        model_prefill_progress="request_completion",
    )


def test_model_demand_and_uncontended_tool_path_can_produce_different_orders():
    work = ObservedWork(artifact())
    candidates = [("b", "short")]
    assert rank_waiting("long", candidates, work, 0., field="model_work_tokens", minimum_ratio=2.)
    assert rank_waiting("long", candidates, work, 0., field="model_tool_path_s", minimum_ratio=2.) is None


def test_only_observed_partial_output_and_exact_completion_reduce_work():
    work = ObservedWork(artifact())
    assert work.score("long", 0.)["model_work_tokens"] == 21.
    work.progress("a", 7)
    assert work.score("long", 1000.)["model_work_tokens"] == 14.
    work.complete("a", 10000.)
    assert work.score("long", 10000.)["model_work_tokens"] == 0.
    assert work.score("long", 10000.)["model_tool_path_s"] == 0.
    with pytest.raises(ValueError, match="duplicate"):
        work.complete("a", 10001.)


def test_external_wait_deducts_elapsed_time_only_after_its_dependency_completes():
    work = ObservedWork(artifact())
    assert work.score("short", 100000.)["model_tool_path_s"] == 35.
    work.complete("b", 100000.)
    assert work.score("short", 110000.)["model_tool_path_s"] == 20.
    work.event("short-end", 111000.)
    assert work.score("short", 111000.)["model_tool_path_s"] == 0.


@pytest.mark.parametrize("outputs", [-1, 21, 1.2, True])
def test_output_progress_rejects_out_of_label_or_noninteger_values(outputs):
    with pytest.raises(ValueError):
        ObservedWork(artifact()).progress("a", outputs)


def test_output_progress_cannot_go_backwards():
    work = ObservedWork(artifact())
    work.progress("a", 10)
    with pytest.raises(ValueError, match="backwards"):
        work.progress("a", 9)


def request_and_state():
    request = {
        "request_id": "b",
        "native_receipt": {
            "identity": ["short", "agent", "context", 0],
            "session_id": "session", "session_generation": 2,
        },
    }
    row = {
        "key": {
            "request_id": "b", "root_workflow_id": "short", "invocation_id": "agent",
            "context_id": "context", "context_epoch": 0,
            "session_id": "session", "session_generation": 2,
        },
        "application_ready": True, "pending_before": True,
        "active_after_pressure": True, "marked_after_pressure": False,
    }
    return request, row


@pytest.mark.parametrize(
    "change,reason",
    [
        ({}, "recent_observed_active_ready"),
        ({"active_after_pressure": False}, "not_observed_active_after_pressure"),
        ({"application_ready": False}, "not_observed_application_ready"),
        ({"pending_before": False}, "not_observed_application_ready"),
        ({"marked_after_pressure": True}, "not_observed_active_after_pressure"),
    ],
)
def test_logical_state_does_not_promote_paused_or_blocked_work(change, reason):
    request, row = request_and_state()
    row.update(change)
    assert observed_eligibility(
        request, row, 1000., 1500., {}, maximum_age_ms=2500.,
    ) == reason


def test_stale_identity_and_any_later_intervention_invalidate_preceding_eligibility():
    request, row = request_and_state()
    assert observed_eligibility(
        request, row, 1000., 4000., {}, maximum_age_ms=2500.,
    ) == "preceding_state_too_old"
    assert observed_eligibility(
        request, row, 1000., 1500., {"agent": [(1200., 1)]}, maximum_age_ms=2500.,
    ) == "intervening_admission_change"
    assert observed_eligibility(
        request, row, 1000., 1500., {"agent": [(1600., 1)]}, maximum_age_ms=2500.,
    ) == "recent_observed_active_ready"
    row["key"]["session_generation"] = 1
    assert observed_eligibility(
        request, row, 1000., 1500., {}, maximum_age_ms=2500.,
    ) == "preceding_request_identity_changed"


def test_optimistic_return_needs_actual_later_decode_steps():
    assert unchanged_cadence_return(110., 100., 1, [], []) == 110.
    assert unchanged_cadence_return(110., 100., 3, [90., 120., 140.], [95., 130., 150.]) == 150.
    assert unchanged_cadence_return(110., 100., 4, [90., 120., 140.], [95., 130., 150.]) is None


def test_same_workflow_is_not_a_new_workflow_priority_choice():
    assert rank_waiting(
        "long", [("a", "long")], ObservedWork(artifact()), 0.,
        field="model_work_tokens", minimum_ratio=2.,
    ) is None


def test_summary_deduplicates_repeated_boundaries_and_only_then_marks_cohort():
    rows = [
        {
            "request_id": "b", "workflow_id": "short",
            "remaining_first_service_wait_ms": delay,
            "unchanged_cadence_advance_ms": delay,
            "future_first_prefill_extend_tokens": 50,
        }
        for delay in (1000., 500.)
    ]
    result = summarize(rows, {"short"}, {"b"})
    assert result["repeated_prefill_boundary_choices"] == 2
    assert result["distinct_waiting_requests"] == 1
    assert result["measured_critical_first_service_wait_ms"]["sum"] == 1000.
