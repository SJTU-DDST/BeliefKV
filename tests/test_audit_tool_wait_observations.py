from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_tool_wait_observations import audit


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _event(wf: str, index: int, ts_ms: float, kind: str,
           **attributes: object) -> dict:
    return {
        "event_id": f"{wf}:{index:03d}", "workflow_id": wf,
        "kind": kind, "ts_ms": ts_ms, "invocation_id": f"{wf}:child",
        "attributes": attributes,
    }


def _run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    workloads = root / "intent_workloads"
    ids = [f"seed-{i}" for i in range(4)] + ["target"]
    source = tmp_path / "source.json"
    _write_json(source, {
        "workloads": [{"instance_id": instance, "repo": "repo"}
                      for instance in ids],
    })
    _write_json(workloads / "manifest.json", {
        "instance_ids": ids,
        "config": {"workload_manifest": str(source)},
    })
    _write_json(workloads / "summary.json", {
        "workflow_count": 5,
        "tool_wait_shadow": {
            "published": 1, "errors": 0, "dropped": 0, "pending": 0,
        },
    })
    (root / "source_commit.txt").write_text("frozen\n", encoding="utf-8")
    for i, instance in enumerate(ids):
        base = i * 4_000.
        if instance == "target":
            base = 20_000.
        attrs = {
            "tool_call_id": f"call-{i}", "tool_name": "execute",
            "is_child": True, "observed_command_class": "python_inline",
            "observed_command_shape": "python_inline_complex",
        }
        if instance == "target":
            attrs.update({
                "project_shape_survivor_500ms_total_median_ms": 2400.,
                "project_shape_survivor_500ms_support": 4,
            })
        events = [
            _event(instance, 0, base, "workflow_start"),
            _event(instance, 1, base + 1, "tool_start", **attrs),
        ]
        if instance == "target":
            events.append(_event(
                instance, 2, base + 502, "tool_wait_observation",
                tool_call_id=f"call-{i}", tool_elapsed_ms=501.,
                project_shape_survivor_500ms_total_median_ms=2400.,
                project_shape_survivor_500ms_support=4,
                tool_wait_shape_eta_ms_p50=1899.,
            ))
        events.extend([
            _event(instance, 3, base + 2401, "tool_end",
                   tool_call_id=f"call-{i}", status="success"),
            _event(instance, 4, base + 2402, "workflow_end"),
        ])
        directory = workloads / "workflows" / instance
        _write_json(directory / "result.json", {"outcome": "completed"})
        path = directory / "runtime_events.deepagents.jsonl"
        path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
    return root


def test_complete_canary_audits_causal_survival_and_lag(tmp_path: Path) -> None:
    root = _run(tmp_path)
    result = audit(root, expected_workflows=5)
    assert result["workflow_count"] == 5
    assert result["supported_tool_starts"] == 1
    assert result["observed_calls"] == 1
    assert result["returned_after_750ms"] == 1
    assert result["observed_success_point_absolute_error"]["p50_ms"] == 0.
    assert result["observed_lag_after_500ms"]["p50_ms"] == 1.
    assert result["observed_return_lead"]["p50_ms"] == 1899.


def test_canary_rejects_partial_and_bad_timer_counters(tmp_path: Path) -> None:
    root = _run(tmp_path)
    with pytest.raises(ValueError, match="complete frozen"):
        audit(root, expected_workflows=4)
    result = root / "intent_workloads/workflows/seed-0/result.json"
    result.unlink()
    with pytest.raises(ValueError, match="missing terminal"):
        audit(root, expected_workflows=5)
    _write_json(result, {"outcome": "completed"})
    summary = root / "intent_workloads/summary.json"
    _write_json(summary, {
        "workflow_count": 5,
        "tool_wait_shadow": {
            "published": 0, "errors": 0, "dropped": 0, "pending": 0,
        },
    })
    with pytest.raises(ValueError, match="timer counters"):
        audit(root, expected_workflows=5)


def test_canary_rejects_stale_or_repeated_observation(tmp_path: Path) -> None:
    root = _run(tmp_path)
    path = (root / "intent_workloads/workflows/target/"
            "runtime_events.deepagents.jsonl")
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events.insert(-2, {
        **events[2], "event_id": "target:005", "ts_ms": events[2]["ts_ms"] + 1,
    })
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="supported open call"):
        audit(root, expected_workflows=5)
