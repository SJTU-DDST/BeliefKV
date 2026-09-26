from __future__ import annotations

import pytest

from scripts.evaluate_cold_tool_survival_landmarks import evaluate


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
