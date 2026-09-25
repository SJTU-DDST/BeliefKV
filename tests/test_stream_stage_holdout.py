from __future__ import annotations

import pytest

from scripts.audit_stream_stage_eta import STAGES
from scripts.evaluate_stream_stage_holdout import _paths, evaluate


def _row(project: str, workflow: str, *, lead: float, final: bool = True):
    return {
        "join": (f"/data/{project}__{workflow}/runtime_events.deepagents.jsonl", "join"),
        "child": "child",
        "trigger_ms": 100,
        "final": final,
        "lead_ms": lead if final else None,
    }


def _eligible(rows):
    return {(row["join"][0], row["child"]) for row in rows if row["final"]}


def test_stage_selection_is_frozen_on_training_and_reports_holdout_failure():
    training_rows = [
        _row("pydata", str(i), lead=600 + 10 * (i % 3))
        for i in range(10)
    ]
    other = [_row("pydata", f"long-{i}", lead=4000) for i in range(10)]
    heldout = [
        _row("django", str(i), lead=2800, final=i != 9)
        for i in range(10)
    ]
    train = {stage: [] for stage in STAGES}
    train["content_4200"] = training_rows
    train["result"] = other
    test = {stage: [] for stage in STAGES}
    test["content_4200"] = heldout
    report = evaluate(train, test, _eligible(training_rows), _eligible(heldout))
    assert report["development_stage"] == "content_4200"
    assert report["heldout"]["content_4200"]["false_next_return_join"] == 1
    assert report["heldout"]["content_4200"]["eta_error_p50_ms"] >= 2000
    assert report["heldout"]["content_4200"]["complete_last_child_recall"] == 1
    assert report["pre_registered_holdout_gate_passed"] is False
    assert report["heldout_by_project"]["django"]["content_4200"] == (
        report["heldout"]["content_4200"]
    )


def test_stage_evaluation_rejects_overlap_and_no_coverage():
    row = _row("pydata", "same", lead=800)
    train = {stage: [] for stage in STAGES}
    train["content_1024"] = [row]
    eval_rows = {stage: [] for stage in STAGES}
    eval_rows["content_1024"] = [_row("pydata", "new", lead=1000)]
    with pytest.raises(ValueError, match="projects overlap"):
        evaluate(train, eval_rows, _eligible([row]), set())
    eval_rows["content_1024"] = [_row("django", "new", lead=1000)]
    report = evaluate(train, eval_rows, _eligible([row]), set())
    assert report["development_stage"] is None
    assert report["heldout"]["content_1024"]["complete_last_child_recall"] is None


def test_multi_batch_evaluation_rejects_duplicate_workflow_instance(tmp_path):
    directories = []
    for run in ("run-a", "run-b"):
        path = tmp_path / run / "same__task"
        path.mkdir(parents=True)
        (path / "runtime_events.deepagents.jsonl").write_text("")
        directories.append(path.parent)
    with pytest.raises(ValueError, match="repeated workflow instances"):
        _paths(directories)
