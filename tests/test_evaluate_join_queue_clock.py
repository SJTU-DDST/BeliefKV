import pytest

from scripts.evaluate_join_queue_clock import (
    fit_queue_clock, predict, summarize,
)


def _row(project, index, queue, lead):
    return {
        "project": project, "task_id": f"{project}__{index}",
        "pressure_bin": "heavy_queue", "queued": float(queue),
        "lead_ms": float(lead),
    }


def test_queue_clock_is_monotone_and_reduces_repeated_tasks():
    train = [
        _row(project, index, 10 + index * 3, 5000 + index * 300)
        for project in ("a", "b", "c")
        for index in range(5)
    ]
    train.extend([
        {**train[0], "lead_ms": 6000.},
        {**train[0], "lead_ms": 5500.},
    ])
    model = fit_queue_clock(train)
    assert model is not None
    intercept, slope = model
    assert intercept >= 0
    assert slope == pytest.approx(100)
    assert fit_queue_clock(train[:5]) is None


def test_queue_clock_fails_closed_on_heldout_project_overlap():
    train = [_row("a", i, 20 + i, 2000) for i in range(10)]
    with pytest.raises(ValueError, match="held-out"):
        predict(train, [_row("a", 11, 40, 6000)])


def test_summary_clusters_replicated_task_in_bootstrap():
    rows = [{
        **_row(project, 0, 20, 4000),
        "selective_eta_ms": 6000.,
        "queue_clock_eta_ms": 4100.,
        "queue_clock_applied": True,
    } for project in ("a", "b", "c", "d", "e")]
    rows.extend({**rows[0]} for _ in range(4))
    report = summarize(rows)
    assert report["events"] == 9
    assert report["distinct_tasks"] == 5
    assert report["queue_clock_within_500ms"] == 9
