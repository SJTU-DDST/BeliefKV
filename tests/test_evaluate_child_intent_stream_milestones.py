import json

import pytest

from scripts.evaluate_child_eos_shadow import audit as eos_audit
from scripts.evaluate_child_intent_stream_milestones import collect, evaluate


def _workflow(root, task, *, lead_ms, false_first=False, stale=False,
              offset_ms=0, first64=False, final_chars=20,
              planned_chars=None):
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
             "output_chars": final_chars, "tool_call_count": 0,
         }, **successor},
    ]
    if first64:
        events.append({
            "kind": "structured_action", "ts_ms": 1060,
            "attributes": {
                "request_id": "first",
                "beliefkv_child_substantial_content_shadow": True,
                "content_threshold_chars": 64,
            }, **successor,
        })
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
        **(
            {"planned_final_report_chars": planned_chars}
            if planned_chars is not None else {}
        ),
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


def test_stage_collector_counts_join_last_without_notice(tmp_path):
    task = "alpha__one"
    _workflow(tmp_path, task, lead_ms=200, first64=True)
    path = tmp_path / task / "runtime_events.deepagents.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    silent = "deepagents-invocation:unannounced"
    next_return = 1400
    for event in events:
        if event["kind"] == "join_create":
            event["member_invocation_ids"].append(silent)
        elif event["kind"] == "join_satisfied":
            event["ts_ms"] = next_return
    events.extend([
        {"kind": "spawn", "target_invocation_id": silent, "ts_ms": 0},
        {"kind": "llm_result", "invocation_id": silent, "ts_ms": 1390,
         "attributes": {
             "request_id": "silent", "finish_reason": "stop",
             "output_chars": 12, "tool_call_count": 0,
         }},
        {"kind": "return", "invocation_id": silent, "ts_ms": next_return},
    ])
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    rows, counts = collect(tmp_path, 64)
    assert len(rows) == 1
    assert not rows[0]["join_last"]
    assert counts["natural_child_returns_total"] == 2
    assert counts["natural_join_last_total"] == 1
    assert counts["observed_join_last_total"] == 1
    assert counts["excluded_join_last_due_to_blocked_child_total"] == 0
    (tmp_path / task / "child_reports.json").write_text(json.dumps([{
        "invocation_id": silent,
        "semantic_completion": {"status": "blocked"},
    }]))
    rows, counts = collect(tmp_path, 64)
    assert len(rows) == 1
    assert counts["natural_child_returns_total"] == 1
    assert counts["natural_join_last_total"] == 0
    assert counts["observed_join_last_total"] == 1
    assert counts["excluded_join_last_due_to_blocked_child_total"] == 1


def test_first_content_stage_keeps_early_tool_reentry_false_label(tmp_path):
    task = "alpha__early"
    workflows = tmp_path / "workflows"
    _workflow(workflows, task, lead_ms=200, first64=False)
    path = workflows / task / "runtime_events.deepagents.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events = [
        event for event in events
        if not (event["kind"] == "structured_action"
                and (event.get("attributes") or {}).get(
                    "beliefkv_child_substantial_content_shadow"))
    ]
    base = next(
        event for event in events if event["kind"] == "llm_submit"
    )
    events.extend([
        {
            "kind": "structured_action", "ts_ms": 1060,
            "invocation_id": base["invocation_id"],
            "context_id": base["context_id"],
            "context_epoch": base["context_epoch"],
            "attributes": {
                "request_id": "first",
                "beliefkv_child_first_content_shadow": True,
            },
        },
        {
            "kind": "tool_start", "ts_ms": 1210,
            "invocation_id": base["invocation_id"],
            "attributes": {"tool_name": "grep"},
        },
        {
            "kind": "structured_action", "ts_ms": 1070,
            "invocation_id": base["invocation_id"],
            "context_id": base["context_id"],
            "context_epoch": base["context_epoch"],
            "attributes": {
                "request_id": "first", "beliefkv_child_eos_shadow": True,
                "eos_top_probability_threshold": 0.001,
            },
        },
    ])
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    first_content, counts = collect(workflows, 0)
    assert len(first_content) == 1
    assert first_content[0]["stage_threshold_chars"] == 0
    assert first_content[0]["observed_first_content_ts_ms"] == 1060
    assert first_content[0]["label"] == "false"
    assert counts["natural_child_returns_total"] == 1
    (tmp_path / "manifest.json").write_text(json.dumps({
        "config": {"child_eos_low_prob_shadow": True},
    }))
    report = eos_audit(workflows, (0.001, 0.01))
    assert report["eligible_first_content_stage"] == 1
    assert report["eligible_first_64_stage"] == 0
    assert report["thresholds"]["0.001"]["false_first_trigger"] == 1


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


def test_first_content_rate_and_future_length_are_separate_fields(tmp_path):
    _workflow(tmp_path, "astropy__one", lead_ms=700, first64=True,
              final_chars=1900, planned_chars=1800)
    rows, _ = collect(tmp_path, 1024)
    assert len(rows) == 1
    assert rows[0]["observed_first_content_ts_ms"] == 1060
    assert rows[0]["final_output_chars_oracle"] == 1900
    assert rows[0]["invocation_id"] == "deepagents-invocation:astropy__one"
    assert rows[0]["request_id"] == "first"
    assert rows[0]["planned_final_report_chars_at_notice"] == 1800
    assert rows[0]["result_to_return_ms_oracle"] == 550
