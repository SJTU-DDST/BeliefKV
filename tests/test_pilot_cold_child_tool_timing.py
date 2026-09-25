import numpy as np
from types import SimpleNamespace

from scripts.pilot_cold_child_tool_timing import (
    _as_of_project_signals, _cold, _evaluate, _fixed_clock,
    _inflight_peers, _threshold,
)
from beliefkv.predictor.structured_frontier import (
    EmpiricalDistribution, LocalFrontierFeatures, WaitBelief, WaitBeliefKind,
)


def test_cold_filters_project_and_exact_history():
    attrs = {"tool_name": "execute", "is_child": True}
    assert _cold(attrs)
    assert _cold({**attrs, "tool_name": "glob"})
    assert _cold({
        **attrs, "tool_name": "glob",
        "previous_same_input_status": "success",
    })
    assert not _cold({**attrs, "previous_same_input_status": "success"})
    assert not _cold({
        **attrs, "project_class_duration_median_ms": 100,
        "project_class_completed_support": 16,
    })
    assert _cold({
        **attrs, "project_class_duration_median_ms": 100,
        "project_class_completed_support": 3,
    })


def test_threshold_uses_only_supplied_development_labels():
    scores = np.asarray([.9, .8, .7, .6, .1])
    labels = np.asarray([1, 1, 1, 0, 0])
    assert _threshold(scores, labels, min_precision=.8, min_positive=2) == .7
    assert _threshold(scores, np.zeros(5), min_positive=2) is None


def test_evaluate_accounts_for_false_alarms_and_long_call_coverage():
    samples = [
        {"workflow": "one", "actual_ms": 3_000, "baseline_ms": 10},
        {"workflow": "two", "actual_ms": 50, "baseline_ms": 10},
    ]
    report = _evaluate(
        samples, np.asarray([.9, .8]), np.asarray([3_000, 3_000]), .8
    )
    assert report["selected"] == 2
    assert report["true_long_selected"] == 1
    assert report["selection_precision"] == .5
    assert report["trigger_quality"]["candidate"]["predicted_long_count"] == 2
    assert report["trigger_quality"]["long_floor"]["predicted_long_count"] == 2


def test_inflight_peer_count_uses_only_other_workflow_live_at_start():
    rows = [
        {"workflow_id": "a", "tool_call_id": "a", "project": "p",
         "observed_command_class": "glob", "start_ts_ms": 0,
         "terminal_ts_ms": 5_000},
        {"workflow_id": "a", "tool_call_id": "b", "project": "p",
         "observed_command_class": "glob", "start_ts_ms": 2_100,
         "terminal_ts_ms": 2_200},
        {"workflow_id": "c", "tool_call_id": "c", "project": "other",
         "observed_command_class": "glob", "start_ts_ms": 2_200,
         "terminal_ts_ms": 3_000},
        {"workflow_id": "b", "tool_call_id": "d", "project": "p",
         "observed_command_class": "glob", "start_ts_ms": 2_300,
         "terminal_ts_ms": 3_000},
        {"workflow_id": "b", "tool_call_id": "e", "project": "p",
         "observed_command_class": "glob", "start_ts_ms": 5_001,
         "terminal_ts_ms": 5_300},
    ]
    counts = _inflight_peers(rows)
    assert counts["a", "b"] == 0
    assert counts["b", "d"] == 1
    assert counts["b", "e"] == 0
    signals = _as_of_project_signals([
        {**rows[0], "status": "success"},
        {**rows[4], "start_ts_ms": 5_001},
    ])
    assert signals["b", "e"]["project_long_completed_median_ms"] == 5_000
    same_timestamp = _as_of_project_signals([
        {**rows[0], "status": "success"},
        {**rows[4], "start_ts_ms": 5_000},
    ])
    assert same_timestamp["b", "e"]["project_long_completed_support"] == 0


def test_fixed_clock_uses_live_elapsed_without_future_duration_as_feature():
    class Model:
        def predict(self, features):
            assert features.elapsed_wait_ms in (500, 2_000, 4_000)
            return SimpleNamespace(wait_belief=WaitBelief(
                kind=WaitBeliefKind.TOOL,
                residual_duration=EmpiricalDistribution((50,), (1,), 1),
            ))

    samples = [{
        "workflow": "w", "total_duration_ms": 4_001,
        "start_features": LocalFrontierFeatures(
            invocation_id="child", state="wait_tool",
        ),
        "long_history_ms": 3_000.,
    }]
    report = _fixed_clock(samples, np.asarray([1.]), .7, Model())
    early = report["by_elapsed_ms"]["500"]
    assert early["alive"] == 1
    assert early["false_imminent_with_over_2s_remaining"] == {
        "reference": 1, "candidate": 0,
    }
    assert early["long"]["candidate"]["p50_absolute_error_ms"] < (
        early["long"]["reference"]["p50_absolute_error_ms"]
    )
