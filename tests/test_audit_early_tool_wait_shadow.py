import json

import pytest

from scripts.audit_early_tool_wait_shadow import audit


def _event(kind, ts_ms, call_id=None, **attrs):
    return {
        "kind": kind, "ts_ms": ts_ms, "invocation_id": "child",
        "attributes": {"tool_call_id": call_id, **attrs},
    }


def _write(workflows, name, rows):
    folder = workflows / name
    folder.mkdir(parents=True)
    (folder / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )


def test_audit_includes_missing_workflows_and_late_or_expired_landmarks(tmp_path):
    workflows = tmp_path / "workflows"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "workloads": [{"instance_id": "a"}, {"instance_id": "b"}]
    }))
    attrs = {
        "is_child": True, "tool_name": "execute",
        "input_sha256": "a" * 64,
        "project_shape_survivor_100ms_total_median_ms": 2400,
        "project_shape_survivor_100ms_support": 4,
        "project_shape_survivor_100ms_deviation_p90_ms": 100,
    }
    _write(workflows, "a", [
        _event("tool_start", 1000, "candidate", **attrs),
        _event("structured_action", 1130, "candidate",
               beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
        _event("tool_end", 1700, "candidate", status="success"),
        _event("tool_start", 2000, "short", **{
            **attrs, "input_sha256": "b" * 64,
        }),
        _event("tool_end", 2080, "short", status="success"),
        _event("tool_start", 3000, "missed", **{
            **attrs, "input_sha256": "c" * 64,
        }),
        _event("tool_end", 3900, "missed", status="error"),
        _event("workflow_end", 4000),
    ])
    report = audit(workflows, manifest=manifest)
    assert report["missing_workflows"] == ["b"]
    assert report["counts"]["eligible_tool_starts"] == 3
    assert report["counts"]["observations"] == 1
    assert report["counts"]["returned_by_100ms"] == 1
    assert report["counts"]["missed_live_landmark"] == 1
    assert report["counts"]["lead_at_least_500ms"] == 1
    assert report["counts"]["successful_lead_at_least_500ms"] == 1
    assert report["success_predicted_500ms_lead"] == {
        "selected": 1, "true_at_least_500ms": 1,
        "returned_before_500ms": 0,
    }
    assert report["successful_distinct_inputs"] == 1
    assert report["dispatch_lateness_p50_ms"] == 30
    assert report["lead_p50_ms"] == 570
    assert report["eta_absolute_error_p50_ms"] == 1700
    assert report["per_workflow"]["a"]["distinct_candidate_inputs"] == 3
    assert report["per_workflow"]["a"]["distinct_observed_returned_inputs"] == 1


def test_audit_rejects_orphan_and_wrong_manifest(tmp_path):
    workflows = tmp_path / "workflows"
    _write(workflows, "a", [
        _event("structured_action", 200, "missing",
               beliefkv_tool_wait_early_shadow=True),
    ])
    with pytest.raises(ValueError, match="orphan early observation"):
        audit(workflows)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"workloads": [{"instance_id": "b"}]}))
    with pytest.raises(ValueError, match="outside frozen manifest"):
        audit(workflows, manifest=manifest)


def test_audit_rejects_signal_with_ineligible_frozen_history(tmp_path):
    workflows = tmp_path / "workflows"
    _write(workflows, "a", [
        _event("tool_start", 1000, "candidate", is_child=True,
               tool_name="execute",
               project_shape_survivor_100ms_total_median_ms=2400,
               project_shape_survivor_100ms_support=4,
               project_shape_survivor_100ms_deviation_p90_ms=float("nan")),
        _event("structured_action", 1101, "candidate",
               diagnostic_only=True, beliefkv_tool_wait_early_shadow=True),
    ])
    with pytest.raises(ValueError, match="unqualified early observation"):
        audit(workflows)


def test_audit_reports_repeated_failed_input_separately(tmp_path):
    workflows = tmp_path / "workflows"
    attrs = {
        "is_child": True, "tool_name": "execute",
        "input_sha256": "b" * 64,
        "project_shape_survivor_100ms_total_median_ms": 1700,
        "project_shape_survivor_100ms_support": 4,
        "project_shape_survivor_100ms_deviation_p90_ms": 200,
    }
    _write(workflows, "a", [
        _event("tool_start", 1000, "first", **attrs),
        _event("structured_action", 1101, "first",
               beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
        _event("tool_end", 2800, "first", status="error"),
        _event("tool_start", 4000, "second", **attrs,
               previous_same_input_status="error"),
        _event("structured_action", 4101, "second",
               beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
        _event("tool_end", 5700, "second", status="success"),
    ])
    report = audit(workflows)
    assert report["counts"]["observations"] == 2
    assert report["counts"]["repeat_after_error_starts"] == 1
    assert report["per_workflow"]["a"]["distinct_candidate_inputs"] == 1
    assert report["per_workflow"]["a"]["distinct_observed_returned_inputs"] == 1
    assert report["eta_absolute_error_p50_ms"] == 50
    assert report["success_eta_absolute_error_p50_ms"] == 0
    assert report["failed_eta_absolute_error_p50_ms"] == 100
    assert report["distinct_input_eta_absolute_error_p50_ms"] == 100
    assert report["successful_distinct_inputs"] == 1
    assert report["success_predicted_500ms_lead"]["true_at_least_500ms"] == 1
    assert report["error_predicted_500ms_lead"]["true_at_least_500ms"] == 1


def test_audit_does_not_invent_distinct_input_for_missing_hash(tmp_path):
    workflows = tmp_path / "workflows"
    attrs = {
        "is_child": True, "tool_name": "execute",
        "project_shape_survivor_100ms_total_median_ms": 2400,
        "project_shape_survivor_100ms_support": 4,
        "project_shape_survivor_100ms_deviation_p90_ms": 100,
    }
    _write(workflows, "a", [
        _event("tool_start", 1000, "candidate", **attrs),
        _event("structured_action", 1101, "candidate",
               beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
        _event("tool_end", 2000, "candidate", status="success"),
    ])
    report = audit(workflows)
    assert report["counts"]["successful_observed_missing_input_sha256"] == 1
    assert report["successful_distinct_inputs"] == 0


def test_audit_counts_predicted_window_that_ends_too_soon(tmp_path):
    workflows = tmp_path / "workflows"
    attrs = {
        "is_child": True, "tool_name": "execute",
        "input_sha256": "c" * 64,
        "project_shape_survivor_100ms_total_median_ms": 2400,
        "project_shape_survivor_100ms_support": 4,
        "project_shape_survivor_100ms_deviation_p90_ms": 100,
    }
    _write(workflows, "a", [
        _event("tool_start", 1000, "candidate", **attrs),
        _event("structured_action", 1101, "candidate",
               beliefkv_tool_wait_early_shadow=True, diagnostic_only=True),
        _event("tool_end", 1400, "candidate", status="success"),
    ])
    report = audit(workflows)
    assert report["success_predicted_500ms_lead"] == {
        "selected": 1, "true_at_least_500ms": 0,
        "returned_before_500ms": 1,
    }
    assert report["successful_distinct_inputs_lead_at_least_500ms"] == 0
