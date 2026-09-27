from __future__ import annotations

import json
import sys

import pytest

from scripts import evaluate_cold_tool_survival_landmarks as survival
from scripts.evaluate_cold_tool_survival_landmarks import (
    _online_project_predictions, _scheduled_window, evaluate,
)


def _row(project, workflow, shape, duration):
    return {
        "project": project, "workflow": workflow,
        "shape": shape, "duration_ms": duration, "status": "success",
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
    assert result["landmarks"]["100"]["heldout"]["heldout"]["survivors"] == 4
    assert result["landmarks"]["250"]["heldout"]["heldout"]["survivors"] == 3
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


def test_landmark_history_is_frozen_at_tool_start_not_updated_by_peer_return():
    prior = {"global": 2500., "shape": {"x": 2500.}}
    past = [
        {
            **_row("heldout", f"prior-{i}", "x", 600.),
            "start_ts_ms": float(i * 1000),
            "terminal_ts_ms": float(i * 1000 + 600),
        }
        for i in range(3)
    ]
    peer = {
        **_row("heldout", "peer", "x", 800.),
        "start_ts_ms": 3000., "terminal_ts_ms": 3800.,
    }
    target = {
        **_row("heldout", "target", "x", 2600.),
        "start_ts_ms": 3600., "terminal_ts_ms": 6200.,
    }
    predictions, supported = _online_project_predictions(
        past + [peer, target], 500, prior,
    )
    assert len(past) + 1 not in supported
    assert predictions[-1] == 2500.


def test_lower_duration_quantile_uses_only_past_successful_shape_calls():
    prior = {"global": 2500., "shape": {"x": 2500.}}
    durations = [1000., 1200., 1400., 5000.]
    calls = [
        {
            **_row("heldout", f"wf-{i}", "x", duration),
            "start_ts_ms": float(i * 6000),
            "terminal_ts_ms": float(i * 6000 + duration),
        }
        for i, duration in enumerate(durations)
    ]
    calls.append({
        **_row("heldout", "target", "x", 2500.),
        "start_ts_ms": 24000., "terminal_ts_ms": 26500.,
    })
    predictions, supported = _online_project_predictions(
        calls, 100, prior, shape_quantile=.25,
    )
    assert supported == [4]
    assert predictions[-1] == 1150.
    with pytest.raises(ValueError, match="shape quantile"):
        _online_project_predictions(calls, 100, prior, shape_quantile=0.)


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


def test_returned_errors_add_causal_support_without_looking_ahead():
    prior = {"global": 2500., "shape": {"x": 2500.}}
    calls = [
        {
            **_row("heldout", f"wf-{i}", "x", 3000),
            "status": "error", "start_ts_ms": float(i * 5000),
            "terminal_ts_ms": float(i * 5000 + 3000),
        }
        for i in range(4)
    ]
    calls.append({
        **_row("heldout", "target", "x", 3100),
        "status": "success", "start_ts_ms": 20000.,
        "terminal_ts_ms": 23100.,
    })
    returned, returned_support = _online_project_predictions(
        calls, 500, prior, success_only_history=False,
    )
    success, success_support = _online_project_predictions(
        calls, 500, prior,
    )
    assert returned_support == [4]
    assert returned[-1] == 3000.
    assert success_support == []
    assert success[-1] == 2500.


def test_history_ablation_compares_identical_survivors():
    train = [
        _row("train", f"train-{i}", "x", 2500) for i in range(9)
    ]
    heldout = [
        {
            **_row("heldout", f"wf-{i}", "x", 3000),
            "status": "error" if i < 4 else "success",
            "start_ts_ms": float(i * 5000),
            "terminal_ts_ms": float(i * 5000 + 3000),
        }
        for i in range(9)
    ]
    result = evaluate(
        train, heldout, online_project_history=True,
        compare_success_history=True,
    )
    assert result["online_history_snapshot"] == "tool_start_success_only"
    assert result["landmarks"]["500"]["heldout_online_project"]["heldout"][
        "online_project_shape_supported"
    ] == 1
    ablation = result["landmarks"]["500"][
        "heldout_online_project"]["heldout"]["returned_failure_history_ablation"]
    assert ablation["returned_history_supported"] == 5
    assert ablation["success_history_supported"] == 1
    assert ablation["common_supported"] == 1
    assert ablation["newly_supported"]["survivors"] == 4
    assert ablation["newly_supported_frozen"]["survivors"] == 4
    assert ablation["lost_success_support"]["survivors"] == 0
    assert ablation["success_preserving_fallback"]["survivors"] == 5


def test_returned_errors_can_evict_success_support_in_shared_window():
    prior = {"global": 2500., "shape": {"x": 2500.}}
    calls = [
        {
            **_row("heldout", f"success-{i}", "x", 3000),
            "status": "success", "start_ts_ms": float(i * 5000),
            "terminal_ts_ms": float(i * 5000 + 3000),
        }
        for i in range(4)
    ] + [
        {
            **_row("heldout", "repeated-error", "x", 3000),
            "status": "error", "start_ts_ms": float((i + 4) * 5000),
            "terminal_ts_ms": float((i + 4) * 5000 + 3000),
        }
        for i in range(64)
    ] + [{
        **_row("heldout", "target", "x", 3100),
        "status": "success", "start_ts_ms": 340000.,
        "terminal_ts_ms": 343100.,
    }]
    _, returned_support = _online_project_predictions(
        calls, 500, prior, success_only_history=False,
    )
    _, success_support = _online_project_predictions(
        calls, 500, prior, success_only_history=True,
    )
    assert len(calls) - 1 not in returned_support
    assert len(calls) - 1 in success_support


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


def test_survival_cli_applies_returned_failure_scope_to_both_sides(
    tmp_path, monkeypatch,
):
    output = tmp_path / "result.json"
    scopes = []
    monkeypatch.setattr(
        survival, "require_complete_batch",
        lambda _path: (["task__one"], []),
    )
    monkeypatch.setattr(
        survival, "cold_calls",
        lambda _path, *, include_returned_failures: (
            scopes.append(include_returned_failures) or [],
            {"included_returned_error_by_class": {}},
        ),
    )
    monkeypatch.setattr(
        survival, "evaluate",
        lambda _train, _heldout, *, online_project_history, shape_quantile: {
            "online_project_history": online_project_history,
            "shape_quantile": shape_quantile,
        },
    )
    monkeypatch.setattr(sys, "argv", [
        "evaluate_cold_tool_survival_landmarks.py",
        "--train-workflows", str(tmp_path / "train" / "workflows"),
        "--heldout-workflows", str(tmp_path / "heldout" / "workflows"),
        "--output", str(output),
        "--include-returned-failures",
    ])
    survival.main()

    report = json.loads(output.read_text(encoding="utf-8"))
    assert scopes == [True, True]
    assert report["shape_quantile"] == .5
    assert report["include_returned_failures"] is True
    assert report["train_frozen_workflows"] == 1
    assert report["heldout_frozen_workflows"] == 1
