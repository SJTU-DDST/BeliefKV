import pytest

from scripts.evaluate_join_pressure_strata import (
    attach_asof, evaluate, forecast, pressure_bin, summarize,
)


def _row(project, task, lead, pressure):
    return {
        "project": project, "task_id": f"{project}__{task}",
        "lead_ms": lead, "pressure_bin": pressure,
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
