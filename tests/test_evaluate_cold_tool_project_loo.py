from __future__ import annotations

import json

import pytest

from scripts import evaluate_cold_tool_project_loo as loo


def test_project_leave_one_out_never_trains_on_heldout_project(monkeypatch):
    rows = [
        {"project": project, "workflow": f"{project}-{index}",
         "duration_ms": 2_500}
        for project in ("a", "b", "c")
        for index in range(5)
    ]
    calls = []

    def fake_evaluate(train, test):
        training = {item["project"] for item in train}
        heldout = {item["project"] for item in test}
        assert len(heldout) == 1 and not training & heldout
        calls.append((training, heldout))
        return {"evidence_gates": {"all_met": True}}

    monkeypatch.setattr(loo, "evaluate", fake_evaluate)
    result = loo.project_leave_one_out(rows)
    assert len(calls) == 3
    assert result["supported_projects"] == ["a", "b", "c"]
    assert result["evidence_gates"]["every_supported_project_passed"] is True
    assert result["folds"]["b"]["heldout_long_workflows"] == 5


def test_project_leave_one_out_reports_unsupported_folds(monkeypatch):
    rows = [
        {"project": project, "workflow": f"{project}-{index}",
         "duration_ms": 2_500}
        for project in ("a", "b", "c")
        for index in range(5 if project != "c" else 2)
    ]
    monkeypatch.setattr(loo, "evaluate", lambda train, test: {
        "evidence_gates": {"all_met": True}
    })
    result = loo.project_leave_one_out(rows)
    assert result["supported_projects"] == ["a", "b"]
    assert result["folds"]["c"]["heldout_long"] == 2
    assert result["evidence_gates"]["two_supported_heldout_projects"] is True


def test_incomplete_batch_is_rejected_before_scoring(tmp_path):
    run = tmp_path / "run"
    workflows = run / "workflows"
    workflows.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({
        "instance_ids": ["a", "b"],
    }))
    with pytest.raises(ValueError, match="final manifest and summary"):
        loo.require_complete_batch(workflows)
    (run / "summary.json").write_text(json.dumps({
        "workflow_count": 2,
        "workflows": [
            {"instance_id": "a", "outcome": "completed"},
            {"instance_id": "b", "outcome": "completed"},
        ],
    }))
    (workflows / "a").mkdir()
    (workflows / "a" / "result.json").write_text("{}")
    with pytest.raises(ValueError, match="lacks workflow results"):
        loo.require_complete_batch(workflows)
    (workflows / "b").mkdir()
    (workflows / "b" / "result.json").write_text("{}")
    assert loo.require_complete_batch(workflows) == (["a", "b"], [])
    (workflows / "b" / "result.json").unlink()
    (run / "summary.json").write_text(json.dumps({
        "workflow_count": 2,
        "workflows": [
            {"instance_id": "a", "outcome": "completed"},
            {"instance_id": "b", "outcome": "runner_error"},
        ],
    }))
    assert loo.require_complete_batch(workflows) == (["a", "b"], ["b"])
