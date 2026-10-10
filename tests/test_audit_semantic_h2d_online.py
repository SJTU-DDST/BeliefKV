import json

import pytest

from scripts.audit_semantic_h2d_online import (
    audit, notice_feature_alignment, sampled_trigger_replay, snapshot_records,
)


def test_audit_groups_requests_and_keeps_native_eos_separate(tmp_path):
    client = tmp_path / "client_36"
    workflow = client / "workflows/task"
    server = tmp_path / "server"
    opportunities = tmp_path / "opportunities"
    for directory in (workflow, server, opportunities):
        directory.mkdir(parents=True)
    (client / "summary.json").write_text("{}")
    (workflow / "runtime_events.deepagents.jsonl").write_text("\n".join(
        json.dumps(row) for row in (
            {"kind": "invocation_create", "invocation_id": "child",
             "attributes": {"source": "deepagents_task"}},
            {"kind": "llm_result", "invocation_id": "child",
             "attributes": {"request_id": "final"}},
            {"kind": "return", "invocation_id": "child",
             "attributes": {"source": "deepagents_task", "outcome": "completed"}},
        )
    ) + "\n")
    (server / "runtime_events.sglang.jsonl").write_text(json.dumps({
        "kind": "llm_result", "ts_ms": 100,
        "attributes": {"request_id": "final", "output_tokens": 100},
    }) + "\n")
    (opportunities / "admission_opportunities.jsonl").write_text("\n".join(
        json.dumps(row) for row in (
            {"event": "semantic_child_forecast", "request_id": "final",
             "ts_ms": 50, "score": .9, "observed_output_tokens": 20, "remaining_tokens": 70},
            {"event": "semantic_child_forecast", "request_id": "final",
             "ts_ms": 110, "score": .9, "observed_output_tokens": 95, "remaining_tokens": 35},
            {"event": "semantic_child_forecast", "request_id": "tool",
             "score": .8, "observed_output_tokens": 10, "remaining_tokens": 40},
            {"event": "final_stage_latest_start", "child_request_id": "final",
             "ts_ms": 90},
            {"event": "final_stage_latest_start", "child_request_id": "final",
             "ts_ms": 110},
        )
    ) + "\n")
    result = audit(tmp_path, .6)
    assert result["first_crossing_request_count"] == 2
    assert result["first_crossing_natural_final_count"] == 1
    assert result["first_crossing_other_count"] == 1
    assert result["work_errors"]["first_threshold_crossing"]["median_signed_error_tokens"] == -10
    assert result["work_errors"]["last_accepted_snapshot"]["median_signed_error_tokens"] == 30
    assert result["work_errors"]["last_pre_native_result_snapshot"]["median_signed_error_tokens"] == -10
    assert result["latest_start_before_native_result_count"] == 1
    assert result["latest_start_at_or_after_native_result_count"] == 1


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_live_requests_are_unresolved_until_return_or_a_known_tool_round(tmp_path):
    write_rows(tmp_path / "client_36/workflows/task/runtime_events.deepagents.jsonl", [
        {"kind": "invocation_create", "invocation_id": "child",
         "attributes": {"source": "deepagents_task"}},
        {"kind": "llm_result", "invocation_id": "child",
         "attributes": {"request_id": "tool", "tool_call_count": 1}},
        {"kind": "llm_result", "invocation_id": "child",
         "attributes": {"request_id": "pending", "finish_reason": "length"}},
    ])
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "semantic_child_forecast", "request_id": rid, "ts_ms": 50,
         "score": .9, "observed_output_tokens": 20, "remaining_tokens": 70}
        for rid in ("tool", "pending")
    ])
    with pytest.raises(ValueError, match="terminal workload summary"):
        audit(tmp_path, .6)
    result = audit(tmp_path, .6, allow_partial=True)
    assert result["partial_snapshot"] is True
    assert result["first_crossing_other_count"] == 1
    assert result["first_crossing_unresolved_count"] == 1
    assert result["first_crossing_natural_final_count"] == 0


def test_trigger_uses_a_causal_forecast_and_separates_work_from_rate_error(tmp_path):
    write_rows(tmp_path / "client_36/workflows/task/runtime_events.deepagents.jsonl", [
        {"kind": "llm_result", "invocation_id": "child",
         "attributes": {"request_id": "final"}},
        {"kind": "return", "invocation_id": "child", "ts_ms": 800,
         "attributes": {"source": "deepagents_task", "outcome": "completed"}},
    ])
    (tmp_path / "client_36/summary.json").write_text("{}")
    write_rows(tmp_path / "server/runtime_events.sglang.jsonl", [
        {"kind": "llm_result", "ts_ms": 1500,
         "attributes": {"request_id": "final", "output_tokens": 100}},
    ])
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "safe_point_census", "ts_ms": 1100, "monotonic_ms": 100},
        {"event": "semantic_child_forecast", "request_id": "final", "ts_ms": 50,
         "score": .9, "observed_output_tokens": 20, "remaining_tokens": 15,
         "upper_tokens": 50, "observation_age_ms": 30},
        {"event": "final_stage_latest_start", "child_request_id": "final", "ts_ms": 100,
         "trigger_kind": "estimated_work", "generated_tokens": 30,
         "effective_work_statistic": "center", "remaining_ms": 100,
         "forecast_center_tokens": 15, "forecast_upper_tokens": 50},
        {"event": "semantic_child_forecast", "request_id": "final", "ts_ms": 120,
         "score": .9, "observed_output_tokens": 35, "remaining_tokens": 2,
         "upper_tokens": 5},
        {"event": "final_stage_latest_start", "child_request_id": "final", "ts_ms": 200,
         "trigger_kind": "estimated_work", "generated_tokens": 40, "remaining_ms": 10},
    ])
    diagnostics = audit(tmp_path, .6)["estimated_work_trigger_diagnostics"]
    assert diagnostics["request_count"] == 1
    row = diagnostics["rows"][0]
    assert row["trigger_count"] == 2
    assert row["observed_output_tokens"] == 20
    assert row["forecast_observation_age_at_trigger_ms"] == 80
    assert row["advanced_since_forecast_tokens"] == 10
    assert row["projected_remaining_tokens"] == 5
    assert row["actual_remaining_tokens"] == 70
    assert row["trigger_tokens_per_second"] == 50
    assert row["signed_eos_time_error_ms"] == -1300
    assert row["work_error_at_trigger_rate_ms"] == -1300
    assert row["remaining_service_rate_gap_ms"] == 0
    assert row["lead_to_child_return_ms"] == 1700


def test_missing_native_result_and_mismatched_forecast_remain_unknown(tmp_path):
    (tmp_path / "client_36").mkdir()
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "semantic_child_forecast", "request_id": "final", "ts_ms": 50,
         "score": .9, "observed_output_tokens": 20, "remaining_tokens": 15,
         "upper_tokens": 50},
        {"event": "final_stage_latest_start", "child_request_id": "final", "ts_ms": 100,
         "trigger_kind": "estimated_work", "generated_tokens": 30,
         "effective_work_statistic": "center", "remaining_ms": 100,
         "forecast_center_tokens": 8, "forecast_upper_tokens": 50},
    ])
    result = audit(tmp_path, .6, allow_partial=True)["estimated_work_trigger_diagnostics"]
    assert result["missing_native_result_count"] == 1
    assert result["matched_causal_forecast_count"] == 0
    assert result["work_error_summary"]["request_count"] == 0
    assert result["rows"][0]["lead_to_native_eos_ms"] is None
    assert result["rows"][0]["projected_remaining_tokens"] is None


def test_work_update_replay_reports_early_triggers_without_using_future_labels():
    forecasts = {"final": [
        {"ts_ms": 50, "score": .9, "notice_active": True, "current_output_tokens": 34,
         "observed_output_tokens": 20, "remaining_tokens": 15, "upper_tokens": 50,
         "sampled_tokens_per_second": 50, "observation_age_ms": 300},
        {"ts_ms": 4950, "score": .9, "notice_active": True, "current_output_tokens": 91,
         "observed_output_tokens": 90, "remaining_tokens": 4, "upper_tokens": 10,
         "sampled_tokens_per_second": 50, "observation_age_ms": 30},
    ]}
    native = {"final": {"ts_ms": 5000}}
    result = sampled_trigger_replay(forecasts, native, {"final"}, .6)["policies"]
    early = result["progress_countdown"]["100"]
    center = result["snapshot_center"]["100"]
    assert early["lead_over_2000ms_count"] == 1
    assert center["lead_0_to_500ms_count"] == 1
    without_labels = sampled_trigger_replay(forecasts, {}, set(), .6)["policies"]
    for policy in result:
        assert without_labels[policy]["100"]["first_trigger_rows"][0]["ts_ms"] == (
            result[policy]["100"]["first_trigger_rows"][0]["ts_ms"]
        )
        assert without_labels[policy]["100"]["missing_native_result_count"] == 1


def test_notice_input_alignment_binds_only_the_announced_request_and_prior_events():
    events = {"child": [
        {"kind": "structured_action", "ts_ms": 10, "context_id": "ctx", "context_epoch": 0,
         "join_id": "join",
         "attributes": {"beliefkv_child_completion_intent": True,
                        "child_completion_signal_kind": "stage",
                        "estimated_final_report_tokens": 128}},
        {"kind": "llm_submit", "ts_ms": 20, "context_id": "ctx", "context_epoch": 1,
         "attributes": {"request_id": "report"}},
        {"kind": "tool_start", "ts_ms": 40, "attributes": {"tool_name": "execute"}},
        {"kind": "llm_submit", "ts_ms": 50, "context_id": "ctx", "context_epoch": 2,
         "attributes": {"request_id": "next"}},
    ], "sibling": [
        {"kind": "return", "ts_ms": 25, "invocation_id": "sibling"},
    ]}
    joins = [
        {"kind": "join_create", "ts_ms": 1, "join_id": "join",
         "member_invocation_ids": ["child", "sibling"]},
    ]
    forecasts = {
        "report": [
            {"ts_ms": 1040, "observation_age_ms": 10, "notice_active": False},
            {"ts_ms": 1060, "observation_age_ms": 10, "notice_active": False},
        ],
        "next": [{"ts_ms": 1070, "observation_age_ms": 10, "notice_active": False}],
    }
    result = notice_feature_alignment(
        events, forecasts, 1000., accepted_stages=set(), join_events=joins,
    )
    assert result["announced_without_online_notice_request_count"] == 1
    assert result["snapshot_counts"]["client_notice_True_online_notice_False"] == 1
    assert result["snapshot_counts"]["client_notice_False_online_notice_False"] == 2
    [missing] = result["announced_without_online_notice_rows"]
    assert missing["request_id"] == "report"
    assert missing["first_notice_age_ms"] == 20
    assert missing["estimated_report_tokens"] == 128
    assert missing["snapshot_count"] == 1
    assert missing["unfinished_members_at_notice"] == 2
    assert missing["native_action_stage_accepted"] is False
    assert notice_feature_alignment(events, forecasts, None)["unmatched_snapshot_count"] == 3


@pytest.mark.parametrize("tail", [b'{"event":', b'{"text":"\xe4'])
def test_live_snapshot_ignores_only_an_incomplete_trailing_record(tmp_path, tail):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"event":"known"}\n' + tail)
    assert list(snapshot_records(path, allow_partial=True)) == [{"event": "known"}]
    with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
        list(snapshot_records(path, allow_partial=False))
    path.write_bytes(tail + b"\n")
    with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
        list(snapshot_records(path, allow_partial=True))
