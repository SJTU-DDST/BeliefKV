import json

import pytest

from scripts.evaluate_child_intent_project_holdout import evaluate


def _workflow(root, task, *, lead_ms):
    workflow = root / "workflows" / task
    workflow.mkdir(parents=True)
    child = f"deepagents-invocation:{task}"
    events = [
        {"kind": "spawn", "target_invocation_id": child, "ts_ms": 0},
        {"kind": "invocation_create", "invocation_id": child, "ts_ms": 1},
        {"kind": "join_create", "join_id": task, "ts_ms": 2,
         "member_invocation_ids": [child]},
        {"kind": "llm_result", "invocation_id": child, "ts_ms": 900,
         "attributes": {"request_id": "notify", "finish_reason": "tool_calls",
                        "output_chars": 5, "tool_call_count": 1}},
        {"kind": "llm_result", "invocation_id": child, "ts_ms": 1000 + lead_ms - 10,
         "attributes": {"request_id": "final", "finish_reason": "stop",
                        "output_chars": 20, "tool_call_count": 0}},
        {"kind": "return", "invocation_id": child, "ts_ms": 1000 + lead_ms},
        {"kind": "join_satisfied", "join_id": task, "ts_ms": 1000 + lead_ms},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
    )
    (workflow / "sandbox_audit.jsonl").write_text(
        json.dumps({"event": "child_return_intent_shadow",
                    "invocation_id": child, "ts_ms": 1000}) + "\n",
    )


def test_frozen_train_task_prior_and_disjoint_join_error(tmp_path):
    train = tmp_path / "train"
    test = tmp_path / "test"
    _workflow(train, "sphinx-doc__one", lead_ms=800)
    _workflow(train, "sphinx-doc__two", lead_ms=1200)
    _workflow(test, "astropy__one", lead_ms=900)
    report = evaluate(train, test)
    assert report["frozen_task_balanced_notice_prior_ms"] == 1000
    assert report["heldout_notice_point_error_ms"]["within_500ms"] == 1
    assert report["heldout_join_last_point_error_ms"]["count"] == 1
    assert report["heldout_join_last_notice_to_return_ms"]["mae_ms"] == 900


def test_overlapping_project_cannot_be_held_out(tmp_path):
    train = tmp_path / "train"
    test = tmp_path / "test"
    _workflow(train, "sphinx-doc__one", lead_ms=800)
    _workflow(test, "sphinx-doc__two", lead_ms=800)
    with pytest.raises(ValueError, match="disjoint projects"):
        evaluate(train, test)
