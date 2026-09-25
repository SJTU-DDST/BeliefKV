from __future__ import annotations

import json
import hashlib

import pytest

from beliefkv.predictor.completion_lead import (
    CompletionLead,
    completion_signal_records,
    evaluate_completion_lead,
    load_pinned_completion_lead,
)
from scripts.audit_native_stream_shadow import (
    _satisfied_last_children,
    audit as audit_stream_shadow,
)
from scripts.fit_native_completion_lead import _content_threshold_audit


def _event(timestamp: float, kind: str, **attributes):
    relation_type = attributes.pop("relation_type", None)
    return {
        "workflow_id": "workflow", "invocation_id": "child",
        "ts_ms": timestamp, "kind": kind, "relation_type": relation_type,
        "attributes": attributes,
    }


def _join():
    return {
        "reentry_kind": "join", "terminal_status": "satisfied",
        "training_eligible": True, "workflow_id": "workflow",
        "member_outcomes": [{
            "invocation_id": "child", "return_ts_ms": 1200.0,
        }],
    }


def test_completion_lead_fits_and_scores_independent_events():
    train = [
        {"returned": True, "lead_ms": duration}
        for duration in (100.0, 200.0, 300.0, 400.0, 500.0)
    ]
    model = CompletionLead.fit(train)
    assert model == CompletionLead(100.0, 300.0, 500.0, 5)
    assert CompletionLead.from_dict(model.to_dict()) == model
    report = evaluate_completion_lead(
        model, ({"last_child": True, "returned": True, "lead_ms": 290.0},)
    )
    assert report["median_absolute_error_ms"] == 10.0
    assert report["p10_p90_coverage"] == 1.0
    with pytest.raises(ValueError, match="invalid completion lead"):
        CompletionLead.from_dict({
            "p10_ms": 200, "p50_ms": 100, "p90_ms": 500,
            "training_children": 5,
        })


def test_completion_lead_runtime_load_requires_pinned_diagnostic(tmp_path):
    path = tmp_path / "completion.json"
    payload = {
        "status": "offline_conditional_signal_diagnostic_only",
        "model": CompletionLead(145, 186, 246, 428).to_dict(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_pinned_completion_lead(path, digest).p50_ms == 186
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_pinned_completion_lead(path, "0" * 64)
    payload["status"] = "online_eligible"
    path.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="diagnostic-only"):
        load_pinned_completion_lead(path, digest)


def test_completion_signal_does_not_promote_prior_final_or_internal_message():
    events = [
        _event(0, "invocation_create", relation_type="spawn"),
        _event(100, "llm_result", tool_call_count=0, output_chars=7,
               finish_reason="stop"),
        _event(150, "llm_submit"),
        _event(300, "llm_result", runtime_internal=True,
               tool_call_count=0, output_chars=20, finish_reason="stop"),
        _event(700, "llm_result", tool_call_count=0, output_chars=12,
               finish_reason="stop"),
        _event(1200, "return"),
    ]
    records, counts = completion_signal_records((_join(),), events)
    assert counts["joined_child_count"] == 1
    assert counts["false_or_unknown_signals"] == 1
    assert records[0]["returned"] is False
    assert records[1]["returned"] is True
    assert records[1]["lead_ms"] == 500.0
    assert records[1]["last_child"] is True


def test_completion_signal_audits_canceled_child_outside_eligible_joins():
    canceled = {
        **_event(100, "invocation_create"), "invocation_id": "canceled",
        "relation_type": "spawn",
    }
    events = [
        _event(0, "invocation_create", relation_type="spawn"),
        _event(700, "llm_result", tool_call_count=0, output_chars=12,
               finish_reason="stop"),
        _event(1200, "return"),
        canceled,
        {**_event(200, "llm_result", tool_call_count=0, output_chars=7,
                  finish_reason="stop"), "invocation_id": "canceled"},
        {**_event(300, "invocation_cancel"), "invocation_id": "canceled"},
    ]
    records, counts = completion_signal_records((_join(),), events)
    assert len(records) == 2
    assert counts["confirmed_return_signals"] == 1
    assert counts["confirmed_nonreturn_signals"] == 1
    assert counts["false_or_unknown_signals"] == 1


def test_stream_shadow_audits_early_cue_and_tool_call_false_positive(tmp_path):
    dataset = tmp_path / "dataset"
    workflow = tmp_path / "workflows" / "one"
    dataset.mkdir()
    workflow.mkdir(parents=True)
    (dataset / "reentries.jsonl").write_text(
        json.dumps(_join()) + "\n", encoding="utf-8"
    )
    events = [
        _event(10, "structured_action",
               beliefkv_child_first_content_shadow=True, request_id="first"),
        _event(20, "llm_result", request_id="first", tool_call_count=1),
        _event(30, "tool_start"),
        _event(100, "structured_action",
               beliefkv_child_first_content_shadow=True, request_id="final"),
        _event(500, "structured_action",
               beliefkv_child_substantial_content_shadow=True, request_id="final",
               content_threshold_chars=64),
        _event(700, "structured_action",
               beliefkv_child_substantial_content_shadow=True, request_id="final",
               content_threshold_chars=1024),
        _event(1000, "llm_result", request_id="final", tool_call_count=0,
               invalid_tool_call_count=0, finish_reason="stop", output_chars=12),
        _event(1200, "return"),
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8"
    )
    result = audit_stream_shadow(tmp_path / "workflows", dataset)
    assert result["cues"] == 2
    assert result["confirmed_final_return"] == 1
    assert result["confirmed_not_final"] == 1
    assert result["lead_ms"]["p50"] == 1100
    assert result["complete_response_lead_p50_ms"] == 200
    substantial = audit_stream_shadow(
        tmp_path / "workflows", dataset, cue="substantial_content"
    )
    assert substantial["confirmed_final_return"] == 1
    assert substantial["confirmed_not_final"] == 0
    assert substantial["lead_ms"]["p50"] == 700
    late = audit_stream_shadow(
        tmp_path / "workflows", dataset, cue="substantial_content",
        content_threshold_chars=1024,
    )
    assert late["lead_ms"]["p50"] == 500


def test_stream_timer_excludes_early_tool_chunk_but_not_late_tool_chunk(tmp_path):
    dataset = tmp_path / "dataset"
    workflow = tmp_path / "workflows" / "one"
    dataset.mkdir()
    workflow.mkdir(parents=True)
    (dataset / "reentries.jsonl").write_text(
        json.dumps(_join()) + "\n", encoding="utf-8"
    )
    events = [
        _event(100, "structured_action",
               beliefkv_child_first_content_shadow=True, request_id="tool"),
        _event(400, "structured_action",
               beliefkv_child_first_tool_chunk_shadow=True, request_id="tool"),
        _event(900, "llm_result", request_id="tool", tool_call_count=1),
        _event(950, "tool_start"),
        _event(1000, "structured_action",
               beliefkv_child_first_content_shadow=True, request_id="final"),
        _event(2200, "llm_result", request_id="final", tool_call_count=0,
               finish_reason="stop", output_chars=12),
        _event(2300, "return"),
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8"
    )
    timers = audit_stream_shadow(tmp_path / "workflows", dataset)[
        "first_content_timer_shadow"
    ]
    assert timers["250"]["triggered"] == 2
    assert timers["250"]["false_triggers"] == 1
    assert timers["250"]["first_trigger_precision"] == 0
    assert timers["500"]["triggered"] == 1
    assert timers["500"]["precision"] == 1
    assert timers["500"]["first_trigger_precision"] == 1
    assert timers["500"]["true_return_lead_p50_ms"] == 800


def test_stream_shadow_rejects_empty_stop_even_if_child_returns(tmp_path):
    workflow = tmp_path / "workflows" / "one"
    workflow.mkdir(parents=True)
    events = [
        _event(100, "structured_action",
               beliefkv_child_first_content_shadow=True, request_id="empty"),
        _event(500, "llm_result", request_id="empty", tool_call_count=0,
               finish_reason="stop", output_chars=0),
        _event(600, "return"),
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8"
    )
    result = audit_stream_shadow(tmp_path / "workflows")
    assert result["confirmed_final_return"] == 0
    assert result["confirmed_not_final"] == 1
    assert result["first_content_timer_shadow"]["250"]["false_triggers"] == 1


def test_stream_audit_derives_last_child_only_for_complete_satisfied_join():
    first = [
        {**_event(0, "join_create"), "join_id": "join",
         "member_invocation_ids": ["a", "b"],
         "attributes": {"mode": "all"}},
        {**_event(10, "return"), "invocation_id": "a"},
        {**_event(20, "return"), "invocation_id": "b"},
        {**_event(20, "join_satisfied"), "join_id": "join"},
    ]
    assert _satisfied_last_children(first) == {("workflow", "b")}
    assert _satisfied_last_children(first[:-2] + [first[-1]]) == set()
    assert _satisfied_last_children(
        first[:-1] + [{**first[-1], "ts_ms": 50}]
    ) == set()


def test_natural_content_threshold_audit_counts_returns_and_false_signals():
    records = [
        {"output_chars": 2, "returned": False, "last_child": False,
         "next_event_kind": "llm_submit"},
        {"output_chars": 2, "returned": True, "last_child": True,
         "next_event_kind": "return"},
        {"output_chars": 10, "returned": True, "last_child": True,
         "next_event_kind": "return"},
    ]
    report = _content_threshold_audit(records)
    assert report["1"]["confirmed_nonreturn_signals"] == 1
    assert report["3"]["confirmed_nonreturn_signals"] == 0
    assert report["3"]["last_child_signals"] == 1
