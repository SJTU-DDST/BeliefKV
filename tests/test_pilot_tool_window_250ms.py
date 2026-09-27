import numpy as np
import pytest

from scripts.pilot_tool_window_250ms import (
    first_inputs, quality, select_threshold,
)


def _row(project, index, *, score=.75, duration=900):
    return {
        "project": project,
        "workflow": f"{project}-{index}",
        "invocation": f"{project}-{index}-child",
        "input_sha256": f"{project}-{index}-input",
        "tool_call_id": f"{project}-{index}-call",
        "start_ts_ms": float(index * 2000),
        "duration_ms": float(duration),
        "score": score,
        "status": "success",
    }


def test_short_first_input_is_not_replaced_by_long_retry():
    first = _row("django", 0, duration=150)
    second = {
        **first, "tool_call_id": "retry",
        "start_ts_ms": 1000., "duration_ms": 1200.,
    }
    independent = _row("django", 1, duration=800)
    assert first_inputs([second, independent, first]) == [independent]


def test_training_threshold_requires_three_supported_projects():
    projects = ("django", "pydata", "pytest-dev")
    rows = [
        _row(project, index) for project in projects
        for index in range(12)
    ]
    selected, report = select_threshold(rows)
    assert selected == .7
    assert report["0.7"]["selected"] == 36
    bad = [
        {**row, "duration_ms": 100.}
        if (
            row["project"] == "django"
            and int(row["tool_call_id"].split("-")[-2]) < 5
        ) else row
        for row in rows
    ]
    assert select_threshold(bad)[0] is None


def test_quality_counts_real_lead_and_eta_error_on_same_calls():
    rows = [_row("astropy", index) for index in range(5)]
    result = quality(
        rows, np.asarray([.8] * 5), threshold=.7,
        eta=[910.] * 5, prior=1300.,
    )
    assert result["selected"]["true_windows"] == 5
    assert result["actual_remaining_at_250ms_p50_ms"] == 650
    assert result["selected_eta_error_p50_ms"] == 10
    assert result["true_window_eta_gain_vs_global"][
        "paired_positive_95pct"
    ]
    with pytest.raises(ValueError, match="identities differ"):
        quality(rows, np.asarray([.8]), threshold=.7, eta=[], prior=900.)
