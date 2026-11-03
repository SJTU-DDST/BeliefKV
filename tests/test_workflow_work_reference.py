from copy import deepcopy

import numpy as np
import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from beliefkv.predictor.workflow_work_reference import (
    ReferenceWorkCandidate, SurvivalWorkCandidate, WorkflowWorkReferences, fit_references,
)
from tests.test_workflow_work_distribution import distribution_raw


def training_rows():
    rows = []
    for index, total in enumerate((30., 60., 90., 120., 150., 180.)):
        for snapshot, attained in enumerate((0., total / 2, total)):
            features = [0.] * len(R5_FEATURES)
            features[1] = attained
            features[R5_FEATURES.index("completed_calls")] = snapshot
            rows.append({
                "workflow": str(index), "snapshot": snapshot, "text": "ab",
                "features": features, "total_work": total,
                "attained_work": attained, "remaining_work": total - attained,
            })
    return rows


def fitted():
    model = WorkflowWorkModel(distribution_raw())
    raw = fit_references(training_rows(), model, maximum_states=8)
    return model, raw, WorkflowWorkReferences(raw)


def test_query_uses_observed_service_not_future_query_labels_or_wall_time():
    model, _raw, references = fitted()
    features = [0.] * len(R5_FEATURES)
    features[1] = 20.
    query = {"features": features, "remaining_work": 0., "tool_duration": 1000.}
    projection = model.task_projection("ab")
    original = references.predict(projection, query["features"])
    query.update(remaining_work=10**12, tool_duration=.001, future_children=999)
    assert references.predict(projection, query["features"]) == original
    assert sum(original) == pytest.approx(1.)
    assert min(original) > 0.


def test_repeated_identical_training_observations_do_not_increase_workflow_weight():
    model, raw, references = fitted()
    rows = training_rows()
    for _index in range(40):
        rows.insert(2, {**rows[1]})
    duplicate = WorkflowWorkReferences(fit_references(rows, model, maximum_states=8))
    for count in (0., 20., 50.):
        features = [0.] * len(R5_FEATURES)
        features[1] = count
        assert duplicate.predict(model.task_projection("ab"), features) == pytest.approx(
            references.predict(model.task_projection("ab"), features),
        )
    assert len(raw["totals"]) == 6


def test_outside_training_survival_support_falls_back_to_original_probabilities():
    model, _raw, references = fitted()
    candidate = ReferenceWorkCandidate(
        model, references, blend=.5, bandwidth=.7, task_weight=1., progress_only=True,
    )
    projection = model.task_projection("ab")
    features = [0.] * len(R5_FEATURES)
    assert candidate.predict(projection, features) == model.predict(projection, features)
    features[1] = 1000.
    assert references.predict(projection, features) is None
    assert candidate.predict(projection, features) == model.predict(projection, features)


@pytest.mark.parametrize("corruption", ("future_input", "negative_work", "unordered_work", "nonfinite"))
def test_invalid_reference_labels_and_observation_sequences_fail_closed(corruption):
    model = WorkflowWorkModel(distribution_raw())
    rows = training_rows()
    if corruption == "future_input":
        rows[1]["features"][1] += 1
    elif corruption == "negative_work":
        rows[1]["remaining_work"] = -1.
    elif corruption == "unordered_work":
        rows[1], rows[2] = rows[2], rows[1]
        rows[1]["snapshot"], rows[2]["snapshot"] = 1, 2
    else:
        rows[1]["features"][0] = float("nan")
    with pytest.raises(ValueError, match="conserved|ordered"):
        fit_references(rows, model)


@pytest.mark.parametrize("corruption", ("nan", "shape", "decreasing", "negative_scale", "changed_features"))
def test_corrupted_serialized_references_are_rejected(corruption):
    _model, raw, _references = fitted()
    raw = deepcopy(raw)
    if corruption == "nan":
        raw["states"][0][0][0] = float("nan")
    elif corruption == "shape":
        raw["states"][0].pop()
    elif corruption == "decreasing":
        raw["attained"][0][1] = -1.
    elif corruption == "negative_scale":
        raw["scale"][0] = -1.
    else:
        raw["features"][0] = "future_duration"
    with pytest.raises(ValueError):
        WorkflowWorkReferences(raw)


def test_reference_geometry_contains_no_query_graph_tokens_or_execution_identity():
    _model, raw, _references = fitted()
    assert not {"workflow", "workflows", "requests", "project", "text", "tokens"} & raw.keys()
    assert np.asarray(raw["states"]).shape == (6, 8, 17)
    assert raw["reference_weighting"] == "one_conditional_vote_per_independent_training_workflow"


def test_survival_conditions_on_observed_model_work_and_preserves_original_initial_predictions():
    model = WorkflowWorkModel(distribution_raw())
    candidate = SurvivalWorkCandidate(
        model, (5., 8., 15., 18., 30., 40., 60., 80.),
        blend=.5, after_short_threshold=True,
    )
    projection, features = model.task_projection("ab"), [0.] * len(R5_FEATURES)
    assert candidate.predict(projection, features) == model.predict(projection, features)
    features[1] = 20.
    result = candidate.predict(projection, features)
    assert sum(result) == pytest.approx(1.)
    assert result[2] > model.predict(projection, features)[2]
    # Tool/queue lifecycle changes with no service do not advance survival.
    changed = list(features)
    changed[R5_FEATURES.index("completed_tools")] = 100.
    assert candidate.predict(projection, changed) == result


def test_zero_surviving_initial_mass_falls_back_without_publishing_nan_probabilities():
    raw = distribution_raw()
    raw["output_bias"] = [1000., 0., 0.]
    model = WorkflowWorkModel(raw)
    candidate = SurvivalWorkCandidate(
        model, (5., 8., 15., 18., 30., 40., 60., 80.),
        blend=.5, after_short_threshold=True,
    )
    features = [0.] * len(R5_FEATURES)
    features[1] = 20.
    projection = model.task_projection("ab")
    assert candidate.predict(projection, features) == model.predict(projection, features)
