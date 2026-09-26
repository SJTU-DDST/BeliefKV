import numpy as np
import pytest

from scripts.pilot_cold_child_tool_long import (
    _shape_matrix, _shape_threshold_report, shape_transfer_pilot,
    transfer_pilot,
)


def test_transfer_pilot_requires_project_disjoint_data():
    call = {
        "project": "pydata", "class": "test",
        "input_chars": 100, "duration_ms": 3000.,
    }
    with pytest.raises(ValueError, match="project-disjoint"):
        transfer_pilot([call], [call])
    with pytest.raises(ValueError, match="insufficient"):
        transfer_pilot([call], [{**call, "project": "astropy"}])


def test_shape_features_do_not_depend_on_future_duration():
    row = {
        "shape": "test_suite_targeted", "input_chars": 100,
        "duration_ms": 3000., "project_class_completed_support": 4,
        "other_workflow_2s_peers": 2,
    }
    vocabulary = {"test_suite_targeted": 0}
    features = _shape_matrix([row], vocabulary)
    row["duration_ms"] = 100.
    assert np.array_equal(features, _shape_matrix([row], vocabulary))
    assert features.shape == (1, 4)


def test_shape_screen_requires_project_split_and_reports_realized_precision():
    row = {
        "project": "pydata", "workflow": "a", "shape": "test_suite_targeted",
        "class": "test_suite", "input_chars": 100, "duration_ms": 3000.,
    }
    with pytest.raises(ValueError, match="three training projects"):
        shape_transfer_pilot([row], [{**row, "project": "astropy"}])
    with pytest.raises(ValueError, match="disjoint"):
        shape_transfer_pilot(
            [row, {**row, "project": "django"},
             {**row, "project": "pytest-dev"}], [row],
        )
    result = _shape_threshold_report(
        [(row, .8), ({**row, "workflow": "b", "duration_ms": 100.}, .8)],
        .5,
    )
    assert (result["selected"], result["true_long"],
            result["false_short"], result["precision"]) == (2, 1, 1, .5)


def test_shape_screen_does_not_select_threshold_from_heldout_calls():
    train = [
        {
            "project": project, "workflow": f"{project}-{index}",
            "shape": "python_inline_simple", "input_chars": 100,
            "duration_ms": 3000. if index < 10 else 100.,
        }
        for project in ("django", "pylint-dev", "pytest-dev")
        for index in range(30)
    ]
    heldout = [
        {**row, "project": "pydata", "workflow": f"heldout-{index}",
         "duration_ms": 3000.}
        for index, row in enumerate(train[:15])
    ]
    report = shape_transfer_pilot(train, heldout)
    assert report["threshold_chosen_on_train_cv"] is None
    assert report["heldout_at_frozen_threshold"] is None
    assert report["heldout_long_calls"] is None
