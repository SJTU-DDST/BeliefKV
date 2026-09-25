from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from beliefkv.predictor.structured_frontier import (
    EmpiricalDistribution, WaitBelief, WaitBeliefKind,
)
from scripts.compare_qwen35_tool_timing_models import compare


class _FakeModel:
    def __init__(self, *, repeat: bool) -> None:
        self.repeat = repeat
        self.tool_feature_contract = (
            "observed_command_child_repeat_v2" if repeat else "legacy"
        )

    def predict(self, features):
        predicted = (
            features.previous_same_input_duration_ms
            if self.repeat and features.previous_same_input_duration_ms is not None
            else 200.0
        )
        return SimpleNamespace(
            wait_belief=WaitBelief(
                kind=WaitBeliefKind.TOOL,
                residual_duration=EmpiricalDistribution(
                    (predicted,), (1.0,), 1.0
                ),
                support_level="backoff",
            )
        )


def test_compare_first_trigger_matches_identity_and_explicit_prior(
    tmp_path: Path,
) -> None:
    waits = [{
        "workflow_id": "workflow", "tool_call_id": "call-1",
        "invocation_id": "child", "terminal_ts_ms": 110.0,
        "training_eligible_survival": True, "censored": False,
    }]
    decisions = [{
        "workflow_id": "workflow", "trigger_kind": "tool_start",
        "timestamp_ms": 10.0, "trigger_invocation_id": "child",
        "trigger_attributes": {
            "tool_call_id": "call-1", "tool_name": "execute",
            "is_child": True, "observed_command_class": "test_suite",
            "previous_same_input_status": "success",
            "previous_same_input_duration_ms": 100.0,
        },
        "invocations": [
            {"invocation_id": "sibling", "state": "wait_tool", "is_child": True},
            {"invocation_id": "child", "state": "wait_tool", "is_child": True},
        ],
    }]
    (tmp_path / "external_waits.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in waits)
    )
    (tmp_path / "frontier_decision_points.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in decisions)
    )
    result = compare(tmp_path, _FakeModel(repeat=False), _FakeModel(repeat=True))
    assert "child_long_with_prior" not in result["groups"]
    assert "child_cold_with_project_prior" not in result["groups"]
    assert result["groups"]["child_with_prior"]["reference"]["p50_absolute_error_ms"] == 100
    assert result["groups"]["child_with_prior"]["candidate"]["p50_absolute_error_ms"] == 0


def test_compare_reports_cold_project_prior_separately(tmp_path: Path) -> None:
    waits = [{
        "workflow_id": "workflow", "tool_call_id": "call",
        "invocation_id": "child", "terminal_ts_ms": 3010.0,
        "training_eligible_survival": True, "censored": False,
    }]
    decisions = [{
        "workflow_id": "workflow", "trigger_kind": "tool_start",
        "timestamp_ms": 10.0, "trigger_invocation_id": "child",
        "trigger_attributes": {
            "tool_call_id": "call", "tool_name": "execute",
            "is_child": True, "observed_command_class": "test_suite",
            "project_class_duration_median_ms": 3000.0,
            "project_class_completed_support": 16,
        },
        "invocations": [{
            "invocation_id": "child", "state": "wait_tool", "is_child": True,
        }],
    }]
    for name, items in (
        ("external_waits.jsonl", waits),
        ("frontier_decision_points.jsonl", decisions),
    ):
        (tmp_path / name).write_text(
            "".join(json.dumps(item) + "\n" for item in items)
        )
    result = compare(tmp_path, _FakeModel(repeat=False), _FakeModel(repeat=True))
    assert result["groups"]["child_long_cold_with_project_prior"][
        "joint_samples"
    ] == 1
