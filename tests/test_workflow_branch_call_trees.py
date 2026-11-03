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
