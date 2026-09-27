import pytest

from scripts.pilot_tool_return_window_100ms import (
    _quality, evaluate, first_inputs, score_disjoint_heldout,
)


def _row(project, workflow, index, duration, *, status="success", sha=None):
    return {
        "project": project,
        "workflow": workflow,
        "invocation": f"{workflow}-child",
        "tool_call_id": f"{workflow}-{index}",
        "input_sha256": sha or f"{workflow}-{index}",
        "start_ts_ms": index * 3000.,
        "duration_ms": float(duration),
        "status": status,
        "shape": "python_inline_complex" if index % 2 else "test_suite_targeted",
        "input_chars": 120 if index % 2 else 220,
        "project_class_completed_support": 4,
        "other_workflow_2s_peers": 1,
        "project_class_duration_median_ms": 820.,
        "project_input_neighbor_duration_ms": 750.,
        "project_input_neighbor_support": 4,
    }


def test_first_input_excludes_fast_first_error_and_repeated_failed_input():
    rows = [
        _row("one", "one-0", 0, 60, status="error", sha="repeat"),
        _row("one", "one-0", 1, 800, status="success", sha="repeat"),
        _row("one", "one-0", 2, 700, status="error", sha="different"),
        _row("one", "one-0", 3, 1200, status="success", sha="different"),
    ]
    result = first_inputs(rows)
    assert len(result) == 1
    assert result[0]["status"] == "error"
    assert result[0]["duration_ms"] == 700


def test_window_quality_preserves_failed_returns_as_predictions():
    rows = [
        _row("one", "one-0", 0, 800, status="success"),
        _row("one", "one-1", 1, 200, status="error"),
    ]
    report = _quality(
        rows, [0.8, 0.9], .7, [750., 750.], 650., [790., 800.],
    )
    assert report["positive_500ms_windows"] == 1
    assert report["true_windows"] == 1
    assert report["false_windows"] == 1
    assert report["success"] == {"selected": 1, "true_windows": 1}
    assert report["error"] == {"selected": 1, "true_windows": 0}
    assert report["regression_eta_p50_absolute_error_ms"] == 10


def test_training_loo_keeps_projects_separate_and_has_no_action_claim():
    rows = [
        _row(project, f"{project}-{i % 6}", i, 180 if i % 3 == 0 else 850)
        for project in ("django", "pydata", "pytest-dev")
        for i in range(54)
    ]
    report = evaluate(rows)
    assert report["completed_cold_tools"] == 162
    assert report["survived_100ms_distinct_inputs"] == 162
    assert report["positive_500ms_windows"] == 108
    for arm in report["arms"].values():
        for project, fold in arm["by_project"].items():
            assert project not in fold["frozen_train_projects"]
            assert fold["survivors"] == 54
            assert fold["positive_500ms_windows"] == 36
        assert report["status"] == (
            "train_only_project_loo_100ms_window_screen_not_online"
        )
    with pytest.raises(ValueError, match="three or more"):
        evaluate(rows[:54])


def test_frozen_holdout_refuses_same_project_even_when_threshold_missing():
    train = [_row("django", "d-0", 0, 850)]
    holdout = [_row("django", "d-1", 0, 850)]
    training = {
        "arms": {"shape_size_peers_history": {
            "exploratory_training_threshold": None,
        }},
    }
    with pytest.raises(ValueError, match="disjoint"):
        score_disjoint_heldout(train, holdout, training)
    holdout[0]["project"] = "sphinx"
    report = score_disjoint_heldout(train, holdout, training)
    assert report["status"] == "no_training_threshold_heldout_not_scored"
