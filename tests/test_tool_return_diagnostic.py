from beliefkv.experiments.tool_return_diagnostic import diagnose_tool_returns
from beliefkv.predictor.structured_frontier import (
    EmpiricalDistribution,
    LocalFrontierPrediction,
    WaitBelief,
    WaitBeliefKind,
)


class StubToolModel:
    def predict(self, features):
        dist = EmpiricalDistribution((2.0, 10.0, 20.0), (0.2, 0.5, 0.3), 10)
        return LocalFrontierPrediction(
            invocation_id=features.invocation_id,
            boundary_distribution={},
            current_sequence_tokens=0,
            remaining_decode_tokens=EmpiricalDistribution.empty(),
            remaining_external_wait=dist,
            tool_terminal_distribution={"success": 1.0},
            prompt_growth_tokens=EmpiricalDistribution.empty(),
            next_output_tokens=EmpiricalDistribution.empty(),
            support_level="backoff",
            calibration_coverage=.9,
            wait_belief=WaitBelief(
                kind=WaitBeliefKind.TOOL,
                residual_duration=dist,
                support_level="backoff",
            ),
        )


def decision(ts):
    return {
        "workflow_id": "wf", "timestamp_ms": ts,
        "invocations": [{"invocation_id": "child", "state": "wait_tool"}],
        "labels": [{
            "invocation_id": "child",
            "target_training_eligible": {"external_wait": True},
        }],
    }


def tool(**overrides):
    return {
        "workflow_id": "wf", "invocation_id": "child", "tool_call_id": "call",
        "start_ts_ms": 10, "terminal_ts_ms": 120,
        "censored": False, "training_eligible_survival": True,
        **overrides,
    }


def test_one_tool_episode_at_each_horizon():
    result = diagnose_tool_returns(
        StubToolModel(),
        [decision(10), decision(115), decision(119)],
        [tool()],
        horizons_ms=(5, 1),
    )
    assert result["completed_episode_count"] == 1
    assert result["first_snapshot"]["median_absolute_error_ms"] == 100
    assert result["first_snapshot"]["zero_baseline_mean_absolute_error_ms"] == 110
    assert result["by_horizon_ms"]["5"]["median_absolute_error_ms"] == 5
    assert result["by_horizon_ms"]["1"]["episode_count"] == 1


def test_censored_tools_and_missing_late_snapshot_not_synthetic():
    result = diagnose_tool_returns(
        StubToolModel(), [decision(10)], [tool()],
        horizons_ms=(1,),
    )
    assert result["by_horizon_ms"]["1"]["episode_coverage"] == 0
    censored = diagnose_tool_returns(
        StubToolModel(), [decision(10)],
        [tool(censored=True)], horizons_ms=(1,),
    )
    assert censored["completed_episode_count"] == 0
    assert censored["exclusions"]["censored_or_ineligible_tool"] == 1


def test_tool_trigger_reports_first_eligible_crossing_and_long_wait():
    result = diagnose_tool_returns(
        StubToolModel(),
        [decision(10), decision(115), decision(119)],
        [tool()],
        trigger_windows_ms=(1, 2, 200),
    )["online_like_trigger"]["by_window_ms"]
    assert result["1"]["never_triggered_episodes"] == 1
    assert result["2"]["triggered_episodes"] == 1
    assert result["2"]["median_observed_lead_ms"] == 110
    assert result["2"]["lead_above_window"] == 1
    assert result["2"]["lead_within_window_and_at_least_500ms"] == 0
    assert result["200"]["lead_above_window"] == 0
