import json

import pytest

from beliefkv.predictor.tool_window_shadow import ToolWindowEstimate
from scripts.audit_frozen_tool_window_shadow import audit


class _FakeHead:
    threshold = .8
    artifact_sha256 = "frozen-sha"
    training_projects = frozenset({"django"})

    def estimate(self, _attrs):
        return ToolWindowEstimate(.9, 1300., 1000.)


def _event(kind, ts_ms, *, status=None, signal=False):
    attrs = {"tool_call_id": "one"}
    if kind == "tool_start":
        attrs.update({
            "is_child": True, "tool_name": "execute",
            "input_sha256": "a" * 64,
            "tool_window_shadow_probability": .9,
            "tool_window_shadow_total_eta_ms": 1300.,
            "tool_window_shadow_artifact_sha256": "frozen-sha",
        })
    if kind == "tool_end":
        attrs["status"] = status
    if signal:
        attrs.update({
            "beliefkv_tool_window_100ms_shadow": True,
            "source": "deepagents_tool_window_shadow",
            "diagnostic_only": True,
            "tool_elapsed_ms": ts_ms,
            "tool_window_probability": .9,
            "tool_window_remaining_eta_ms": 1300. - ts_ms,
            "tool_window_artifact_sha256": "frozen-sha",
        })
    return {
        "kind": kind, "ts_ms": ts_ms,
        "invocation_id": "child" if kind != "workflow_end" else None,
        "attributes": attrs,
    }


def _batch(tmp_path, outcomes):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}")
    root = tmp_path / "run"
    workloads = root / "intent_workloads"
    workflows = workloads / "workflows"
    workflows.mkdir(parents=True)
    instances = [f"sphinx-doc__{index}" for index in range(len(outcomes))]
    (workloads / "manifest.json").write_text(json.dumps({
        "instance_ids": instances,
        "config": {
            "tool_window_shadow_artifact": str(artifact),
            "early_tool_wait_shadow": False,
        },
    }))
    (workloads / "summary.json").write_text(json.dumps({
        "workflow_count": len(instances),
        "workflows": [
            {"instance_id": item, "outcome": "completed"} for item in instances
        ],
        "tool_wait_shadow": {
            "published": len(instances), "errors": 0,
            "dropped": 0, "pending": 0,
        },
    }))
    for item, (status, end_ms) in zip(instances, outcomes):
        path = workflows / item
        path.mkdir()
        (path / "result.json").write_text("{}")
        events = [
            _event("tool_start", 0),
            _event("structured_action", 101, signal=True),
            _event("tool_end", end_ms, status=status),
            _event("workflow_end", end_ms + 100),
        ]
        (path / "runtime_events.deepagents.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
        )
    return root, artifact


def test_audit_reports_selected_success_and_error_without_physical_claim(
    tmp_path, monkeypatch,
):
    root, artifact = _batch(
        tmp_path, [("success", 900), ("error", 420)],
    )
    monkeypatch.setattr(
        "scripts.audit_frozen_tool_window_shadow.FrozenToolWindowShadow",
        lambda _path: _FakeHead(),
    )
    report = audit(root, artifact=artifact)
    assert report["first_cold_inputs"] == 2
    assert report["actual_start_to_return_600ms_windows"] == 1
    assert report["counts"] == {
        "observed": 2, "observed_error": 1,
        "observed_success": 1, "selected_starts": 2,
    }
    assert report["by_status"]["success"]["true_remaining_500ms"] == 1
    assert report["by_status"]["error"]["true_remaining_500ms"] == 0
    assert report["observed_workflows"] == 2
    assert report["largest_workflow_observation_share"] == .5
    assert report["by_workflow"]["sphinx-doc__0"] == {
        "first_cold_inputs": 1,
        "actual_start_to_return_600ms_windows": 1,
        "selected_starts": 1,
        "observed": 1,
        "true_remaining_500ms": 1,
        "eta_p50_absolute_error_ms": 400.,
    }
    assert report["by_workflow"]["sphinx-doc__1"][
        "actual_start_to_return_600ms_windows"
    ] == 0
    assert report["by_status"]["success"][
        "paired_eta_gain_vs_global"]["paired_positive_95pct"
    ] is False


def test_audit_rejects_stale_signal_and_timer_mismatch(tmp_path, monkeypatch):
    root, artifact = _batch(tmp_path, [("success", 80)])
    monkeypatch.setattr(
        "scripts.audit_frozen_tool_window_shadow.FrozenToolWindowShadow",
        lambda _path: _FakeHead(),
    )
    with pytest.raises(ValueError, match="live identity"):
        audit(root, artifact=artifact)
    path = root / "intent_workloads/workflows/sphinx-doc__0"
    trace = path / "runtime_events.deepagents.jsonl"
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    events[2]["ts_ms"] = 900
    events[3]["ts_ms"] = 1000
    trace.write_text("".join(json.dumps(event) + "\n" for event in events))
    summary = root / "intent_workloads/summary.json"
    data = json.loads(summary.read_text())
    data["tool_wait_shadow"]["published"] = 2
    summary.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="timer accounting"):
        audit(root, artifact=artifact)
