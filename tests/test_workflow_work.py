import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import EventConfidence, RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import (
    FEATURES, SCHEMA, WorkflowProgress, WorkflowWorkModel, task_requirements,
)
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from scripts.train_workflow_work import fit


def event(kind, **kwargs):
    return RuntimeEvent("event-" + kind.value, 0., kind, "wf", **kwargs)


def model_raw():
    return {
        "schema": SCHEMA, "features": list(FEATURES), "vocabulary": ["ab"],
        "work_thresholds": [10., 20.], "idf": [1.],
        "center": [0.] * len(FEATURES), "scale": [1.] * len(FEATURES),
        "static_weight": [[1.]], "dynamic_weight": [[0.]] * len(FEATURES),
        "bias": [0.], "hidden_weight": [[1.]], "hidden_bias": [0.],
        "output_weight": [[0., 0., 0.]], "output_bias": [5., 0., 0.],
        "metadata": {
            "calibration_status": "project_heldout_temperature",
            "calibration_class_projects": [1, 1, 1],
            "decision_eligible": True,
        },
    }


def model_file(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model_raw()))
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def test_model_prefix_accounting_avoids_full_prompt_and_retry_recomputation():
    progress = WorkflowProgress()
    assert progress.completed("r1", "a", "c", (1, 2, 3), (4, 5)) == 3
    assert progress.completed("r2", "a", "c", (1, 2, 3, 4, 5, 6), (7,)) == 1
    assert progress.completed("r2", "a", "c", (1, 2, 3, 4, 5, 6), (7,)) == 0
    assert progress.counts["input_tokens"] == 4
    assert progress.counts["output_tokens"] == 3
    assert progress.counts["completed_calls"] == 2
    # Compaction/editing needs a new suffix after the actual common prefix.
    assert progress.completed("r3", "a", "c", (1, 8, 9), (10,)) == 2
    # A fresh independent context cannot claim another context's prefix credit.
    assert progress.completed("r4", "b", "d", (1, 8, 9), (10,)) == 3


def test_partial_decode_charged_once_and_not_from_recorded_length():
    progress = WorkflowProgress()
    progress.started("r", "a", "c", (1,))
    progress.generated("r", 128)
    progress.generated("r", 256)
    progress.completed("r", "a", "c", (1,), (2,) * 300)
    assert progress.counts["output_tokens"] == 300
    assert progress.counts["completed_calls"] == 1


def test_unobserved_future_join_and_recorded_output_hints_are_not_features():
    progress = WorkflowProgress()
    before = progress.features()
    progress.event(event(
        Kind.JOIN_CREATE, join_id="hidden", member_invocation_ids=("future",),
        confidence=EventConfidence.INFERRED,
    ))
    progress.event(event(
        Kind.LLM_SUBMIT, invocation_id="a",
        attributes={"expected_output_tokens": 1000000, "remaining_work": 0, "future_children": 5},
    ))
    assert progress.features() == before


def test_observed_sibling_and_tool_progress_is_root_shared():
    progress = WorkflowProgress()
    progress.event(event(Kind.INVOCATION_CREATE, invocation_id="a", relation_type=RelationType.ROOT))
    progress.event(event(
        Kind.INVOCATION_CREATE, invocation_id="b", parent_invocation_id="a",
        relation_type=RelationType.SPAWN,
    ))
    progress.event(event(Kind.TOOL_START, invocation_id="b", attributes={"tool_call_id": "t"}))
    progress.event(event(Kind.JOIN_CREATE, join_id="j", member_invocation_ids=("b", "c")))
    progress.event(event(Kind.JOIN_WAIT, invocation_id="a", join_id="j"))
    features = dict(zip(FEATURES, progress.features()))
    assert features["children"] == features["tools"] == features["max_depth"] == 1
    assert features["root_waiting_join"] == 1 and features["max_fanout"] == 2


def test_task_extraction_keeps_only_the_task_requirements():
    assert task_requirements(
        "Project: memorized\nTask requirements:\nImplement feature.\n\n"
        "The complete initial project source secret"
    ) == "Implement feature."
    assert not task_requirements("system repository source without a task marker")


def test_worker_coalesces_events_shares_one_projection_and_terminal_wins(tmp_path):
    path, digest = model_file(tmp_path)
    worker = WorkflowWorkWorker(path, digest, autostart=False)
    try:
        worker.submit("wf", "event", event(
            Kind.WORKFLOW_START, attributes={"workflow_task_instructions": "Task requirements:\nab ab"},
        ))
        for i in range(20):
            worker.submit("wf", "event", event(Kind.LLM_SUBMIT))
        worker.process_pending()
        assert worker.bucket("wf", 0.) == 0
        assert worker.counts["task_encodings"] == worker.counts["inferences"] == 1
        worker.submit("wf", "event", event(Kind.WORKFLOW_END))
        assert worker.prediction("wf") is None
        worker.process_pending()
        assert "wf" not in worker._progress
    finally:
        worker.close()


def test_uncalibrated_or_ambiguous_models_fall_back_to_medium(tmp_path):
    for calibrated, logits in ((False, [5., 0., 0.]), (True, [0., 0., 0.])):
        raw = model_raw()
        raw["output_bias"] = logits
        raw["metadata"]["calibration_status"] = (
            "project_heldout_temperature" if calibrated else "uncalibrated"
        )
        worker = WorkflowWorkWorker("", "", model=WorkflowWorkModel(raw), autostart=False)
        worker.submit("wf", "event", event(
            Kind.WORKFLOW_START, attributes={"workflow_task_instructions": "Task requirements:\nab"},
        ))
        worker.process_pending()
        assert worker.bucket("wf", 0.) == 1
        worker.close()


def test_queue_overflow_abstains_without_blocking_or_discarding_runtime_work(tmp_path):
    path, digest = model_file(tmp_path)
    worker = WorkflowWorkWorker(path, digest, autostart=False)
    worker.queue_limit = 2
    for _ in range(3):
        worker.submit("wf", "event", event(Kind.LLM_SUBMIT))
    assert worker.error == "observation_queue_overflow"
    assert worker.bucket("wf", 0.) == 1
    worker.close()


@pytest.mark.parametrize("field,value", (
    ("scale", [0.] * len(FEATURES)), ("temperature", -1.),
    ("work_thresholds", [20., 10.]), ("output_bias", [0., float("nan"), 1.]),
))
def test_model_rejects_invalid_numeric_contract(field, value):
    raw = model_raw()
    raw[field] = value
    with pytest.raises(ValueError):
        WorkflowWorkModel(raw)


def test_model_load_requires_exact_fingerprint(tmp_path):
    path, digest = model_file(tmp_path)
    WorkflowWorkModel.load(path, digest)
    with pytest.raises(ValueError, match="fingerprint"):
        WorkflowWorkModel.load(path, "0" * 64)


def test_missing_long_calibration_support_cannot_promote_a_short_prediction():
    raw = model_raw()
    raw["metadata"]["calibration_class_projects"] = [2, 2, 0]
    worker = WorkflowWorkWorker("", "", model=WorkflowWorkModel(raw), autostart=False)
    worker.submit("wf", "event", event(
        Kind.WORKFLOW_START, attributes={"workflow_task_instructions": "Task requirements:\nab"},
    ))
    worker.process_pending()
    assert worker.prediction("wf").evidence == "uncertain_medium"
    assert worker.bucket("wf", 0.) == 1
    worker.close()


def test_rejected_calibration_cannot_promote_a_confident_prediction():
    raw = model_raw()
    raw["metadata"]["decision_eligible"] = False
    worker = WorkflowWorkWorker("", "", model=WorkflowWorkModel(raw), autostart=False)
    worker.submit("wf", "event", event(
        Kind.WORKFLOW_START, attributes={"workflow_task_instructions": "Task requirements:\nab"},
    ))
    worker.process_pending()
    assert worker.bucket("wf", 0.) == 1
    worker.close()


def test_delayed_or_failed_worker_falls_back_without_waiting(tmp_path):
    path, digest = model_file(tmp_path)
    worker = WorkflowWorkWorker(path, digest, autostart=False)
    worker.submit("wf", "event", event(
        Kind.WORKFLOW_START, attributes={"workflow_task_instructions": "Task requirements:\nab"},
    ))
    worker.process_pending()
    assert worker.bucket("wf", 0.) == 0
    worker._pending_since["wf"] = -100.
    assert worker.bucket("wf", 0.) == 1
    worker.error = "worker_failed"
    assert worker.bucket("wf", 0.) == 1
    worker.close()


def test_training_rejects_project_and_template_snapshot_leakage():
    row = {
        "project": "p", "workflow": "w", "task_template": "task",
        "text": "ab", "features": [0.] * len(FEATURES),
        "total_work": 10., "remaining_work": 10.,
    }
    with pytest.raises(ValueError, match="projects overlap"):
        fit([row], [row], epochs=1)
    with pytest.raises(ValueError, match="templates overlap"):
        fit([row], [{**row, "project": "other"}], epochs=1)
