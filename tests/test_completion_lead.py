from __future__ import annotations

import json
import hashlib

import numpy as np
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
from scripts.pilot_stream_final_classifier import samples as stream_classifier_samples
from scripts.pilot_stream_final_classifier import _quality as stream_classifier_quality
from scripts.pilot_stream_eta_regression import fit_eta, predict_eta
from scripts.pilot_stream_actionable_window import _quality as actionable_quality
from scripts.pilot_stream_online_eta import evaluate_online
from scripts.audit_stream_join_beneficiary import (
    _first_per_join, _quality as beneficiary_quality,
)
from scripts.audit_stream_stage_eta import candidates as stage_eta_candidates
from scripts.audit_stream_stage_eta import _quality as stage_eta_quality
from scripts.pilot_stream_rate_eta import evaluate as evaluate_stream_rate
from scripts.pilot_tool_nearest_history import _neighbor_prior


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
        _event(0, "invocation_create", relation_type="spawn"),
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
    assert substantial["substantial_content_timer_shadow"]["250"][
        "first_trigger_precision"
    ] == 1
    assert substantial["substantial_content_timer_shadow"]["250"][
        "returned_child_first_trigger_recall"
    ] == 1
    late = audit_stream_shadow(
        tmp_path / "workflows", dataset, cue="substantial_content",
        content_threshold_chars=1024,
    )
    assert late["lead_ms"]["p50"] == 500
    assert substantial["substantial_content_timer_shadow"]["250"][
        "eligible_last_children"
    ] == 1
    assert substantial["substantial_content_timer_shadow"]["250"][
        "last_child_first_trigger_at_least_500ms"
    ] == 0
    assert substantial["substantial_content_timer_shadow"]["250"][
        "last_child_first_trigger_true"
    ] == 1
    assert substantial["substantial_content_timer_shadow"]["250"][
        "last_child_first_trigger_recall"
    ] == 1
    timed = audit_stream_shadow(
        tmp_path / "workflows", dataset, cue="substantial_content",
        content_threshold_chars=64, eta_prior_ms=300,
    )
    assert timed["substantial_content_timer_shadow"]["250"][
        "first_trigger_eta_error_p50_ms"
    ] == 150
    assert timed["substantial_content_timer_shadow"]["250"][
        "first_trigger_eta_within_500ms"
    ] == 1


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


def test_stream_audit_can_exclude_in_progress_workflows(tmp_path):
    workflows = tmp_path / "workflows"
    for name, done in (("complete", True), ("pending", False)):
        path = workflows / name
        path.mkdir(parents=True)
        events = [
            _event(100, "structured_action",
                   beliefkv_child_first_content_shadow=True, request_id=name),
            _event(200, "llm_result", request_id=name, tool_call_count=0,
                   finish_reason="stop", output_chars=12),
            _event(300, "return"),
        ]
        events.append(_event(400, "workflow_end",
                             outcome="completed" if done else "incomplete"))
        (path / "runtime_events.deepagents.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in events),
            encoding="utf-8",
        )
    report = audit_stream_shadow(
        workflows, completed_workflows_only=True,
    )
    assert report["included_workflows"] == 1
    assert report["cues"] == 1


def test_stream_eta_uses_only_previously_completed_returns(tmp_path):
    workflows = tmp_path / "workflows"
    for index in range(9):
        path = workflows / str(index)
        path.mkdir(parents=True)
        started = index * 1000
        events = [
            {**_event(started, "invocation_create", relation_type="spawn"),
             "workflow_id": str(index)},
            {**_event(started, "structured_action",
                      beliefkv_child_first_content_shadow=True,
                      request_id=str(index)),
             "workflow_id": str(index)},
            {**_event(started + 500, "llm_result",
                      request_id=str(index), tool_call_count=0,
                      finish_reason="stop", output_chars=128),
             "workflow_id": str(index)},
            {**_event(started + (600 if index < 8 else 900), "return"),
             "workflow_id": str(index)},
        ]
        (path / "runtime_events.deepagents.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
    result = audit_stream_shadow(workflows)
    online = result["first_content_timer_shadow"]["250"][
        "online_completed_history_eta"
    ]
    assert online["evaluated_true_returns"] == 1
    assert online["error_p50_ms"] == 300
    assert online["within_500ms"] == 1


def test_stream_classifier_features_do_not_use_future_tool_chunk(tmp_path):
    workflows = tmp_path / "workflows"
    for name, final in (("final", True), ("tool", False)):
        path = workflows / name
        path.mkdir(parents=True)
        events = [
            {**_event(0, "invocation_create", relation_type="spawn"),
             "workflow_id": name},
            {**_event(100, "llm_submit", request_id="req"),
             "workflow_id": name},
            {**_event(150, "structured_action",
                      request_id="req",
                      beliefkv_child_first_content_shadow=True),
             "workflow_id": name},
            {**_event(500, "structured_action",
                      request_id="req", content_threshold_chars=1024,
                      beliefkv_child_substantial_content_shadow=True),
             "workflow_id": name},
        ]
        if not final:
            events.append({
                **_event(2700, "structured_action", request_id="req",
                         beliefkv_child_first_tool_chunk_shadow=True),
                "workflow_id": name,
            })
        events.extend([
            {**_event(3000, "llm_result", request_id="req",
                      tool_call_count=0 if final else 1,
                      output_chars=1700, finish_reason="stop" if final else "tool_calls"),
             "workflow_id": name},
            {**_event(3500, "return" if final else "tool_start"),
             "workflow_id": name},
        ])
        (path / "runtime_events.deepagents.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
    rows, censored, last_children = stream_classifier_samples(workflows)
    assert censored == 0
    assert not last_children
    assert len(rows) == 2
    assert rows[0]["features"] == rows[1]["features"]
    assert {row["final"] for row in rows} == {False, True}
    assert next(row for row in rows if row["final"])["return_lead_ms"] == 1000
    report = stream_classifier_quality(
        rows, np.array([1.0, 1.0]), .5, set(), eta_prior_ms=800
    )
    assert report["fixed_eta_error_p50_ms"] == 200
    assert report["fixed_eta_within_500ms"] == 1
    assert report["raw_candidate_precision"] == .5


def test_stream_join_feature_only_sees_completed_siblings(tmp_path):
    workflows = tmp_path / "workflows"
    for name, sibling_return in (("ready", 2000), ("pending", 2600)):
        path = workflows / name
        path.mkdir(parents=True)
        events = [
            {
                **_event(0, "join_create", mode="all"),
                "workflow_id": name, "join_id": "join",
                "member_invocation_ids": ["child", "other"],
            },
            {**_event(100, "llm_submit", request_id="req"),
             "workflow_id": name},
            {**_event(150, "structured_action", request_id="req",
                      beliefkv_child_first_content_shadow=True),
             "workflow_id": name},
            {
                **_event(500, "structured_action", request_id="req",
                         beliefkv_child_substantial_content_shadow=True,
                         content_threshold_chars=1024),
                "workflow_id": name, "join_id": "join",
            },
            {**_event(sibling_return, "return"),
             "workflow_id": name, "invocation_id": "other"},
            {**_event(3000, "llm_result", request_id="req",
                      tool_call_count=0, finish_reason="stop",
                      output_chars=1500),
             "workflow_id": name},
            {**_event(3500, "return"), "workflow_id": name},
        ]
        (path / "runtime_events.deepagents.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
    rows, censored, _ = stream_classifier_samples(workflows)
    assert censored == 0
    assert {row["workflow"]: row["join_last_outstanding"] for row in rows} == {
        "ready": True, "pending": False,
    }


def test_stream_join_counts_repeated_workflows_in_distinct_runs(tmp_path):
    workflows = tmp_path / "workflows"
    for name in ("first", "second"):
        path = workflows / name
        path.mkdir(parents=True)
        events = [
            {
                **_event(0, "join_create", mode="all"),
                "join_id": "join", "member_invocation_ids": ["child"],
            },
            _event(100, "llm_submit", request_id="req"),
            _event(150, "structured_action", request_id="req",
                   beliefkv_child_first_content_shadow=True),
            _event(500, "structured_action", request_id="req",
                   beliefkv_child_substantial_content_shadow=True,
                   content_threshold_chars=1024),
            _event(3000, "llm_result", request_id="req",
                   tool_call_count=0, finish_reason="stop", output_chars=1600),
            _event(3500, "return"),
            {**_event(3500, "join_satisfied"), "join_id": "join"},
        ]
        (path / "runtime_events.deepagents.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
    rows, censored, last_children = stream_classifier_samples(workflows)
    assert censored == 0
    assert len(rows) == len(last_children) == 2
    result = stream_classifier_quality(
        rows, np.array([1.0, 1.0]), .5, last_children
    )
    assert result["selected_true_last_children"] == 2
    assert result["last_child_recall"] == 1
    assert result["selected_last_child_lead_at_least_2000ms"] == 0


def test_join_beneficiary_does_not_credit_later_return_after_tool(tmp_path):
    workflows = tmp_path / "workflows"
    for name, end_kind, final in (
        ("good", "join_satisfied", True),
        ("late", "join_satisfied", False),
        ("timeout", "join_timeout", False),
    ):
        path = workflows / name
        path.mkdir(parents=True)
        events = [
            {**_event(0, "join_create", mode="all"),
             "join_id": "join", "member_invocation_ids": ["child"]},
            _event(100, "llm_submit", request_id="req"),
            _event(150, "structured_action", request_id="req",
                   beliefkv_child_first_content_shadow=True),
            {**_event(500, "structured_action", request_id="req",
                       beliefkv_child_substantial_content_shadow=True,
                       content_threshold_chars=1024), "join_id": "join"},
            _event(3000, "llm_result", request_id="req",
                   tool_call_count=0 if final else 1,
                   finish_reason="stop" if final else "tool_calls",
                   output_chars=1600),
            _event(3500, "return" if final else "tool_start"),
            *([_event(8000, "return")] if name == "late" else []),
            {**_event(8000 if name == "late" else 3500, end_kind),
             "join_id": "join"},
            _event(9000, "workflow_end", outcome="completed"),
        ]
        (path / "runtime_events.deepagents.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
    rows, _, last_children = stream_classifier_samples(workflows)
    report = beneficiary_quality(_first_per_join(rows), last_children)
    assert report["signaled_join_groups"] == 3
    assert report["observed_satisfied"] == 1
    assert report["premature_then_satisfied"] == 1
    assert report["observed_timeout"] == 1
    assert report["precision_on_determined"] == 1 / 3
    assert report["satisfied_lead_p50_ms"] == 1000


def test_stage_eta_requires_last_child_and_reports_remaining_window(tmp_path):
    path = tmp_path / "workflows" / "one"
    path.mkdir(parents=True)
    events = [
        {**_event(0, "join_create", mode="all"),
         "join_id": "join", "member_invocation_ids": ["child", "other"]},
        {**_event(100, "return"), "invocation_id": "other"},
        _event(200, "llm_submit", request_id="req"),
        _event(250, "structured_action", request_id="req",
               beliefkv_child_first_content_shadow=True),
        _event(500, "structured_action", request_id="req",
               beliefkv_child_substantial_content_shadow=True,
               content_threshold_chars=1024),
        _event(1000, "structured_action", request_id="req",
               beliefkv_child_substantial_content_shadow=True,
               content_threshold_chars=1700),
        _event(3000, "llm_result", request_id="req", tool_call_count=0,
               output_chars=1800, finish_reason="stop"),
        _event(3200, "return"),
        {**_event(3200, "join_satisfied"), "join_id": "join"},
    ]
    (path / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    stages = stage_eta_candidates(tmp_path / "workflows")
    assert {
        stage: rows[0]["lead_ms"] for stage, rows in stages.items()
    } == {"content_1024": 700, "content_1700": 1950, "result": 200}
    assert all(rows[0]["eligible_last_child"] for rows in stages.values())
    quality = stage_eta_quality(stages["result"], prior=210)
    assert quality["eta_error_p50_ms"] == 10
    assert quality["lead_at_least_500ms"] == 0


def test_stream_rate_eta_fits_training_only_and_scores_join_subset():
    train = [
        {"rate_interval_ms": i * 100, "lead_ms": 2000 + i * 200,
         "join_last_child": False}
        for i in range(12)
    ]
    heldout = [
        {"rate_interval_ms": 600, "lead_ms": 3200, "join_last_child": True},
        {"rate_interval_ms": 400, "lead_ms": 2800, "join_last_child": False},
    ]
    result = evaluate_stream_rate(train, heldout)
    assert result["train_samples"] == 12
    assert result["rate_slope"] == pytest.approx(2)
    assert result["cohorts"]["last_child_join"]["rate_mae_p50_ms"] == 0
    assert result["cohorts"]["last_child_join"]["fixed_mae_p50_ms"] > 0


def test_tool_nearest_history_excludes_overlapping_and_future_results():
    now = {"start_ts_ms": 2000, "input_chars": 100}
    completed = [
        {"input_chars": 98, "duration_ms": 3000,
         "terminal_ts_ms": 1000 + index, "status": "success",
         "is_child": True}
        for index in range(4)
    ]
    overlapping = {
        "input_chars": 100, "duration_ms": 90000,
        "terminal_ts_ms": 2100, "status": "success", "is_child": True,
    }
    failed = {
        "input_chars": 100, "duration_ms": 90000,
        "terminal_ts_ms": 1500, "status": "error", "is_child": True,
    }
    root = {
        "input_chars": 100, "duration_ms": 90000,
        "terminal_ts_ms": 1500, "status": "success", "is_child": False,
    }
    now["class"] = "python_inline"
    result, support = _neighbor_prior(
        now, completed + [overlapping, failed, root]
    )
    assert (result, support) == (3000, 4)
    assert _neighbor_prior(now, completed[:3]) == (None, 3)


def test_stream_eta_regression_only_consumes_causal_features():
    rows = [
        {
            "features": [index / 10, .5, 2., 1.],
            "join_last_outstanding": index % 2 == 0,
            "return_lead_ms": 1500 + 90 * index,
        }
        for index in range(25)
    ]
    model = fit_eta(rows, join_aware=True)
    predictions = predict_eta(rows, model, join_aware=True)
    assert len(predictions) == len(rows)
    assert np.isfinite(predictions).all()
    assert predictions[0] < predictions[-1]


def test_actionable_window_counts_late_returns_as_not_useful():
    rows = [
        {"final": True, "return_lead_ms": 3000,
         "trace_path": "a", "workflow": "w", "child": "c1"},
        {"final": True, "return_lead_ms": 1000,
         "trace_path": "a", "workflow": "w", "child": "c2"},
        {"final": False, "return_lead_ms": None,
         "trace_path": "a", "workflow": "w", "child": "c3"},
    ]
    quality = actionable_quality(
        rows, np.ones(3), .5, {("a", "w", "c1")}
    )
    assert quality["selected_useful"] == 1
    assert quality["selected_late_return"] == 1
    assert quality["selected_nonreturn"] == 1
    assert quality["last_child_useful_recall"] == 1
    assert quality["raw_candidate_useful_precision"] == 1 / 3


def test_online_eta_uses_only_completed_project_returns():
    rows = [
        {
            "trace_path": f"/workflows/project__task-{index}/trace.jsonl",
            "trigger_ms": index * 1000,
            "final": True,
            "return_lead_ms": 300 if index < 8 else 350,
        }
        for index in range(9)
    ]
    result = evaluate_online(rows, np.ones(9), .5, 800)
    assert result["project_supported_selected_returns"] == 1
    assert result["project_local_eta_error_p50_ms"] == 50
    assert result["fixed_prior_same_cohort_error_p50_ms"] == 450


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
