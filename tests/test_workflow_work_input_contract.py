from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace as NS

import numpy as np
import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import (
    EXECUTION_INPUT_KIND, WorkflowProgress, WorkflowWorkModel, execution_control_text,
    execution_controls, execution_controls_from_environment,
)
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.run_causal_comparison import preflight_runtime
from scripts.prepare_workflow_size_candidate import prepare
from tests.test_workflow_work import event, model_raw
from tests.test_workflow_size_admission import settings


ENVIRONMENT = {
    "FANOUT_PROFILE": "native_in_graph_short_workstreams",
    "CHILD_WORK_BUDGET_TARGET_CALLS": "8", "ROOT_WORK_BUDGET_TARGET_CALLS": "16",
}


def profiled_raw():
    raw = model_raw()
    raw["vocabulary"] = ["ab", "de"]
    raw["idf"] = [1., 1.]
    raw["static_weight"] = [[1.], [5.]]
    raw["output_weight"] = [[1., -1., 0.]]
    raw["metadata"]["task_input_contract"] = {
        "kind": EXECUTION_INPUT_KIND,
        "deployment_controls": execution_controls_from_environment(ENVIRONMENT),
    }
    return raw


def test_worker_matches_offline_projection_without_changing_task_or_counting_prefix_as_work():
    model = WorkflowWorkModel(profiled_raw())
    worker = WorkflowWorkWorker(
        "", "", model=model, autostart=False, execution_environment=ENVIRONMENT,
    )
    try:
        worker.submit("wf", "event", event(
            Kind.WORKFLOW_START, attributes={"workflow_task_instructions": "Task requirements:\nab ab"},
        ))
        worker.process_pending()
        expected = model.task_projection(execution_control_text(model.deployment_controls) + "ab ab")
        assert not np.allclose(expected, model.task_projection("ab ab"))
        np.testing.assert_array_equal(worker._projections["wf"], expected)
        np.testing.assert_array_equal(
            worker.prediction("wf").probabilities,
            model.predict(expected, worker._progress["wf"].features()),
        )
        assert worker._progress["wf"].text == "ab ab"
        assert worker._progress["wf"].counts["input_tokens"] == 0
        assert worker.counts["task_encodings"] == 1
        assert worker.status()["task_input_kind"] == EXECUTION_INPUT_KIND
        assert worker.counts["worker_observations_processed"] == 1
        assert worker.counts["worker_thread_cpu_us"] > 0
    finally:
        worker.close()


@pytest.mark.parametrize("environment", (
    None, {}, {**ENVIRONMENT, "ROOT_WORK_BUDGET_TARGET_CALLS": "32"},
    {**ENVIRONMENT, "FANOUT_PROFILE": "native_in_graph_dynamic_nested"},
))
def test_profiled_model_rejects_missing_or_different_execution_instructions(environment):
    with pytest.raises(ValueError):
        WorkflowWorkWorker(
            "", "", model=WorkflowWorkModel(profiled_raw()), autostart=False,
            execution_environment=environment,
        )


def test_old_model_keeps_exact_task_only_input_even_with_unrelated_runtime_environment():
    model = WorkflowWorkModel(model_raw())
    assert model.task_input_prefix({"FANOUT_PROFILE": "unrelated"}) == ""
    assert model.task_input_kind == "task_requirements_v1"


@pytest.mark.parametrize("change", (
    {"expected_output_tokens": 1000}, {"remaining_work": 0},
    {"minimum_parallel_join": True}, {"child_soft_target": "-1"},
))
def test_control_input_rejects_future_hints_and_invalid_instruction_types(change):
    with pytest.raises(ValueError):
        execution_controls({**execution_controls_from_environment(ENVIRONMENT), **change})


def test_candidate_checks_actual_configuration_against_profiled_model(tmp_path):
    raw = profiled_raw()
    raw["metadata"].update(
        training_projects=["train"], calibration_projects=["cal"], prefill_token_weight=.01,
    )
    model, report = tmp_path / "model.json", tmp_path / "evaluation.json"
    model.write_text(json.dumps(raw))
    report.write_text(json.dumps({
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(), "test_projects": ["test"],
    }))
    root = Path(__file__).resolve().parents[1]
    original = root / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
    candidate = prepare(original, model, report)
    assert candidate["workflow_work_qualification"]["task_input_kind"] == EXECUTION_INPUT_KIND
    changed = deepcopy(json.loads(original.read_bytes()))
    changed["common_environment"]["ROOT_WORK_BUDGET_TARGET_CALLS"] = "32"
    mismatched = tmp_path / "base.json"
    mismatched.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="controls differ"):
        prepare(mismatched, model, report)


def test_actual_admission_receives_the_declared_profile_and_preflight_rejects_mismatch(monkeypatch, tmp_path):
    config = {**settings(tmp_path), **ENVIRONMENT}
    path = Path(config["WORKFLOW_WORK_MODEL"])
    path.write_text(json.dumps(profiled_raw()))
    config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    configured = arm_environment(config, "full")
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "profile.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        worker = runtime._agent_admission._work
        assert worker.status()["task_input_kind"] == EXECUTION_INPUT_KIND
        assert worker.task_prefix == execution_control_text(worker.model.deployment_controls)
    finally:
        runtime.close()
    mismatched = {**configured, "ROOT_WORK_BUDGET_TARGET_CALLS": "32"}
    with pytest.raises(ValueError, match="controls differ"):
        preflight_runtime(mismatched, str(tmp_path))


def test_vector_prefix_accounting_matches_reference_through_edits_truncations_and_retries():
    rng = random.Random(21)
    progress, histories, inputs, outputs = WorkflowProgress(), {}, 0, 0
    for turn in range(160):
        context = "context-" + str(turn % 3)
        previous = histories.get(context, ())
        prompt = list(previous) + [rng.randrange(100) for _ in range(rng.randrange(5))]
        if prompt and rng.random() < .5:
            prompt = prompt[:rng.randrange(len(prompt) + 1)]
        if prompt and rng.random() < .5:
            prompt[rng.randrange(len(prompt))] = rng.randrange(100)
        output = [rng.randrange(100) for _ in range(rng.randrange(5))]
        common = 0
        for left, right in zip(previous, prompt):
            if left != right:
                break
            common += 1
        necessary = len(prompt) - common
        assert progress.completed(str(turn), "agent", context, prompt, output) == necessary
        inputs, outputs = inputs + necessary, outputs + len(output)
        histories[context] = tuple(prompt) + tuple(output)
        assert tuple(progress.histories[context]) == histories[context]
        assert progress.completed(str(turn), "agent", context, prompt, output) == 0
    assert progress.counts["input_tokens"] == inputs
    assert progress.counts["output_tokens"] == outputs


def test_completed_history_owns_its_tokens_after_request_buffer_mutation():
    progress = WorkflowProgress()
    prompt, output = [1, 2], [3]
    progress.completed("one", "agent", "context", prompt, output)
    prompt[0], output[0] = 99, 99
    assert progress.completed("two", "agent", "context", [1, 2, 3, 4], [5]) == 1
