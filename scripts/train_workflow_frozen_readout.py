"""Evaluate continuous quantities on a frozen R5 representation before serving changes."""

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel, before_observed_work
from beliefkv.predictor.workflow_work_readout import (
    KIND, FrozenReadoutCandidate, log_work_proxy, representation, representation_fingerprint,
    ridge_fit, ridge_predict,
)
from beliefkv.predictor.workflow_work_training import project_task_weights, ready_weights, remaining_call_counts
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_task_diversity import initial_project_folds, training_population
from scripts.train_workflow_work import write


def observation_weights(rows):
    import numpy as np

    counts = Counter(row["workflow"] for row in rows)
    weights = np.asarray([1. / counts[row["workflow"]] for row in rows])
    weights /= weights.sum()
    return project_task_weights(rows, ready_weights(rows, weights))


def latent_matrix(model, rows):
    import numpy as np

    projections, values, valid = {}, [], []
    width = model.arrays["hidden_weight"].shape[1]
    for row in rows:
        text = row.get("task_text", row["text"])
        if text not in projections:
            projections[text] = model.task_projection(text)
        projection = projections[text]
        valid.append(projection is not None)
        values.append(
            representation(model, projection, row["features"])
            if projection is not None else np.zeros(width)
        )
    return np.asarray(values), np.asarray(valid)


def fit_readout(rows, x, y):
    import numpy as np

    weights = observation_weights(rows)
    folds = initial_project_folds(rows, count=5)
    summaries = []
    for alpha in (.1, 1., 10.):
        predicted = np.full_like(y, np.nan)
        for training, held_out in folds:
            fitted = ridge_fit(x[training], y[training], weights[training], alpha)
            predicted[held_out] = ridge_predict(fitted, x[held_out])
        if not np.isfinite(predicted).all():
            raise ValueError("readout folds omit a training observation")
        summaries.append({
            "alpha": alpha, "weighted_log_MSE": float(np.sum(weights * ((y - predicted) ** 2).mean(1))),
        })
    selected = min(summaries, key=lambda row: row["weighted_log_MSE"])["alpha"]
    return ridge_fit(x, y, weights, selected), {
        "selected_alpha": selected, "trials": summaries,
        "fold_scope": "readout_only_conditional_on_pretrained_frozen_representation",
        "trunk_already_trained_on_these_training_projects": True,
        "external_calibration_or_development_used_for_ridge_fitting": False,
        "folds": [{
            "training_projects": sorted({rows[index]["project"] for index in training}),
            "held_out_projects": sorted({rows[index]["project"] for index in held_out}),
        } for training, held_out in folds],
    }


def cost_priors(rows):
    import numpy as np

    _remaining, counts = remaining_call_counts(rows)
    last = {}
    for row in rows:
        if row["workflow"] not in last or row["snapshot"] > last[row["workflow"]]["snapshot"]:
            last[row["workflow"]] = row
    endings = list(last.values())
    weights = project_task_weights(endings, np.ones(len(endings)))
    result = {}
    for owner in ("root", "child"):
        values = []
        for row, weight in zip(endings, weights):
            calls = counts[row["workflow"]][owner]
            if calls:
                observed = dict(zip(R5_FEATURES, row["features"]))
                work = .01 * observed[owner + "_input_tokens"] + observed[owner + "_output_tokens"]
                values.append((work / calls, weight))
        if not values:
            raise ValueError("continuous readout lacks a role cost prior")
        values.sort()
        total, cumulative = sum(weight for _value, weight in values), 0.
        for value, weight in values:
            cumulative += weight
            if cumulative >= total / 2.:
                result[owner] = float(value)
                break
    return result


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "additional-training-data",
                 "baseline-model", "baseline-evaluation", "representation-model",
                 "representation-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous continuous readout studies")
    paths = (
        args.dataset, args.cohort, args.previous_training_data, args.additional_training_data,
        args.baseline_model, args.baseline_evaluation, args.representation_model,
        args.representation_evaluation,
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, previous, additional, baseline, evaluation, trunk_raw, trunk_evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    dataset_sha = pins[str(args.dataset.resolve())]
    if (
        evaluation["model_sha256"] != pins[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != dataset_sha
        or trunk_evaluation["model_sha256"] != pins[str(args.representation_model.resolve())]
        or trunk_evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != dataset_sha
        or trunk_evaluation["candidate_selection_used_test_labels"] is not False
        or trunk_raw.get("initial_state_head") != baseline.get("initial_state_head")
        or trunk_raw["work_thresholds"] != baseline["work_thresholds"]
    ):
        raise ValueError("readout requires the exact calibrated representation and preserved initial head")
    training, calibration, development, population = training_population(
        data, cohort, previous, additional, evaluation, dataset_sha,
    )
    if trunk_evaluation["original_split"] != population["original_split"]:
        raise ValueError("frozen trunk and readout holdouts differ")
    for dataset in (data, previous, additional):
        for path, digest in dataset["sources_sha256"].items():
            if path in pins and pins[path] != digest:
                raise ValueError("readout source identities conflict")
            pins[path] = digest
    for path in (
        Path(__file__), ROOT / "beliefkv/predictor/workflow_work_readout.py",
        ROOT / "beliefkv/predictor/workflow_work.py",
        ROOT / "scripts/train_workflow_task_diversity.py",
        ROOT / "scripts/train_workflow_ready_ranking.py",
        ROOT / "scripts/improve_workflow_work_distribution.py",
        ROOT / "beliefkv/predictor/workflow_work_training.py",
    ):
        pins[str(path.resolve())] = sha256_file(path)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen readout source changed")
    base, trunk = WorkflowWorkModel(baseline), WorkflowWorkModel(trunk_raw)
    previous_ids = set(population["previous_expanded_split"]["training_execution_sha256"])
    settings = {"retained": None, "auxiliary_primary": None}
    settings.update({
        f"{group}_{target}_blend{blend}": (group, target, blend)
        for group in ("previous", "expanded") for target in ("work", "branch_calls") for blend in (.5, 1.)
    })
    write(args.output_dir / "plan.json", {
        "candidates": settings, **population,
        "readout_regularization": [.1, 1., 10.], "observed_cost_prior_strength": 4.,
        "trunk_frozen_no_neural_retraining": True,
        "initial_head_preserved": True, "unknown_future_quantities_are_training_labels_only": True,
        "folds_are_readout_only_not_full_model_generalization": True,
        "selection": "unchanged_calibration_support_gates_then_existing_ready_ranking_score",
        "development_labels_used_for_selection": False, "inputs_sha256": pins, "GPU_action": False,
    })
    x, valid = latent_matrix(trunk, training)
    call_targets, _totals = remaining_call_counts(training)
    target_arrays = {
        "work": np.log1p([row["remaining_work"] for row in training])[:, None],
        "branch_calls": np.log1p(call_targets),
    }
    cal_x, cal_valid = latent_matrix(trunk, calibration)
    cal_indices = [index for index, row in enumerate(calibration)
                   if cal_valid[index] and not before_observed_work(row["features"])]
    cal_rows = [calibration[index] for index in cal_indices]
    cal_weights = observation_weights(cal_rows)
    true_log_work = np.log1p([row["remaining_work"] for row in cal_rows])
    classes = np.searchsorted(baseline["work_thresholds"], [row["remaining_work"] for row in cal_rows], side="right")
    class_support = [len({row["workflow"] for row, label in zip(cal_rows, classes) if label == target})
                     for target in range(3)]
    heads = {}
    for group in ("previous", "expanded"):
        group_rows = [row for row in training if group == "expanded" or row["source_workflow_sha256"] in previous_ids]
        prior = cost_priors(group_rows)
        indices = [
            index for index, row in enumerate(training)
            if valid[index] and not before_observed_work(row["features"])
            and (group == "expanded" or row["source_workflow_sha256"] in previous_ids)
        ]
        selected_rows = [training[index] for index in indices]
        for target, values in target_arrays.items():
            print("fitting_frozen_readout: " + group + "/" + target, flush=True)
            ridge, diagnostic = fit_readout(selected_rows, x[indices], values[indices])
            head = {
                "kind": KIND, "target": target, "ridge": ridge,
                "representation_sha256": representation_fingerprint(trunk),
                "per_call_prior": prior, "cost_prior_strength": 4.,
                "calibration_class_workflows": class_support,
            }
            predicted = log_work_proxy(head, cal_x[cal_indices], [row["features"] for row in cal_rows])
            residual = true_log_work - predicted
            offset = float(np.sum(cal_weights * residual))
            head.update(
                calibration_offset=offset,
                calibration_sigma=max(.25, float(np.sqrt(np.sum(cal_weights * (residual - offset) ** 2)))),
            )
            heads[group, target] = head
            write(args.output_dir / f"{group}_{target}" / "head.json", head)
            write(args.output_dir / f"{group}_{target}" / "training_validation.json", diagnostic)
    models = {"retained": base, "auxiliary_primary": trunk}
    models.update({name: FrozenReadoutCandidate(trunk, heads[group, target], blend)
                   for name, setting in settings.items() if setting is not None
                   for group, target, blend in (setting,)})
    summaries = {}
    for name, model in models.items():
        values = metrics(model, calibration)
        gate_raw = deepcopy(baseline)
        gate_raw["metadata"] = model.metadata
        reasons = calibration_rejections(values, gate_raw)
        summaries[name] = {
            "calibration": values, "selection_score": score(values),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
        }
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({"candidate": name, "score": score(values), "rejections": reasons}), flush=True)
    selected = max((name for name in models if summaries[name]["decision_eligible"]),
                   key=lambda name: summaries[name]["selection_score"])
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {name: metrics(models[name], development) for name in dict.fromkeys(("retained", selected))}
    raw = deepcopy(baseline if selected == "retained" else trunk_raw)
    if settings[selected] is not None:
        group, target, blend = settings[selected]
        raw["frozen_work_readout_experimental"] = {"head": heads[group, target], "blend": blend}
        raw["metadata"].update(
            decision_eligible=False, decision_rejection_reasons=["offline_readout_not_serving_qualified"],
        )
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen readout input changed during evaluation")
    write(args.output_dir / "model.json", raw)
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        **population, "inputs_sha256": pins, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "deployment_adapter_implemented": False, "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "ready_ranking": values["ready_cross_progress_pairs"]["accuracy"],
        "classification": values["all"]["workflow_equal_weight_accuracy"],
        "long_to_short": values["all"]["long_to_short_rate"],
    } for name, values in held_out.items()}), flush=True)


if __name__ == "__main__":
    main()
