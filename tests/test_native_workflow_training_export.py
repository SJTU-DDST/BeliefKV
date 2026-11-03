import json

import pytest

from scripts import export_native_workflow_training as exporting
from tests.test_workflow_work_distribution_training import execution


def captured_task(tmp_path, identity="original-task"):
    capture = tmp_path / "native_capture"
    task = capture / "client_1/workflows" / identity
    task.mkdir(parents=True)
    events, _requests, _progress = execution()
    (task / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event.to_dict()) + "\n" for event in events),
    )
    (task / "result.json").write_text(json.dumps({"natural_terminal": True, "agent_control": {}}))
    manifest = {
        "requested_workflows": 1,
        "workloads": [{"instance_id": identity, "repo": "locobench/original-project"}],
    }
    return capture, task, manifest


def test_native_export_keeps_original_task_and_project_identity_and_binds_raw_events(tmp_path):
    capture, task, manifest = captured_task(tmp_path)
    rows, evidence = exporting.capture_workflows(capture, manifest)
    assert len(rows) == 1 and rows[0]["workflow_id"] == "wf"
    assert rows[0]["project"] == "locobench/original-project"
    assert rows[0]["task_id"] == task.name
    assert rows[0]["events"] == execution()[0]
    assert str(task / "result.json") in evidence
    assert evidence[str(task / "runtime_events.deepagents.jsonl")] == rows[0]["event_sha256"]


@pytest.mark.parametrize("change", ("incomplete", "intervened", "foreign_task", "shared_project", "mixed_workflow"))
def test_native_export_rejects_changed_or_incomplete_original_population(tmp_path, change):
    capture, task, manifest = captured_task(tmp_path)
    if change == "incomplete":
        (task / "result.json").write_text(json.dumps({"natural_terminal": False}))
    elif change == "intervened":
        (task / "result.json").write_text(json.dumps({
            "natural_terminal": True, "agent_control": {"forced_semantic_completions": 1},
        }))
    elif change == "foreign_task":
        manifest["workloads"][0]["instance_id"] = "other-task"
    elif change == "shared_project":
        manifest["workloads"].append({
            **manifest["workloads"][0], "instance_id": "second-task",
        })
        manifest["requested_workflows"] = 2
    else:
        path = task / "runtime_events.deepagents.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[-1]["workflow_id"] = "other-workflow"
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
    with pytest.raises(ValueError):
        exporting.capture_workflows(capture, manifest)


def test_native_export_checks_complete_source_and_actual_exit_before_accessing_training_data(tmp_path, monkeypatch):
    calls = []

    def incomplete(path, qualifications):
        calls.append((path, qualifications))
        raise ValueError("source did not finish its original work")

    monkeypatch.setattr(exporting, "origin_completion", incomplete)
    registration = tmp_path / "registration.json"
    with pytest.raises(ValueError, match="original work"):
        exporting.export_native(registration)
    assert calls == [(registration, {})]
