from copy import deepcopy
import json
import math

import pytest

from beliefkv.predictor.workflow_work import R5_FEATURES
from scripts.audit_workflow_project_forecast_phases import inspect, phase, summarize


def row(name, work, score, *, attained=0., snapshot=0, project=None):
    features = [0.] * len(R5_FEATURES)
    features[R5_FEATURES.index("ready")] = 1.
    features[R5_FEATURES.index("output_tokens")] = attained
    return {
        "workflow": name, "project": project or name, "source_workflow_sha256": name,
        "snapshot": snapshot, "features": features, "true_class": 2, "supported": False,
        "total_work": work + attained, "attained_work": attained, "remaining_work": work,
        "reference_work": 1000., "raw_binned_weight": .25, "raw_continuous_weight": score,
        "binned_log_work": math.log1p(10000.), "continuous_log_work": math.log1p(work),
    }


def test_raw_phase_metrics_preserve_rejected_support_and_same_class_resolution():
    rows = [row("short", 24000., .1), row("long", 230000., .01)]
    result = summarize(rows)
    assert result["supported_project_equal_mass"] == 0.
    assert result["binned"]["raw_initial"]["project_pair_equal_weight_accuracy"] == .5
    assert result["continuous"]["raw_initial"]["project_pair_equal_weight_accuracy"] == 1.
    assert result["continuous"]["raw_same_true_class_ready"]["project_pair_equal_weight_accuracy"] == 1.
    assert result["continuous"]["raw_project_equal_log_work_RMSE"] == 0.


def test_phase_boundaries_separate_unserved_and_observed_work():
    assert phase(row("a", 100., .5)) == "before_model_progress"
    assert phase(row("a", 100., .5, attained=1.)) == "early_progress"
    assert phase(row("a", 200., .5, attained=100.)) == "middle_progress"
    assert phase(row("a", 100., .5, attained=200.)) == "late_progress"


@pytest.mark.parametrize("invalid", ("duplicate", "nonconserved", "nonfinite", "negative",
                                   "probability", "feature_shape", "changed_owner"))
def test_phase_audit_rejects_inconsistent_forecast_evidence(invalid):
    rows = [row("a", 100., .9), row("b", 10000., .1)]
    if invalid == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif invalid == "nonconserved":
        rows[0]["remaining_work"] += 1.
    elif invalid == "nonfinite":
        rows[0]["raw_continuous_weight"] = math.nan
    elif invalid == "negative":
        rows[0]["features"][0] = -1.
    elif invalid == "probability":
        rows[0]["raw_continuous_weight"] = 1.1
    elif invalid == "feature_shape":
        rows[0]["features"].pop()
    else:
        rows.append({**rows[0], "snapshot": 1, "project": "different-project"})
    with pytest.raises(ValueError):
        summarize(rows)


def test_project_equal_error_does_not_give_dense_execution_more_weight():
    short, long = row("a", 100., .9), row("b", 10000., .1)
    expected = summarize([short, long])["continuous"]["raw_project_equal_utility_MSE"]
    dense = [short, *[{**long, "snapshot": index} for index in range(20)]]
    assert summarize(dense)["continuous"]["raw_project_equal_utility_MSE"] == pytest.approx(expected)


@pytest.mark.parametrize("overlap", (False, True))
def test_complete_fold_audit_keeps_outer_projects_separate(tmp_path, overlap):
    splits, reports = [], []
    for index in range(3):
        name = str(index)
        split = {
            "training": {"projects": [str((index + 1) % 3)]},
            "calibration": {"projects": [str((index + 2) % 3)]},
            "evaluation": {"projects": [name], "executions": [name]},
        }
        if overlap and index == 1:
            split["training"]["projects"].append(name)
        splits.append(split)
        report = {"fold": index, "calibration_decision_eligible": False,
                  "calibration_rejections": ["long_to_short"]}
        reports.append(report)
        directory = tmp_path / ("fold_" + name)
        directory.mkdir()
        (directory / "predictions.json").write_text(json.dumps([row(name, 100., .5)]))
        (directory / "evaluation.json").write_text(json.dumps(report))
    (tmp_path / "plan.json").write_text(json.dumps({
        "outer_folds": 3, "folds": splits, "projects": 3,
        "vocabulary_scaling_both_MLPs_and_tree_refitted_inside_each_fold": True,
        "calibration_uses_a_separate_project_fold": True,
        "original_calibration_safety_gates_retained": True,
    }))
    (tmp_path / "evaluation.json").write_text(json.dumps({
        "folds": reports, "all_projects_evaluated_once": True,
    }))
    if overlap:
        with pytest.raises(ValueError, match="whole projects"):
            inspect(tmp_path)
    else:
        result = inspect(tmp_path)
        assert result["outer_projects"] == 3
        assert not result["new_model_fitted"]
        assert result["all_scores_are_raw_diagnostics_not_deployment_eligibility"]
        assert result["mean_of_within_fold_metrics"]["all"]["continuous"]["raw_ready"] == {
            "mean_fold_project_pair_accuracy": None, "folds_with_pairs": 0,
        }
