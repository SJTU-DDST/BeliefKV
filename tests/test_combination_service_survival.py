from copy import deepcopy

import pytest

from scripts.audit_combination_service_survival import cohort_exposure, inspect, native_counter_bounds


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
        "journal_sequence": 0,
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
        "sample_id": "native-service-1", "batch_size": 1,
        "launch_monotonic_ms": 2000., "service_start_monotonic_ms": 2010.,
        "complete_monotonic_ms": 2050.,
        "request_samples": [{
            "request_id": "request", "workflow_id": "workflow", "invocation_id": "agent",
            "context_id": "context", "context_epoch": 0,
            "sequence_tokens_before": 10,
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
        {"request_id": "other-a", "phase": "prefill", "sequence_tokens_before": 10, "token_delta": 2},
        {"request_id": "other-b", "phase": "prefill", "sequence_tokens_before": 20, "token_delta": 3},
    ]
    row = inspect(report, records, [batch])["episodes"][0]
    assert row["worker_overlap_ms_by_phase"] == {"prefill": 100., "decode": 0.}
    assert row["overlapping_prefill_request_counts"] == {"other-a": 1, "other-b": 1}


def test_overlapping_worker_intervals_are_not_double_counted_as_wait_exposure():
    report, records, batch = fixture()
    with pytest.raises(ValueError, match="overlapping native worker"):
        inspect(report, records, [batch, deepcopy(batch)])


def test_native_rejection_bounds_keep_global_counts_and_missing_counters_separate():
    snapshots = [
        {"monotonic_ms": when, "counts": {"native_OTHER": value}}
        for when, value in ((0., 0), (2., 5), (4., 10), (6., 15))
    ]
    result = native_counter_bounds(snapshots, 1., 5.)
    assert result["native_OTHER"] == {"available": True, "lower": 5, "upper": 15}
    assert result["native_NO_TOKEN"] == {"available": False}
    result = native_counter_bounds(snapshots, 2.5, 3.5)
    assert result["native_OTHER"] == {"available": True, "lower": 0, "upper": 5}


@pytest.mark.parametrize("change", ("backwards", "decreased"))
def test_invalid_native_counter_sequences_cannot_support_wait_attribution(change):
    snapshots = [
        {"monotonic_ms": 0., "counts": {"native_OTHER": 3}},
        {"monotonic_ms": 2., "counts": {"native_OTHER": 5}},
    ]
    snapshots[-1]["monotonic_ms" if change == "backwards" else "counts"] = (
        -1. if change == "backwards" else {"native_OTHER": 2}
    )
    with pytest.raises(ValueError):
        native_counter_bounds(snapshots, .5, 1.)


def test_selection_geometry_is_not_replaced_by_the_later_warm_prefix():
    report, records, batch = fixture()
    key = records[1]["programs"][0]["key"]
    records[0] = {
        "event": "workflow_combination_selection",
        "keys": {"agent": key},
        "resources": {"device_tokens": 1, "input_tokens": 2048},
        "candidates": [{
            "identity": "agent", "context_tokens": 12, "input_tokens": 2,
            "missing_extents": [("extent", 5)],
        }],
        "decision": {"selected_evaluation": {"current_device_summary_fits": False}},
    }
    batch.update(phase="prefill", launch_monotonic_ms=5990.,
                 service_start_monotonic_ms=6000., complete_monotonic_ms=6010.)
    batch["request_samples"][0].update(
        attempt_id=2, session_id="session", session_generation=3,
        phase="prefill", token_delta=2,
    )
    row = inspect(report, records, [batch])["episodes"][0]
    assert row["selected_candidate_geometry"]["missing_checkpoint_tokens"] == 5
    assert row["selected_candidate_geometry"]["input_tokens"] == 2
    assert row["first_exact_service_geometry"]["prefix_tokens"] == 10
    assert row["first_exact_service_geometry"]["input_tokens"] == 2


def test_mixed_decode_member_does_not_report_its_output_as_prefill_input():
    report, records, batch = fixture()
    batch.update(phase="prefill", launch_monotonic_ms=5990.,
                 service_start_monotonic_ms=6000., complete_monotonic_ms=6010.)
    batch["request_samples"][0].update(
        attempt_id=2, session_id="session", session_generation=3, phase="decode",
    )
    row = inspect(report, records, [batch])["episodes"][0]
    assert row["first_exact_service_geometry"]["phase"] == "decode"
    assert row["first_exact_service_geometry"]["input_tokens"] is None


@pytest.mark.parametrize("field", ("workflow_id", "invocation_id", "context_id", "context_epoch"))
def test_missing_base_identity_cannot_prove_first_exact_service(field):
    report, records, batch = fixture()
    batch.update(launch_monotonic_ms=5990.,
                 service_start_monotonic_ms=6000., complete_monotonic_ms=6010.)
    member = batch["request_samples"][0]
    member.update(attempt_id=2, session_id="session", session_generation=3)
    member.pop(field)
    assert inspect(report, records, [batch])["episodes"][0]["first_exact_service_geometry"] is None


def test_prefill_request_geometry_discloses_multiple_attempts():
    report, records, first = fixture()
    first.update(phase="prefill", service_start_monotonic_ms=1100., complete_monotonic_ms=1200.)
    first["request_samples"] = [{
        "request_id": "other", "phase": "prefill", "attempt_id": 1,
        "sequence_tokens_before": 0, "token_delta": 8,
    }]
    second = deepcopy(first)
    second.update(service_start_monotonic_ms=1200., complete_monotonic_ms=1300.)
    second["request_samples"][0]["attempt_id"] = 2
    shape = inspect(report, records, [first, second])["episodes"][0]["dominant_overlapping_prefill_geometry"]
    assert shape["prefill_batches"] == 2
    assert [item["attempt_id"] for item in shape["native_identity_variants"]] == [1, 2]
    assert all(item["session_id"] is None for item in shape["native_identity_variants"])


@pytest.mark.parametrize("begin,end", ((0., float("inf")), (float("nan"), 1.), (True, 2.)))
def test_invalid_wait_clocks_cannot_support_native_counter_bounds(begin, end):
    with pytest.raises(ValueError):
        native_counter_bounds([], begin, end)


def test_measurement_cohort_keeps_background_and_parallel_waits_separate():
    rows = [
        {"key": ("rid", workflow), "begin_ms": begin, "first_exact_service_ms": end,
         "bounded_service": bounded}
        for workflow, begin, end, bounded in (
            ("measured", 1., 5., True), ("measured", 3., 7., False),
            ("measured", 8., 10., True), ("background", 1., 21., False),
        )
    ]
    result = cohort_exposure(rows, ("measured", "no_new_selections"))
    assert result["measurement_cohort_size"] == 2
    assert result["measured"] == {
        "episodes": 3, "distinct_workflows": 1, "episode_wait_sum_ms": 10.,
        "within_workflow_wait_union_sum_ms": 8., "late_episodes": 1, "late_episode_wait_sum_ms": 4.,
    }
    assert result["nonmeasured"]["episode_wait_sum_ms"] == 20.
    assert result["nonmeasured"]["within_workflow_wait_union_sum_ms"] == 20.
    assert cohort_exposure([], ("measured",))["measured"]["episodes"] == 0


@pytest.mark.parametrize("members", ((), ("wf", "wf"), ("",), (None,)))
def test_invalid_measurement_members_cannot_redefine_the_original_cohort(members):
    with pytest.raises(ValueError, match="cohort"):
        cohort_exposure([], members)
