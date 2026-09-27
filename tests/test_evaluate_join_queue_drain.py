import pytest

from scripts.evaluate_join_queue_drain import (
    attach_drain, choose_weight, fit_residual, forecast, summarize,
)


def _row(project, index, queue, lead, rate):
    return {
        "project": project, "task_id": f"{project}__{index}",
        "pressure_bin": "heavy_queue", "queued": float(queue),
        "lead_ms": float(lead), "prior_queue_drain_qps": rate,
    }


def test_drain_uses_only_pre_notice_metrics():
    rows = [{
        **_row("heldout", 0, 20, 5000, None),
        "trigger_ts_ms": 40_000., "metric_age_ms": 100.,
    }]
    metrics = [
        {"monotonic_ts_ms": 19_000., "num_queue_reqs": 60.},
        {"monotonic_ts_ms": 39_900., "num_queue_reqs": 20.},
        {"monotonic_ts_ms": 40_000., "num_queue_reqs": 0.},
    ]
    result = attach_drain(rows, metrics)
    assert result[0]["prior_queue_drain_qps"] == pytest.approx(40 / 20.9)
    metrics[0]["num_queue_reqs"] = 10.
    assert attach_drain(rows, metrics)[0]["prior_queue_drain_qps"] is None
    with pytest.raises(ValueError, match="ordered"):
        attach_drain(rows, list(reversed(metrics)))


def test_project_separation_and_nested_weight_selection():
    train = [
        _row(project, i, 15 + 4 * i, (15 + 4 * i) * 500, 2.)
        for project in ("a", "b", "c", "d", "e")
        for i in range(12)
    ]
    assert fit_residual(train) == pytest.approx(0.)
    selected, gains = choose_weight(train)
    assert selected in (0., .25, .5, .75, 1.)
    assert len(gains) == 5
    heldout = [_row("heldout", 1, 30, 15_000, 2.)]
    prediction = forecast(train, heldout, 1.)
    assert prediction[0]["drain_clock_applied"] is True
    assert forecast(train, heldout, 0.)[0]["drain_clock_applied"] is False
    assert summarize(prediction)["events"] == 1
    with pytest.raises(ValueError, match="held-out"):
        forecast(train, [train[0]], selected)
