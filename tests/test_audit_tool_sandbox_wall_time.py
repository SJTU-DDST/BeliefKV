from __future__ import annotations

import json

from scripts.audit_tool_sandbox_wall_time import audit


def test_sandbox_audit_keeps_lock_and_execution_segments_separate(tmp_path):
    workflow = tmp_path / "workflows" / "pydata__sample"
    workflow.mkdir(parents=True)
    events = [
        {"kind": "tool_start", "ts_ms": 1000, "sequence": 1,
         "workflow_id": "wf", "invocation_id": "child", "attributes": {
             "tool_name": "execute", "tool_call_id": "call",
             "is_child": True}},
        {"kind": "tool_end", "ts_ms": 4000, "sequence": 2,
         "workflow_id": "wf", "invocation_id": "child", "attributes": {
             "tool_name": "execute", "tool_call_id": "call",
             "status": "success"}},
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (workflow / "sandbox_audit.jsonl").write_text(
        json.dumps({
            "event": "sandbox_execute", "ts_ms": 3999,
            "duration_ms": 2990, "lock_wait_ms": 2750,
            "execute_elapsed_ms": 230,
        }) + "\n",
        encoding="utf-8",
    )
    report = audit(tmp_path / "workflows")
    stats = report["metrics"]["cold_long_child"]
    assert report["counts"]["matched"] == 1
    assert stats["segmented_sandbox_count"] == 1
    assert stats["lock_wait_p50_ms"] == 2750
    assert stats["execute_elapsed_p50_ms"] == 230
    assert stats["lock_wait_dominant_count"] == 1
