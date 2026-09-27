from __future__ import annotations

from pathlib import Path

import pytest

from scripts.pilot_join_stream_load import attach_asof, evaluate_rows


def _row(project: str, task: int, *, when: float = 1200.) -> dict:
    return {
        "trace_path": (
            f"/workflows/{project}__{task}/runtime_events.deepagents.jsonl"
        ),
        "trigger_ms": when,
        "lead_ms": float(2000 + task * 50),
        "features": [1., 2., 3., 4., 5.],
    }


def test_asof_load_requires_prior_fresh_valid_snapshot():
    rows = [
        _row("alpha", 1, when=1200.),
        _row("alpha", 2, when=4000.),
        _row("alpha", 3, when=7000.),
    ]
    selected, excluded = attach_asof(rows, [
        {
            "monotonic_ts_ms": 1200., "num_running_reqs": 47,
            "num_queue_reqs": 0,
        },
        {
            "monotonic_ts_ms": 1300., "num_running_reqs": 3,
            "num_queue_reqs": 1,
        },
        {
            "monotonic_ts_ms": 5000., "num_running_reqs": -1,
            "num_queue_reqs": 0,
        },
    ])
    assert selected == []
    assert excluded == {
        "no_recent_prior_snapshot": 2,
        "invalid_snapshot": 1,
    }

    selected, excluded = attach_asof(
        [_row("alpha", 1)], [
            {
                "monotonic_ts_ms": 1199.,
                "num_running_reqs": 8, "num_queue_reqs": 4,
            },
            {
                "monotonic_ts_ms": 1200.,
                "num_running_reqs": 99, "num_queue_reqs": 1,
            },
        ],
    )
    assert excluded == {}
    assert selected[0]["metric_age_ms"] == 1.
    assert selected[0]["load_features"][-2:] == [
        pytest.approx(2.1972245773362196),
        pytest.approx(1.6094379124341003),
    ]


def test_asof_rejects_unordered_timestamps():
    with pytest.raises(ValueError, match="finite and ordered"):
        attach_asof([], [{"monotonic_ts_ms": 100.}, {"monotonic_ts_ms": 10.}])


def test_join_stream_load_scores_heldout_projects_on_identical_rows():
    rows = []
    for project in ("alpha", "beta", "gamma"):
        for index in range(12):
            row = _row(project, index)
            row["load_features"] = [*row["features"], 1., 2.]
            row["metric_age_ms"] = 200.
            rows.append(row)
    report = evaluate_rows(rows)
    assert report["training_projects"] == ["alpha", "beta", "gamma"]
    for project in report["training_projects"]:
        fold = report["folds"][project]
        assert fold["status"] == "scored"
        assert fold["train"] == 24
        assert fold["heldout"] == 12
        assert fold["workflows"] == 12
        assert fold["stream_only"]["natural_returns"] == 12
        assert fold["stream_plus_load"]["natural_returns"] == 12
        assert fold["paired_vs_stream_only"]["workflows"] == 12
    with pytest.raises(ValueError, match="three training projects"):
        evaluate_rows([_row("alpha", 0), _row("beta", 0)])
