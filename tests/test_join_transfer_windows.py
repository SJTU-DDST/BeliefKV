import json

from scripts.audit_join_transfer_windows import audit


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _run(
    tmp_path, *, native_epoch=1, client_tool_calls=0, stream_timing=False,
    close_timing=False, close_epoch=1, close_elapsed=10.,
):
    _write(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "safe_point_census", "ts_ms": 1000., "monotonic_ms": 0.},
        {"event": "prefetch_native_issued", "source": "join_ticket",
         "command_id": "prefetch", "context_id": "parent", "node_id": 10,
         "join_id": "join", "child_invocation_id": "child",
         "child_request_id": "child-final"},
    ])
    identity = {
        "workflow_id": "workflow", "invocation_id": "child",
        "context_id": "child-ctx", "context_epoch": 1,
    }
    parent = {"invocation_id": "root", "context_id": "parent", "context_epoch": 3}
    _write(tmp_path / "client_1/workflows/task/runtime_events.deepagents.jsonl", [
        {"kind": "llm_result", "ts_ms": 80., **identity,
         "attributes": {"request_id": "child-final", "finish_reason": "stop",
                        "tool_call_count": client_tool_calls, "invalid_tool_call_count": 0,
                        **({
                            "stream_final_chunk_ts_ms": 65.,
                            "llm_end_callback_entry_ts_ms": 75.,
                        } if stream_timing else {})}},
        {"kind": "return", "ts_ms": 100., "invocation_id": "child",
         "attributes": {"source": "deepagents_task", "outcome": "completed"}},
        {"kind": "join_satisfied", "ts_ms": 105., "join_id": "join", "attributes": {}},
        {"kind": "llm_submit", "ts_ms": 104., **parent,
         "attributes": {"request_id": "earlier-tool"}},
        {"kind": "llm_submit", "ts_ms": 125., **parent,
         "attributes": {"request_id": "parent-next"}},
    ])
    _write(tmp_path / "server/runtime_events.sglang.jsonl", [
        {"kind": "llm_result", "ts_ms": 1040., **{**identity, "context_epoch": native_epoch},
         "attributes": {"request_id": "child-final"}},
        {"kind": "llm_submit", "ts_ms": 1150., **parent,
         "attributes": {"request_id": "parent-next"}},
    ])
    _write(tmp_path / "server/transfer_telemetry.jsonl", [
        {"direction": "h2d", "submit_ts_ms": 1030.,
         "tagged_child_commits": [{"command_id": "prefetch", "num_bytes": 100}]},
    ])
    _write(tmp_path / "server/physical_action_ack.jsonl", [
        {"action": "PREFETCH_GPU", "command_id": "prefetch", "ts_ms": 1045.},
    ])
    if stream_timing:
        _write(tmp_path / "client_1/workflows/task/child_stream_content.jsonl", [
            {"event": "llm_stream_http_transport", "request_id": "child-final",
             "last_raw_at_ms": 67., "raw_chunks": 10, "raw_bytes": 100,
             "raw_pull_total_ms": 15., "raw_pull_max_ms": 4.,
             "consumer_pause_total_ms": 25., "consumer_pause_max_ms": 8.,
             "stream_consumed": True},
            {"event": "llm_stream_http_transport", "request_id": "other",
             "last_raw_at_ms": 1., "raw_pull_total_ms": 500.},
        ])
    if close_timing:
        _write(tmp_path / "client_1/workflows/task/sandbox_audit.jsonl", [
            {"event": "native_session_retire_complete", "workflow_id": "workflow",
             "context_id": "child-ctx", "context_epoch": close_epoch,
             "session_id": "beliefkv-child", "close_start_monotonic_ms": 106.,
             "close_elapsed_ms": close_elapsed, "enqueue_to_close_start_ms": 4.},
        ])
    return audit(tmp_path)


def test_join_audit_separates_native_completion_callback_return_and_parent(tmp_path):
    report = _run(tmp_path)
    [row] = report["rows"]
    assert row["child_result_identity_matches"]
    assert row["native_done_to_client_result_ms"] == 40.
    assert row["client_result_to_child_return_ms"] == 20.
    assert row["child_return_to_join_ms"] == 5.
    assert row["join_to_parent_submit_ms"] == 20.
    assert row["parent_submit_to_first_service_ms"] == 25.
    assert row["parent_next_request_id"] == "parent-next"
    assert row["native_submit_lead_to_child_native_done_ms"] == 10.
    assert report["completion_intervals"]["native_done_to_client_result_ms"]["count"] == 1


def test_join_audit_keeps_completion_identity_mismatch_unknown(tmp_path):
    report = _run(tmp_path, native_epoch=2)
    [row] = report["rows"]
    assert row["child_result_identity_matches"] is False
    assert row["native_done_to_client_result_ms"] is None
    assert row["client_result_to_child_return_ms"] is None
    assert report["child_return_observed_count"] == 1


def test_join_audit_retains_tool_result_for_false_final_stage_diagnosis(tmp_path):
    [row] = _run(tmp_path, client_tool_calls=1)["rows"]
    assert row["child_client_tool_call_count"] == 1
    assert row["child_result_identity_matches"]


def test_join_audit_splits_http_consumer_and_callback_intervals(tmp_path):
    report = _run(tmp_path, stream_timing=True)
    [row] = report["rows"]
    assert row["native_done_to_client_finish_chunk_ms"] == 25.
    assert row["client_finish_chunk_to_callback_entry_ms"] == 10.
    assert row["callback_entry_to_client_result_ms"] == 5.
    assert row["native_done_to_last_http_read_ms"] == 27.
    assert row["last_http_read_to_callback_entry_ms"] == 8.
    assert row["child_http_stream_consumed"] is True
    assert row["child_http_consumer_pause_total_ms"] == 25.
    assert report["unique_child_http_intervals"]["child_http_raw_pull_total_ms"]["p50_ms"] == 15.


def test_join_audit_does_not_mix_native_and_mismatched_client_stream_times(tmp_path):
    report = _run(tmp_path, native_epoch=2, stream_timing=True)
    [row] = report["rows"]
    assert row["native_done_to_client_finish_chunk_ms"] is None
    assert row["native_done_to_last_http_read_ms"] is None
    assert row["callback_entry_to_client_result_ms"] is None
    assert row["child_http_raw_pull_total_ms"] == 15.


def test_join_audit_legacy_stream_timings_remain_unknown(tmp_path):
    report = _run(tmp_path)
    [row] = report["rows"]
    assert row["native_done_to_client_finish_chunk_ms"] is None
    assert row["client_finish_chunk_to_callback_entry_ms"] is None
    assert row["child_http_raw_pull_total_ms"] is None
    assert report["unique_child_http_intervals"]["child_http_raw_pull_total_ms"]["count"] == 0


def test_join_audit_reports_http_close_overlap_without_treating_it_as_reclamation(tmp_path):
    report = _run(tmp_path, close_timing=True)
    [row] = report["rows"]
    assert row["child_session_close_elapsed_ms"] == 10.
    assert row["child_session_close_queue_wait_ms"] == 4.
    assert row["child_return_to_session_close_start_ms"] == 6.
    assert row["child_return_to_session_http_complete_ms"] == 16.
    assert row["session_http_complete_to_parent_submit_ms"] == 9.
    assert row["session_http_overlap_with_join_to_submit_ms"] == 10.
    assert "not scheduler reference release" in report["completion_timing_scope"]


def test_join_audit_keeps_close_epoch_mismatch_and_legacy_unknown(tmp_path):
    for options in ({}, {"close_timing": True, "close_epoch": 2}):
        report = _run(tmp_path, **options)
        [row] = report["rows"]
        assert row["child_session_close_elapsed_ms"] is None
        assert row["session_http_overlap_with_join_to_submit_ms"] is None


def test_join_audit_allows_deferred_http_completion_after_parent_submission(tmp_path):
    [row] = _run(tmp_path, close_timing=True, close_elapsed=30.)["rows"]
    assert row["session_http_complete_to_parent_submit_ms"] == -11.
    assert row["session_http_overlap_with_join_to_submit_ms"] == 19.
