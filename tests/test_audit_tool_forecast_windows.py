from scripts.audit_tool_forecast_windows import evaluate


def labels(*, proven=True, tools=1):
    return {"wait_event_attribution": {"rows": [{
        "source": "tool_wait", "workflow_id": "w", "invocation_id": "i",
        "context_id": "c", "context_epoch": 2, "observed_tool_call_count": tools,
        "completion_ts_ms": 2000. if proven else None,
        "completion_evidence": "whole_tool_request_round" if proven else "tool_request_round_unproven",
        "early_started_and_reused_full_bytes": 0, "remaining_native_full_host_hit_bytes": 100,
    }]}}


def forecast(**changes):
    return {
        "event": "tool_wait_forecast", "workflow_id": "w", "invocation_id": "i",
        "context_id": "c", "context_epoch": 2, "ts_ms": 1700.,
        "observed_monotonic_ms": 1400., "p10_ms": 50., "p50_ms": 100., "p90_ms": 500.,
        "release_cdf": [[100., .1], [500., .5], [1000., .9]],
        "residual_duration_mass": [[100., .1], [300., .8], [600., .1]],
        "residual_support_detail": "same_input_completed", **changes,
    }


def records(*rows):
    return [{"event": "safe_point_census", "ts_ms": 1100., "monotonic_ms": 1000.}, *rows]


def plan(*, predicted=400., ts=1800., fits=True):
    return {
        "event": "wait_prefetch_plan", "source": "tool_wait",
        "workflow_id": "w", "invocation_id": "i", "context_id": "c",
        "context_epoch": 2, "ts_ms": ts, "planned_pool_bytes": {"kv": 100},
        "predicted_remaining_ms": predicted, "start_window_ms": 250.,
        "fits_current_free_lists": fits,
    }


def test_forecasts_use_recorded_mass_conditioned_at_acceptance():
    result = evaluate(labels(), records(forecast()))
    row = result["rows"][0]["last_pre_completion_forecast"]
    assert row["forecast_age_ms"] == 200.
    assert row["predicted_remaining_ms"] == 100.
    assert row["actual_remaining_ms"] == 300.
    assert row["signed_error_ms"] == -200.
    assert result["single_tool_round_forecast_timing"]["rounds_with_last_second_forecasts"] == 1


def test_missing_boundaries_expired_and_later_forecasts_are_not_accuracy_labels():
    result = evaluate(labels(), records(
        forecast(ts_ms=1900., observed_monotonic_ms=-4000.),
        forecast(ts_ms=2001.), forecast(context_epoch=3),
    ))
    assert result["counts"]["forecasts_expired_or_clock_reversed"] == 1
    assert result["counts"]["tool_wait_forecast_at_or_after_completion"] == 1
    assert result["counts"]["records_without_round_label"] == 1
    assert result["forecast_timing"]["rounds_with_pre_completion_forecasts"] == 0
    unknown = evaluate(labels(proven=False), records(forecast(), plan()))
    assert unknown["counts"]["records_with_unproven_completion"] == 2
    assert unknown["positive_full_plan_window_categories"]["missed_recorded_window"]["snapshots"] == 0


def test_window_categories_keep_capacity_and_per_round_byte_counts_separate():
    result = evaluate(labels(), records(
        plan(), plan(ts=1801.), plan(predicted=100., ts=1600.),
        plan(predicted=100., ts=1900., fits=False), plan(predicted=400., ts=1600.),
    ))
    categories = result["positive_full_plan_window_categories"]
    missed = categories["missed_recorded_window_with_observed_free_capacity"]
    assert missed == {
        "snapshots": 2, "independent_tool_rounds": 1,
        "sum_per_round_max_observed_full_bytes": 100,
    }
    assert categories["predicted_too_early"]["independent_tool_rounds"] == 1
    assert categories["timely_candidate"]["independent_tool_rounds"] == 1
    assert categories["timely_candidate_with_observed_free_capacity"]["snapshots"] == 0
    assert categories["not_yet_due"]["snapshots"] == 1


def test_multi_call_rounds_are_reported_separately_and_unknown_timing_is_not_zero():
    result = evaluate(labels(tools=2), records(forecast(), {
        **plan(), "predicted_remaining_ms": None,
    }))
    assert result["multi_tool_round_forecast_timing"]["rounds_with_pre_completion_forecasts"] == 1
    assert result["single_tool_round_forecast_timing"]["independent_tool_rounds"] == 0
    assert result["counts"]["positive_full_plans_without_timing"] == 1
