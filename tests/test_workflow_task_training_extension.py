import numpy as np
import pytest

from beliefkv.predictor.workflow_work import R5_SCHEMA
from beliefkv.predictor.workflow_work_training import project_task_weights, work_ranking_pairs
from scripts.extend_workflow_prediction_training import (
    disjoint_training_rows, diverse_task_training_rows,
)
from scripts.train_workflow_work import fit
from tests.test_workflow_work_distribution_training import row


def example(identity, project):
    return {**row(identity), "project": project, "attained_work": 0., "remaining_work": 100.}


def test_new_original_tasks_are_allowed_only_inside_the_existing_training_projects():
    original, added = [example("old", "train")], [example("new", "train")]
    calibration, evaluation = [example("cal", "cal")], [example("dev", "dev")]
    assert diverse_task_training_rows(original, added, calibration, evaluation) == original + added
    with pytest.raises(ValueError, match="project"):
        disjoint_training_rows(original, added, calibration, evaluation)


@pytest.mark.parametrize("change", ("held_out", "unseen_project", "same_task", "same_execution", "missing_initial"))
def test_task_diversification_rejects_holdout_leakage_duplicates_and_incomplete_initial_state(change):
    original, added = [example("old", "train")], [example("new", "train")]
    calibration, evaluation = [example("cal", "cal")], [example("dev", "dev")]
    if change == "held_out":
        added[0]["project"] = "dev"
    elif change == "unseen_project":
        added[0]["project"] = "unrelated"
    elif change == "same_task":
        added[0]["task_template"] = original[0]["task_template"]
    elif change == "same_execution":
        added[0]["source_workflow_sha256"] = original[0]["source_workflow_sha256"]
    else:
        added[0]["snapshot"] = 1
    with pytest.raises(ValueError):
        diverse_task_training_rows(original, added, calibration, evaluation)


def test_extra_tasks_do_not_increase_total_project_mass_or_overweight_dense_snapshots():
    rows = [
        {"project": "a", "workflow": "a1"},
        {"project": "a", "workflow": "a1"},
        {"project": "a", "workflow": "a2"},
        {"project": "b", "workflow": "b1"},
    ]
    original = np.asarray((.1, .3, .2, .4))
    saved = original.copy()
    weights = project_task_weights(rows, original)
    assert np.array_equal(original, saved)
    assert weights[:3].sum() == pytest.approx(.5)
    assert weights[3] == pytest.approx(.5)
    assert weights[:2].sum() == weights[2] == pytest.approx(.25)
    assert weights[1] / weights[0] == pytest.approx(3.)


def test_project_balanced_ranking_retains_old_default_and_draws_only_distinct_projects():
    rows = [
        {**example(str(index), project), "total_work": total, "remaining_work": total}
        for index, (project, total) in enumerate((
            ("a", 10.), ("a", 100.), ("b", 1000.), ("c", 10000.),
        ))
    ]
    assert work_ranking_pairs(rows, seed=21) == work_ranking_pairs(
        rows, seed=21, balance_projects=False,
    )
    balanced = work_ranking_pairs(rows, seed=21, pairs_per_phase=1024, balance_projects=True)
    assert all(rows[left]["project"] != rows[right]["project"] for left, right, _direction, _weight in balanced)
    participation = {
        project: sum(rows[left]["project"] == project or rows[right]["project"] == project
                     for left, right, _direction, _weight in balanced)
        for project in ("a", "b", "c")
    }
    assert max(participation.values()) / min(participation.values()) < 1.2


def test_real_training_exports_project_task_weight_contract_without_a_project_input():
    training = [
        {**example(str(index), "shared" if index < 2 else str(index)),
         "total_work": 100. * (index + 1), "remaining_work": 100. * (index + 1)}
        for index in range(6)
    ]
    calibration = [example("cal-" + str(index), "cal-" + str(index)) for index in range(6)]
    settings = {
        "epochs": 2, "split_mode": "workflow", "hidden_width": 4,
        "vocabulary_size": 4, "feature_schema": R5_SCHEMA, "work_thresholds": (200., 400.),
    }
    raw = fit(training, calibration, balance_training_projects=True, ranking_loss_weight=.1, **settings)
    contract = raw["metadata"]["project_task_balance"]
    assert contract["projects"] == 5 and contract["workflows"] == 6
    assert contract["project_identity_used_as_online_feature"] is False
    assert raw["metadata"]["loss_weighting"] == "equal_project_then_equal_task_workflow"
    assert "project" not in raw["features"]
    assert fit(training, calibration, **settings) == fit(
        training, calibration, balance_training_projects=False, **settings,
    )
