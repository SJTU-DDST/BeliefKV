from copy import deepcopy

import pytest

from beliefkv.predictor.workflow_progress_tree import (
    FEATURES, KIND, ProgressTreeHead, features,
)
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from tests.test_workflow_work_distribution import distribution_raw


def raw_head():
    return {
        "kind": KIND, "features": list(FEATURES), "objective": "multiclass",
        "trees": [[[0, 1., 1, 2, 0.], [-1, 0., -1, -1, 1.], [-1, 0., -1, -1, -1.]],
                  [[-1, 0., -1, -1, 0.]], [[-1, 0., -1, -1, 0.]]],
        "maximum_depth": 1, "blend": .5, "temperature": 1.,
        "regression_offset": 0., "regression_scale": 1.,
    }


def test_progress_features_use_only_observed_counts_and_explicit_task_structure():
    values = [0.] * len(R5_FEATURES)
    values[R5_FEATURES.index("output_tokens")] = 128.
    values[R5_FEATURES.index("completed_calls")] = 2.
    result = features(values, [0.] * 16)
    import math
    assert result[FEATURES.index("output_per_call")] == pytest.approx(math.log1p(64.))
    assert len(result) == len(FEATURES)
    assert not any("total_work" in name or "project" in name for name in FEATURES)


def test_portable_tree_prediction_and_probability_blend_match_manual_results():
    import numpy as np
    head = ProgressTreeHead(raw_head())
    vector = np.zeros(len(FEATURES))
    assert head.score(vector).tolist() == [1., 0., 0.]
    expected = np.exp([1., 0., 0.])
    expected /= expected.sum()
    assert head.probabilities(vector, (10., 20.)).tolist() == pytest.approx(expected)
    vector[0] = 2.
    assert head.score(vector).tolist() == [-1., 0., 0.]


@pytest.mark.parametrize("mutation", ("cycle", "nan", "extra_node", "deep", "feature", "incomplete_round"))
def test_malformed_tree_artifacts_fail_closed(mutation):
    raw = raw_head()
    if mutation == "cycle":
        raw["trees"][0][0][2] = 0
    elif mutation == "nan":
        raw["trees"][0][0][1] = float("nan")
    elif mutation == "extra_node":
        raw["trees"][0].append([-1, 0., -1, -1, 1.])
    elif mutation == "deep":
        raw["trees"][0][1] = [1, 0., 3, 4, 0.]
        raw["trees"][0].extend([[-1, 0., -1, -1, 0.]] * 2)
    elif mutation == "feature":
        raw["trees"][0][0][0] = len(FEATURES)
    else:
        raw["trees"].pop()
    with pytest.raises(ValueError):
        ProgressTreeHead(raw)


def test_log_work_regression_has_ordered_finite_probabilities():
    import math
    raw = raw_head()
    raw.update(objective="log_remaining_work", trees=[[[-1, 0., -1, -1, math.log1p(10.)]]])
    values = ProgressTreeHead(raw).probabilities([0.] * len(FEATURES), (10., 20.))
    assert values[0] == pytest.approx(.5)
    assert sum(values) == pytest.approx(1.)
    assert all(0 <= value <= 1 for value in values)


def test_offline_head_uses_cached_task_structure_without_a_production_inference_path():
    from scripts.train_workflow_work_progress_trees import ExperimentalTreeWorkModel

    raw = distribution_raw()
    raw["progress_tree_head"] = raw_head()
    model = ExperimentalTreeWorkModel(raw)
    projection, _coverage = model.task_encoding("ab")
    values = [0.] * len(R5_FEATURES)
    actual = model.predict(projection, values)
    base = WorkflowWorkModel(distribution_raw())
    expected = model.progress_head.combine(
        base.predict(base.task_projection("ab"), values), values,
        projection.task_structure, model.thresholds,
    )
    assert actual == pytest.approx(expected)
    with pytest.raises(ValueError, match="cached task structure"):
        model.predict([0.], values)
    other = deepcopy(raw)
    other["observed_delegation_inputs"] = True
    with pytest.raises(ValueError, match="explicit-task"):
        ExperimentalTreeWorkModel(other)


def test_subset_export_keeps_whole_originals_and_rejects_missing_or_duplicate_ids():
    from scripts.train_workflow_work_distribution import select_export_workflows

    selected = [{"workflow_id": "a"}, {"workflow_id": "b"}]
    assert select_export_workflows(selected, None) is selected
    assert select_export_workflows(selected, ["b"]) == [{"workflow_id": "b"}]
    for invalid in ([], ["a", "a"], ["missing"], "a", [None]):
        with pytest.raises(ValueError):
            select_export_workflows(selected, invalid)


@pytest.mark.parametrize("field", ("workflow", "source_workflow_sha256", "project", "task_template"))
def test_training_extensions_cannot_contaminate_evaluation_or_duplicate_existing_work(field):
    from scripts.extend_workflow_prediction_training import disjoint_training_rows

    def row(prefix):
        return {
            "workflow": prefix, "source_workflow_sha256": prefix * 64,
            "project": prefix, "task_template": prefix, "source_capture": prefix,
            "logical_workflow_id": prefix,
        }

    train, extra, calibration, evaluation = [[row(name)] for name in "abcd"]
    assert disjoint_training_rows(train, extra, calibration, evaluation) == train + extra
    for existing in (train, calibration, evaluation):
        bad = deepcopy(extra)
        bad[0][field] = existing[0][field]
        with pytest.raises(ValueError, match="overlaps"):
            disjoint_training_rows(train, bad, calibration, evaluation)
