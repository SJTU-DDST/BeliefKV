import json

from scripts.audit_semantic_h2d_online import audit


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
             "score": .9, "observed_output_tokens": 20, "remaining_tokens": 70},
            {"event": "semantic_child_forecast", "request_id": "final",
             "score": .9, "observed_output_tokens": 95, "remaining_tokens": 35},
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
    assert result["latest_start_before_native_result_count"] == 1
    assert result["latest_start_at_or_after_native_result_count"] == 1
