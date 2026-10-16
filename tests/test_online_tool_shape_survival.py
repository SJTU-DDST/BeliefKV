import pytest

from scripts.evaluate_online_tool_shape_survival import (
    CausalShapeHistory,
    eligible_call,
    evaluate,
    tool_calls,
)


def call(*, duration=100., start=0., shape="test_suite_full",
         project="django", status="success", **changes):
    return {
        "project": project, "command_class": "test_suite", "shape": shape,
        "is_child": True, "tool_name": "execute", "start_ms": start,
        "end_ms": start + duration, "duration_ms": duration,
        "status": status, "exception_type": None, "tool_error_class": None,
        **changes,
    }


def test_only_strictly_past_completions_enter_the_empirical_head():
    current = call(start=500., duration=10_000)
    history = CausalShapeHistory([
        call(duration=100.), call(start=100., duration=400.),
        current, call(start=1000., duration=5.),
    ], window=64, include_errors=True)
    assert history.remaining(current, now_ms=500., minimum_support=1) == (100., 1, 1)
    assert history.remaining(current, now_ms=550., minimum_support=1) == (200., 2, 2)
    assert history.remaining(current, now_ms=650., minimum_support=1) == (250., 2, 1)


def test_elapsed_survival_removes_fast_failures_but_keeps_actual_timeout_returns():
    current = call(start=200_000., duration=180_000.)
    past = [call(duration=value, status="error") for value in (50., 100., 180_000., 180_100.)]
    history = CausalShapeHistory(past, window=64, include_errors=True)
    remaining, support, surviving = history.remaining(
        current, now_ms=210_000., minimum_support=4,
    )
    assert remaining == 170_050.
    assert (support, surviving) == (4, 2)
    success_only = CausalShapeHistory(past, window=64, include_errors=False)
    assert success_only.remaining(current, now_ms=210_000., minimum_support=1) == (None, 0, 0)


def test_shape_project_and_role_do_not_borrow_unrelated_history():
    past = [
        call(duration=180_000., shape="test_suite_targeted"),
        call(duration=180_000., project="pytest"),
        call(duration=180_000., is_child=False),
    ]
    history = CausalShapeHistory(past, window=64, include_errors=True)
    assert history.remaining(call(start=200_000.), now_ms=200_001., minimum_support=1) == (None, 0, 0)


def test_cancellation_is_not_a_completed_duration_and_exhausted_support_is_unknown():
    history = CausalShapeHistory([
        call(duration=180_000., exception_type="CancelledError"),
        call(duration=180_000., exception_type="ActivationDeadlineExceeded"),
        call(duration=500., tool_error_class="administrative_cancel"),
        call(duration=100.),
    ], window=64, include_errors=True)
    assert history.remaining(call(start=200_000.), now_ms=200_200., minimum_support=1) == (None, 1, 0)


def test_bounded_history_replaces_old_samples_in_completion_order():
    history = CausalShapeHistory([
        call(duration=100.), call(start=100., duration=200.),
        call(start=300., duration=300.),
    ], window=2, include_errors=True)
    assert history.remaining(call(start=1000.), now_ms=1050., minimum_support=2) == (200., 2, 2)


def event(kind, timestamp, *, epoch=None, run=None, **attrs):
    return {
        "kind": kind, "ts_ms": timestamp, "sequence": int(timestamp),
        "workflow_id": "w", "invocation_id": "i", "context_id": "c",
        "context_epoch": epoch,
        "attributes": {**attrs, "tool_run_id": run},
    }


def test_tool_run_identity_keeps_the_previous_llm_epoch_across_next_submission():
    calls = tool_calls([
        event("llm_result", 10., epoch=2),
        event("tool_start", 11., run="r", tool_name="execute", is_child=True,
              observed_command_class="test_suite", observed_command_shape="test_suite_full"),
        event("llm_result", 20., epoch=3),
        event("tool_end", 21., run="r", status="error"),
    ], project="django")
    assert len(calls) == 1
    assert calls[0]["context_epoch"] == 2
    assert calls[0]["duration_ms"] == 10.
    label = {
        "completion_ts_ms": 121., "completion_evidence": "whole_tool_request_round",
        "observed_tool_call_count": 1,
    }
    assert eligible_call(calls[0], label, now_ms=15., offset_ms=100.)
    assert not eligible_call(calls[0], {**label, "observed_tool_call_count": 2}, now_ms=15., offset_ms=100.)
    assert not eligible_call(calls[0], {**label, "completion_ts_ms": 123.}, now_ms=15., offset_ms=100.)


def test_reversed_tool_boundary_is_rejected():
    with pytest.raises(ValueError, match="precedes"):
        tool_calls([
            event("tool_start", 10., run="r", tool_name="execute"),
            {**event("tool_end", 11., run="r", status="success"), "ts_ms": 9.},
        ], project="django")


def test_initial_prediction_upper_deadline_does_not_move_with_new_forecasts():
    current = call(
        start=200_000., duration=10_000.,
        workflow_id="w", invocation_id="i", context_id="c", context_epoch=2,
    )
    past = [call(duration=180_000.) for _ in range(4)]
    label = {
        "source": "tool_wait", "workflow_id": "w", "invocation_id": "i",
        "context_id": "c", "context_epoch": 2,
        "completion_evidence": "whole_tool_request_round",
        "observed_tool_call_count": 1, "completion_ts_ms": current["end_ms"],
    }

    def forecast(at, upper):
        return {
            "workflow_id": "w", "invocation_id": "i", "context_id": "c",
            "context_epoch": 2, "ts_ms": at, "observed_monotonic_ms": at,
            "p10_ms": 50., "p50_ms": 100., "p90_ms": upper,
        }

    result = evaluate(past + [current], [label], [
        forecast(200_100., 500.), forecast(200_500., 100_000.),
    ], offset_ms=0., after_initial_p90=True)
    row = result["rows"][0]
    assert row["empirical_frames"] == 0
    assert row["candidate_first_1s_window_actual_lead_ms"] == 9900.
    result = evaluate(past + [current], [label], [
        forecast(200_100., 500.), forecast(200_601., 100_000.),
    ], offset_ms=0., after_initial_p90=True)
    row = result["rows"][0]
    assert row["empirical_frames"] == 1
    assert row["last_pre_completion_frame"]["candidate_ms"] == 179_399.
    assert row["original_p90_deadline_mono_ms"] == 200_600.


@pytest.mark.parametrize("include_roots", (False, True))
def test_root_pilot_is_explicit_and_keeps_role_specific_past_history(include_roots):
    current = call(
        start=200_000., duration=10_000., is_child=False,
        workflow_id="w", invocation_id="i", context_id="c", context_epoch=2,
    )
    label = {
        "source": "tool_wait", "workflow_id": "w", "invocation_id": "i",
        "context_id": "c", "context_epoch": 2,
        "completion_evidence": "whole_tool_request_round",
        "observed_tool_call_count": 1, "completion_ts_ms": current["end_ms"],
    }
    forecasts = [{
        "workflow_id": "w", "invocation_id": "i", "context_id": "c",
        "context_epoch": 2, "ts_ms": 200_100., "observed_monotonic_ms": 200_100.,
        "p10_ms": 50., "p50_ms": 100., "p90_ms": 500.,
    }]
    past = [call(duration=100.) for _ in range(4)]
    root_past = [call(duration=180_000., is_child=False) for _ in range(4)]
    result = evaluate(
        past + root_past + [current], [label], forecasts,
        offset_ms=0., include_roots=include_roots,
    )
    assert len(result["rows"]) == int(include_roots)
    assert result["summary"]["independent_single_execute_child_waits"] == 0
    assert result["summary"]["independent_single_execute_root_waits"] == int(include_roots)
    if include_roots:
        row = result["rows"][0]
        assert row["last_pre_completion_frame"]["support"] == 4
        assert row["last_pre_completion_frame"]["candidate_ms"] == 179_900.
        assert result["by_role"]["root"]["waits_with_empirical_support"] == 1
        missing = evaluate(
            past + [current], [label], forecasts, offset_ms=0., include_roots=True,
        )
        assert missing["rows"][0]["empirical_frames"] == 0
        assert missing["rows"][0]["last_pre_completion_frame"]["candidate_ms"] == 100.
