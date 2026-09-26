from __future__ import annotations

import pytest

from scripts.evaluate_cold_tool_survival_project_loo import (
    project_leave_one_out,
)


def _row(project: str, index: int) -> dict:
    duration = 2500.
    start = float(index * 4000)
    return {
        "project": project,
        "workflow": f"{project}-{index % 3}",
        "shape": "test_suite_targeted",
        "duration_ms": duration,
        "start_ts_ms": start,
        "terminal_ts_ms": start + duration,
    }


def test_project_loo_freezes_other_project_prior_and_reports_online_arm():
    rows = [
        _row(project, index)
        for project in ("django", "pydata", "pytest-dev")
        for index in range(9)
    ]
    result = project_leave_one_out(rows)

    assert result["projects"] == ["django", "pydata", "pytest-dev"]
    for project, report in result["folds"].items():
        assert report["train_projects"] == sorted(
            set(result["projects"]) - {project}
        )
        assert report["heldout_projects"] == [project]
        assert report["online_project_history"] is True
        assert report["landmarks"]["500"]["heldout"][project]["survivors"] == 9
        assert report["landmarks"]["500"]["heldout_online_project"][project][
            "online_project_shape_supported"
        ] == 5
        online = report["landmarks"]["500"]["heldout_online_project"][project]
        assert online["online_selected"]["adapted"]["survivors"] == 5
        assert online["online_selected"]["frozen"]["survivors"] == 5
        assert online["online_selected"]["paired_gain"]["status"] == (
            "insufficient_independent_workflows"
        )
        assert online["scheduled_window"]["eligible"] == 5
        assert online["scheduled_window"]["actual_lead_at_least_500ms"] == 5


def test_project_loo_requires_multiple_projects():
    with pytest.raises(ValueError, match="three projects"):
        project_leave_one_out([_row("one", 0), _row("two", 0)])
