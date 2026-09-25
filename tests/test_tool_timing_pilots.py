from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_tool_sandbox_wall_time import match
from scripts.pilot_online_project_tool_prior import pilot


def _workflow(root: Path, name: str, calls: list[tuple[float, float]]) -> Path:
    path = root / name
    path.mkdir(parents=True)
    events = []
    for index, (start, end) in enumerate(calls):
        for kind, ts in (("tool_start", start), ("tool_end", end)):
            attrs = {
                "tool_name": "execute", "tool_call_id": f"{name}-{index}",
                "input_sha256": f"command-{index}", "is_child": True,
                "input_chars": 100, "observed_command_class": "test_suite",
            }
            if kind == "tool_end":
                attrs["status"] = "success"
            events.append({
                "kind": kind, "ts_ms": ts, "sequence": len(events) + 1,
                "workflow_id": name, "invocation_id": "child",
                "attributes": attrs,
            })
    (path / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    return path


def test_sandbox_match_rejects_ambiguous_timing(tmp_path: Path) -> None:
    path = _workflow(tmp_path, "django__one", [(0, 110), (200, 330)])
    audits = [
        {"event": "sandbox_execute", "ts_ms": 105, "duration_ms": 95},
        {"event": "sandbox_execute", "ts_ms": 310, "duration_ms": 95},
        {"event": "sandbox_execute", "ts_ms": 320, "duration_ms": 95},
    ]
    (path / "sandbox_audit.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in audits)
    )
    pairs, counts = match(path)
    assert counts["matched"] == 1
    assert counts["ambiguous"] == 1
    assert pairs[0]["outside_sandbox_ms"] == 15


def test_project_adaptation_never_uses_unfinished_history(tmp_path: Path) -> None:
    _workflow(tmp_path, "django__one", [(0, 100), (200, 300), (400, 500)])
    _workflow(tmp_path, "astropy__one", [(50, 150)])
    result = pilot(tmp_path, minimum_support=2)
    assert result["supported"]["count"] == 1
    assert result["by_project"]["django"]["supported"]["prior_p50_error_ms"] == 0
