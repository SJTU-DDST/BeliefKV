from copy import deepcopy
import math

import numpy as np
import pytest

from beliefkv.predictor.workflow_progress_tree import FEATURES, features
from beliefkv.predictor.workflow_task_encoder import task_features
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from beliefkv.predictor.workflow_work_readout import representation_fingerprint
from scripts.train_workflow_branch_call_trees import (
    KIND, ConditionalTreeCandidate, fit_trees, log_work, matrix, tree_inputs, work_probabilities,
)
from scripts.export_workflow_tree_predictor import portable_head
from tests.test_workflow_work_distribution import distribution_raw


def model():
    return WorkflowWorkModel(distribution_raw())


def row(index, *, observed=1.):
    state = [0.] * len(R5_FEATURES)
    state[R5_FEATURES.index("output_tokens")] = observed
    state[R5_FEATURES.index("ready")] = 1.
    return {
        "workflow": "w" + str(index % 4), "project": "p" + str(index % 4),
        "task_text": "ab", "text": "ab", "features": state,
    }


def test_tree_inputs_ignore_offline_labels_ids_and_unavailable_future_task_fields():
    base = model()
    original = row(0)
    altered = {
        **deepcopy(original), "total_work": 1e9, "remaining_work": 1e9,
        "workflow": "future-identity", "snapshot": 1000,
        "future_graph": {"root": ["unknown_child"]}, "text": "future_prompt_leak",
    }
    first, valid = matrix(base, [original])
    second, other_valid = matrix(base, [altered])
    assert valid.tolist() == other_valid.tolist() == [True]
    assert first == pytest.approx(second, abs=0.)
    projection = base.task_projection("ab")
    values = tree_inputs(base, projection, original["features"], task_features("ab"))
    assert values[:len(FEATURES)] == pytest.approx(features(original["features"], task_features("ab")))


def test_tree_call_proxy_uses_current_observed_role_cost_with_shrunken_training_prior():
    observed = row(0)["features"]
    observed[R5_FEATURES.index("root_completed_calls")] = 2.
    observed[R5_FEATURES.index("root_output_tokens")] = 60.
    observed[R5_FEATURES.index("child_completed_calls")] = 1.
    observed[R5_FEATURES.index("child_input_tokens")] = 1000.
    before = observed.copy()
    expected = math.log1p(2. * (60. + 40.) / 6. + 3. * (10. + 80.) / 5.)
    assert log_work(
        np.log1p([[2., 3.]]), [observed], "branch_calls", {"root": 10., "child": 20.},
    )[0] == pytest.approx(expected)
    assert observed == before
    with pytest.raises(ValueError, match="unknown"):
        log_work(np.zeros((1, 2)), [observed], "oracle_graph", {})


def test_tree_probability_mass_is_finite_for_wide_work_predictions():
    scores = work_probabilities(np.asarray([-1000., 0., 1000.]), [5., 10.], 0., .25)
    assert np.isfinite(scores).all() and (scores >= 0).all()
    assert scores.sum(1) == pytest.approx(np.ones(3))
    assert scores[0, 0] == 1. and scores[-1, -1] == 1.


def trained_candidate():
    base = model()
    rows = [row(index, observed=float(index + 1)) for index in range(256)]
    x, _valid = matrix(base, rows)
    target = np.column_stack((np.log1p(np.arange(256) + 1.), np.log1p(np.arange(256) + 2.)))
    _trees, fitted = fit_trees(x, target, rows, leaves=4, rounds=3)
    head = {
        **fitted, "kind": KIND, "target": "branch_calls",
        "representation_sha256": representation_fingerprint(base),
        "features": list(FEATURES) + ["frozen_hidden_0"],
        "per_call_prior": {"root": 10., "child": 20.},
        "calibration_offset": 0., "calibration_sigma": .5,
    }
    return base, head


@pytest.fixture(scope="module")
def candidate_data():
    return trained_candidate()


def test_native_serialization_and_single_state_adapter_preserve_initial_branch(candidate_data):
    base, head = candidate_data
    assert head["serialized_prediction_max_abs_error"] <= 1e-12
    candidate = ConditionalTreeCandidate(base, head, .5)
    initial = row(0, observed=0.)["features"]
    projection = candidate.task_projection("ab")
    assert candidate.predict(projection, initial) == base.predict(base.task_projection("ab"), initial)
    state = row(0, observed=2.)["features"]
    probabilities = candidate.predict(projection, state)
    assert all(type(value) is float and 0 <= value <= 1 for value in probabilities)
    assert sum(probabilities) == pytest.approx(1.)
    with pytest.raises(ValueError, match="cached"):
        candidate.predict(base.task_projection("ab"), state)


@pytest.mark.parametrize("change", ("source", "dimensions", "nonfinite", "target", "prior"))
def test_conditional_tree_adapter_rejects_incompatible_parameters(candidate_data, change):
    base, original = candidate_data
    head = deepcopy(original)
    if change == "source":
        head["representation_sha256"] = "0" * 64
    elif change == "dimensions":
        head["features"].append("unknown_future_feature")
    elif change == "nonfinite":
        head["calibration_sigma"] = float("nan")
    elif change == "target":
        head["target"] = "future_graph"
    else:
        head["per_call_prior"]["root"] = 0.
    with pytest.raises(ValueError):
        ConditionalTreeCandidate(base, head, .5)


def test_offline_tree_package_cannot_silently_load_as_a_serving_model(candidate_data):
    base, head = candidate_data
    with pytest.raises(ValueError):
        WorkflowWorkModel({"kind": KIND, "base_model": base.raw, "head": head, "blend": .5})


def selected_artifact(candidate_data):
    base, head = candidate_data
    raw = {
        "kind": KIND, "base_model": deepcopy(base.raw), "head": deepcopy(head), "blend": .5,
        "metadata": {"decision_eligible": False},
    }
    evaluation = {
        "selected": "chosen", "candidate_selection_used_test_labels": False,
        "deployment_adapter_implemented": False,
        "calibration_summary": {"chosen": {
            "decision_eligible": True, "decision_rejection_reasons": [], "blend": .5,
        }},
    }
    return raw, evaluation, base.raw


def test_selected_offline_audit_needs_explicit_adapter_and_keeps_serving_model_disabled(candidate_data):
    from scripts.audit_workflow_call_decomposition import selected_candidate_model

    raw, evaluation, baseline = selected_artifact(candidate_data)
    before = deepcopy((raw, evaluation, baseline))
    with pytest.raises((ValueError, KeyError)):
        selected_candidate_model(raw, evaluation, baseline)
    candidate = selected_candidate_model(raw, evaluation, baseline, offline_tree=True)
    assert candidate.predict(candidate.task_projection("ab"), row(0)["features"])
    assert (raw, evaluation, baseline) == before
    assert raw["metadata"]["decision_eligible"] is False


@pytest.mark.parametrize("change", ("selection", "labels", "gate", "base", "serving", "blend"))
def test_selected_offline_audit_rejects_changed_selection_or_failed_calibration(candidate_data, change):
    from scripts.audit_workflow_call_decomposition import selected_candidate_model

    raw, evaluation, baseline = selected_artifact(candidate_data)
    if change == "selection":
        evaluation["selected"] = "retained"
    elif change == "labels":
        evaluation["candidate_selection_used_test_labels"] = True
    elif change == "gate":
        evaluation["calibration_summary"]["chosen"]["decision_eligible"] = False
    elif change == "base":
        raw["base_model"]["output_bias"] = [1., 2., 3.]
    elif change == "serving":
        raw["metadata"]["decision_eligible"] = True
    else:
        raw["blend"] = 1.
    with pytest.raises(ValueError):
        selected_candidate_model(raw, evaluation, baseline, offline_tree=True)


def serving_model(candidate_data):
    base, call_head = candidate_data
    work_head = {**call_head, "target": "work", "models": call_head["models"][:1]}
    candidate = ConditionalTreeCandidate(base, work_head, .5)
    raw = deepcopy(base.raw)
    raw["conditional_progress_tree_head"] = portable_head(candidate)
    return WorkflowWorkModel(raw), candidate, raw


def test_portable_serving_export_matches_native_tree_scores_and_preserves_initial_state(candidate_data):
    serving, candidate, raw = serving_model(candidate_data)
    for state in (row(0, observed=0.), row(1, observed=2.), row(2, observed=300.)):
        observed = state["features"]
        expected = candidate.predict(candidate.task_projection("ab"), observed)
        assert serving.predict(serving.task_projection("ab"), observed) == pytest.approx(expected, abs=1e-12)
    with pytest.raises(ValueError, match="cached task structure"):
        serving.predict(candidate.base.task_projection("ab"), row(0)["features"])
    assert raw["conditional_progress_tree_head"]["representation_sha256"] == representation_fingerprint(candidate.base)
    with pytest.raises(ValueError, match="direct-work"):
        portable_head(ConditionalTreeCandidate(candidate.base, candidate_data[1], .5))
    raw["conditional_progress_tree_head"]["features"].append("export_only_change")
    assert "export_only_change" not in candidate.head["features"]


def test_portable_projection_cache_keys_preserve_equal_values_and_distinct_task_structure(candidate_data):
    from beliefkv.predictor.workflow_progress_tree import TreeProjection
    from scripts.train_workflow_reference_distribution import CachedCandidate, projection_key

    serving, _candidate, _raw = serving_model(candidate_data)
    first, second = serving.task_projection("ab"), serving.task_projection("ab")
    assert first is not second and projection_key(first) == projection_key(second)
    changed = TreeProjection(first.progress_projection, tuple(value + 1. for value in first.task_structure))
    assert projection_key(first) != projection_key(changed)
    cached = CachedCandidate(serving)
    assert cached.predict(first, row(0)["features"]) == cached.predict(second, row(0)["features"])
    assert len(cached.cache) == 1


@pytest.mark.parametrize("change", ("trunk", "source", "features", "trees", "cycle", "depth", "sigma"))
def test_serving_tree_rejects_changed_trunk_and_malformed_portable_nodes(candidate_data, change):
    _serving, _candidate, raw = serving_model(candidate_data)
    head = raw["conditional_progress_tree_head"]
    if change == "trunk":
        raw["dynamic_weight"][0][0] += 1.
    elif change == "source":
        head["representation_sha256"] = "0" * 64
    elif change == "features":
        head["features"].append("unobserved_future_work")
    elif change == "trees":
        head["trees"] *= 129
    elif change == "cycle":
        head["trees"][0] = [[0, 0., 0, 0, 0.]]
    elif change == "depth":
        head["maximum_depth"] = 7
    else:
        head["calibration_sigma"] = 0.
    with pytest.raises(ValueError):
        WorkflowWorkModel(raw)


def test_serving_tree_uses_no_lightgbm_import(candidate_data, monkeypatch):
    import builtins

    _serving, candidate, raw = serving_model(candidate_data)
    original = builtins.__import__

    def without_training_library(name, *args, **kwargs):
        if name.startswith("lightgbm"):
            raise AssertionError("serving inference must not import its training library")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_training_library)
    model = WorkflowWorkModel(raw)
    state = row(1)["features"]
    expected = candidate.base.predict(candidate.base.task_projection("ab"), state)
    assert len(expected) == len(model.predict(model.task_projection("ab"), state)) == 3


def test_actual_worker_event_progress_uses_the_portable_conditional_branch(candidate_data):
    from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
    from beliefkv.predictor.workflow_rank_input import WorkflowRankInput
    from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker

    serving, candidate, _raw = serving_model(candidate_data)
    worker = WorkflowWorkWorker("", "", model=serving, autostart=False)
    try:
        metadata = WorkflowRankInput.from_instructions(task_instructions="ab")
        worker.submit("w", "event", RuntimeEvent(
            "start", 0., Kind.WORKFLOW_START, "w",
            attributes={"workflow_ranking_input": metadata.to_dict()},
        ))
        worker.submit("w", "event", RuntimeEvent(
            "create", 1., Kind.INVOCATION_CREATE, "w",
            invocation_id="root", context_id="ctx", relation_type=RelationType.ROOT,
        ))
        worker.process_pending()
        initial = worker.prediction("w")
        assert initial is not None
        worker.submit("w", "started", "req", "root", "ctx", (1, 2))
        worker.submit("w", "generated", "req", 128)
        worker._next_inference["w"] = 0.
        worker.process_pending()
        prediction = worker.prediction("w")
        state = worker._progress["w"].features()
        expected = candidate.predict(candidate.task_projection("ab"), state)
        assert prediction.probabilities == pytest.approx(expected, abs=1e-12)
        assert prediction.revision > initial.revision
        assert worker.counts["task_encodings"] == 1
        assert worker.counts["inferences"] == 2
        assert not worker.error
    finally:
        worker.close()


@pytest.mark.parametrize("changed", ("calibration_evaluation", "competition"))
def test_export_rejects_a_changed_fixed_measurement_cohort(tmp_path, monkeypatch, changed):
    import json
    import sys
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts.export_workflow_tree_predictor import main

    names = ("candidate-model", "candidate-evaluation", "competition", "baseline-model", "dataset", "cohort")
    paths = {name: tmp_path / (name + ".json") for name in names}
    for path in paths.values():
        path.write_text("{}")
    identity = {str(path): sha256_file(path) for path in paths.values()}
    evaluation = {
        "model_sha256": identity[str(paths["candidate-model"])],
        "inputs_sha256": {str(paths[name]): identity[str(paths[name])] for name in ("dataset", "cohort")},
    }
    if changed == "calibration_evaluation":
        evaluation["inputs_sha256"][str(paths["cohort"])] = "f" * 64
    paths["candidate-evaluation"].write_text(json.dumps(evaluation))
    identity[str(paths["candidate-evaluation"])] = sha256_file(paths["candidate-evaluation"])
    competition = {
        "offline_tree_readout_adapter_used": True, "offline_tree_serving_eligibility_granted": False,
        "inputs_sha256": identity,
    }
    if changed == "competition":
        competition["inputs_sha256"][str(paths["cohort"])] = "f" * 64
    paths["competition"].write_text(json.dumps(competition))
    monkeypatch.setattr(sys, "argv", [
        "export", *(value for name, path in paths.items() for value in ("--" + name, str(path))),
        "--output-dir", str(tmp_path / "export"),
    ])
    with pytest.raises(ValueError, match="already completed paired audit"):
        main()
    assert not (tmp_path / "export").exists()
