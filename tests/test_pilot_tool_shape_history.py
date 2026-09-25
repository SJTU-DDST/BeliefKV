from scripts.pilot_tool_shape_history import replay, stable_long_prior


def _row(index: int, duration: int, *, shape: str = "python_inline_test"):
    start = index * 4000
    return {
        "project": "repo",
        "workflow": f"wf-{index}",
        "class": "python_inline",
        "shape": shape,
        "is_child": True,
        "previous": None,
        "input_chars": 100,
        "start_ts_ms": start,
        "terminal_ts_ms": start + duration,
        "duration_ms": duration,
        "status": "success",
    }


def test_shape_prior_needs_completed_stable_long_history():
    target = _row(4, 3200)
    history = [_row(index, 3000 + (index % 2) * 100)
               for index in range(4)]
    assert stable_long_prior(target, history) == 3050
    assert stable_long_prior(target, history[:3]) is None
    overlapping = _row(3, 5000)
    assert stable_long_prior(target, history[:3] + [overlapping]) is None
    assert stable_long_prior(
        target, [history[0], _row(1, 8000), history[2], history[3]]
    ) is None
    assert stable_long_prior(
        {**target, "shape": "unknown"}, history
    ) is None


def test_shape_replay_reports_selective_precision_without_future_data():
    rows = [_row(index, 3000 + (index % 2) * 100)
            for index in range(4)]
    rows.append(_row(4, 3100))
    result = replay(rows)
    assert result["cold_child_calls"] == 5
    assert result["actual_cold_long"] == 5
    assert result["selected_predicted_long"] == 1
    assert result["selected_true_long"] == 1
    assert result["shape_timing"]["p50_error_ms"] == 50
    assert result["long_precision"] == 1
    assert result["selected_false_long"] == 0
    assert result["selected_true_long_timing"]["p50_error_ms"] == 50
    assert result["pre_registered_acceptance"]["accepted"] is False
    assert result["pre_registered_acceptance"]["checks"][
        "at_least_30_actual_long"
    ] is False
    assert result["scheduling_windows_zero_overhead_upper_bound"][
        "desired_lead_500ms"
    ]["at_least_500ms_before_end"] == 1


def test_shape_replay_counts_short_false_positives_and_late_tickets():
    rows = [_row(index, 3000 + (index % 2) * 100)
            for index in range(4)]
    rows.extend((_row(4, 3150), _row(5, 250)))
    result = replay(rows)
    assert result["selected_predicted_long"] == 2
    assert result["selected_false_long"] == 1
    assert result["actual_cold_long"] == 5
    assert result["long_precision"] == .5
    assert result["long_recall"] == .2
    assert result["scheduling_windows_zero_overhead_upper_bound"][
        "desired_lead_500ms"
    ]["after_tool_end"] == 1


def test_equal_input_sizes_do_not_choose_shortest_durations_in_recency_ablation():
    history = [_row(index, 100) for index in range(4)]
    history.extend(_row(index, 3000) for index in range(4, 12))
    target = _row(12, 3050)
    assert stable_long_prior(target, history) is None
    assert stable_long_prior(target, history, recent_ties=True) == 3000
    result = replay([*history, target])
    assert result["selected_predicted_long"] == 0
    assert result["recency_tie_ablation_read_only"]["selected_predicted_long"] == 1
    assert result["recency_tie_ablation_read_only"]["timing"]["p50_error_ms"] == 50
