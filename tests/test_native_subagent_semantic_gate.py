from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_native_subagent_semantic_gate import _analyze_workflow


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows),
        encoding="utf-8",
    )


def test_semantic_gate_requires_physical_parent_prefix_reuse(tmp_path: Path) -> None:
    workflow = tmp_path / "workflows" / "repo__task-1"
    workflow.mkdir(parents=True)
    parent = "wf:root"
    parent_context = "wf:context:root"
    rows = [
        {
            "sequence": 1,
            "ts_ms": 1.0,
            "kind": "invocation_create",
            "invocation_id": parent,
            "context_id": parent_context,
            "relation_type": "root",
            "attributes": {},
        },
        {
            "sequence": 2,
            "ts_ms": 2.0,
            "kind": "llm_submit",
            "invocation_id": parent,
            "context_id": parent_context,
            "context_epoch": 0,
            "attributes": {"request_id": "req-pre", "runtime_internal": False},
        },
        {
            "sequence": 3,
            "ts_ms": 3.0,
            "kind": "invocation_create",
            "invocation_id": "child-a",
            "context_id": "ctx-a",
            "parent_invocation_id": parent,
            "context_mode": "fresh",
            "attributes": {"source": "deepagents_task"},
        },
        {
            "sequence": 4,
            "ts_ms": 3.0,
            "kind": "spawn",
            "invocation_id": parent,
            "target_invocation_id": "child-a",
            "attributes": {
                "source": "deepagents_task",
                "tool_call_id": "task-a",
            },
        },
        {
            "sequence": 5,
            "ts_ms": 3.0,
            "kind": "invocation_create",
            "invocation_id": "child-b",
            "context_id": "ctx-b",
            "parent_invocation_id": parent,
            "context_mode": "fresh",
            "attributes": {"source": "deepagents_task"},
        },
        {
            "sequence": 6,
            "ts_ms": 3.0,
            "kind": "spawn",
            "invocation_id": parent,
            "target_invocation_id": "child-b",
            "attributes": {
                "source": "deepagents_task",
                "tool_call_id": "task-b",
            },
        },
        {
            "sequence": 7,
            "ts_ms": 3.0,
            "kind": "join_create",
            "join_id": "join-1",
            "member_invocation_ids": ["child-a", "child-b"],
            "attributes": {"source": "deepagents_task", "mode": "all"},
        },
        {
            "sequence": 8,
            "ts_ms": 3.0,
            "kind": "join_wait",
            "join_id": "join-1",
            "invocation_id": parent,
            "attributes": {"source": "deepagents_task"},
        },
        {
            "sequence": 9,
            "ts_ms": 9.0,
            "kind": "join_satisfied",
            "join_id": "join-1",
            "attributes": {"source": "deepagents_task"},
        },
        {
            "sequence": 10,
            "ts_ms": 10.0,
            "kind": "llm_submit",
            "invocation_id": parent,
            "context_id": parent_context,
            "context_epoch": 1,
            "attributes": {"request_id": "req-post", "runtime_internal": False},
        },
    ]
    _write_jsonl(workflow / "runtime_events.deepagents.jsonl", rows)
    (workflow / "result.json").write_text(
        json.dumps({"semantic_gate_controlled_stop": True}), encoding="utf-8"
    )
    (workflow / "trajectory.json").write_text(
        json.dumps(
            [
                {
                    "message_type": "tool",
                    "name": "task",
                    "tool_call_id": "task-a",
                    "content": "repository report",
                },
                {
                    "message_type": "tool",
                    "name": "task",
                    "tool_call_id": "task-b",
                    "content": "test report",
                },
            ]
        ),
        encoding="utf-8",
    )
    physical = {
        "req-pre": {
            "request_id": "req-pre",
            "event": "request_physical_start",
            "prompt_tokens": 10_000,
            "cache_hit_tokens": 0,
        },
        "req-post": {
            "request_id": "req-post",
            "event": "request_physical_start",
            "prompt_tokens": 13_000,
            "cache_hit_tokens": 9_500,
        },
    }

    result = _analyze_workflow(
        workflow,
        physical,
        minimum_prefix_reuse=0.90,
        require_controlled_stop=True,
    )

    assert result["passed"]
    assert result["checks"]["child_reports_increase_parent_prompt"]
    assert result["retained_parent_prefix_ratio"] == 0.95
    assert result["total_post_prompt_hit_ratio"] < 0.90
    assert result["matched_task_report_count"] == 2
