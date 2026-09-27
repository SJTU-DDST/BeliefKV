from __future__ import annotations

import numpy as np
import pytest

from scripts.evaluate_tool_live_remaining_loo import (
    evaluate, score, survivor_prior,
)


def _row(project: str, task: str, duration: float, *, shape: str = "test") -> dict:
    return {
        "project": project,
        "task_id": f"{project}__{task}",
        "workflow": f"batch::{project}__{task}",
        "duration_ms": duration,
        "shape": shape,
        "input_chars": 20,
    }


def test_survival_prior_requires_independent_workflows() -> None:
    rows = [_row("a", "one", 900.) for _ in range(10)]
    rows += [_row("a", "two", 1200.) for _ in range(10)]
    prior = survivor_prior(rows)
    assert prior["global"] == 1050
    assert prior["shape"] == {}


def test_score_uses_remaining_at_landmark_and_full_denominator() -> None:
    rows = [_row("a", str(i), duration) for i, duration in enumerate(
        (600., 750., 1600., 4000., 6200.)
    )]
    prior = {"global": 1600., "shape": {"test": 750.}}
    report = score(
        rows, 500, prior, np.asarray([0., 300., 600., 600., 5000.]),
        frozen_total=np.asarray([650., 850., 1500., 3500., 5500.]),
    )
    assert report["survivors"] == 5
    assert report["real_500ms_windows"] == 3
    assert report["landmark_decision"] == {
        "selected": 3,
        "true_500ms_windows": 3,
        "false_500ms_windows": 0,
        "missed_500ms_windows": 0,
        "remaining_over_2000ms": 2,
    }
    assert report["oracle_long_calls"]["calls"] == 5
    assert report["frozen_calls"]["within_500ms"] == 4
    with pytest.raises(ValueError, match="invalid remaining"):
        score(rows, 500, prior, np.asarray([-1., 300., 600., 600., 5000.]))
    with pytest.raises(ValueError, match="alive"):
        score(
            rows + [_row("a", "expired", 500.)],
            500, prior, np.asarray([0., 300., 600., 600., 5000., 0.]),
        )


def test_projects_are_left_out_and_long_landmarks_report_coverage() -> None:
    rows = [
        _row(project, str(i), 250. + 175. * (i % 20))
        for project in ("a", "b", "c")
        for i in range(40)
    ]
    report = evaluate(rows)
    assert report["status"] == "training_only_project_loo_not_action_eligible"
    assert report["landmarks"]["100"]["pooled_survivors"] == 120
    assert report["landmarks"]["2000"]["pooled_survivors"] < 120
    assert set(report["landmarks"]["2000"]["by_project"]) == {"a", "b", "c"}
    rows.append({**rows[0], "project": "b"})
    with pytest.raises(ValueError, match="different projects"):
        evaluate(rows)
