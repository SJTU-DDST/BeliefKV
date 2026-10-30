from copy import deepcopy

import numpy as np
import pytest

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import FEATURES, WorkflowProgress, WorkflowWorkModel
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from experiments.reports.workflow_work_training_20261029.qualify_input_contract import (
    qualify_progress_observations,
)
from experiments.reports.workflow_work_training_20261029.refresh_native_progress import (
    verify_context_projection,
)
from scripts.train_workflow_work import export_capture, fit, probabilities
from tests.test_workflow_work import event, model_raw
from tests.test_workflow_work_native_progress import make_capture, native_decode_progress, batches


CONTEXT_SCHEMA = "workflow_remaining_model_work_context_v2"
CONTEXT_FEATURES = FEATURES + (
    "initial_prompt_tokens", "active_prompt_tokens", "max_prompt_tokens",
)


def context_model_raw():
    raw = model_raw()
    raw.update(schema=CONTEXT_SCHEMA, features=list(CONTEXT_FEATURES))
    raw["center"] += [0.] * 3
    raw["scale"] += [1.] * 3
    raw["dynamic_weight"] += [[0.]] * 3
    return raw


def values(progress):
    return dict(zip(CONTEXT_FEATURES, progress.features(feature_schema=CONTEXT_SCHEMA)))


def root_progress():
    progress = WorkflowProgress()
    progress.event(event(Kind.INVOCATION_CREATE, invocation_id="root", relation_type=RelationType.ROOT))
    return progress


def test_observed_prompt_lengths_are_available_before_first_completion():
    progress = root_progress()
    progress.started("r", "root", "c", (1,) * 8192)
    observed = values(progress)
    assert observed["input_tokens"] == observed["completed_calls"] == 0
    assert observed["initial_prompt_tokens"] == observed["active_prompt_tokens"] == 8192
    assert observed["max_prompt_tokens"] == 8192


def test_context_features_track_concurrent_calls_without_changing_logical_work():
    progress = root_progress()
    progress.started("r1", "root", "c1", (1, 2, 3))
    progress.started("r2", "child", "c2", (1,) * 10)
    progress.started("r1", "root", "c1", (9,) * 1000)
    assert values(progress)["active_prompt_tokens"] == 13
    progress.generated("r1", 128)
    assert values(progress)["active_prompt_tokens"] == 13
    progress.completed("r1", "root", "c1", (1, 2, 3), (4,) * 128)
    progress.started("r3", "root", "c1", (1, 2, 3) + (4,) * 128 + (5,))
    observed = values(progress)
    assert observed["initial_prompt_tokens"] == 3
    assert observed["active_prompt_tokens"] == 142
    assert observed["max_prompt_tokens"] == 132
    assert observed["input_tokens"] == 3 and observed["output_tokens"] == 128
    progress.completed("r2", "child", "c2", (1,) * 10, (7,))
    progress.completed("r3", "root", "c1", (1, 2, 3) + (4,) * 128 + (5,), (6,))
    progress.started("r3", "root", "c1", (8,) * 1000)
    assert values(progress)["active_prompt_tokens"] == 0
    assert values(progress)["max_prompt_tokens"] == 132
    assert progress.counts["input_tokens"] == 14


def test_future_metadata_and_child_prompts_cannot_set_initial_root_length():
    progress = root_progress()
    progress.event(event(
        Kind.LLM_SUBMIT, invocation_id="root",
        attributes={"prompt_tokens": 10**9, "future_context_tokens": 10**9},
    ))
    progress.started("child", "child", "child-context", (1,) * 16)
    assert values(progress)["initial_prompt_tokens"] == 0
    progress.started("root-first", "root", "root-context", ())
    progress.completed("root-first", "root", "root-context", (), (1,))
    progress.started("root-next", "root", "root-context", (1, 2))
    assert values(progress)["initial_prompt_tokens"] == 0
    assert values(progress)["max_prompt_tokens"] == 16


def test_legacy_model_and_progress_keep_exact_feature_contract():
    progress = root_progress()
    progress.started("r", "root", "c", (1,) * 100)
    legacy = progress.features()
    assert legacy == progress.features(feature_schema=model_raw()["schema"])
    assert len(legacy) == len(FEATURES)
    model = WorkflowWorkModel(model_raw())
    assert model.feature_names == FEATURES
    assert model.predict(model.task_projection("ab"), legacy) == WorkflowWorkModel(
        model_raw(),
    ).predict(model.task_projection("ab"), legacy)


@pytest.mark.parametrize("change", ("schema", "names", "weights", "features"))
def test_model_refuses_mismatched_context_contract(change):
    raw = context_model_raw()
    if change == "schema":
        raw["schema"] = "unknown"
    elif change == "names":
        raw["features"][-1] = "future_prompt_tokens"
    elif change == "weights":
        raw["dynamic_weight"].pop()
    if change != "features":
        with pytest.raises(ValueError):
            WorkflowWorkModel(raw)
    else:
        model = WorkflowWorkModel(raw)
        with pytest.raises(ValueError, match="features"):
            model.predict(model.task_projection("ab"), [0.] * len(FEATURES))


def test_worker_uses_model_schema_without_waiting_for_completion():
    model = WorkflowWorkModel(context_model_raw())
    worker = WorkflowWorkWorker("", "", model=model, autostart=False)
    try:
        worker.submit("wf", "event", event(
            Kind.WORKFLOW_START, attributes={"workflow_task_instructions": "Task requirements:\nab"},
        ))
        worker.submit("wf", "event", event(
            Kind.INVOCATION_CREATE, invocation_id="root", relation_type=RelationType.ROOT,
        ))
        worker.submit("wf", "started", "r", "root", "c", (1,) * 8192)
        worker.process_pending()
        progress = worker._progress["wf"]
        assert values(progress)["initial_prompt_tokens"] == 8192
        assert worker.prediction("wf").probabilities == model.predict(
            model.task_projection("ab"), progress.features(feature_schema=CONTEXT_SCHEMA),
        )
        assert worker.status()["feature_schema"] == CONTEXT_SCHEMA
    finally:
        worker.close()


def test_export_keeps_exact_execution_snapshots_and_labels(tmp_path):
    capture = make_capture(tmp_path)
    legacy, _, _ = export_capture(capture, prefill_weight=.01, native_progress=True)
    current, _, _ = export_capture(
        capture, prefill_weight=.01, native_progress=True, feature_schema=CONTEXT_SCHEMA,
    )
    assert len(legacy) == len(current)
    for before, after in zip(legacy, current):
        assert before["features"] == after["features"][:len(FEATURES)]
        assert {key: value for key, value in before.items() if key != "features"} == {
            key: value for key, value in after.items() if key != "features"
        }
    first = next(row for row in current if row["event"] == "llm_submit")
    assert first["features"][-3:] == (3., 3., 3.)


def test_context_worker_and_native_progress_export_have_exact_input_parity(tmp_path):
    from types import SimpleNamespace
    import json
    from beliefkv.core.events import RuntimeEvent

    capture = make_capture(tmp_path)
    offline, _, _ = export_capture(
        capture, prefill_weight=.01, native_progress=True, feature_schema=CONTEXT_SCHEMA,
    )
    source = next(capture.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"))
    events = [RuntimeEvent.from_dict(json.loads(line)) for line in source.open()]
    observations = native_decode_progress(events, batches(), {"r1": 300, "r2": 1})
    prompt, output = (1, 2, 3), (4,) * 300
    calls = {
        "r1": SimpleNamespace(prompt_tokens=prompt, output_tokens=output),
        "r2": SimpleNamespace(prompt_tokens=prompt + output + (5,), output_tokens=(6,)),
    }
    model = WorkflowWorkModel(context_model_raw())
    worker = WorkflowWorkWorker("", "", model=model, autostart=False)
    tokenizer = SimpleNamespace(decode=lambda *_args, **_kwargs: (
        "<|im_start|>user\nTask requirements:\nab implement.<|im_end|>"
    ))
    try:
        checks = qualify_progress_observations(
            worker, events, observations, SimpleNamespace(get=calls.__getitem__),
            offline, model, tokenizer=tokenizer,
        )
        assert sum(checks["events"].values()) == len(offline)
    finally:
        worker.close()


def test_fit_and_portable_inference_use_declared_context_dimensions():
    rows = [{
        "project": f"p{i}", "workflow": f"w{i}", "task_template": f"t{i}",
        "text": "ab", "features": [0.] * len(FEATURES) + [float(i + 1)] * 3,
        "total_work": float((i + 1) * 100), "remaining_work": float((i + 1) * 100),
    } for i in range(3)]
    calibrated = [{**deepcopy(row), "project": row["project"] + "c",
                   "workflow": row["workflow"] + "c", "task_template": row["task_template"] + "c"}
                  for row in rows]
    raw = fit(
        rows, calibrated, epochs=1, hidden_width=4, vocabulary_size=4,
        feature_schema=CONTEXT_SCHEMA,
    )
    model = WorkflowWorkModel(raw)
    assert model.feature_names == CONTEXT_FEATURES
    assert np.asarray(raw["dynamic_weight"]).shape == (len(CONTEXT_FEATURES), 4)
    assert probabilities(model, rows).shape == (3, 3)


@pytest.mark.parametrize("change", (
    None, "population", "label", "legacy_feature", "execution", "missing_context",
))
def test_context_export_preserves_whole_executions_and_existing_labels(change):
    row = {"workflow": "capture:wf", "snapshot": 0, "source_workflow_sha256": "a" * 64,
           "remaining_work": 100., "features": [1.] * len(FEATURES)}
    original = {"rows": [row]}
    current = {"rows": [{**deepcopy(row), "features": row["features"] + [100.] * 3}]}
    if change == "population":
        current["rows"].append(deepcopy(current["rows"][0]))
    elif change == "label":
        current["rows"][0]["remaining_work"] = 0.
    elif change == "legacy_feature":
        current["rows"][0]["features"][0] = 2.
    elif change == "execution":
        current["rows"][0]["source_workflow_sha256"] = "b" * 64
    elif change == "missing_context":
        current["rows"][0]["features"].pop()
    if change is None:
        verify_context_projection(original, current)
    else:
        with pytest.raises(ValueError):
            verify_context_projection(original, current)
