from __future__ import annotations

import pytest

from scripts.pilot_join_project_online import (
    causal_project_predictions, project_leave_one_out,
)


def _row(task: str, signal: float, lead: float) -> dict:
    return {
        "task_id": task,
        "project": task.split("__", 1)[0],
        "signal_ts_ms": signal,
        "join_lead_ms": lead,
        "parent_lead_ms": lead + 100.,
        "group_label": "natural",
        "label": "true",
    }


def test_project_history_uses_only_strictly_completed_joins():
    rows = [
        _row(f"django__{index}", index * 4000., 3000.)
        for index in range(3)
    ] + [
        _row("django__three", 12000., 4000.),
        _row("django__same_timestamp", 16000., 3000.),
        _row("django__later", 17000., 3000.),
    ]
    selected = causal_project_predictions(rows, "join_lead_ms")
    assert [row["task_id"] for row, _ in selected] == ["django__later"]
    assert selected[0][1] == 3000.
    with pytest.raises(ValueError, match="unsupported"):
        causal_project_predictions(rows, "tool_end_ms")


def test_train_only_loo_reports_projects_with_zero_candidate():
    rows = [_row("django__one", 10000., 3000.)]
    frozen_ids = ["django__one", "astropy__two", "psf__three"]
    report = project_leave_one_out({
        1024: rows, 1700: rows,
    }, frozen_ids)
    assert report["thresholds"]["1024"]["join_lead_ms"]["astropy"][
        "natural_candidates"
    ] == 0
    assert report["thresholds"]["1024"]["join_lead_ms"]["django"][
        "online_supported"
    ] == 0
    with pytest.raises(ValueError, match="not in frozen"):
        project_leave_one_out({
            1024: rows + [_row("pydata__unexpected", 20000., 3000.)],
            1700: rows,
        }, frozen_ids)
