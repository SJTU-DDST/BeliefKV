from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import FEATURES, WorkflowWorkModel
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from experiments.reports.workflow_work_training_20261029.prepare import (
    training_configuration, training_manifest,
)
from scripts.prepare_workflow_size_candidate import prepare
from scripts.train_workflow_work import capture_split, fit
from tests.test_workflow_work import event, model_raw

ROOT = Path(__file__).resolve().parents[1]


def row(capture, execution, *, snapshot=0):
    return {
        "workflow": capture + ":stable-workflow", "logical_workflow_id": "stable-workflow",
        "source_capture": capture, "source_workflow_sha256": execution * 64,
        "project": "shared-project", "task_template": "shared-task",
        "text": "Implement feature ab.", "features": [float(snapshot)] * len(FEATURES),
        "snapshot": snapshot, "total_work": 10. + snapshot, "remaining_work": 10.,
    }


def test_capture_split_allows_independent_repeats_of_the_same_task_and_workflow_id():
    split = capture_split([row("train", "a")], [row("cal", "b")], [row("test", "c")])
    assert split["mode"] == "capture"
    assert split["complete_workflows_disjoint"]
    assert not split["project_disjoint_required"]
    assert not split["unseen_project_generalization_claimed"]
    assert split["training_execution_sha256"] == ["a" * 64]


@pytest.mark.parametrize("overlap", ("capture", "execution"))
def test_capture_split_rejects_snapshot_leakage_and_copied_execution(overlap):
    first = row("first", "a")
    second = row(
        "first" if overlap == "capture" else "copied",
        "a" if overlap == "execution" else "b", snapshot=1,
    )
    with pytest.raises(ValueError, match="overlap"):
        capture_split([first], [second], [])


def test_capture_split_requires_verifiable_execution_identity():
    source = row("train", "a")
    del source["source_workflow_sha256"]
    with pytest.raises(ValueError, match="fingerprints"):
        capture_split([source], [], [])


def test_workflow_holdout_allows_independent_tasks_from_the_same_capture():
    split = capture_split(
        [row("capture", "a")], [row("capture", "b")], [row("other", "c")],
        require_capture_disjoint=False,
    )
    assert split["mode"] == "workflow"
    assert not split["complete_captures_disjoint"]
    assert split["complete_workflows_disjoint"]
    with pytest.raises(ValueError, match="executions overlap"):
        capture_split(
            [row("capture", "a")], [row("copied", "a")], [],
            require_capture_disjoint=False,
        )


def test_fit_capture_mode_permits_project_overlap_but_default_still_rejects_it():
    def population(capture, digest):
        return [
            {
                **row(capture, digest, snapshot=i), "workflow": capture + ":" + str(i),
                "total_work": 10. + i, "remaining_work": 10. + i,
            }
            for i in range(3)
        ]

    training, calibration = population("train", "a"), population("cal", "b")
    with pytest.raises(ValueError, match="projects overlap"):
        fit(training, calibration, epochs=1)
    raw = fit(training, calibration, epochs=1, split_mode="capture")
    assert raw["metadata"]["calibration_status"] == "capture_heldout_temperature"
    assert raw["metadata"]["validation_scope"] == "known_projects_independent_captures_development"


def test_worker_uses_eligible_capture_calibration_and_declares_its_scope():
    check_worker_calibration("capture")


def test_worker_uses_eligible_workflow_calibration_and_declares_its_scope():
    check_worker_calibration("workflow")


def check_worker_calibration(mode):
    raw = model_raw()
    raw["metadata"].update({
        "calibration_status": mode + "_heldout_temperature",
        "validation_scope": "known_projects_independent_captures_development",
    })
    worker = WorkflowWorkWorker("", "", model=WorkflowWorkModel(raw), autostart=False)
    try:
        worker.submit("wf", "event", event(
            Kind.WORKFLOW_START,
            attributes={"workflow_task_instructions": "Task requirements:\nab"},
        ))
        worker.process_pending()
        assert worker.bucket("wf", 0.) == 0
        assert worker.status()["validation_scope"] == raw["metadata"]["validation_scope"]
    finally:
        worker.close()


def test_compact_regularized_model_is_portable_and_keeps_complete_workflows():
    training = [
        {**row("one", "a", snapshot=i), "workflow": "train:" + str(i),
         "total_work": 10. + i, "remaining_work": 10. + i}
        for i in range(3)
    ]
    calibration = [
        {**row("one", "b", snapshot=i), "workflow": "cal:" + str(i),
         "total_work": 10. + i, "remaining_work": 10. + i}
        for i in range(3)
    ]
    raw = fit(
        training, calibration, epochs=2, split_mode="workflow",
        hidden_width=8, vocabulary_size=16, dropout=.25, weight_decay=.1,
    )
    model = WorkflowWorkModel(raw)
    assert model.metadata["calibration_status"] == "workflow_heldout_temperature"
    assert model.metadata["training_parameters"]["hidden_width"] == 8
    assert len(model.vocabulary) <= 16
    probabilities = model.predict(model.task_projection(training[0]["text"]), training[0]["features"])
    assert sum(probabilities) == pytest.approx(1.)


@pytest.mark.parametrize("overlap", (False, True))
def test_candidate_preserves_explicit_capture_scope_and_rejects_execution_overlap(tmp_path, overlap):
    raw = model_raw()
    split = capture_split([row("train", "a")], [row("cal", "b")], [row("test", "c")])
    if overlap:
        split["evaluation_execution_sha256"] = split["training_execution_sha256"]
    raw["metadata"].update(
        training_projects=["shared"], calibration_projects=["shared"],
        validation_scope=split["validation_scope"], split_contract=split, prefill_token_weight=.01,
    )
    model = tmp_path / "model.json"
    model.write_text(json.dumps(raw))
    report = tmp_path / "evaluation.json"
    report.write_text(json.dumps({
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "test_projects": ["shared"], "split_contract": split,
    }))
    base = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
    if overlap:
        with pytest.raises(ValueError, match="separate complete"):
            prepare(base, model, report)
    else:
        candidate = prepare(base, model, report)
        assert candidate["workflow_work_qualification"]["validation_scope"] == split["validation_scope"]
        assert candidate["common_environment"]["BELIEFKV_WORKFLOW_SIZE_RESUME"] == "1"


def test_candidate_accepts_workflow_holdout_with_shared_capture(tmp_path):
    split = capture_split(
        [row("same", "a")], [row("same", "b")], [row("other", "c")],
        require_capture_disjoint=False,
    )
    raw = model_raw()
    raw["metadata"].update(
        training_projects=["shared"], calibration_projects=["shared"],
        calibration_status="workflow_heldout_temperature", validation_scope=split["validation_scope"],
        split_contract=split, prefill_token_weight=.01,
    )
    model, report = tmp_path / "model.json", tmp_path / "evaluation.json"
    model.write_text(json.dumps(raw))
    report.write_text(json.dumps({
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "test_projects": ["shared"], "split_contract": split,
    }))
    base = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
    candidate = prepare(base, model, report)
    assert candidate["workflow_work_qualification"]["split_contract"] == split


def test_training_manifest_changes_only_original_task_not_qualified_project_environment(tmp_path):
    source = {
        "repo": "locobench/p", "instance_id": "locobench__old",
        "source_repo": "/original/source", "base_commit": "original",
        "docker_image": "sha256:original", "sandbox_environment": {"PYTHONPATH": "/workspace"},
        "frozen_repository_context": {"sha256": "original"},
        "dataset_provenance": {"task_category": "feature_implementation"},
    }
    original = deepcopy(source)
    path = "data/p_cross_file_refactoring_01.json"
    base = {
        "workloads": [source],
        "candidates": [{"project_id": "p", "scenario_paths": [path]}],
        "tool_environment_qualification": {"sha256": "original"},
    }
    archive = Mock()
    archive.read.return_value = json.dumps({
        "id": "new", "task_category": "cross_file_refactoring", "difficulty": "hard",
        "title": "New task", "task_prompt": "Refactor original project.",
        "expected_approach": "secret reference answer",
    }).encode()
    prepared = training_manifest(base, archive, tmp_path, count=1)
    task = prepared["workloads"][0]
    for key in ("source_repo", "base_commit", "docker_image", "sandbox_environment", "frozen_repository_context"):
        assert task[key] == original[key]
    assert source == original
    assert task["instance_id"] == "locobench__new"
    assert "secret" not in task["problem_statement"]
    assert prepared["tool_environment_qualification"] == base["tool_environment_qualification"]
    assert json.loads(Path(task["dataset_provenance"]["reference_path"]).read_bytes())["id"] == "new"


def test_training_capture_uses_current_budgets_and_plain_native_controls():
    base = json.loads((ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json").read_bytes())
    config = training_configuration(base)
    assert config["order"] == ["native_matched"]
    assert config["common_environment"]["ROOT_COUNT"] == "8"
    assert config["common_environment"]["SGLANG_PATCH_FLAVOR"] == "staging"
    assert config["training_protocol"]["child_warning_target"] == [4, 8]
    assert config["training_protocol"]["root_warning_target"] == [8, 16]
    assert config["training_protocol"]["tool_timing_overlay"] is None
