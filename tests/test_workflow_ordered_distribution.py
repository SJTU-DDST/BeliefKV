from copy import deepcopy

import numpy as np
import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES, R5_SCHEMA, WorkflowWorkModel
from scripts.train_workflow_work import fit
from tests.test_workflow_work_distribution import distribution_raw
from tests.test_workflow_work_distribution_training import row


def ordered_raw():
    raw = distribution_raw()
    raw["ordered_work_cutpoints"] = [-1., 1.]
    raw["output_weight"] = np.zeros((len(raw["hidden_bias"]), 1)).tolist()
    raw["output_bias"] = [0.]
    return raw


def test_ordered_head_preserves_probability_mass_and_moves_tails_in_opposite_directions():
    raw = ordered_raw()
    observed = []
    for score in (-5., 0., 5.):
        raw["output_bias"] = [score]
        model = WorkflowWorkModel(deepcopy(raw))
        values = model.predict(model.task_projection("ab"), [0.] * len(R5_FEATURES))
        assert min(values) >= 0.
        assert sum(values) == pytest.approx(1.)
        observed.append(values)
    assert observed[0][0] > observed[1][0] > observed[2][0]
    assert observed[0][2] < observed[1][2] < observed[2][2]


@pytest.mark.parametrize("cutpoints", ((0., 0.), (1., -1.), (float("nan"), 1.), (0.,), "bad"))
def test_invalid_ordered_cutpoints_cannot_silently_use_categorical_inference(cutpoints):
    raw = ordered_raw()
    raw["ordered_work_cutpoints"] = list(cutpoints) if isinstance(cutpoints, tuple) else cutpoints
    with pytest.raises(ValueError, match="cutpoints"):
        WorkflowWorkModel(raw)


def test_real_ordered_training_exports_one_score_and_uses_the_same_frozen_work_bins():
    training = [
        {**row(str(index)), "total_work": 100. * (index + 1),
         "attained_work": 0., "remaining_work": 100. * (index + 1)}
        for index in range(6)
    ]
    calibration = [row("cal-" + str(index), remaining=100. * (index + 1)) for index in range(6)]
    raw = fit(
        training, calibration, epochs=3, split_mode="workflow", hidden_width=4,
        vocabulary_size=4, feature_schema=R5_SCHEMA, work_thresholds=(200., 400.),
        ordered_work_head=True, ranking_loss_weight=.1,
    )
    assert raw["work_thresholds"] == [200., 400.]
    assert np.asarray(raw["output_weight"]).shape == (4, 1)
    assert raw["ordered_work_cutpoints"][0] < raw["ordered_work_cutpoints"][1]
    model = WorkflowWorkModel(raw)
    assert sum(model.predict(model.task_projection("ab"), [0.] * len(R5_FEATURES))) == pytest.approx(1.)


def test_legacy_training_and_explicit_disabled_ordered_head_remain_bitwise_identical():
    training = [
        {**row(str(index)), "total_work": 100. * (index + 1),
         "attained_work": 0., "remaining_work": 100. * (index + 1)}
        for index in range(6)
    ]
    calibration = [row("cal-" + str(index), remaining=100. * (index + 1)) for index in range(6)]
    settings = {
        "epochs": 2, "split_mode": "workflow", "hidden_width": 4,
        "vocabulary_size": 4, "feature_schema": R5_SCHEMA, "work_thresholds": (200., 400.),
    }
    assert fit(training, calibration, **settings) == fit(
        training, calibration, ordered_work_head=False, **settings,
    )
