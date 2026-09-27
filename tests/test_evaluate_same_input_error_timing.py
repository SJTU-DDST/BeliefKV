import json

import pytest

from scripts.evaluate_same_input_error_timing import (
    candidates, evaluate, validate_manifest,
)


def _row(wf, duration, previous, *, status="success", baseline=1800):
    return {
        "project": "repo", "workflow": wf, "invocation": "child",
        "input_sha256": "identical", "is_child": True,
        "status": status, "duration_ms": duration, "start_ts_ms": 10000,
        "previous": previous, "project_class_duration_median_ms": baseline,
        "project_class_completed_support": 16,
    }


def test_exact_failed_input_requires_strictly_completed_causal_history():
    assert candidates([
        _row("wf", 1700, (1700, 9999, "error")),
    ])[0]["exact_failed_input_ms"] == 1700
    assert not candidates([
        _row("wf", 1700, (1700, 10000, "error")),
        _row("wf", 1700, (1700, 10001, "error")),
        _row("wf", 1700, (1700, 9000, "success")),
        _row("wf", 1700, None),
    ])


def test_paired_comparison_uses_identical_calls_and_workflow_clusters():
    rows = [
        _row(f"wf-{i}", 1750, (1700, 9500, "error"))
        for i in range(6)
    ]
    rows.append(_row("wf-no-support", 1650, (1700, 9500, "error"),
                     baseline=None))
    report = evaluate(rows)
    matched = report["matched"]
    assert matched["count"] == 6
    assert matched["workflow_count"] == 6
    assert matched["baseline_sources"] == {
        "project_class_duration_median_ms": 6,
    }
    assert matched["baseline_error_p50_ms"] == 50
    assert matched["exact_error_p50_ms"] == 50
    assert type(matched["exact_within_500ms"]) is int
    assert matched["paired_workflow_gain"]["ci95_lower_ms"] == 0


def test_prefers_100ms_project_history_on_same_cohort():
    row = _row("wf", 1600, (1600, 9500, "error"), baseline=3000)
    row.update({
        "project_shape_survivor_100ms_total_median_ms": 1700.,
        "project_shape_survivor_100ms_support": 4,
    })
    matched = candidates([row])
    assert matched[0]["baseline_ms"] == 1700.
    assert matched[0]["baseline_source"] == (
        "project_shape_survivor_100ms_total_median_ms"
    )


def test_unique_input_paired_cohort_is_not_multiplied_by_retries():
    first = _row("wf", 1750, (1700, 9000, "error"))
    later = {
        **first, "start_ts_ms": 20000,
        "previous": (1750, 19000, "error"), "duration_ms": 1750,
    }
    report = evaluate([later, first])
    assert report["matched"]["count"] == 2
    assert report["first_per_workflow_invocation_input"]["count"] == 1
    assert report["first_per_workflow_invocation_input"][
        "exact_error_p50_ms"
    ] == 50


def test_manifest_validation_refuses_missing_and_unexpected_workflows(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "workloads": [
            {"instance_id": "django__one"}, {"instance_id": "pydata__two"},
        ],
    }), encoding="utf-8")
    workflows = tmp_path / "workflows"
    (workflows / "django__one").mkdir(parents=True)
    (workflows / "django__one" / "runtime_events.deepagents.jsonl").touch()
    with pytest.raises(ValueError, match="missing"):
        validate_manifest(workflows, manifest)
    (workflows / "pydata__two").mkdir()
    (workflows / "pydata__two" / "runtime_events.deepagents.jsonl").touch()
    assert validate_manifest(workflows, manifest)["projects"] == [
        "django", "pydata",
    ]
    (workflows / "other__three").mkdir()
    (workflows / "other__three" / "runtime_events.deepagents.jsonl").touch()
    with pytest.raises(ValueError, match="unexpected"):
        validate_manifest(workflows, manifest)
