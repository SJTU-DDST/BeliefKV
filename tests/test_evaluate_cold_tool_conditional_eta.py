import numpy as np
import pytest

from scripts import evaluate_cold_tool_conditional_eta as eta
from scripts.pilot_cold_child_tool_long import _shape_matrix


def _row(project, workflow, duration, *, shape="execute", peers=0):
    return {
        "project": project,
        "workflow": f"{project}__{workflow}",
        "duration_ms": duration,
        "shape": shape,
        "input_chars": 80,
        "project_class_completed_support": 0,
        "other_workflow_2s_peers": peers,
    }


def _train():
    return [
        _row(project, index, 3000 + 50 * index, peers=index % 3)
        for project in ("django", "pydata", "pytest-dev")
        for index in range(10)
    ] + [
        _row(project, 10 + index, 100 + 10 * index)
        for project in ("django", "pydata", "pytest-dev")
        for index in range(10)
    ]


def test_only_training_projects_select_timing_cohort(monkeypatch):
    heldout = [
        _row("astropy", index, duration, peers=1)
        for index, duration in enumerate((2400, 2800, 100, 180))
    ]
    monkeypatch.setattr(eta, "shape_transfer_pilot", lambda *a, **k: {
        "threshold_chosen_on_train_cv": .5,
    })
    monkeypatch.setattr(eta, "_fit_shape_head", lambda rows, **kw: object())
    monkeypatch.setattr(eta, "_shape_scores", lambda model, rows, **kw: np.array(
        [.9, .9, .9, .1],
    ))
    report = eta.evaluate(_train(), heldout)
    assert report["heldout_selected"] == 3
    assert report["heldout_selected_long"] == 2
    assert report["heldout_selected_false_short"] == 1
    assert report["heldout_long_recall"] == 1
    assert report["heldout_selected_precision"] == pytest.approx(2 / 3)
    for head in report["heldout_by_project"]["astropy"]["modes"].values():
        assert head["all_selected_timing"]["count"] == 3
        assert head["true_selected_long_timing"]["count"] == 2
        assert head["opportunity_on_all_selected"]["selected"] == 3


def test_no_qualified_training_screen_makes_no_heldout_predictions(monkeypatch):
    monkeypatch.setattr(eta, "shape_transfer_pilot", lambda *a, **k: {
        "threshold_chosen_on_train_cv": None,
    })
    monkeypatch.setattr(eta, "_fit_duration", lambda *a, **k: pytest.fail(
        "duration head must not be fitted after training gate rejects",
    ))
    report = eta.evaluate(_train(), [_row("astropy", 0, 2500)])
    assert report["status"] == "train_project_cv_no_qualified_long_screen"
    assert "heldout_by_project" not in report


def test_long_only_head_requires_cross_project_training_support():
    rows = [
        _row("django", index, 3000) for index in range(20)
    ] + [
        _row("pydata", index, 120) for index in range(20)
    ] + [
        _row("pytest-dev", index, 120) for index in range(20)
    ]
    report = eta.evaluate(rows, [_row("astropy", 0, 2500)])
    assert report["status"] == "insufficient_cross_project_long_train_support"


def test_project_cv_does_not_fit_unsupported_long_fold():
    train = [
        _row("django", index, 3000) for index in range(25)
    ] + [
        _row(project, index, 3000 if index == 0 else 120)
        for project in ("pydata", "pytest-dev") for index in range(25)
    ]
    report = eta.evaluate(train, [_row("astropy", 0, 2500)])
    assert report["status"] == "insufficient_project_cv_fold_support"
    assert report["unsupported_train_cv_folds"] == ["django"]


def test_project_and_workflow_overlap_are_rejected():
    with pytest.raises(ValueError, match="disjoint"):
        eta.evaluate(_train(), [_row("django", 0, 2500)])
    duplicate = _row("astropy", 0, 2500)
    original = _train()
    original.append({**_row("django", 0, 2500), "workflow": duplicate["workflow"]})
    with pytest.raises(ValueError, match="overlapping"):
        eta.evaluate(original, [duplicate])


def test_opportunity_keeps_early_and_expired_selected_calls():
    rows = [
        _row("astropy", "short", 100),
        _row("astropy", "long", 3000),
    ]
    report = eta._opportunity(rows, np.array([3000., 3000.]))
    assert report["selected"] == 2
    assert report["expired_before_estimated_latest_start"] == 1
    assert report["actual_lead_at_least_500ms"] == 1


def test_long_history_uses_only_supported_start_observations():
    rows = [
        {**_row("astropy", 0, 3000),
         "project_long_completed_median_ms": 3500.,
         "project_long_completed_support": 3},
        {**_row("astropy", 1, 120),
         "project_long_completed_median_ms": 3500.,
         "project_long_completed_support": 2},
        {**_row("astropy", 2, 3000),
         "project_long_completed_median_ms": float("nan"),
         "project_long_completed_support": 5},
        _row("astropy", 3, 200),
    ]
    prediction, supported = eta._causal_long_history(
        rows, np.array([1000., 1000., 1000., 1000.]),
    )
    assert supported == 1
    assert prediction.tolist() == [3500., 1000., 1000., 1000.]


def test_history_features_are_opt_in_and_missing_is_explicit():
    supported = {
        **_row("django", 1, 3000, peers=2),
        "project_long_completed_support": 4,
        "project_long_completed_median_ms": 3200.,
    }
    unsupported = _row("astropy", 2, 2500)
    legacy = _shape_matrix([supported, unsupported], {"execute": 0})
    with_history = _shape_matrix(
        [supported, unsupported], {"execute": 0},
        include_long_history=True,
    )
    assert legacy.shape == (2, 4)
    assert with_history.shape == (2, 6)
    np.testing.assert_array_equal(with_history[:, :4], legacy)
    assert with_history[0, 4] == pytest.approx(np.log1p(4))
    assert with_history[0, 5] == pytest.approx(np.log1p(3200))
    np.testing.assert_array_equal(with_history[1, 4:], [0, 0])


def test_completed_duration_priors_do_not_change_legacy_feature_columns():
    prior = {
        **_row("django", 1, 3000),
        "project_class_duration_median_ms": 1700.,
        "project_input_neighbor_duration_ms": 2500.,
        "project_input_neighbor_support": 5,
    }
    missing = _row("astropy", 2, 2500)
    original = _shape_matrix(
        [prior, missing], {"execute": 0}, include_long_history=True,
    )
    extended = _shape_matrix(
        [prior, missing], {"execute": 0}, include_long_history=True,
        include_duration_priors=True,
    )
    assert extended.shape == (2, 9)
    np.testing.assert_array_equal(extended[:, :6], original)
    np.testing.assert_allclose(extended[0, 6:], np.log1p([1700, 2500, 5]))
    np.testing.assert_array_equal(extended[1, 6:], [0, 0, 0])


def test_duration_priors_are_only_used_when_opted_in(monkeypatch):
    calls = []

    def capture_screen(*_args, **kwargs):
        calls.append(kwargs)
        return {"threshold_chosen_on_train_cv": None}

    monkeypatch.setattr(eta, "shape_transfer_pilot", capture_screen)
    report = eta.evaluate(
        _train(), [_row("astropy", 0, 2500)],
        include_duration_priors=True,
    )
    assert report["status"] == "train_project_cv_no_qualified_long_screen"
    assert report["include_duration_priors"] is True
    assert "screen_without_duration_priors" in report
    assert calls == [
        {"include_live_peers": True, "include_long_history": True,
         "include_duration_priors": True},
        {"include_live_peers": True, "include_long_history": True},
    ]
