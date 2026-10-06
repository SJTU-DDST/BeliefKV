import json

from scripts.audit_prefetch_lifecycle import audit


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_lifecycle_keeps_expired_but_reused_and_missing_use_distinct(tmp_path):
    write_rows(tmp_path / "opportunities/admission_opportunities.jsonl", [
        {"event": "prefetch_native_issued", "command_id": "prefetch1",
         "source": "join_ticket", "workflow_id": "w", "context_id": "c",
         "context_epoch": 1, "node_id": 10, "ts_ms": 100., "child_request_id": "child"},
        {"event": "prefetch_native_issued", "command_id": "prefetch2",
         "source": "tool_wait", "workflow_id": "w", "context_id": "other",
         "context_epoch": 2, "node_id": 20, "ts_ms": 400.},
        {"event": "prefetch_residency_released", "command_id": "prefetch1",
         "reason": "service_window_expired", "ts_ms": 120.},
    ])
    write_rows(tmp_path / "server/transfer_telemetry.jsonl", [{
        "direction": "h2d", "node_ids": [10], "submit_ts_ms": 101.,
        "complete_ts_ms": 110., "submit_to_ack_ms": 9., "enqueue_to_submit_ms": 1.,
        "tagged_child_commits": [{"command_id": "prefetch1", "num_bytes": 100,
                                 "num_tokens_by_pool": {"kv": 5}}],
    }])
    write_rows(tmp_path / "server/runtime_events.sglang.jsonl", [{
        "kind": "llm_result", "ts_ms": 102., "attributes": {"request_id": "child"},
    }])
    write_rows(tmp_path / "server/physical_action_use.jsonl", [{
        "event": "beliefkv_prefetch_first_service", "command_id": "prefetch1",
        "ack_ts_ms": 110., "first_service_ts_ms": 200., "full_node_reused": True,
    }])
    result = audit(tmp_path)
    assert result["summary"]["join_submitted_before_eos"] == 1
    assert result["summary"]["full_reused"] == 1
    assert result["summary"]["full_use_unknown"] == 1
    assert result["rows"][0]["lease_events"][0]["reason"] == "service_window_expired"
    assert result["summary"]["redemoted_before_first_service"] == 0
    assert result["rows"][1]["actual_bytes"] is None
