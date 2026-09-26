from __future__ import annotations

import pytest

from scripts.evaluate_cold_tool_structure_holdout import (
    _features, _paired_long_gain, _threshold, _timing, cold_calls, evaluate,
)
import numpy as np
import json


def _row(project="train", duration_ms=3000, workflow="wf", **extra):
    return {
        "project": project, "workflow": workflow,
        "class": "python_inline", "shape": "python_inline_complex",
        "input_chars": 100, "duration_ms": duration_ms,
        **extra,
    }


def test_structure_features_do_not_read_future_duration():
    start = _row(inline_structure={
        "nodes": 2, "loops": 1, "calls": 3,
        "functions": 0, "comprehensions": 0, "exception_blocks": 1,
    })
    a = _features([start], "structure", {"python_inline_complex": 1})
    b = _features([
        {**start, "duration_ms": 15, "status": "error"},
    ], "structure", {"python_inline_complex": 1})
    np.testing.assert_array_equal(a, b)
    assert list(a[0, 2:]) == [2, 1, 3, 0, 0, 1]
    assert list(_features([
        _row(inline_structure=None),
    ], "structure", {"python_inline_complex": 1})[0, 2:]) == [-1] * 6


def test_holdout_must_exclude_training_projects():
    with pytest.raises(ValueError, match="disjoint"):
        evaluate([_row()], [_row()])


def test_train_threshold_requires_multiple_workflows_and_precision():
    rows = [_row(workflow="a") for _ in range(8)]
    assert _threshold(rows, np.ones(len(rows)))["frozen_threshold"] is None
    rows = [
        _row(workflow="a" if i < 4 else "b") for i in range(8)
    ] + [_row(duration_ms=30) for _ in range(3)]
    result = _threshold(rows, np.ones(len(rows)))
    assert result["frozen_threshold"] is not None
    assert result["train_workflow_cv"]["0.2"]["precision"] < .75


def test_timing_empty_selection_does_not_look_like_perfect_accuracy():
    assert _timing([], np.array([]))["p50_error_ms"] is None


def test_paired_long_gain_requires_independent_workflows_and_shared_support():
    rows = [_row(workflow=f"wf-{i // 2}") for i in range(10)]
    baseline = np.full(10, 2000.)
    better = np.full(10, 2800.)
    result = _paired_long_gain(rows, baseline, better, draws=100)
    assert result["status"] == "workflow_cluster_bootstrap"
    assert result["workflows"] == 5
    assert result["p50_error_gain_ms"] == 800.
    assert result["ci95_lower_ms"] == 800.
    assert result["paired_positive_95pct"] is True
    worse = _paired_long_gain(rows, baseline, np.full(10, 1000.), draws=100)
    assert worse["ci95_upper_ms"] == -1000.
    assert worse["paired_positive_95pct"] is False
    insufficient = _paired_long_gain(rows[:8], baseline[:8], better[:8])
    assert insufficient["status"] == "insufficient_independent_workflows"
    assert insufficient["ci95_lower_ms"] is None
    assert insufficient["paired_positive_95pct"] is False
    with pytest.raises(ValueError, match="identical"):
        _paired_long_gain(rows, baseline[:8], better)


def test_cold_calls_do_not_label_failed_or_open_as_short(tmp_path):
    workflow = tmp_path / "wf__a"
    workflow.mkdir()
    events = []
    for number, status, duration in (
        (1, "success", 100), (2, "error", 200)
    ):
        attrs = {
            "tool_name": "execute", "tool_call_id": str(number),
            "input_chars": 10, "is_child": True,
            "observed_inline_structure": {"nodes": 2},
        }
        events.extend([
            {"kind": "tool_start", "ts_ms": number * 1000,
             "sequence": number * 2, "workflow_id": "wf",
             "invocation_id": "deepagents-invocation:child", "attributes": attrs},
            {"kind": "tool_end", "ts_ms": number * 1000 + duration,
             "sequence": number * 2 + 1, "workflow_id": "wf",
             "invocation_id": "deepagents-invocation:child", "attributes": {
                 **attrs, "status": status,
             }},
        ])
    events.append({
        "kind": "tool_start", "ts_ms": 3000, "sequence": 6,
        "workflow_id": "wf", "invocation_id": "deepagents-invocation:child", "attributes": {
            "tool_name": "execute", "tool_call_id": "open",
            "is_child": True,
        },
    })
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (workflow / "sandbox_audit.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in (
            {
                "event": "agent_tool_duplicate_suppressed",
                "agent_scope": "autonomous:supervisor", "ts_ms": 500,
            },
            {
                "event": "agent_tool_duplicate_suppressed",
                "agent_scope": "planned:child:deepagents-invocation:child",
                "ts_ms": 2050,
            },
        )),
        encoding="utf-8",
    )
    rows, counts = cold_calls(tmp_path)
    assert len(rows) == 1
    assert rows[0]["inline_structure"] == {"nodes": 2}
    assert counts["completed_cold_child_status"] == {"error": 1, "success": 1}
    assert counts["open_child_execute_unlabeled"] == 1
    assert counts["completed_cold_child_excluded_after_intervention"] == 1


def test_evaluate_runs_frozen_project_split_without_heldout_threshold_search():
    train = [
        _row(
            project="train", workflow=f"train-{i // 20}",
            duration_ms=3200 if i % 3 == 0 else 120,
            inline_structure={"loops": i % 3},
        )
        for i in range(100)
    ]
    heldout = [
        _row(
            project="heldout", workflow=f"heldout-{i // 10}",
            duration_ms=2800 if i % 4 == 0 else 100,
            inline_structure={"loops": i % 4},
        )
        for i in range(20)
    ]
    report = evaluate(train, heldout)
    assert report["train_long"] == 34
    assert report["train_inline_structure_count"] == len(train)
    assert report["evidence_gates"]["train_long_from_two_projects"] is False
    assert report["evidence_gates"]["structure_paired_gain_positive_95pct"] is False
    assert report["evidence_gates"]["frozen_structure_trigger_covers_long"] is False
    assert report["heldout"]["heldout"]["paired_long_gain_vs_baselines"]["class"][
        "status"
    ] == "insufficient_independent_workflows"
    head = report["heldout"]["heldout"]["heads"]["structure"]
    assert head["long_precision"] == (
        head["true_long"] / head["selected"] if head["selected"] else None
    )
    assert head["true_long_workflows"] <= head["true_long"]
    assert report["evidence_gates"]["all_met"] is False
    assert report["heldout"]["heldout"]["zero_duration_oracle_long_baseline"][
        "count"
    ] == 5
    for mode in ("class", "shape", "structure"):
        assert report["heads"][mode]["cv_scored_count"] == len(train)
        assert report["heldout"]["heldout"]["heads"][mode][
            "oracle_long_timing"
        ]["count"] == 5
