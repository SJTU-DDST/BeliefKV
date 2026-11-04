"""Fit small continuous utility heads using only frozen causal workflow inputs."""

import argparse
from collections import defaultdict
from itertools import combinations
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import ContinuousWorkReadout, fit_training_reference
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import support_status
from scripts.train_workflow_branch_call_trees import matrix
from scripts.train_workflow_frozen_readout import observation_weights
from scripts.train_workflow_task_diversity import training_population
from scripts.train_workflow_work import write


def sampled(rows, maximum):
    import numpy as np

    groups = defaultdict(list)
    for row in rows:
        groups[row["workflow"]].append(row)
    return [
        values[index] for _workflow, values in sorted(groups.items())
        for index in np.unique(np.linspace(0, len(values) - 1, min(len(values), maximum), dtype=int))
    ]


def utility_metrics(rows, weights, reference_work):
    import numpy as np

    if len(rows) != len(weights) or any(not 0 <= value <= 1 for value in weights):
        raise ValueError("utility metrics require one bounded prediction per observed input")
    groups = defaultdict(list)
    for row, value in zip(rows, weights):
        groups[row["workflow"]].append((row, value))
    errors = [
        np.mean([
            (value - reference_work / (reference_work + row["remaining_work"])) ** 2
            for row, value in values
        ]) for values in groups.values()
    ]
    quality = {}
    for phase in ("initial", "ready"):
        selected = {}
        for workflow, values in groups.items():
            if phase == "initial":
                values = [item for item in values if item[0]["snapshot"] == 0]
            else:
                values = [
                    item for item in values if item[0].get("decision_state") is True
                    or item[0]["features"][R5_FEATURES.index("ready")] > 0
                ]
            if values:
                indices = np.unique(np.linspace(0, len(values) - 1, min(len(values), 8), dtype=int))
                selected[workflow] = [values[index] for index in indices]
        pair_scores, wide_scores, total, wide_total = [], [], 0, 0
        for left, right in combinations(sorted(selected), 2):
            scores, wide = [], []
            for (a, pa), (b, pb) in (
                (a, b) for a in selected[left] for b in selected[right]
            ):
                difference = a["remaining_work"] - b["remaining_work"]
                if abs(difference) < 1e-9:
                    continue
                margin = pa - pb
                score = .5 if abs(margin) < 1e-10 else float(margin * difference < 0)
                scores.append(score)
                if max(a["remaining_work"], b["remaining_work"]) >= 2 * max(
                    1., min(a["remaining_work"], b["remaining_work"]),
                ):
                    wide.append(score)
            if scores:
                pair_scores.append(sum(scores) / len(scores))
                total += len(scores)
            if wide:
                wide_scores.append(sum(wide) / len(wide))
                wide_total += len(wide)
        quality[phase] = {
            "pair_equal_weight_accuracy": float(np.mean(pair_scores)) if pair_scores else None,
            "work_ratio_at_least_two_pair_accuracy": float(np.mean(wide_scores)) if wide_scores else None,
            "workflow_pairs": len(pair_scores), "pair_observations": total,
            "large_gap_pair_observations": wide_total,
        }
    return {
        "workflows": len(groups), "snapshots": len(rows),
        "workflow_equal_weight_utility_MSE": float(np.mean(errors)), **quality,
    }


def selection_score(metrics):
    return (
        metrics["initial"]["pair_equal_weight_accuracy"]
        + metrics["ready"]["pair_equal_weight_accuracy"]
    ) / 2.


def baseline_predictions(model, rows, contract):
    import numpy as np
    from beliefkv.policy.workflow_combination import probability_weight

    readout = ContinuousWorkReadout(model, contract)
    encodings, binned, continuous, initial = {}, [], [], []
    for row in rows:
        text = row.get("task_text", row["text"])
        if text not in encodings:
            encodings[text] = model.task_encoding(text)
        projection, coverage = encodings[text]
        support = support_status(model, row["features"], coverage)
        probability = model.predict(projection, row["features"])
        point = readout.predict(
            projection, row["features"], probabilities=probability, support=support,
        )
        binned.append(probability_weight(probability) if support == "calibrated_distribution" else .5)
        continuous.append(point["distribution_effective_weight"])
        initial.append(row["snapshot"] == 0)
    return np.asarray(binned), np.asarray(continuous), np.asarray(initial)


def ranking_queries(rows, count=512, width=6):
    import numpy as np

    by_workflow = defaultdict(list)
    for index, row in enumerate(rows):
        if row["features"][R5_FEATURES.index("ready")] > 0 or row["snapshot"] == 0:
            by_workflow[row["workflow"]].append(index)
    if len(by_workflow) < width:
        raise ValueError("pairwise training requires distinct complete training workflows")
    rng = np.random.default_rng(21)
    workflows = sorted(by_workflow)
    indices, relevance = [], []
    for _query in range(count):
        chosen = [
            int(rng.choice(by_workflow[owner]))
            for owner in rng.choice(workflows, width, replace=False)
        ]
        work = np.asarray([rows[index]["remaining_work"] for index in chosen])
        order = np.argsort(work, kind="stable")
        labels = np.empty(width, dtype=int)
        labels[order] = np.arange(width - 1, -1, -1)
        indices.extend(chosen)
        relevance.extend(labels)
    return np.asarray(indices), np.asarray(relevance), [width] * count


def calibration_mapping(scores, rows, reference_work):
    import numpy as np
    from scipy.optimize import minimize
    from scipy.special import expit

    target = np.asarray([reference_work / (reference_work + row["remaining_work"]) for row in rows])
    weights = observation_weights(rows)

    def loss(parameters):
        return float(np.sum(weights * (expit(parameters[0] * scores + parameters[1]) - target) ** 2))

    fitted = minimize(loss, (1., 0.), method="L-BFGS-B", bounds=((.001, 100.), (-20., 20.)))
    if not fitted.success:
        raise ValueError("pairwise score calibration did not converge")
    return [float(value) for value in fitted.x]


def main():
    import lightgbm as lgb
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "additional-training-data",
                 "model", "model-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--family", choices=("utility", "pairwise"), default="utility")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output_dir.exists():
        raise ValueError("hide CUDA and preserve previous utility-head evidence")
    paths = tuple(getattr(args, name.replace("-", "_")) for name in (
        "dataset", "cohort", "previous-training-data", "additional-training-data", "model", "model-evaluation",
    ))
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, previous, additional, raw, evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    if evaluation["model_sha256"] != pins[str(args.model.resolve())]:
        raise ValueError("the utility study requires its exact source-bound baseline")
    population_evaluation = {**evaluation, "expanded_split": evaluation["previous_expanded_split"]}
    train, calibration, test, population = training_population(
        data, cohort, previous, additional, population_evaluation, pins[str(args.dataset.resolve())],
    )
    model = WorkflowWorkModel(raw)
    base = WorkflowWorkModel({
        key: value for key, value in raw.items() if key != "conditional_progress_tree_head"
    })
    contract = fit_training_reference(train, model.thresholds)
    train = sampled(train, 128)
    calibration, test = sampled(calibration, 64), sampled(test, 64)
    write(args.output_dir / "plan.json", {
        "population": population, "reference": contract, "inputs_sha256": pins,
        "training_sample_maximum_per_workflow": 128,
        "evaluation_sample_maximum_per_workflow": 64,
        "candidates": ["continuous_reference", args.family + "_leaves8", args.family + "_leaves16"],
        "blends": [.5, 1.], "calibration_selection": "initial_and_ready_pair_accuracy_then_utility_MSE",
        "future_work_is_supervision_only": True, "candidate_selection_used_test_labels": False,
        "GPU_action": False, "online_policy_changed": False,
    })
    x, valid = matrix(base, train)
    if not valid.all():
        raise ValueError("a utility training row lacks the declared task representation")
    target = np.asarray([
        contract["reference_work"] / (contract["reference_work"] + row["remaining_work"])
        for row in train
    ])
    calibration_x, calibration_valid = matrix(base, calibration)
    actual, continuous, initial = baseline_predictions(model, calibration, contract)
    forecasts = {"retained_binned": actual, "continuous_reference": continuous}
    candidates, summaries = {}, {}
    for leaves in (8, 16):
        parameters = {
            "objective": "regression", "num_threads": 1, "deterministic": True,
            "force_col_wise": True, "seed": 21, "verbosity": -1, "num_leaves": leaves,
            "max_depth": 5, "learning_rate": .05, "min_data_in_leaf": 80,
            "lambda_l2": 20.,
        }
        mapping = None
        if args.family == "pairwise":
            from scipy.special import expit

            indices, relevance, sizes = ranking_queries(train)
            parameters.update(
                objective="lambdarank", metric="ndcg", label_gain=list(range(6)),
                lambdarank_truncation_level=3,
            )
            fitted = lgb.train(parameters, lgb.Dataset(
                x[indices], label=relevance, group=sizes,
            ), num_boost_round=96)
            raw_scores = fitted.predict(calibration_x, num_threads=1)
            mapping = calibration_mapping(raw_scores, calibration, contract["reference_work"])
            candidate = expit(mapping[0] * raw_scores + mapping[1])
        else:
            fitted = lgb.train(parameters, lgb.Dataset(
                x, label=target, weight=observation_weights(train) * len(train),
            ), num_boost_round=96)
            candidate = np.clip(fitted.predict(calibration_x, num_threads=1), 0., 1.)
        candidate = np.where(initial | ~calibration_valid, continuous, candidate)
        for blend in (.5, 1.):
            name = f"{args.family}_leaves{leaves}_blend{blend}"
            forecasts[name] = blend * candidate + (1 - blend) * continuous
            candidates[name] = fitted, blend, mapping
            write(args.output_dir / name / "head.json", {
                "kind": "offline_causal_workflow_utility_head_v1", "parameters": parameters,
                "model": fitted.model_to_string(), "blend": blend, "reference": contract,
                "monotone_calibration_mapping": mapping,
                "initial_prediction": "retained_training_class_continuous_utility",
                "decision_eligible": False, "GPU_action": False,
            })
    for name, forecast in forecasts.items():
        summaries[name] = utility_metrics(calibration, forecast, contract["reference_work"])
    selected = max(
        forecasts, key=lambda name: (
            selection_score(summaries[name]),
            -summaries[name]["workflow_equal_weight_utility_MSE"],
            name == "retained_binned",
        ),
    )
    write(args.output_dir / "selection.json", {
        "selected": selected, "calibration": summaries,
        "candidate_selection_used_test_labels": False,
    })
    test_actual, test_continuous, test_initial = baseline_predictions(model, test, contract)
    selected_forecast = test_actual if selected == "retained_binned" else test_continuous
    if selected in candidates:
        fitted, blend, mapping = candidates[selected]
        test_x, valid = matrix(base, test)
        forecast = fitted.predict(test_x, num_threads=1)
        if mapping is not None:
            from scipy.special import expit

            forecast = expit(mapping[0] * forecast + mapping[1])
        else:
            forecast = np.clip(forecast, 0., 1.)
        forecast = np.where(test_initial | ~valid, test_continuous, forecast)
        selected_forecast = blend * forecast + (1 - blend) * test_continuous
    evaluation = {
        "selected": selected, "reference": contract, "population": population,
        "calibration": summaries, "development": {
            "retained_binned": utility_metrics(test, test_actual, contract["reference_work"]),
            "continuous_reference": utility_metrics(test, test_continuous, contract["reference_work"]),
            "selected": utility_metrics(test, selected_forecast, contract["reference_work"]),
        },
        "development_has_been_reused": True, "candidate_selection_used_test_labels": False,
        "inputs_sha256": pins, "GPU_action": False, "online_policy_changed": False,
        "whole_run_JCT_gain_proven": False,
    }
    write(args.output_dir / "evaluation.json", evaluation)
    print(json.dumps({"selected": selected, "development": evaluation["development"]}), flush=True)


if __name__ == "__main__":
    main()
