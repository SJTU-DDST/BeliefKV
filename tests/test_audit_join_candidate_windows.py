import json

from scripts.audit_join_candidate_windows import audit


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
    assert audit(tmp_path, .6, sample_max_age_ms=5)["joins_with_sampled_fit_phase_intersection"] == 0
