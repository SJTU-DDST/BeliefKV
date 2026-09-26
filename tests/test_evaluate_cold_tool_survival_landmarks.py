from __future__ import annotations

import sys

import pytest

from scripts import evaluate_cold_tool_survival_landmarks as survival
from scripts.evaluate_cold_tool_survival_landmarks import (
    _online_project_predictions, _scheduled_window, evaluate,
)


def _row(project, workflow, shape, duration):
    return {
        "project": project, "workflow": workflow,
        "shape": shape, "duration_ms": duration,
    }


def test_survival_landmarks_use_training_durations_and_actual_survivors_only():
    train = [
        _row("train", f"wf-{i % 3}", "test_suite_targeted", 2500)
        for i in range(9)
    ] + [
        _row("train", "short", "test_suite_targeted", 300)
        for _ in range(15)
    ]
    heldout = [
        _row("heldout", "h1", "test_suite_targeted", 2600),
        _row("heldout", "h2", "test_suite_targeted", 1900),
        _row("heldout", "h3", "python_inline_simple", 2800),
        _row("heldout", "h4", "test_suite_targeted", 250),
    ]
    result = evaluate(train, heldout)
    half_second = result["landmarks"]["500"]
    assert half_second["train_survivors"] == 9
    assert half_second["shape_supported"] == {"test_suite_targeted": 9}
    assert half_second["heldout"]["heldout"]["survivors"] == 3
    assert half_second["heldout"]["heldout"]["p50_error_ms"] == 300
    assert half_second["heldout"]["heldout"]["actual_500ms_lead_count"] == 3
    assert result["landmarks"]["2000"]["heldout"]["heldout"]["survivors"] == 2
    assert result["landmarks"]["2000"]["heldout"]["heldout"]["actual_500ms_lead_count"] == 2


def test_landmark_training_and_heldout_cannot_share_project():
    with pytest.raises(ValueError, match="disjoint"):
        evaluate([_row("p", "a", "x", 3000)], [_row("p", "b", "x", 3500)])


def test_online_project_history_uses_only_finished_distinct_workflows():
    prior = {"global": 2500., "shape": {"x": 2500.}}
    calls = [
        {
            **_row("heldout", f"wf-{i % 3}", "x", 4000),
            "start_ts_ms": i * 5000,
            "terminal_ts_ms": i * 5000 + 4000,
        }
        for i in range(4)
    ]
    calls += [
        {
            **_row("heldout", "early", "x", 4100),
            "start_ts_ms": 18500., "terminal_ts_ms": 22600.,
        },
        {
            **_row("heldout", "later", "x", 4200),
            "start_ts_ms": 20500., "terminal_ts_ms": 24700.,
        },
    ]
    predictions, supported = _online_project_predictions(calls, 500, prior)

    assert supported == [5]
    assert predictions[-2] == 2500.
    assert predictions[-1] == 4000.


def test_online_project_history_rejects_missing_timestamps():
    with pytest.raises(ValueError, match="timestamps"):
        _online_project_predictions(
            [_row("heldout", "wf", "x", 3000)], 500,
            {"global": 2500., "shape": {}},
        )


def test_online_project_history_evicts_old_workflow_support():
    past = [
        {
            **_row("heldout", f"older-{i}", "x", 4000.),
            "start_ts_ms": float(i * 5000),
            "terminal_ts_ms": float(i * 5000 + 4000),
        }
        for i in range(4)
    ]
    recent = [
        {
            **_row("heldout", "one-workflow", "x", 4000.),
            "start_ts_ms": float(20000 + i * 5000),
            "terminal_ts_ms": float(24000 + i * 5000),
        }
        for i in range(64)
    ]
    current = {
        **_row("heldout", "current", "x", 4200.),
        "start_ts_ms": 340000.,
        "terminal_ts_ms": 344200.,
    }
    predicted, supported = _online_project_predictions(
        past + recent + [current], 500,
        {"global": 2500., "shape": {"x": 2500.}},
    )

    assert len(supported) > 0
    assert len(past + recent) not in supported
    assert predicted[-1] == 2500.


def test_scheduled_window_censors_return_before_predicted_trigger():
    survivors = [
        _row("p", "early", "x", 700),
        _row("p", "useful", "x", 3200),
        _row("p", "too_late", "x", 3100),
    ]
    window = _scheduled_window(
        survivors, [3000., 3000., 1200.], [0, 1, 2], 500,
    )
    assert window["eligible"] == 2
    assert window["actual_return_before_scheduled"] == 1
    assert window["actual_lead_at_least_500ms"] == 1


def test_survival_cli_rejects_unfinished_batches(tmp_path, monkeypatch):
    output = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", [
        "evaluate_cold_tool_survival_landmarks.py",
        "--train-workflows", str(tmp_path / "train" / "workflows"),
        "--heldout-workflows", str(tmp_path / "heldout" / "workflows"),
        "--output", str(output),
    ])
    with pytest.raises(ValueError, match="final manifest and summary"):
        survival.main()
    assert not output.exists()
