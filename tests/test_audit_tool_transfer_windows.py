import json

from scripts.audit_tool_transfer_windows import audit


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_tool_transfer_audit_uses_tool_completion_not_first_gpu_service(tmp_path):
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "safe_point_census", "ts_ms": 1000., "monotonic_ms": 0.},
        {"event": "prefetch_native_issued", "source": "tool_wait",
         "command_id": "c1", "context_id": "ctx", "invocation_id": "child",
         "active_tool_ids": ["a", "b"]},
    ])
    write_rows(tmp_path / "client_64/workflows/task/runtime_events.deepagents.jsonl", [
        {"kind": "tool_end", "ts_ms": 300., "invocation_id": "child",
         "attributes": {"tool_run_id": "a"}},
        {"kind": "tool_end", "ts_ms": 500., "invocation_id": "child",
         "attributes": {"tool_run_id": "b"}},
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [
        {"submit_ts_ms": 1200., "tagged_child_commits": [{"command_id": "c1", "num_bytes": 100}]},
    ])
    write_rows(tmp_path / "server/physical_action_use.jsonl", [
        {"event": "beliefkv_prefetch_first_service", "command_id": "c1",
         "full_node_reused": True, "ts_ms": 2500.},
    ])
    result = audit(tmp_path)
    assert result["started_100_to_1000ms_before_tool_end_count"] == 1
    assert result["median_submit_lead_to_tool_end_ms"] == 300.
    assert result["rows"][0]["full_first_service_reused"] is True
