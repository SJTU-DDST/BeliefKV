from copy import deepcopy

import pytest

from scripts.audit_combination_service_survival import inspect


def fixture():
    key = ("request", "workflow", "agent", "context", 0, 2, "session", 3)
    key_dict = dict(zip((
        "request_id", "root_workflow_id", "invocation_id", "context_id", "context_epoch",
        "attempt_id", "session_id", "session_generation",
    ), key))
    member = {
        "key": key, "episode": {
            "key": key, "selection_ms": 1000., "journal_sequence": 0,
            "following_native_service_ms": 5000., "first_new_useful_service_ms": 5000.,
            "new_service_before_next_control_or_intervention": False,
        },
    }
    report = {"decisions": [{
        "source": "periodic_decay_resume", "periodic_service": [member], "free_service": [],
    }]}
    control = {
        "event": "agent_periodic_admission_decision", "monotonic_s": 3.,
        "programs": [{
            "invocation_id": "agent", "key": key_dict,
            "active_before": True, "active_after_pressure": True,
        }],
    }
    batch = {
        "event": "gpu_service_sample", "phase": "decode",
        "launch_monotonic_ms": 2000., "service_start_monotonic_ms": 2010.,
        "complete_monotonic_ms": 2050.,
        "request_samples": [{
            "request_id": "request", "workflow_id": "workflow", "invocation_id": "agent",
            "context_id": "context", "context_epoch": 0,
            "token_delta": 1, "output_token_delta": 1,
        }],
    }
    return report, [{"event": "grant"}, control], batch


def test_global_control_with_unchanged_active_identity_does_not_imply_pause():
    report, records, batch = fixture()
    before = deepcopy((report, records, batch))
    result = inspect(report, records, [batch])
    assert result["counts"]["late_exact_with_earlier_partial_positive_service"] == 1
    assert result["episodes"][0]["controls_with_same_active_identity"] == 1
    assert result["counts"]["with_observed_inactive_control"] == 0
    assert (report, records, batch) == before


@pytest.mark.parametrize("change", ("foreign_attempt", "foreign_session", "zero", "earlier_launch", "later_service"))
def test_irrelevant_partial_progress_does_not_shorten_the_observed_wait(change):
    report, records, batch = fixture()
    sample = batch["request_samples"][0]
    if change == "foreign_attempt":
        sample["attempt_id"] = 99
    elif change == "foreign_session":
        sample["session_id"] = "other"
    elif change == "zero":
        sample.update(token_delta=0, output_token_delta=0)
    elif change == "earlier_launch":
        batch["launch_monotonic_ms"] = 999.
    else:
        batch["service_start_monotonic_ms"] = 6000.
        batch["complete_monotonic_ms"] = 6040.
    assert inspect(report, records, [batch])["counts"]["with_earlier_partial_positive_service"] == 0


def test_an_unaccounted_earlier_exact_service_is_a_reader_error():
    report, records, batch = fixture()
    batch["request_samples"][0].update(attempt_id=2, session_id="session", session_generation=3)
    with pytest.raises(ValueError, match="missed an earlier exact"):
        inspect(report, records, [batch])


def test_unknown_progress_and_missing_control_identity_remain_unknown():
    report, records, batch = fixture()
    batch["request_samples"][0].pop("output_token_delta")
    records[1]["programs"] = []
    result = inspect(report, records, [batch])
    assert result["counts"]["with_earlier_unknown_progress"] == 1
    assert result["counts"]["with_earlier_partial_positive_service"] == 0
    assert result["counts"]["with_unknown_control_identity"] == 1


@pytest.mark.parametrize("change", ("inactive", "offload", "pressure", "planned_pressure", "regrant"))
def test_real_pauses_and_regrants_are_separate_from_global_observation_cutoffs(change):
    report, records, batch = fixture()
    key = records[1]["programs"][0]["key"]
    if change == "inactive":
        records[1]["programs"][0]["active_after_pressure"] = False
    elif change == "offload":
        records.append({"event": "agent_full_offload_paused", "monotonic_s": 4., "key": key})
    elif "pressure" in change:
        records.append({
            "event": "workflow_remaining_pressure_pause", "monotonic_s": 4.,
            "selected": "agent", "marked_for_pause": change == "planned_pressure",
        })
    else:
        records.append({
            "event": "agent_dispatch_free_capacity_admitted", "monotonic_s": 4.,
            "programs": [{"key": key}],
        })
    result = inspect(report, records, [batch])["counts"]
    assert result["with_observed_inactive_control"] == int(change == "inactive")
    assert result["with_explicit_pause_before_exact_service"] == int(change in ("offload", "pressure"))
    assert result["with_same_agent_regrant_before_exact_service"] == int(change == "regrant")


def test_wait_exposure_counts_whole_worker_intersection_once_for_a_shared_prefill():
    report, records, batch = fixture()
    batch.update(phase="prefill", service_start_monotonic_ms=900., complete_monotonic_ms=1100.)
    batch["request_samples"] = [
        {"request_id": "other-a", "phase": "prefill"},
        {"request_id": "other-b", "phase": "prefill"},
    ]
    row = inspect(report, records, [batch])["episodes"][0]
    assert row["worker_overlap_ms_by_phase"] == {"prefill": 100., "decode": 0.}
    assert row["overlapping_prefill_request_counts"] == {"other-a": 1, "other-b": 1}


def test_overlapping_worker_intervals_are_not_double_counted_as_wait_exposure():
    report, records, batch = fixture()
    with pytest.raises(ValueError, match="overlapping native worker"):
        inspect(report, records, [batch, deepcopy(batch)])
