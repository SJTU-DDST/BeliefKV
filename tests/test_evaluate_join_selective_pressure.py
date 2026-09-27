import pytest

from scripts.evaluate_join_selective_pressure import (
    apply_bins, evaluate, project_oof, select_bins, summarize,
)


def _row(project, task, lead, pressure):
    return {
        "project": project, "task_id": f"{project}__{task}",
        "lead_ms": float(lead), "pressure_bin": pressure,
        "batch": "synthetic", "trigger_ts_ms": 1000.,
    }


def test_pressure_gate_requires_cross_project_gain():
    oof = [
        {
            **_row(project, index, 1200, "idle"),
            "global_eta_ms": 60_000., "pressure_eta_ms": 1300.,
        }
        for project in ("a", "b", "c") for index in range(3)
    ] + [
        {
            **_row(project, index + 3, 60_000, "heavy_queue"),
            "global_eta_ms": 60_000., "pressure_eta_ms": 1200.,
        }
        for project in ("a", "b", "c") for index in range(3)
    ]
    selected, evidence = select_bins(oof)
    assert selected == {"idle"}
    assert evidence["idle"]["independent_workflows"] == 9
    assert not evidence["heavy_queue"]["qualified"]
    predictions = apply_bins(oof, selected)
    assert summarize(predictions)["selective_within_500ms"] == len(oof)


def test_inner_holdout_gate_does_not_read_outer_project():
    rows = [
        _row(project, index, lead, pressure)
        for project in ("a", "b", "c", "d")
        for pressure, lead in (("idle", 1200), ("heavy_queue", 60_000))
        for index in range(5)
    ]
    without_d = project_oof([row for row in rows if row["project"] != "d"])
    baseline = select_bins(without_d)
    altered = [
        {**row, "lead_ms": 1_000_000.}
        if row["project"] == "d" else row
        for row in rows
    ]
    assert baseline == select_bins(project_oof([
        row for row in altered if row["project"] != "d"
    ]))
    assert {row["project"] for row in without_d} == {"a", "b", "c"}


def test_heldout_manifest_overlap_rejected_before_fitting(monkeypatch, tmp_path):
    def fake_load(path, *, notice_source="shadow"):
        return (
            [_row("train", i, 1200, "idle") for i in range(8)]
            if path.name == "train" else [],
            {"frozen_projects": ["train"]},
        )

    monkeypatch.setattr(
        "scripts.evaluate_join_selective_pressure.load", fake_load,
    )
    with pytest.raises(ValueError, match="manifests overlap"):
        evaluate([tmp_path / "train"], tmp_path / "heldout")
