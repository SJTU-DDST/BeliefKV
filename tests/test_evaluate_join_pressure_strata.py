import pytest

from scripts.evaluate_join_pressure_strata import (
    attach_asof, evaluate, forecast, online_forecast, pressure_bin,
    summarize, summarize_online,
)


def _row(project, task, lead, pressure):
    return {
        "project": project, "task_id": f"{project}__{task}",
        "lead_ms": lead, "pressure_bin": pressure,
        "batch": "synthetic_batch", "trigger_ts_ms": 1000.,
    }


def test_asof_never_reads_current_or_future_metric():
    groups = [{
        "label": "natural", "trigger_ts_ms": 1000.,
        "task_id": "heldout__a", "project": "heldout",
        "join_id": "j", "lead_ms": 1400.,
    }]
    metrics = [
        {"monotonic_ts_ms": 999., "num_running_reqs": 4,
         "num_queue_reqs": 0},
        {"monotonic_ts_ms": 1000., "num_running_reqs": 48,
         "num_queue_reqs": 64},
    ]
    rows, skipped = attach_asof(groups, metrics, batch="b")
    assert skipped == {}
    assert rows[0]["pressure_bin"] == "idle"
    assert rows[0]["metric_age_ms"] == 1.
    assert pressure_bin(48, 12) == "heavy_queue"
    with pytest.raises(ValueError, match="ordered"):
        attach_asof(groups, list(reversed(metrics)), batch="b")
    rows, skipped = attach_asof(groups, metrics[1:], batch="b")
    assert not rows
    assert skipped["missing_recent_prior_metric"] == 1
    stale = [{
        **metrics[0], "monotonic_ts_ms": -1001.,
    }]
    rows, skipped = attach_asof(groups, stale, batch="b")
    assert not rows
    assert skipped["missing_recent_prior_metric"] == 1


def test_project_holdout_and_sparse_strata_fallback():
    train = [
        _row(project, i, 4000 + i * 10, "idle")
        for project in ("train1", "train2", "train3")
        for i in range(3)
    ] + [
        _row(project, i + 10, 60_000 + i * 10, "heavy_queue")
        for project in ("train1", "train2", "train3")
        for i in range(3)
    ]
    test = [
        _row("heldout", "idle", 4200, "idle"),
        _row("heldout", "heavy", 62000, "heavy_queue"),
        _row("heldout", "busy", 5000, "busy_no_queue"),
    ]
    rows = forecast(train, test)
    assert [row["prior_source"] for row in rows] == [
        "idle", "heavy_queue", "no_queue",
    ]
    assert rows[0]["pressure_eta_ms"] < rows[1]["pressure_eta_ms"]
    assert summarize(rows)["pressure_within_500ms"] == 1
    with pytest.raises(ValueError, match="held-out"):
        forecast(train, [_row("train1", "leak", 4100, "idle")])


def test_no_single_project_can_supply_pressure_stratum():
    train = [
        _row("train1", i, 5000, "idle") for i in range(10)
    ] + [
        _row(project, i, 60000, "heavy_queue")
        for project in ("train1", "train2", "train3")
        for i in range(5)
    ]
    row, = forecast(train, [_row("heldout", "one", 3000, "idle")])
    assert row["prior_source"] == "global"


def test_heldout_manifest_project_is_rejected_even_without_natural_joins(
    monkeypatch, tmp_path,
):
    def fake_load(path):
        if path.name == "train":
            return [
                _row(project, i, 1000, "idle")
                for project in ("one", "two", "three")
                for i in range(5)
            ], {"frozen_projects": ["one", "two", "three"]}
        return [], {"frozen_projects": ["three"]}

    monkeypatch.setattr(
        "scripts.evaluate_join_pressure_strata.load", fake_load,
    )
    with pytest.raises(ValueError, match="manifests overlap"):
        evaluate([tmp_path / "train"], tmp_path / "heldout")


def test_online_history_uses_only_completed_other_workflows_in_same_batch_and_bin():
    rows = [
        {
            **_row("heldout", name, lead, pressure),
            "batch": batch, "trigger_ts_ms": when,
            "pressure_eta_ms": 3000.,
        }
        for name, when, lead, pressure, batch in [
            ("a", 0, 100, "idle", "b"),
            ("b", 200, 120, "idle", "b"),
            ("c", 400, 140, "idle", "b"),
            ("same_input", 500, 120, "idle", "other_batch"),
            ("other_bin", 500, 120, "heavy_queue", "b"),
            ("future", 600, 2000, "idle", "b"),
            ("candidate", 1000, 150, "idle", "b"),
            ("same_task", 1200, 100, "idle", "b"),
        ]
    ]
    rows[-1]["task_id"] = rows[-2]["task_id"]
    forecast_rows = online_forecast(rows)
    candidate = next(
        row for row in forecast_rows if row["trigger_ts_ms"] == 1000
    )
    assert candidate["causal_same_batch_bin_workflows"] == 3
    assert candidate["online_eta_ms"] == 120
    last = next(row for row in forecast_rows if row["trigger_ts_ms"] == 1200)
    assert last["causal_same_batch_bin_workflows"] == 3
    assert summarize_online(forecast_rows)["with_causal_history"] == 3
    with pytest.raises(ValueError, match="history"):
        online_forecast(rows, min_history=0)
