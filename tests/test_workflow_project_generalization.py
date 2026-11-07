from copy import deepcopy
import hashlib

import pytest

from beliefkv.predictor.workflow_continuous_work import serving_contract
from beliefkv.predictor.workflow_work import R5_FEATURES, R5_SCHEMA, WorkflowWorkModel
from scripts.evaluate_workflow_continuous_projects import (
    combine_datasets, evaluate_fold, fit_fold, forecast_metrics, project_folds, ranking_metrics,
)
from scripts.train_workflow_work_distribution import snapshots
from scripts.prepare_continuous_work_readout import training_rows
from tests.test_workflow_work_distribution_training import execution


def dataset():
    events, requests, progress = execution()
    original = snapshots(events, requests, progress=progress)
    rows = []
    for index in range(15):
        for task in range(2):
            identity = f"workflow-{index}-{task}"
            scale = 100. * (index + 1) * (task + 1)
            for row in original:
                features = list(row["features"])
                for name in (
                    "input_tokens", "output_tokens", "root_input_tokens", "child_input_tokens",
                    "root_output_tokens", "child_output_tokens",
                ):
                    features[R5_FEATURES.index(name)] *= scale
                rows.append({
                    **row, "workflow": identity, "project": f"project-{index}",
                    "source_workflow_sha256": hashlib.sha256(identity.encode()).hexdigest(),
                    "task_template": f"task-{index}-{task}", "features": features,
                    "text": f"ab implement task {index} variant {task}",
                    "attained_work": row["attained_work"] * scale,
                    "remaining_work": row["remaining_work"] * scale,
                    "total_work": row["total_work"] * scale,
                })
    return {
        "rows": rows, "schema": R5_SCHEMA, "prefill_token_weight": .01,
        "progress_observation_contract": "observed_native_decode_progress_128_v1",
    }


def test_project_folds_exclude_every_execution_of_a_project_from_fitting_and_calibration():
    rows = combine_datasets([dataset()])
    folds = project_folds(rows, count=5)
    evaluation = []
    for training, calibration, test in folds:
        assert {row["project"] for row in training}.isdisjoint(
            {row["project"] for row in calibration + test},
        )
        assert {row["project"] for row in calibration}.isdisjoint({row["project"] for row in test})
        assert len({row["project"] for row in test}) == 3
        assert len({row["workflow"] for row in test}) == 6
        evaluation.extend((row["workflow"], row["snapshot"]) for row in test)
    assert sorted(evaluation) == sorted((row["workflow"], row["snapshot"]) for row in rows)
    changed = [{**row, "total_work": row["total_work"] * 100.} for row in rows]
    assert [
        [{row["project"] for row in group} for group in fold]
        for fold in project_folds(changed)
    ] == [[{row["project"] for row in group} for group in fold] for fold in folds]


@pytest.mark.parametrize("change", ("duplicate", "partial", "relabel", "repeated_digest"))
def test_evaluation_refuses_duplicate_partial_or_relabelled_source_executions(change):
    data = dataset()
    if change == "duplicate":
        with pytest.raises(ValueError):
            combine_datasets([data, data])
        return
    if change == "partial":
        last = max(row["snapshot"] for row in data["rows"])
        data["rows"] = [row for row in data["rows"] if row["snapshot"] != last]
        # The last RETURN does no model work; remove the model-completion tail too.
        data["rows"] = [row for row in data["rows"] if row["snapshot"] < last - 3]
    elif change == "relabel":
        data["rows"][0]["project"] = "different"
    else:
        digest = data["rows"][0]["source_workflow_sha256"]
        for row in data["rows"]:
            if row["workflow"] == "workflow-0-1":
                row["source_workflow_sha256"] = digest
    with pytest.raises(ValueError):
        combine_datasets([data])


def test_project_ranking_omits_within_project_pairs_and_counts_exact_ties():
    features = [0.] * len(R5_FEATURES)
    features[R5_FEATURES.index("ready")] = 1.
    rows = [{
        "workflow": owner, "project": project, "snapshot": 0, "features": features,
        "remaining_work": work, "true_class": 2, "supported": True,
    } for owner, project, work in (("a", "p", 24000.), ("b", "p", 100.), ("c", "q", 230000.))]
    good = ranking_metrics(rows, (1., 1., 0.), phase="ready")
    assert good["project_pairs"] == 1
    assert good["workflow_pairs"] == good["pair_observations"] == 2
    assert good["project_pair_equal_weight_accuracy"] == 1.
    tied = ranking_metrics(rows, (.25,) * 3, phase="ready", within_class_only=True)
    assert tied["project_pair_equal_weight_accuracy"] == .5
    assert ranking_metrics(rows, (.25,) * 3, phase="initial") == tied


def test_real_refit_and_portable_forecast_do_not_read_outer_work_labels():
    training, calibration, evaluation = project_folds(combine_datasets([dataset()]))[0]
    fitted, _proof = fit_fold(training, calibration, epochs=1, tree_rounds=2)
    raw = deepcopy(fitted.raw)
    raw["continuous_work_readout"] = serving_contract(fitted, training)
    model = WorkflowWorkModel(raw)
    original = evaluate_fold(model, evaluation)
    changed = evaluate_fold(model, [{
        **row, "remaining_work": row["remaining_work"] * 1000.,
        "total_work": row["total_work"] * 1000.,
    } for row in evaluation])
    for left, right in zip(original, changed):
        for name in ("binned_weight", "continuous_weight", "binned_log_work", "continuous_log_work"):
            assert left[name] == right[name]
    assert forecast_metrics(original, method="continuous")["projects"] == 3
    assert raw["continuous_work_readout"]["training_projects"] == 9
    if not raw["metadata"]["decision_eligible"]:
        assert all(row["binned_weight"] == row["continuous_weight"] == .5 for row in original)


def test_readout_reference_uses_exact_training_executions_and_rejects_overlap():
    rows = combine_datasets([dataset()])
    training, calibration, evaluation = project_folds(rows)[0]
    split = {
        name + "_execution_sha256": sorted({row["source_workflow_sha256"] for row in group})
        for name, group in zip(("training", "calibration", "evaluation"), (training, calibration, evaluation))
    }
    raw = {"metadata": {"split_contract": split}}
    assert training_rows(raw, rows) == [row for row in rows if row in training]
    missing = [row for row in rows if row["workflow"] != training[0]["workflow"]]
    with pytest.raises(ValueError, match="every exact"):
        training_rows(raw, missing)
    split["calibration_execution_sha256"].append(split["training_execution_sha256"][0])
    with pytest.raises(ValueError, match="overlapping"):
        training_rows(raw, rows)
