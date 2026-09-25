from __future__ import annotations

import json

from scripts.audit_stream_content_accounting import audit


def test_stream_content_milestone_exceeding_final_visible_text_is_reported(
    tmp_path,
) -> None:
    workflow = tmp_path / "workflows" / "pytest-dev__task"
    workflow.mkdir(parents=True)
    events = [
        {
            "kind": "structured_action",
            "attributes": {
                "request_id": "req",
                "beliefkv_child_substantial_content_shadow": True,
                "content_threshold_chars": 2400,
            },
        },
        {
            "kind": "llm_result",
            "attributes": {
                "request_id": "req",
                "output_chars": 1603,
                "tool_call_count": 0,
            },
        },
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    report = audit(workflow.parent)
    assert report["totals"]["paired"] == 1
    assert report["totals"]["large_milestone_exceeds_final"] == 1
    assert report["examples"][0]["max_milestone"] == 2400
