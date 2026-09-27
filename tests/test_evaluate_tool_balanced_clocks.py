import numpy as np
import pytest

from scripts.evaluate_tool_balanced_clocks import summarize, weights


def test_weights_equalize_workflows_and_projects():
    rows = [
        {"project": project, "workflow": workflow}
        for project, workflow, count in (
            ("one", "one-1", 4),
            ("one", "one-2", 2),
            ("two", "two-1", 1),
        )
        for _ in range(count)
    ]
    assert np.allclose(weights(rows, "calls"), 1)
    by_workflow = weights(rows, "workflows")
    assert sum(by_workflow[:4]) == pytest.approx(sum(by_workflow[4:6]))
    by_project = weights(rows, "projects_and_workflows")
    assert sum(by_project[:6]) == pytest.approx(sum(by_project[6:]))
    assert by_project.sum() == pytest.approx(len(rows))
    with pytest.raises(ValueError, match="more than one project"):
        weights(rows + [{"project": "two", "workflow": "one-1"}],
                "workflows")


def test_threshold_scan_uses_same_true_long_calls_and_raw_eta():
    rows = [
        {
            "project": "one", "workflow": f"one-{index}",
            "duration_ms": duration,
            "probability": {mode: score for mode in (
                "calls", "workflows", "projects_and_workflows"
            )},
            "eta_ms": (
                {"calls": 800., "workflows": 850.,
                 "projects_and_workflows": 900.}
                if duration >= 600 else None
            ),
        }
        for index, (duration, score) in enumerate((
            (700., .82), (900., .96), (100., .83),
        ))
    ]
    report = summarize(rows)["modes"]["calls"]
    assert report["selected"] == 3
    assert report["true_windows"] == 2
    assert report["by_threshold"]["0.95"]["selected"] == 1
    assert report["by_threshold"]["0.95"][
        "selected_true_eta_calls_p50_absolute_error_ms"
    ] == 100.
