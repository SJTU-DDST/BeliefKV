import json

import pytest

from scripts.evaluate_join_service_window_gate import (
    evaluate, join_prior, load_batch, lower_bound, score,
)


def _row(project, task, *, pressure="heavy_queue", service=1200):
    return {
        "project": project, "task_id": f"{project}__{task}",
        "pressure_bin": pressure, "parent_service_lead_ms": service,
        "lead_ms": 250.,
    }


def test_only_prior_heavy_queue_can_select_and_no_metric_fails_closed():
    rows = [
        _row("a", 1, service=1200),
        _row("b", 2, pressure="idle", service=400),
        _row("c", 3, pressure=None, service=10000),
    ]
    result = score(rows, 1300., 200.)
    assert result["natural_parent_service_groups"] == 3
    assert result["prior_metric_coverage"] == 2
    assert result["selected"] == 1
    assert result["selected_window_counts"]["1000"] == 1
    assert result["selected_prior_overestimates_actual"] == 1
    assert result["selected_join_eta_error_p50_ms"] == 50.
    assert result["selected_join_eta_within_500ms"] == 1
    assert lower_bound(rows) is None
    assert join_prior(rows) == 250.


def test_service_audit_requires_matching_join_event(tmp_path, monkeypatch):
    workflows = tmp_path / "workflows"
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps({
        "workflows": 1,
        "sources": {"llm_result": {
            "matched": {"groups": 1},
            "matched_evidence": [{
                "task_id": "heldout__a", "join_id": "join-1",
                "trigger_ts_ms": 1000., "lead_ms": 300.,
                "parent_service_lead_ms": 900.,
            }],
        }},
    }), encoding="utf-8")

    def fake_load(_workflows, *, notice_source):
        assert notice_source == "llm_result"
        return [{
            "task_id": "heldout__a", "join_id": "join-1",
            "trigger_ts_ms": 1000., "lead_ms": 300.,
            "pressure_bin": "heavy_queue", "queued": 30,
            "metric_age_ms": 50.,
        }], {"frozen_workflows": 1, "frozen_projects": ["heldout"]}

    monkeypatch.setattr("scripts.evaluate_join_service_window_gate.load", fake_load)
    rows, meta = load_batch(workflows, audit_path)
    assert rows[0]["pressure_bin"] == "heavy_queue"
    assert meta["missing_prior_metric_groups"] == 0
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    payload["sources"]["llm_result"]["matched_evidence"][0][
        "trigger_ts_ms"
    ] = 1001.
    audit_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="identity disagree"):
        load_batch(workflows, audit_path)


def test_disjoint_manifest_checked_even_when_no_natural_events(
    monkeypatch, tmp_path,
):
    def fake_load(_workflows, _audit):
        return (
            [
                _row(project, index)
                for project in ("one", "two", "three")
                for index in range(4)
            ] if _workflows.name == "train" else []
        ), {
            "frozen_projects": ["one", "two", "three"]
            if _workflows.name == "train" else ["three"],
        }

    monkeypatch.setattr("scripts.evaluate_join_service_window_gate.load_batch", fake_load)
    with pytest.raises(ValueError, match="overlaps"):
        evaluate(
            [(tmp_path / "train", tmp_path / "train.json")],
            (tmp_path / "test", tmp_path / "test.json"),
        )
