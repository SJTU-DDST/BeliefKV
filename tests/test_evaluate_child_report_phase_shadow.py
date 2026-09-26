from __future__ import annotations

import json

import pytest

from scripts.evaluate_child_report_phase_shadow import audit_workflows, evaluate, load


def _write_workflow(workflows, project, index, *, phase=True):
    path = workflows / f"{project}__{index}"
    path.mkdir(parents=True)
    events = [
        {
            "kind": "structured_action",
            "ts_ms": 1200,
            "invocation_id": "child",
            "context_id": "ctx",
            "context_epoch": 1,
            "attributes": {
                "beliefkv_child_report_phase_shadow": True,
                "request_id": "req",
                "phase_kind": "summary",
                "stream_content_chars": 1200,
            },
        },
        {
            "kind": "structured_action",
            "ts_ms": 1600,
            "invocation_id": "child",
            "attributes": {
                "beliefkv_child_report_phase_shadow": True,
                "request_id": "req",
                "phase_kind": "conclusion",
                "stream_content_chars": 1700,
            },
        },
    ] if phase else []
    (path / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )


def test_phase_audit_pairs_first_trigger_on_same_request_and_scores_missing(
    monkeypatch, tmp_path,
):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    _write_workflow(train, "alpha", 0)
    _write_workflow(heldout, "beta", 0)
    _write_workflow(heldout, "beta", 1, phase=False)
    stage = [
        {
            "project": "alpha",
            "task_id": "alpha__0",
            "invocation_id": "child",
            "context_id": "ctx",
            "context_epoch": 1,
            "request_id": "req",
            "signal_ts_ms": 1000.,
            "return_ts_ms": 2000.,
            "label": "true",
            "lead_ms": 1000.,
            "join_last": True,
        },
    ]
    heldout_stage = [
        {**stage[0], "project": "beta", "task_id": f"beta__{index}"}
        for index in (0, 1)
    ]
    monkeypatch.setattr(
        "scripts.evaluate_child_report_phase_shadow.collect",
        lambda path, threshold: (
            stage if path == train else heldout_stage, {"workflows": 1}
        ),
    )
    rows, _ = load(heldout)
    assert rows[0]["phase_kind"] == "summary"
    assert rows[0]["phase_lead_ms"] == 800.
    assert rows[1]["phase_kind"] is None
    coverage = audit_workflows(heldout)
    assert coverage["stage_count"] == 2
    assert coverage["first_trigger_count"] == 1
    result = evaluate([train], heldout)["evaluation"]
    assert result["candidate_first_trigger"] == 1
    assert result["natural_without_candidate"] == 1
    assert result["join_last_natural_candidate"] == 1
    assert result["return_timing_on_same_first_triggers"][
        "phase_train_prior"
    ]["within_500ms"] == 1
    _write_workflow(heldout, "alpha", 1)
    heldout_stage.append({
        **stage[0], "project": "alpha", "task_id": "alpha__1"
    })
    with pytest.raises(ValueError, match="disjoint"):
        evaluate([train], heldout)
