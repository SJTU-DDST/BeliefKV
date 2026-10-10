import json

from scripts.audit_join_candidate_windows import audit, latest_start_bias


def test_join_candidates_use_pre_eos_acceptance_and_same_waiting_parent(tmp_path):
    workflow = tmp_path / "client_64/workflows/task"
    server = tmp_path / "server"
    opportunities = tmp_path / "opportunities"
    for directory in (workflow, server, opportunities):
        directory.mkdir(parents=True)
    events = [
        {"kind": "join_create", "ts_ms": 10, "join_id": "join",
         "attributes": {"mode": "all"}, "member_invocation_ids": ["child"]},
        {"kind": "join_wait", "ts_ms": 10, "join_id": "join", "invocation_id": "parent"},
        {"kind": "llm_submit", "ts_ms": 20, "invocation_id": "child",
         "attributes": {"request_id": "final"}},
        {"kind": "llm_result", "ts_ms": 90, "invocation_id": "child",
         "attributes": {"request_id": "final"}},
        {"kind": "return", "ts_ms": 95, "invocation_id": "child",
         "attributes": {"source": "deepagents_task", "outcome": "completed"}},
        {"kind": "join_satisfied", "ts_ms": 95, "join_id": "join"},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events)
    )
    (server / "runtime_events.sglang.jsonl").write_text(json.dumps({
        "kind": "llm_result", "ts_ms": 180, "invocation_id": "child",
        "attributes": {"request_id": "final"},
    }) + "\n")
    samples = [
        {"event": "safe_point_census", "ts_ms": 100, "monotonic_ms": 0},
        {"event": "session_h2d_opportunity", "ts_ms": 140,
         "invocation_id": "parent", "invocation_state": "wait_join",
         "node_id": 1, "fits_current_free_lists": True, "reason": "fits_current_free_lists"},
        {"event": "session_h2d_opportunity", "ts_ms": 145,
         "invocation_id": "other-parent", "invocation_state": "wait_join",
         "node_id": 2, "fits_current_free_lists": True, "reason": "fits_current_free_lists"},
        {"event": "semantic_child_forecast", "ts_ms": 150, "request_id": "final",
         "score": .9, "remaining_tokens": 8},
        {"event": "semantic_child_forecast", "ts_ms": 190, "request_id": "final",
         "score": .9, "remaining_tokens": 0},
        {"event": "final_stage_latest_start", "ts_ms": 160, "join_id": "join",
         "child_request_id": "final", "remaining_ms": 10, "trigger_kind": "estimated_work"},
        {"event": "final_stage_latest_start", "ts_ms": 170, "join_id": "join",
         "child_request_id": "previous", "remaining_ms": 0, "trigger_kind": "estimated_work"},
        {"event": "final_stage_latest_start", "ts_ms": 185, "join_id": "join",
         "child_request_id": "final", "remaining_ms": 0, "trigger_kind": "observed_no_tool_eos"},
        {"event": "final_stage_latest_start", "ts_ms": 190, "join_id": "join",
         "child_request_id": "final", "remaining_ms": 0, "trigger_kind": "estimated_work"},
    ]
    (opportunities / "admission_opportunities.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in samples)
    )
    result = audit(tmp_path, .6)
    assert result["joins_with_sampled_fit_phase_intersection"] == 1
    row = result["rows"][0]
    assert row["restore_target_samples_during_final_request"] == 1
    assert row["accepted_forecasts_before_eos"] == 1
    assert row["eos_to_return_ms"] == 15
    assert row["last_pre_eos_forecast_with_sampled_target"]["lead_to_child_return_ms"] == 45
    bias = result["latest_start_signed_bias"]["by_trigger"]
    work = bias["estimated_work"]
    assert work["independent_complete_join_count"] == 1
    assert work["generation_endpoint_error"]["signed_p50_ms"] == -10
    assert work["child_return_endpoint_error"]["signed_p50_ms"] == -25
    assert work["fitted_additive_correction_ms"] is None
    assert bias["observed_no_tool_eos"]["child_return_endpoint_error"]["signed_p50_ms"] == -10
    assert audit(tmp_path, .6, sample_max_age_ms=5)["joins_with_sampled_fit_phase_intersection"] == 0


def test_join_bias_fits_only_earlier_independent_events_and_reports_late_errors():
    rows = []
    for index, error in enumerate((-1000, -2000, -4000, 1000)):
        timestamp = index * 10000
        rows.append({
            "child_return_ts_ms": timestamp + 6000,
            "native_eos_ts_ms": timestamp + 5000,
            "eos_to_return_ms": 1000,
            "latest_start_forecasts": [
                {
                    "trigger_kind": "estimated_work",
                    "matches_final_child_request": True,
                    "ts_ms": timestamp,
                    "signed_error_to_native_eos_ms": error + 1000,
                    "signed_error_to_child_return_ms": error,
                },
            ],
        })
    result = latest_start_bias(list(reversed(rows)))["by_trigger"]["estimated_work"]
    assert result["independent_complete_join_count"] == 4
    assert result["chronological_fit_count"] == result["chronological_heldout_count"] == 2
    assert result["fitted_additive_correction_ms"] == 1500
    assert result["heldout_uncorrected"]["absolute_p50_ms"] == 2500
    assert result["heldout_corrected"]["absolute_p50_ms"] == 2500
    assert result["heldout_corrected"]["late_over_500ms_fraction"] == .5
