from __future__ import annotations

import json
from pathlib import Path

from scripts.pilot_project_level_tool_history import replay


def _workflow(root: Path, name: str, *, start: float, end: float) -> None:
    path = root / name / "runtime_events.deepagents.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    for sequence, kind, ts in (
        (1, "tool_start", start), (2, "tool_end", end)
    ):
        attrs = {
            "tool_call_id": name, "tool_name": "execute",
            "input_sha256": "identical", "is_child": True,
        }
        if kind == "tool_end":
            attrs["status"] = "success"
        else:
            attrs["observed_command_class"] = "test_suite"
        events.append({
            "kind": kind, "sequence": sequence, "ts_ms": ts,
            "workflow_id": name, "invocation_id": "child",
            "attributes": attrs,
        })
    path.write_text("".join(json.dumps(event) + "\n" for event in events))


def test_cross_workflow_history_only_uses_prior_completed_calls(tmp_path: Path) -> None:
    _workflow(tmp_path, "django__early", start=0, end=100)
    _workflow(tmp_path, "django__too_early", start=50, end=80)
    _workflow(tmp_path, "django__after", start=110, end=220)
    _workflow(tmp_path, "sphinx__different_project", start=120, end=240)
    result = replay(tmp_path)
    assert result["counts"]["all_completed_child_execute"] == 4
    assert result["counts"]["new_successful_cross_workflow_prior"] == 1
