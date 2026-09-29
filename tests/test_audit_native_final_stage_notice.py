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
    assert report["notice_to_return"]["p50_ms"] == 33
    assert report["final_submit_to_result"]["p50_ms"] == 28
