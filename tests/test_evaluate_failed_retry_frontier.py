from __future__ import annotations

from beliefkv.predictor.structured_frontier import FrontierBeliefModel
from scripts.evaluate_failed_retry_frontier import evaluate


def _row(
    signature: str | None, status: str, *,
    duration_ms: float, timestamp_ms: float,
) -> dict:
    return {
        "workflow_id": "wf",
        "timestamp_ms": timestamp_ms,
        "training_eligible": True,
        "trigger_kind": "tool_start",
        "trigger_invocation_id": "child",
        "trigger_attributes": {
            "tool_name": "execute",
            "tool_family": "shell",
            "is_child": True,
            "observed_command_class": "test_suite",
            "input_sha256": signature,
            "previous_same_input_status": "error",
            "previous_same_input_duration_ms": 1200,
            "project_class_duration_median_ms": 3000,
            "project_class_completed_support": 16,
        },
        "invocations": [{
            "invocation_id": "child",
            "state": "wait_tool",
            "agent_definition_id": "worker",
            "is_child": True,
            "active_tool_family": "shell",
            "active_tool_elapsed_ms": 0,
            "active_tool_count": 1,
            "current_sequence_tokens": 4096,
            "context_tokens": 4096,
        }],
        "labels": [{
            "invocation_id": "child",
            "next_boundary_status": status,
            "next_boundary_delay_ms": duration_ms,
            "target_training_eligible": {"external_wait": True},
        }],
    }


def test_failed_retry_paired_evaluation_deduplicates_inputs_and_splits_status() -> None:
    baseline = FrontierBeliefModel(
        tool_feature_contract="observed_command_child_project_v3"
    )
    baseline.project_error_p90_ms = 200
    candidate = FrontierBeliefModel(
        tool_feature_contract="observed_command_child_failed_repeat_v4"
    )
    candidate.failed_repeat_error_p90_ms = 200
    report = evaluate([
        _row("same", "success", duration_ms=1220, timestamp_ms=100),
        _row("same", "error", duration_ms=9000, timestamp_ms=200),
        _row("different", "error", duration_ms=1250, timestamp_ms=300),
        _row(None, "success", duration_ms=1230, timestamp_ms=400),
        _row("censored", "censored", duration_ms=1250, timestamp_ms=500),
    ], baseline, candidate)

    assert report["first_per_workflow_invocation_input"]["count"] == 2
    assert report["first_per_workflow_invocation_input"]["candidate"][
        "absolute_error_p50_ms"
    ] == 35
    assert report["natural_success"]["count"] == 1
    assert report["natural_error"]["count"] == 1
    assert report["candidate_failed_input_support"] == 2
    assert report["first_per_workflow_invocation_input"]["baseline"][
        "absolute_error_p50_ms"
    ] > 1700
