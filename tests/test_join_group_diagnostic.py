from beliefkv.experiments.join_group_diagnostic import diagnose_join_groups
from beliefkv.predictor.structured_frontier import EmpiricalDistribution, LocalFrontierPrediction


class StubModel:
    def predict(self, features):
        values = {"a": (20.0, 40.0, 60.0), "b": (50.0, 100.0, 150.0)}
        return LocalFrontierPrediction(
            invocation_id=features.invocation_id,
            boundary_distribution={},
            current_sequence_tokens=0,
            remaining_decode_tokens=EmpiricalDistribution.empty(),
            remaining_external_wait=EmpiricalDistribution.empty(),
            tool_terminal_distribution={},
            prompt_growth_tokens=EmpiricalDistribution.empty(),
            next_output_tokens=EmpiricalDistribution.empty(),
            support_level="backoff",
            calibration_coverage=0.9,
            remaining_to_return_ms=EmpiricalDistribution(
                values[features.invocation_id], (0.2, 0.4, 0.4), 10.0
            ),
        )


def _reentry():
    return {
        "reentry_kind": "join", "training_eligible": True,
        "terminal_status": "satisfied", "workflow_id": "wf",
        "invocation_id": "parent", "reentry_id": "j",
        "reentry_ts_ms": 120.0, "wait_start_ts_ms": 0.0,
        "member_outcomes": [
            {"invocation_id": "a", "return_ts_ms": 60.0},
            {"invocation_id": "b", "return_ts_ms": 120.0},
        ],
    }


def _decision(children=("a", "b")):
    return {
        "workflow_id": "wf", "timestamp_ms": 10.0,
        "invocations": [
            {"invocation_id": "parent", "state": "wait_join"},
            *({"invocation_id": child, "state": "running_llm"} for child in children),
        ],
    }


def test_join_group_uses_latest_pending_child_not_average():
    result = diagnose_join_groups(StubModel(), [_decision()], [_reentry()])
    assert result["counts"]["groups_with_timing_hint"] == 1
    assert result["mean_absolute_error_ms"] == 10.0
    assert result["marginal_envelope_coverage"] == 1.0
    assert "NOT a calibrated" in result["semantics"]


def test_incomplete_snapshot_and_non_all_compatible_not_counted():
    incomplete = diagnose_join_groups(StubModel(), [_decision(("a",))], [_reentry()])
    assert incomplete["counts"]["missing_pending_child"] == 1
    assert incomplete["mean_absolute_error_ms"] is None
    different = dict(_reentry(), reentry_ts_ms=80.0)
    excluded = diagnose_join_groups(StubModel(), [_decision()], [different])
    assert excluded["counts"]["not_all_compatible"] == 1


def test_join_horizons_count_events_not_decision_rows():
    decisions = [
        _decision(),
        {**_decision(), "timestamp_ms": 115.0},
        {**_decision(), "timestamp_ms": 119.5},
    ]
    result = diagnose_join_groups(
        StubModel(), decisions, [_reentry()], horizons_ms=(5, 1)
    )
    assert result["counts"]["groups_with_timing_hint"] == 1
    assert result["by_horizon_ms"]["5"]["groups_with_timing_hint"] == 1
    assert result["by_horizon_ms"]["1"]["groups_with_timing_hint"] == 1
    assert result["by_horizon_ms"]["5"]["median_absolute_error_ms"] == 95.0
    assert result["by_horizon_ms"]["1"]["median_absolute_error_ms"] == 99.5


def test_join_horizon_reports_absent_late_decision():
    result = diagnose_join_groups(
        StubModel(), [_decision()], [_reentry()], horizons_ms=(1,)
    )
    assert result["by_horizon_ms"]["1"]["join_group_coverage"] == 0
    assert result["by_horizon_ms"]["1"]["median_absolute_error_ms"] is None


def test_join_trigger_uses_first_forecast_crossing_not_hindsight_snapshot():
    decisions = [
        _decision(),
        {**_decision(), "timestamp_ms": 115.0},
        {**_decision(), "timestamp_ms": 119.5},
    ]
    result = diagnose_join_groups(
        StubModel(), decisions, [_reentry()],
        trigger_windows_ms=(25, 50, 200),
    )["online_like_trigger"]["by_window_ms"]
    assert result["25"]["never_triggered_groups"] == 1
    assert result["50"]["triggered_groups"] == 1
    assert result["50"]["median_observed_lead_ms"] == 110.0
    assert result["50"]["lead_above_window"] == 1
    assert result["200"]["lead_above_window"] == 0
    assert result["200"]["lead_below_500ms"] == 1


def test_join_trigger_requires_complete_child_snapshot():
    result = diagnose_join_groups(
        StubModel(), [_decision(("a",))], [_reentry()],
        trigger_windows_ms=(100,),
    )["online_like_trigger"]
    assert result["by_window_ms"]["100"]["triggered_groups"] == 0
    assert result["counts"]["groups_with_no_evaluable_snapshot"] == 1
