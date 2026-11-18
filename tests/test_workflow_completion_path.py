from copy import deepcopy
from dataclasses import replace
import math

import pytest

from beliefkv.core.events import EventConfidence, RuntimeEventKind as Kind
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from beliefkv.predictor.workflow_completion_path import (
    KIND, TARGET, TIMING_FEATURES, ObservedToolClock, feature_names,
)
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_readout import representation_fingerprint
from beliefkv.runtime.workflow_work_oracle import cache_remaining_paths
from beliefkv.runtime.workflow_work_worker import CompletionPathPrediction, WorkflowWorkWorker
from tests.test_shortest_work_admission import tool_controller
from tests.test_shortest_work_candidate_window import window_fixture
from tests.test_strict_remaining_work_admission import strict_runtime
from tests.test_workflow_continuous_serving import continuous_model
from tests.test_workflow_continuous_work import observed
from tests.test_workflow_shortest_frontier import fixture
from tests.test_workflow_work import event
from tests.test_workflow_work_distribution import root, start


def path_model(base=None):
    base = base or continuous_model()
    raw = deepcopy(base.raw)
    raw["completion_path_readout"] = {
        "kind": KIND, "target": TARGET,
        "representation_sha256": representation_fingerprint(base),
        "features": list(feature_names(base)),
        "trees": [[
            [len(feature_names(base)) - len(TIMING_FEATURES) + 2, math.log1p(5.), 1, 2, 0.],
            [-1, 0., -1, -1, math.log1p(80.)],
            [-1, 0., -1, -1, math.log1p(20.)],
        ]],
        "maximum_depth": 1, "calibration_offset": 0.,
        "reference_path_s": 100., "model_seconds_per_work_token": .02,
        "calibration_status": "whole_workflow_heldout", "future_graph_used_online": False,
        "training_source": {"path": "/offline/training.json", "sha256": "a" * 64},
        "feature_log_min": [0.] * len(TIMING_FEATURES),
        "feature_log_max": [math.log1p(1000.)] * len(TIMING_FEATURES),
    }
    return WorkflowWorkModel(raw)


def test_seconds_head_preserves_existing_probabilities_and_continuous_work():
    old, model = continuous_model(), path_model()
    for progress in (0., 2., 4.):
        expected_probabilities, expected_work = old.predict(
            old.task_projection("ab"), observed(progress), with_work=True,
        )
        probabilities, actual = model.predict(
            model.task_projection("ab"), observed(progress), with_work=True,
            path_timing=[0.] * len(TIMING_FEATURES),
        )
        assert probabilities == expected_probabilities
        assert {name: actual[name] for name in expected_work} == expected_work
        assert actual["remaining_path_s"] == pytest.approx(80.)
        assert actual["path_source"] == TARGET
    with pytest.raises(ValueError, match="observed tool timing"):
        model.predict(model.task_projection("ab"), observed(2.), with_work=True)


def test_seconds_head_preserves_full_initial_head_override():
    from beliefkv.predictor.workflow_continuous_work import serving_contract
    from tests.test_workflow_continuous_work import training_rows
    from tests.test_workflow_work_distribution import distribution_raw

    raw = deepcopy(continuous_model().raw)
    raw.pop("continuous_work_readout")
    initial = distribution_raw()
    initial["work_thresholds"] = raw["work_thresholds"]
    initial["output_bias"] = [math.log(.6), math.log(.3), math.log(.1)]
    raw["initial_state_head"] = {
        "kind": "before_observed_work_and_children_v1", "blend": 1., "model": initial,
    }
    raw["continuous_work_readout"] = serving_contract(WorkflowWorkModel(raw), training_rows())
    old = WorkflowWorkModel(raw)
    new = path_model(old)
    expected_probabilities, expected_work = old.predict(
        old.task_projection("ab"), observed(0.), with_work=True,
    )
    probabilities, actual = new.predict(
        new.task_projection("ab"), observed(0.), with_work=True,
        path_timing=[0.] * len(TIMING_FEATURES),
    )
    assert probabilities == expected_probabilities
    assert {name: actual[name] for name in expected_work} == expected_work


def test_path_readout_reuses_one_existing_work_forecast_pass(monkeypatch):
    model = path_model()
    calls = []
    for name, tree in (
        ("work", model.conditional_tree.tree), ("path", model.completion_path.tree),
    ):
        score = tree.score
        monkeypatch.setattr(
            tree, "score", lambda values, score=score, name=name: (calls.append(name), score(values))[1],
        )
    model.predict(
        model.task_projection("ab"), observed(2.), with_work=True,
        path_timing=[0.] * len(TIMING_FEATURES),
    )
    assert calls == ["work", "path"]


@pytest.mark.parametrize("mutation", (
    lambda value: value.update(future_graph_used_online=True),
    lambda value: value.update(model_seconds_per_work_token=0.),
    lambda value: value.update(reference_path_s=True),
    lambda value: value.update(representation_sha256="0" * 64),
    lambda value: value["training_source"].update(sha256="z" * 64),
    lambda value: value.update(future_tool_graph=[]),
    lambda value: value.update(calibration_status="training_only"),
))
def test_seconds_head_rejects_invalid_units_future_graphs_and_unbound_sources(mutation):
    raw = deepcopy(path_model().raw)
    mutation(raw["completion_path_readout"])
    with pytest.raises(ValueError, match="source-bound causal seconds"):
        WorkflowWorkModel(raw)


def test_tool_clock_is_causal_parallel_and_duplicate_safe():
    clock = ObservedToolClock()
    a = event(Kind.TOOL_START, invocation_id="a", attributes={"tool_call_id": "same"})
    b = event(Kind.TOOL_START, invocation_id="b", attributes={"tool_call_id": "same"})
    clock.event(a, 10.)
    clock.event(a, 12.)
    clock.event(b, 14.)
    assert tuple(map(math.expm1, clock.features(16.))) == pytest.approx((2., 2., 6., 8., 0., 0.))
    clock.event(replace(a, kind=Kind.TOOL_END), 18.)
    clock.event(replace(a, kind=Kind.TOOL_END), 19.)
    assert tuple(map(math.expm1, clock.features(20.))) == pytest.approx((1., 6., 6., 6., 1., 8.))
    clock.event(replace(b, kind=Kind.INVOCATION_CANCEL), 21.)
    clock.event(replace(b, confidence=EventConfidence.INFERRED), 22.)
    assert not clock.started
    assert clock.completed_count == 1


def test_worker_uses_receipt_clock_and_refreshes_elapsed_wait_without_new_events(monkeypatch):
    from beliefkv.runtime import workflow_work_worker as module

    clock = [100.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    worker = WorkflowWorkWorker("", "", model=path_model(), autostart=False)
    try:
        for value in (start(), root(), event(
            Kind.TOOL_START, invocation_id="root", attributes={"tool_call_id": "a"},
        )):
            worker.submit("wf", "event", value)
        clock[0] = 102.
        worker.process_pending()
        first = worker.prediction("wf")
        assert isinstance(first, CompletionPathPrediction)
        assert first.remaining_path_s == pytest.approx(80.)
        assert worker._tool_clocks["wf"].started["root", "a"] == 100.
        clock[0] = 102.2
        worker.process_pending()
        assert worker.prediction("wf") is first
        clock[0] = 106.
        worker.process_pending()
        assert worker.prediction("wf").remaining_path_s == pytest.approx(20.)
        assert worker.counts["inferences"] == 2
        worker.submit("wf", "event", event(
            Kind.TOOL_END, invocation_id="root", attributes={"tool_call_id": "a"},
        ))
        assert worker.prediction("wf") is None
        clock[0] = 108.
        worker.process_pending()
        assert worker._tool_clocks["wf"].completed_seconds == 6.
        assert "wf" not in worker._dirty
        worker.submit("wf", "event", event(Kind.WORKFLOW_END))
        worker.process_pending()
        assert "wf" not in worker._tool_clocks and worker.prediction("wf") is None
    finally:
        worker.close()


def test_outside_tool_support_and_numerical_overflow_abstain_instead_of_clipping():
    model = path_model()
    _, result = model.predict(
        model.task_projection("ab"), observed(2.), with_work=True,
        path_timing=[math.log1p(100000.)] * len(TIMING_FEATURES),
    )
    assert "remaining_path_s" not in result
    assert result["completion_path_support"] == "tool_observations_outside_training_support"
    raw = deepcopy(model.raw)
    raw["completion_path_readout"]["trees"] = [[[-1, 0., -1, -1, 1000.]]]
    model = WorkflowWorkModel(raw)
    _, result = model.predict(
        model.task_projection("ab"), observed(2.), with_work=True,
        path_timing=[0.] * len(TIMING_FEATURES),
    )
    assert result["completion_path_support"] == "nonfinite_completion_path"
    assert "remaining_path_s" not in result


def prediction(work, path):
    return CompletionPathPrediction(
        (0., 0., 1.), 2, 1, 100., "model", "calibrated_distribution", 200., 1,
        .5, math.log1p(work), .1, 100., "continuous_work", path, 100., TARGET,
    )


def test_path_order_and_model_time_fallback_use_seconds_in_every_entry():
    controller, admission, keys = tool_controller()
    controller.strict_remaining_work = True
    controller.completion_path_profile = path_model().raw["completion_path_readout"]
    admission._protected_join = lambda value: False
    controller.prediction = {
        keys[0].root_workflow_id: prediction(100., 1000.),
        keys[1].root_workflow_id: prediction(10000., 10.),
        keys[2].root_workflow_id: prediction(1000., 50.),
    }.get
    assert controller.order_tool_replacement(keys) == [keys[1], keys[2], keys[0]]
    admission._protected_join = lambda value: value is keys[0]
    assert controller.order_tool_replacement(keys)[0] is keys[0]
    missing = replace(prediction(1000., 1.), path_source="unsupported")
    assert controller._work_weight(keys[2], {keys[2].root_workflow_id: missing}) == pytest.approx(
        1. / (1. + 1000. * .02 / 100.),
    )


def test_point_path_order_cannot_be_reversed_by_uncertainty_utility():
    controller, _admission, keys = tool_controller()
    controller.strict_remaining_work = True
    controller.completion_path_profile = path_model().raw["completion_path_readout"]
    a, b = prediction(100000., 10.), prediction(10., 100.)
    a = replace(a, effective_weight=.1)
    b = replace(b, effective_weight=.9)
    values = {keys[0].root_workflow_id: a, keys[1].root_workflow_id: b}
    assert controller._work_weight(keys[0], values) > controller._work_weight(keys[1], values)


def test_path_choice_changes_real_native_eligibility(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates

    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    admission._combination.completion_path_profile = path_model().raw["completion_path_readout"]
    try:
        admission._work._predictions.update({
            "a": prediction(10., 1000.), "b": prediction(100000., 10.),
            "c": prediction(100000., 1000.),
        })
        admission.tick()
        assert {row.identity for row in admission.policy.programs.values() if row.active} == {"b", "c"}
        requests = list(admission._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=SimpleNamespace(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert {row.rid for row in selected.candidates} == {"b-request", "c-request"}
    finally:
        runtime.close()


@pytest.mark.parametrize("head_kind", ("legacy", "work_prior", "work_residual"))
def test_path_artifact_loads_through_the_real_shared_runtime_constructor(monkeypatch, tmp_path, head_kind):
    import hashlib
    import json
    from pathlib import Path
    from tests import test_workflow_combination_runtime as combination

    settings = combination.configured(tmp_path)
    path = Path(settings["WORKFLOW_WORK_MODEL"])
    profile = json.loads(path.read_bytes())["admission_cost_profile"]
    profile["kind"] = "r5_frozen_batch_cost_proxy_capacity_v7"
    profile["costs"].update(decode_cost_basis="full_batch", selection_objective="benefit")
    if head_kind == "legacy":
        raw = deepcopy(path_model().raw)
    else:
        from beliefkv.predictor.workflow_completion_path import RESIDUAL_KIND
        from tests.test_r5_joint_prediction import model_with_work_prior

        raw = deepcopy(model_with_work_prior().raw)
        if head_kind == "work_residual":
            raw["completion_path_readout"]["kind"] = RESIDUAL_KIND
    raw.update(
        admission_cost_profile=profile, shortest_work_first_admission=True,
        strict_remaining_work_admission=True,
    )
    path.write_text(json.dumps(raw))
    settings["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(combination, "configured", lambda _path: settings)
    runtime, admission, _env = combination.runtime_with_queue(monkeypatch, tmp_path)
    try:
        assert admission._work.model.completion_path is not None
        assert admission._combination.strict_remaining_work
        assert admission._combination.completion_path_profile["target"] == TARGET
        assert admission.rank_kind == "conditional_remaining_completion_path_seconds"
        assert admission._work.status()["scheduler_waits_for_inference"] is False
    finally:
        runtime.close()


def test_mixed_recovery_categories_do_not_split_a_fitting_ready_join():
    rows, resources, costs = fixture()
    first = replace(rows[0], identity="s1", group="join", category=0, logical_tokens=80, unfinished_members=2)
    peer = replace(first, identity="s2", category=2)
    other = replace(first, identity="other", group="single", category=1, unfinished_members=1)
    result = choose_shortest_work_first(
        [first, other, peer], (), replace(resources, logical_tokens=160), costs, clock=lambda: 0,
    )
    assert result["selected"] == ("s1", "s2")
    partial = choose_shortest_work_first(
        [first, peer], (), replace(resources, logical_tokens=80), costs, clock=lambda: 0,
    )
    assert partial["selected"] == ("s1",)


def test_candidate_window_can_expand_to_keep_a_visible_sibling_group(monkeypatch):
    controller, keys, _calls = window_fixture()
    controller.strict_remaining_work = True
    controller.prediction = lambda _workflow: prediction(100., 100.)
    keys[6] = replace(keys[6], root_workflow_id=keys[5].root_workflow_id)
    monkeypatch.setattr(controller, "_group", lambda key, _groups: (
        ("join", 2, 0) if key in keys[5:7] else ("single:" + key.invocation_id, 1, 0)
    ))
    result = controller._candidate_window(keys, keys[:1], 100, {})
    assert keys[5] in result and keys[6] in result
    assert len(result) == 8 and result[0] is keys[0]


def test_parallel_tool_path_uses_max_and_subtracts_elapsed_wait():
    graph = [
        {"node_id": "start", "request_id": None, "dependencies": [], "delay_s": 0.},
        {"node_id": "tool-a", "request_id": None, "dependencies": [0], "delay_s": 2.},
        {"node_id": "tool-b", "request_id": None, "dependencies": [0], "delay_s": 8.},
        {"node_id": "model-a", "request_id": "a", "dependencies": [1], "delay_s": 0.},
        {"node_id": "model-b", "request_id": "b", "dependencies": [2], "delay_s": 0.},
        {"node_id": "join", "request_id": "parent", "dependencies": [3, 4], "delay_s": 0.},
    ]
    requests = {
        name: {"input_increment": 0, "output_tokens": count}
        for name, count in (("a", 3), ("b", 1), ("parent", 4))
    }
    cached = cache_remaining_paths(graph, requests, {"start": 100.}, {}, 1.)
    assert cached.at(100.) == (13., 7., 8.)
    assert cached.at(103.) == (10., 7., 5.)


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("deadline", (None, 1, 2))
def test_old_and_new_group_deadlines_remain_exactly_replayable(grouped, deadline):
    from dataclasses import asdict
    import json
    from beliefkv.policy.workflow_combination import CombinationResources
    from scripts.audit_r5_shortest_contract import candidate
    from scripts.audit_workflow_combination_choices import replay_choice
    from tests.test_combination_choice_audit import evidence

    row, costs = evidence()
    costs = replace(costs, decode_cost_basis="full_batch", selection_objective="benefit")
    rows = []
    for index, raw in enumerate(row["candidates"][:3]):
        value = candidate(raw)
        value = replace(
            value, workflow="workflow-a", group="join", unfinished_members=3,
            weight=.9, logical_tokens=100, category=index,
        )
        row["keys"][value.identity]["root_workflow_id"] = "workflow-a"
        rows.append(value)
    calls = -1

    def clock():
        nonlocal calls
        calls += 1
        return 500_000 if deadline is not None and calls >= deadline else 0

    row["selection_policy"] = "shortest_work_first"
    row["candidates"] = [asdict(value) for value in rows]
    row["decision"] = choose_shortest_work_first(
        rows, ("a",), CombinationResources(**row["resources"]), costs,
        group_ready_siblings=grouped, clock=clock,
    )
    replayed = replay_choice(json.loads(json.dumps(row)), costs, "frozen")
    assert replayed["search_replayed"]
    assert replayed["selected"] == list(row["decision"]["selected"])
