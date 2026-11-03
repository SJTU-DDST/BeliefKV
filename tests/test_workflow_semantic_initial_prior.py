import numpy as np
import pytest

from scripts.train_workflow_semantic_initial_prior import (
    fit_semantic_prior, initial_probabilities, ridge_fit, ridge_predict,
)


def test_weighted_prior_recovers_a_simple_held_out_linear_relationship():
    x = np.asarray(((0., 0.), (1., 0.), (0., 1.), (1., 1.), (2., 1.)))
    y = 5. + 2. * x[:, 0] - .5 * x[:, 1]
    raw = ridge_fit(x, y, np.asarray((1., 1., 2., 1., 3.)), 1e-8)
    assert ridge_predict(raw, np.asarray(((.5, .5), (2., 2.)))) == pytest.approx((5.75, 8.), abs=1e-5)
    with pytest.raises(ValueError, match="finite"):
        ridge_fit(x, y, np.zeros(len(y)), 1.)


def test_initial_probability_mass_is_conserved_and_orders_longer_means_correctly():
    raw = {"center": [0.], "scale": [1.], "coefficient": [1.], "bias": 0., "log_total_sigma": .5}
    values = initial_probabilities(raw, np.asarray(((1.,), (3.,), (5.,))), (10., 100.))
    assert np.all(values >= 0.) and np.all(values <= 1.)
    assert values.sum(axis=1) == pytest.approx(np.ones(3))
    assert values[0, 0] > values[1, 0] > values[2, 0]
    assert values[0, 2] < values[1, 2] < values[2, 2]


def test_prior_regularization_holds_out_complete_projects_including_second_tasks():
    rows, vectors = [], {}
    for index in range(8):
        identity = str(index)
        rows.append({
            "workflow": identity, "project": str(index // 2), "text": identity,
            "total_work": float(1000 + index * 500),
        })
        vectors[identity] = np.asarray((float(index), float(index % 2)))
    for index in range(8, 12):
        identity = str(index)
        rows.append({
            "workflow": identity, "project": identity, "text": identity,
            "total_work": float(1000 + index * 500),
        })
        vectors[identity] = np.asarray((float(index), float(index % 2)))
    prior, receipt = fit_semantic_prior(rows, vectors)
    assert receipt["training_workflows"] == 12 and receipt["training_projects"] == 8
    assert len(receipt["out_of_project_log_predictions"]) == 12
    for fold in receipt["project_grouped_folds"]:
        assert not set(fold["training_projects"]) & set(fold["held_out_projects"])
    assert prior["log_total_sigma"] >= .2
    assert np.isfinite(ridge_predict(prior, np.asarray(list(vectors.values())))).all()
    assert receipt["external_calibration_or_development_used_for_ridge_selection"] is False
