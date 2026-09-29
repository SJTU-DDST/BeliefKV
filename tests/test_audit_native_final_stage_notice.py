from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_native_final_stage_notice import audit


def test_notice_pairs_natural_return_and_excludes_later_tool(tmp_path: Path) -> None:
    path = tmp_path / "workflows/task/runtime_events.deepagents.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"kind": "spawn", "target_invocation_id": "child"},
        {"kind": "spawn", "target_invocation_id": "retry"},
        {"kind": "spawn", "target_invocation_id": "silent"},
        {"kind": "tool_end", "invocation_id": "child", "ts_ms": 10,
         "attributes": {"tool_name": "announce_completion_intent", "status": "success"}},
        {"kind": "llm_submit", "invocation_id": "child", "ts_ms": 12},
        {"kind": "llm_result", "invocation_id": "child", "ts_ms": 40},
        {"kind": "return", "invocation_id": "child", "ts_ms": 43,
         "attributes": {"outcome": "completed"}},
        {"kind": "tool_end", "invocation_id": "retry", "ts_ms": 20,
         "attributes": {"tool_name": "announce_completion_intent", "status": "success"}},
        {"kind": "tool_start", "invocation_id": "retry", "ts_ms": 21,
         "attributes": {"tool_name": "read_file"}},
        {"kind": "return", "invocation_id": "retry", "ts_ms": 50,
         "attributes": {"outcome": "completed"}},
        {"kind": "return", "invocation_id": "silent", "ts_ms": 60,
         "attributes": {"outcome": "completed"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    report = audit(tmp_path)
    assert report["counts"]["paired_natural_returns"] == 1
    assert report["counts"]["notices_invalidated_by_later_tool"] == 1
    assert report["counts"]["natural_returns_without_notice"] == 1
    assert report["counts"].get("stage_published_children", 0) == 0
    assert report["notice_to_return"]["p50_ms"] == 33
    assert report["final_submit_to_result"]["p50_ms"] == 28


def test_gpu_service_label_uses_server_clock_and_reports_cross_clock_lower_bound(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflows/task/runtime_events.deepagents.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"kind": "spawn", "target_invocation_id": "child"},
        {"kind": "tool_end", "invocation_id": "child", "ts_ms": 10,
         "attributes": {"tool_name": "announce_completion_intent", "status": "success"}},
        {"kind": "llm_submit", "invocation_id": "child", "ts_ms": 12},
        {"kind": "llm_result", "invocation_id": "child", "ts_ms": 40,
         "attributes": {"request_id": "r1"}},
        {"kind": "return", "invocation_id": "child", "ts_ms": 43,
         "attributes": {"outcome": "completed"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    server = tmp_path / "server.jsonl"
    server.write_text("\n".join(json.dumps(row) for row in [
        {"kind": "llm_submit", "ts_ms": 1012, "attributes": {"request_id": "r1"}},
        {"kind": "llm_result", "ts_ms": 1040, "attributes": {"request_id": "r1"}},
    ]) + "\n")
    service = tmp_path / "service.jsonl"
    service.write_text(json.dumps({
        "event": "gpu_service_sample", "service_start_ts_ms": 1015,
        "complete_ts_ms": 1025,
        "request_samples": [{"request_id": "r1"}],
    }) + "\n" + json.dumps({
        "event": "gpu_service_sample", "service_start_ts_ms": 1028,
        "complete_ts_ms": 1032,
        "request_samples": [{"request_id": "r1"}],
    }) + "\n")

    report = audit(tmp_path, server_events=server, service_audit=service)
    assert report["service_clock"]["server_submit_to_first_service"]["p50_ms"] == 3
    assert report["service_clock"]["notice_to_first_service_lower_bound"]["p50_ms"] == 5
    assert report["service_clock"]["first_service_to_server_result"]["p50_ms"] == 25
    assert (
        report["service_clock"]["first_service_to_child_return_lower_bound"]["p50_ms"]
        == 28
    )
    assert report["service_clock"]["scheduler_service_intervals"]["p50_ms"] == 14
    assert report["service_clock"]["interleaved_without_service"]["p50_ms"] == 11
    assert report["service_clock"]["records"][0]["first_service_to_return_lower_bound_ms"] == 28
    assert report["service_clock"]["records"][0]["notice_to_first_service_lower_bound_ms"] == 5
