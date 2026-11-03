import numpy as np
import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from beliefkv.predictor.workflow_work_calibration import (
    CalibratedWorkCandidate, correct_probabilities, fit_probability_correction,
)
from scripts.calibrate_workflow_probability_bias import fold_fit
from tests.test_workflow_work_distribution import distribution_raw


def test_identity_correction_keeps_original_probability_values_exactly():
    scores = np.asarray(((.2, .3, .5), (.8, .1, .1)))
    assert np.array_equal(correct_probabilities(scores, (0., 0., 0.)), scores)


def test_whole_held_out_workflow_labels_cannot_influence_fold_parameters():
    rows = [
        {"workflow": str(index // 3), "features": [0.] * len(R5_FEATURES),
         "remaining_work": float(index * 4)} for index in range(12)
    ]
    scores = np.tile((.3, .4, .3), (12, 1))
    original = fold_fit(
        rows, scores, (10., 20.), regularization=.1, progress_only=False, held_out="0",
    )
    for row in rows[:3]:
        row["remaining_work"] = 10**12
    assert fold_fit(
        rows, scores, (10., 20.), regularization=.1, progress_only=False, held_out="0",
    ) == original


def test_progress_only_correction_preserves_initial_head_and_normalized_outputs():
    model = WorkflowWorkModel(distribution_raw())
    candidate = CalibratedWorkCandidate(model, (0., -.3, .3), progress_only=True)
    projection, features = model.task_projection("ab"), [0.] * len(R5_FEATURES)
    original = model.predict(projection, features)
    assert candidate.predict(projection, features) == original
    features[1] = 128.
    result = candidate.predict(projection, features)
    assert sum(result) == pytest.approx(1.)
    assert result[0] < original[0] and result[2] > original[2]


@pytest.mark.parametrize("parameters", ((float("nan"), 0., 0.), (0., 4., 0.), (2., 0., 0.), (0.,)))
def test_corrupted_correction_parameters_fail_closed(parameters):
    with pytest.raises(ValueError):
        correct_probabilities((.2, .3, .5), parameters)


def test_calibration_optimizer_reduces_held_constant_class_bias_without_overconfidence():
    scores = np.tile((.7, .2, .1), (30, 1))
    labels = np.asarray([0, 1, 2] * 10)
    fitted = fit_probability_correction(scores, labels, np.ones(30), regularization=.1)
    corrected = correct_probabilities(scores, fitted)
    assert -np.log(corrected[np.arange(30), labels]).mean() < -np.log(scores[np.arange(30), labels]).mean()
    assert np.isfinite(corrected).all()
    assert np.allclose(corrected.sum(axis=1), 1.)
