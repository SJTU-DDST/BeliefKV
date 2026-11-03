import json
import sys
from types import SimpleNamespace

import pytest

from scripts import finish_workflow_task_training as finishing


def arguments(tmp_path, monkeypatch, status="source_captured_gpu_replay_pending"):
    values = {
        name: tmp_path / (name + ".json") for name in (
            "capture-registration", "dataset", "cohort", "previous-training-data",
            "baseline-model", "baseline-evaluation",
        )
    }
    for path in values.values():
        path.write_text("{}")
    values["capture-registration"].write_text(json.dumps({
        "environment": {"ROOT_COUNT": "18"},
    }))
    values["capture-registration"].with_suffix(".state.json").write_text(json.dumps({
        "status": status, "original_exit_code": 0, "source_requests": 100,
        "source_selection": {"accepted": True, "workflow_count": 18},
        "watcher": {"pid": 1073741824, "start_ticks": "0"},
        "driver": {"pid": 1073741825, "start_ticks": "0"},
    }))
    output = tmp_path / "output"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(finishing, "git_source_state", lambda path: {"dirty": False, "commit": "test"})
    monkeypatch.setattr(sys, "argv", [
        "finish_workflow_task_training.py",
        *(item for name, path in values.items() for item in ("--" + name, str(path))),
        "--output-dir", str(output),
    ])
    return output


def test_followup_exports_only_after_completion_then_trains_and_requires_review(tmp_path, monkeypatch):
    output = arguments(tmp_path, monkeypatch)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "export_native_workflow_training.py" in command[1]:
            (output / "additional_training.json").write_text("{}")
        else:
            assert (output / "additional_training.json").is_file()
            assert "--additional-training-data" in command
            (output / "training").mkdir()
            (output / "training/evaluation.json").write_text(json.dumps({
                "selected": "retained", "expanded_training_executions": 77, "training_projects": 59,
            }))
        return SimpleNamespace(returncode=0, stdout="CPU fixture", stderr="")

    monkeypatch.setattr(finishing.subprocess, "run", run)
    finishing.main()
    state = json.loads((output / "state.json").read_bytes())
    assert len(calls) == 2 and all(call[0] == sys.executable for call in calls)
    assert state["status"] == "training_complete_requires_review"
    assert state["predictor_deployed"] is False
    assert state["automatic_GPU_followup"] is False and state["JCT_gain_proven"] is False


def test_failed_capture_cannot_launch_an_export_or_training(tmp_path, monkeypatch):
    output = arguments(tmp_path, monkeypatch, status="capture_failed")
    calls = []
    monkeypatch.setattr(finishing.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="source did not complete"):
        finishing.main()
    assert not calls
    state = json.loads((output / "state.json").read_bytes())
    assert state["status"] == "stopped_no_GPU_action"
    assert not (output / "additional_training.json").exists()


def test_failed_export_preserves_its_output_and_does_not_train(tmp_path, monkeypatch):
    output = arguments(tmp_path, monkeypatch)
    calls = []

    def failure(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="partial evidence", stderr="source mismatch")

    monkeypatch.setattr(finishing.subprocess, "run", failure)
    with pytest.raises(ValueError, match="original output"):
        finishing.main()
    assert len(calls) == 1
    report = json.loads((output / "export_native_workflow_training.py.result.json").read_bytes())
    assert report["returncode"] == 1 and report["stderr"] == "source mismatch"
    assert not (output / "training").exists()
