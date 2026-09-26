import json

import pytest

from scripts.evaluate_child_intent_stream_milestones import evaluate


def _workflow(root, task, *, lead_ms, false_first=False, stale=False,
              offset_ms=0):
    path = root / task
    path.mkdir(parents=True)
    child = f"deepagents-invocation:{task}"
    base = {"invocation_id": child, "context_id": "context:child",
            "context_epoch": 2}
    successor = {**base, "context_epoch": 3 if not stale else 4}
    events = [
        {"kind": "spawn", "target_invocation_id": child, "ts_ms": 0},
        {"kind": "join_create", "join_id": task,
         "member_invocation_ids": [child], "ts_ms": 1},
        {"kind": "invocation_create", "ts_ms": 2, **base},
        {"kind": "llm_result", "ts_ms": 900,
         "attributes": {"request_id": "notice"}, **base},
        {"kind": "tool_start", "ts_ms": 999,
         "attributes": {"tool_name": "announce_completion_intent"}, **base},
        {"kind": "llm_submit", "ts_ms": 1050,
         "attributes": {"request_id": "first"}, **successor},
        {"kind": "structured_action", "ts_ms": 1100,
         "attributes": {
             "request_id": "first",
             "beliefkv_child_substantial_content_shadow": True,
             "content_threshold_chars": 1024,
         }, **successor},
        {"kind": "structured_action", "ts_ms": 1200,
         "attributes": {
             "request_id": "first",
             "beliefkv_child_substantial_content_shadow": True,
             "content_threshold_chars": 1700,
         }, **successor},
        {"kind": "llm_result", "ts_ms": 1250,
         "attributes": {
             "request_id": "first", "finish_reason": "stop",
             "output_chars": 20, "tool_call_count": 0,
         }, **successor},
    ]
    if false_first:
        events.extend([
            {"kind": "llm_submit", "ts_ms": 1300,
             "attributes": {"request_id": "terminal"},
             **{**successor, "context_epoch": successor["context_epoch"] + 1}},
            {"kind": "llm_result", "ts_ms": 1350,
             "attributes": {
                 "request_id": "terminal", "finish_reason": "stop",
                 "output_chars": 20, "tool_call_count": 0,
             }, **{**successor, "context_epoch": successor["context_epoch"] + 1}},
        ])
    events.extend([
        {"kind": "return", "ts_ms": 1100 + lead_ms, **successor},
        {"kind": "join_satisfied", "join_id": task, "ts_ms": 1100 + lead_ms},
    ])
    for event in events:
        event["ts_ms"] += offset_ms
    (path / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
    )
    (path / "sandbox_audit.jsonl").write_text(json.dumps({
        "event": "child_return_intent_shadow", "invocation_id": child,
        "ts_ms": 1000 + offset_ms, "context_id": base["context_id"],
        "context_epoch": base["context_epoch"],
    }) + "\n")


def test_fixed_stream_stage_prior_requires_disjoint_projects(tmp_path):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    _workflow(train, "sphinx-doc__one", lead_ms=500)
    _workflow(train, "sphinx-doc__two", lead_ms=1000)
    _workflow(heldout, "astropy__one", lead_ms=700)
    _workflow(heldout, "astropy__two", lead_ms=900, false_first=True)
    result = evaluate(train, heldout)
    assert result["1024"]["frozen_task_balanced_prior_ms"] == 750
    assert result["1024"]["heldout_true"] == 1
    assert result["1024"]["heldout_false"] == 1
    assert result["1024"]["heldout_join_last_true"] == 1
    assert result["1024"]["heldout_point_error_ms"]["within_500ms"] == 1
    assert result["1700"]["heldout_true"] == 1
    with pytest.raises(ValueError, match="disjoint projects"):
        evaluate(train, train)


def test_skipped_epoch_does_not_get_a_stage_label(tmp_path):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    _workflow(train, "sphinx-doc__one", lead_ms=500)
    _workflow(heldout, "astropy__one", lead_ms=700, stale=True)
    result = evaluate(train, heldout)
    assert result["1024"]["heldout_counts"]["stage_identity_mismatch"] == 1
    assert result["1024"]["heldout_true"] == 0


def test_rolling_project_prior_only_uses_completed_other_tasks(tmp_path):
    train = tmp_path / "train"
    heldout = tmp_path / "heldout"
    _workflow(train, "sphinx-doc__one", lead_ms=500)
    _workflow(train, "sphinx-doc__two", lead_ms=1000)
    for index, lead_ms in enumerate((500, 600, 500, 600, 550)):
        _workflow(
            heldout, f"astropy__{index}", lead_ms=lead_ms,
            offset_ms=index * 5000,
        )
    _workflow(heldout, "astropy__unfinished_at_probe", lead_ms=22000,
              offset_ms=1000)
    result = evaluate(train, heldout)["1024"]
    assert result["causal_project_history_supported"] == 1
