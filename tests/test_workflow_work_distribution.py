import math
import time

import pytest

from beliefkv.core.events import EventConfidence, RelationType, RuntimeEventKind as Kind
from beliefkv.predictor.workflow_rank_input import WorkflowRankInput
from beliefkv.predictor.workflow_work import R5_FEATURES, R5_SCHEMA, WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import (
    DistributionProgress, support_status, training_contract,
)
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from tests.test_workflow_work import event, model_raw


def distribution_raw():
    raw = model_raw()
    raw.update(
        schema=R5_SCHEMA, features=list(R5_FEATURES), dynamic_weight=[[0.]] * len(R5_FEATURES),
        center=[0.] * len(R5_FEATURES), scale=[1.] * len(R5_FEATURES),
    )
    raw["distribution_contract"] = training_contract([
        {"features": [0.] * len(R5_FEATURES)}, {"features": [10000.] * len(R5_FEATURES)},
    ])
    raw["metadata"]["calibration_class_workflows"] = [3, 3, 3]
    return raw


def start():
    return event(Kind.WORKFLOW_START, attributes={
        "workflow_ranking_input": WorkflowRankInput.from_instructions(task_instructions="ab").to_dict(),
        "full_prompt": "must not be encoded",
    })


def root():
    return event(Kind.INVOCATION_CREATE, invocation_id="root", relation_type=RelationType.ROOT)


def worker(raw=None):
    return WorkflowWorkWorker(
        "", "", model=WorkflowWorkModel(raw or distribution_raw()), autostart=False,
    )


def test_r5_encodes_only_explicit_task_metadata_once_and_never_decodes_full_prompt():
    work = worker()
    try:
        work.submit("wf", "event", start())
        work.submit("wf", "event", root())
        work.submit("wf", "task", (999,) * 8192, None)
        work.process_pending()
        assert work._progress["wf"].text == "ab"
        assert work.counts["task_encodings"] == 1
        assert work.counts["full_prompt_fallback_disabled"] == 1
        assert work.prediction("wf").evidence == "model"
        assert work.status()["refresh_interval_ms"] == 1000
        assert not work.status()["support_uses_maximum_softmax_threshold"]
    finally:
        work.close()


def test_calibrated_probability_distribution_does_not_require_confident_argmax():
    raw = distribution_raw()
    raw["output_bias"] = [math.log(.4), math.log(.35), math.log(.25)]
    work = worker(raw)
    try:
        work.submit("wf", "event", start())
        work.process_pending()
        prediction = work.prediction("wf")
        assert prediction.evidence == "model"
        assert prediction.probabilities == pytest.approx((.4, .35, .25))
        assert prediction.support == "calibrated_distribution"
    finally:
        work.close()


@pytest.mark.parametrize("change,reason", (
    ("eligibility", "calibration_not_eligible"),
    ("class_support", "missing_class_execution_support"),
    ("task_coverage", "task_vocabulary_unsupported"),
    ("progress", "observed_progress_outside_training_support"),
))
def test_high_softmax_cannot_override_calibration_or_observation_support(change, reason):
    raw, values, coverage = distribution_raw(), [0.] * len(R5_FEATURES), 1.
    if change == "eligibility":
        raw["metadata"]["decision_eligible"] = False
    elif change == "class_support":
        raw["metadata"]["calibration_class_workflows"] = [3, 3, 1]
    elif change == "task_coverage":
        coverage = .01
    else:
        values[0] = 10 ** 10
    model = WorkflowWorkModel(raw)
    assert max(model.predict(model.task_projection("ab"), values)) > .9
    assert support_status(model, values, coverage) == reason


def test_per_workflow_refresh_uses_new_observations_and_cache_expires(monkeypatch):
    from beliefkv.runtime import workflow_work_worker as module

    clock = [100.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    work = worker()
    try:
        work.submit("wf", "event", start())
        work.submit("wf", "event", root())
        work.process_pending()
        first = work.prediction("wf")
        work.submit("wf", "started", "r", "root", "c", (1, 2))
        work.submit("wf", "generated", "r", 128)
        clock[0] = 100.2
        work.process_pending()
        assert work.counts["inferences"] == 1
        assert work.prediction("wf") is first
        clock[0] = 101.
        work.process_pending()
        assert work.counts["inferences"] == 2
        assert work.prediction("wf").revision > first.revision
        assert work.counts["task_encodings"] == 1
        clock[0] = 104.01
        assert work.prediction("wf") is None
    finally:
        work.close()


def test_spawn_revokes_old_prediction_immediately_and_terminal_wins(monkeypatch):
    from beliefkv.runtime import workflow_work_worker as module

    clock = [100.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    work = worker()
    try:
        work.submit("wf", "event", start())
        work.submit("wf", "event", root())
        work.process_pending()
        assert work.prediction("wf") is not None
        clock[0] = 100.1
        work.submit("wf", "event", event(
            Kind.INVOCATION_CREATE, invocation_id="child", parent_invocation_id="root",
            relation_type=RelationType.SPAWN, agent_definition_id="repository-explorer",
        ))
        assert work.prediction("wf") is None
        work.process_pending()
        assert work.prediction("wf") is None
        clock[0] = 101.
        work.process_pending()
        assert work.prediction("wf").structural_revision == 2
        work.submit("wf", "event", event(Kind.WORKFLOW_END))
        assert work.prediction("wf") is None
        work.process_pending()
        assert "wf" not in work._next_inference
    finally:
        work.close()


def test_inferred_join_and_future_hints_do_not_change_r5_prediction():
    work = worker()
    try:
        work.submit("wf", "event", start())
        work.process_pending()
        before = work.prediction("wf")
        work.submit("wf", "event", event(
            Kind.JOIN_CREATE, join_id="future", confidence=EventConfidence.INFERRED,
            attributes={"remaining_work": 0, "expected_output_tokens": 1},
        ))
        work.process_pending()
        assert work.prediction("wf") is before
        assert work.counts["unobserved_event_ignored"] == 1
    finally:
        work.close()


def test_roles_and_root_child_work_match_exact_source_summary_without_token_materialization():
    live, offline = DistributionProgress(), DistributionProgress()
    child = event(
        Kind.INVOCATION_CREATE, invocation_id="child", parent_invocation_id="root",
        relation_type=RelationType.SPAWN, agent_definition_id="repository-explorer",
        attributes={"workflow_ranking_input": WorkflowRankInput.from_instructions(
            delegation_instructions="Inspect the gateway", agent_role="repository-explorer",
        ).to_dict()},
    )
    for progress in (live, offline):
        for value in (start(), root(), child):
            progress.event(value)
    for request, invocation, context, prompt, output, necessary in (
        ("r1", "root", "root-context", (1, 2), (3, 4), 2),
        ("r2", "root", "root-context", (1, 2, 3, 4, 5), (6,), 1),
        ("c1", "child", "child-context", (1, 2), (7, 8), 2),
    ):
        assert live.completed(request, invocation, context, prompt, output) == necessary
        offline.completed_summary(request, invocation, context, len(prompt), len(output), necessary)
        assert live.features() == offline.features()
    values = dict(zip(R5_FEATURES, live.features()))
    assert values["root_output_tokens"] == 3
    assert values["child_output_tokens"] == 2
    assert values["root_input_tokens"] == 3
    assert values["role_explore"] == values["unfinished_children"] == 1
    assert values["delegation_chars"] == len("Inspect the gateway")
    before = live.features()
    live.started("pending", "root", "root-context", (1,))
    live.generated("pending", 10)
    with pytest.raises(ValueError, match="backwards"):
        live.generated("pending", 1)
    assert live.counts["root_output_tokens"] == 13
    assert before != live.features()


def test_submitted_successor_stays_blocked_until_all_actual_tool_and_join_dependencies_return():
    progress = DistributionProgress()
    for value in (start(), root()):
        progress.event(value)
    progress.event(event(Kind.JOIN_WAIT, invocation_id="root", join_id="j"))
    for tool in ("a", "b"):
        progress.event(event(Kind.TOOL_START, invocation_id="root", attributes={"tool_call_id": tool}))
    progress.event(event(Kind.LLM_SUBMIT, invocation_id="root"))
    progress.started("next", "root", "c", (1, 2))
    assert progress.invocations["root"][0] == "waiting_join"
    progress.event(event(Kind.JOIN_SATISFIED, invocation_id="root", join_id="j"))
    assert progress.invocations["root"][0] == "tools"
    progress.event(event(Kind.TOOL_END, invocation_id="root", attributes={"tool_call_id": "a"}))
    assert progress.invocations["root"][0] == "tools"
    progress.event(event(Kind.TOOL_END, invocation_id="root", attributes={"tool_call_id": "b"}))
    assert progress.invocations["root"][0] == "ready"
    assert dict(zip(R5_FEATURES, progress.features()))["root_waiting_join"] == 0


def test_r5_worker_thread_retries_dirty_refresh_without_a_new_event():
    raw = distribution_raw()
    raw["distribution_contract"]["minimum_refresh_s"] = 1.
    work = WorkflowWorkWorker("", "", model=WorkflowWorkModel(raw), autostart=True)
    try:
        work.submit("wf", "event", start())
        until = time.monotonic() + 3.
        while work.counts["inferences"] < 1 and time.monotonic() < until:
            time.sleep(.01)
        first = work.prediction("wf")
        assert first is not None
        work.submit("wf", "event", root())
        until = time.monotonic() + 3.
        while work.counts["inferences"] < 2 and time.monotonic() < until:
            time.sleep(.01)
        assert work.counts["inferences"] >= 2
        assert work.prediction("wf").revision > first.revision
    finally:
        work.close()


@pytest.mark.parametrize("field,value", (
    ("minimum_refresh_s", .2), ("maximum_cache_age_s", .5),
    ("feature_log_min", []), ("full_prompt_features_allowed", True),
    ("future_tools_are_work_labels", True),
))
def test_distribution_contract_cannot_silently_change_scope_or_limits(field, value):
    raw = distribution_raw()
    raw["distribution_contract"][field] = value
    with pytest.raises(ValueError):
        WorkflowWorkModel(raw)


def test_learned_artifact_cannot_carry_future_workflow_graphs():
    raw = distribution_raw()
    raw["workflow_graphs"] = {"future": []}
    with pytest.raises(ValueError, match="future"):
        WorkflowWorkModel(raw)
