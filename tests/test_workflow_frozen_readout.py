from copy import deepcopy
import math

import numpy as np
import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from beliefkv.predictor.workflow_work_readout import (
    KIND, FrozenReadoutCandidate, log_work_proxy, representation, representation_fingerprint,
    ridge_fit, ridge_predict,
)
from tests.test_workflow_work_distribution import distribution_raw


def model():
    raw = distribution_raw()
    raw["dynamic_weight"][R5_FEATURES.index("output_tokens")] = [.3]
    raw["output_weight"] = [[.4, -.2, .1]]
    raw["output_bias"] = [.2, 0., -.2]
    raw["temperature"] = 1.7
    return WorkflowWorkModel(raw)


def head(base, target="work"):
    channels = 1 if target == "work" else 2
    return {
        "kind": KIND, "target": target, "representation_sha256": representation_fingerprint(base),
        "ridge": {"center": [0.], "scale": [1.], "coefficient": [[0.] * channels], "bias": [2.] * channels},
        "calibration_offset": 0., "calibration_sigma": .5,
        "calibration_class_workflows": [3, 3, 3],
        "per_call_prior": {"root": 10., "child": 20.}, "cost_prior_strength": 4.,
    }


def test_frozen_representation_reproduces_primary_logits_on_observed_progress():
    base = model()
    observed = [0.] * len(R5_FEATURES)
    observed[R5_FEATURES.index("output_tokens")] = 2.
    projection = base.task_projection("ab")
    latent = representation(base, projection, observed)
    logits = (latent @ base.arrays["output_weight"] + base.arrays["output_bias"]) / base.temperature
    expected = np.exp(logits - logits.max())
    expected /= expected.sum()
    assert base.predict(projection, observed) == pytest.approx(expected, abs=1e-12)
    with pytest.raises(ValueError, match="observed"):
        representation(base, projection, [-1.] * len(R5_FEATURES))


def test_primal_ridge_recovers_multi_output_relationship_with_many_more_rows_than_features():
    x = np.random.default_rng(21).normal(size=(256, 3))
    y = np.column_stack((1. + 2. * x[:, 0] - x[:, 1], 3. + x[:, 2]))
    raw = ridge_fit(x, y, np.ones(len(x)), 1e-8)
    assert ridge_predict(raw, x[:4]) == pytest.approx(y[:4], abs=1e-5)
    with pytest.raises(ValueError, match="positive"):
        ridge_fit(x, y, np.zeros(len(x)), 1.)


def test_call_cost_reconstruction_uses_only_observed_counters_and_training_priors():
    base, observed = model(), [0.] * len(R5_FEATURES)
    forecast = head(base, "branch_calls")
    forecast["ridge"]["bias"] = [math.log1p(2.), math.log1p(3.)]
    for name, value in (
        ("root_completed_calls", 2.), ("child_completed_calls", 1.),
        ("root_output_tokens", 60.), ("child_output_tokens", 10.),
    ):
        observed[R5_FEATURES.index(name)] = value
    saved = observed.copy()
    expected = 2. * (60. + 40.) / 6. + 3. * (10. + 80.) / 5.
    assert log_work_proxy(forecast, [[0.]], [observed])[0] == pytest.approx(math.log1p(expected))
    assert observed == saved


def test_readout_preserves_initial_predictions_and_exports_finite_probability_mass():
    base = model()
    candidate = FrozenReadoutCandidate(base, head(base), .5)
    projection = base.task_projection("ab")
    initial = [0.] * len(R5_FEATURES)
    assert candidate.predict(projection, initial) == base.predict(projection, initial)
    observed = initial.copy()
    observed[R5_FEATURES.index("output_tokens")] = 2.
    probabilities = candidate.predict(projection, observed)
    assert all(type(value) is float and 0 <= value <= 1 for value in probabilities)
    assert sum(probabilities) == pytest.approx(1.)


def test_serving_loader_rejects_offline_readout_instead_of_ignoring_its_predictions():
    raw = deepcopy(model().raw)
    raw["frozen_work_readout_experimental"] = {"head": {}, "blend": .5}
    with pytest.raises(ValueError, match="experimental adapter"):
        WorkflowWorkModel(raw)


@pytest.mark.parametrize("change", ("source", "shape", "scale", "target", "prior"))
def test_readout_rejects_mismatched_or_invalid_frozen_parameters(change):
    base = model()
    forecast = deepcopy(head(base, "branch_calls"))
    if change == "source":
        forecast["representation_sha256"] = "0" * 64
    elif change == "shape":
        forecast["ridge"]["coefficient"] = [[0.]]
    elif change == "scale":
        forecast["calibration_sigma"] = float("nan")
    elif change == "target":
        forecast["target"] = "future_hidden_graph"
    else:
        forecast["cost_prior_strength"] = 0.
    with pytest.raises(ValueError):
        FrozenReadoutCandidate(base, forecast, .5)
