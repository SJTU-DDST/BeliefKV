import json

from scripts.audit_join_transfer_windows import audit


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _run(tmp_path, *, native_epoch=1, client_tool_calls=0):
    _write(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "safe_point_census", "ts_ms": 1000., "monotonic_ms": 0.},
        {"event": "prefetch_native_issued", "source": "join_ticket",
         "command_id": "prefetch", "context_id": "parent", "node_id": 10,
         "join_id": "join", "child_invocation_id": "child",
         "child_request_id": "child-final"},
    ])
    identity = {"invocation_id": "child", "context_id": "child-ctx", "context_epoch": 1}
    parent = {"invocation_id": "root", "context_id": "parent", "context_epoch": 3}
    _write(tmp_path / "client_1/workflows/task/runtime_events.deepagents.jsonl", [
        {"kind": "llm_result", "ts_ms": 80., **identity,
         "attributes": {"request_id": "child-final", "finish_reason": "stop",
                        "tool_call_count": client_tool_calls, "invalid_tool_call_count": 0}},
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
