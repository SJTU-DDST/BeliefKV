from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_repeated_tool_timing import _read_workflow, replay


def _event(kind: str, ts: int, seq: int, call: str, invocation: str,
           signature: str = "same", status: str = "success") -> dict:
    attrs = {"tool_call_id": call, "tool_name": "execute"}
    if kind == "tool_start":
        attrs.update(input_sha256=signature, observed_command_class="test_suite")
    else:
        attrs["status"] = status
    return {
        "kind": kind, "ts_ms": ts, "sequence": seq,
        "invocation_id": invocation, "workflow_id": "workflow",
        "attributes": attrs,
    }


def _write(root: Path, project: str, events: list[dict]) -> Path:
    path = root / f"{project}__task" / "runtime_events.deepagents.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path


def test_only_completed_earlier_same_invocation_available(tmp_path: Path) -> None:
    path = _write(tmp_path, "django", [
        _event("tool_start", 0, 1, "a", "child"),
        _event("tool_start", 10, 2, "concurrent", "child"),
        _event("tool_end", 100, 3, "a", "child"),
        _event("tool_start", 110, 4, "other", "other-child"),
        _event("tool_start", 120, 5, "repeat", "child"),
        _event("tool_end", 200, 6, "concurrent", "child"),
        _event("tool_end", 215, 7, "repeat", "child"),
        _event("tool_end", 300, 8, "other", "other-child"),
    ])
    rows = _read_workflow(path)
    by_start = {row["start_ts_ms"]: row for row in rows}
    assert by_start[0]["previous"] is None
    assert by_start[10]["previous"] is None
    assert by_start[110]["previous"] is None
    assert by_start[120]["previous"] == (100, 100, "success")


def test_project_held_out_repeat_report(tmp_path: Path) -> None:
    for project, duration in (("django", 3000), ("sphinx", 3200)):
        _write(tmp_path, project, [
            _event("tool_start", 0, 1, "first", "child"),
            _event("tool_end", duration, 2, "first", "child"),
            _event("tool_start", duration + 10, 3, "second", "child"),
            _event("tool_end", 2 * duration + 10, 4, "second", "child"),
        ])
    result = replay(tmp_path, minimum_class_samples=1)
    assert result["counts"]["has_completed_same_input"] == 2
    assert result["metrics"]["actual_at_least_2s:previous"]["p50_error_ms"] == 0
