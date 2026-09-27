import numpy as np
import pytest

from scripts.evaluate_tool_balanced_clocks import (
    load_training_batches, namespace_batch, summarize, weights,
)


def test_weights_equalize_workflows_and_projects():
    rows = [
        {"project": project, "workflow": workflow}
        for project, workflow, count in (
            ("one", "one-1", 4),
            ("one", "one-2", 2),
            ("two", "two-1", 1),
        )
        for _ in range(count)
    ]
    assert np.allclose(weights(rows, "calls"), 1)
    by_workflow = weights(rows, "workflows")
    assert sum(by_workflow[:4]) == pytest.approx(sum(by_workflow[4:6]))
    by_project = weights(rows, "projects_and_workflows")
    assert sum(by_project[:6]) == pytest.approx(sum(by_project[6:]))
    assert by_project.sum() == pytest.approx(len(rows))
    with pytest.raises(ValueError, match="more than one project"):
        weights(rows + [{"project": "two", "workflow": "one-1"}],
                "workflows")


def test_threshold_scan_uses_same_true_long_calls_and_raw_eta():
    rows = [
        {
            "project": "one", "workflow": f"one-{index}",
            "duration_ms": duration,
            "probability": {mode: score for mode in (
                "calls", "workflows", "projects_and_workflows"
            )},
            "eta_ms": (
                {"calls": 800., "workflows": 850.,
                 "projects_and_workflows": 900.}
                if duration >= 600 else None
            ),
        }
        for index, (duration, score) in enumerate((
            (700., .82), (900., .96), (100., .83),
        ))
    ]
    report = summarize(rows)["modes"]["calls"]
    assert report["selected"] == 3
    assert report["true_windows"] == 2
    assert report["by_threshold"]["0.95"]["selected"] == 1
    assert report["by_threshold"]["0.95"][
        "selected_true_eta_calls_p50_absolute_error_ms"
    ] == 100.


def test_batch_loader_retains_replicated_tasks_but_rejects_duplicate_sources(
    monkeypatch, tmp_path,
):
    def fake_ids(path):
        return (["one__task", f"two__{path.name}"], [])

    def fake_rows(path, *, include_returned_failures):
        assert include_returned_failures
        return ([{
            "project": "one", "workflow": "same-workflow",
            "invocation": "child", "input_sha256": path.name,
            "tool_call_id": "tool", "start_ts_ms": 1., "duration_ms": 800.,
        }], {})

    monkeypatch.setattr(
        "scripts.evaluate_tool_balanced_clocks.require_complete_batch",
        fake_ids,
    )
    monkeypatch.setattr(
        "scripts.evaluate_tool_balanced_clocks.cold_calls", fake_rows,
    )
    batches = [tmp_path / "high", tmp_path / "low"]
    for batch in batches:
        result = batch / "one__task" / "result.json"
        result.parent.mkdir(parents=True)
        result.write_text(
            '{"instance_id": "one__task", "workflow_id": "same-workflow"}',
            encoding="utf-8",
        )
    rows, projects, metadata = load_training_batches(batches)
    assert len(rows) == 2
    assert len({row["workflow"] for row in rows}) == 2
    assert {row["task_id"] for row in rows} == {"one__task"}
    assert projects == {"one", "two"}
    assert metadata["workflow_runs"] == 4
    assert metadata["distinct_tasks"] == 3
    assert metadata["replicated_task_ids"] == 1
    with pytest.raises(ValueError, match="distinct"):
        load_training_batches([batches[0], batches[0]])
    with pytest.raises(ValueError, match="completed workflow identity"):
        namespace_batch(batches[0], [{"workflow": "other"}])
