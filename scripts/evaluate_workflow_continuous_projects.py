"""Refit R5 inside whole-project folds and compare binned and continuous forecasts on CPU."""

import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import probability_weight
from beliefkv.predictor.workflow_conditional_tree import KIND as HEAD_KIND
from beliefkv.predictor.workflow_continuous_work import (
    remaining_work_weight, serving_contract,
)
from beliefkv.predictor.workflow_progress_tree import FEATURES
from beliefkv.predictor.workflow_work import (
    OBSERVED_RATIOS, R5_FEATURES, R5_SCHEMA, WorkflowWorkModel, before_observed_work,
)
from beliefkv.predictor.workflow_work_distribution import support_status
from beliefkv.predictor.workflow_work_readout import representation_fingerprint
from beliefkv.predictor.workflow_work_training import (
    READY_BALANCED, project_task_weights, remaining_call_counts,
)
from scripts.train_workflow_branch_call_trees import fit_trees, matrix
from scripts.train_workflow_frozen_readout import observation_weights
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_work import fit, write
from scripts.train_workflow_work_distribution import distribution_metrics
from scripts.train_workflow_work_progress_trees import portable_tree
from scripts.train_workflow_utility_readout import sampled


def combine_datasets(datasets):
    """Reject partial, repeated or relabelled executions before drawing any folds."""
    rows, ownership, executions = [], {}, {}
    for dataset in datasets:
        if (
            dataset.get("schema") != R5_SCHEMA or dataset.get("prefill_token_weight") != .01
            or dataset.get("observed_delegation_inputs")
            or dataset.get("progress_observation_contract") != datasets[0].get("progress_observation_contract")
        ):
            raise ValueError("project evaluation requires the same causal work and feature contract")
        groups = defaultdict(list)
        for row in dataset["rows"]:
            groups[row["workflow"]].append(row)
        for workflow, group in groups.items():
            identities = {
                (row["project"], row["source_workflow_sha256"], row["task_template"],
                 row.get("task_text", row["text"])) for row in group
            }
            if len(identities) != 1 or workflow in ownership:
                raise ValueError("a complete execution cannot change owner or occur twice")
            project, digest, task, text = identities.pop()
            if (
                any(type(value) is not str or not value for value in (project, digest, task, text))
                or len(digest) != 64 or digest in executions
            ):
                raise ValueError("complete project labels require unique source executions")
            ownership[workflow], executions[digest] = project, workflow
            rows.extend(group)
    remaining_call_counts(rows)
    return rows


def project_folds(rows, *, count=5, seed=21):
    """A separate complete project fold calibrates each outer model."""
    projects = sorted({row["project"] for row in rows}, key=lambda value: hashlib.sha256(
        f"{seed}:{value}".encode(),
    ).digest())
    if type(count) is not int or not 3 <= count <= min(10, len(projects) // 3):
        raise ValueError("project folds need at least three projects per outer fold")
    assignment = {project: index % count for index, project in enumerate(projects)}
    result = []
    for outer in range(count):
        calibration = (outer + 1) % count
        groups = [
            [row for row in rows if assignment[row["project"]] not in (outer, calibration)],
            [row for row in rows if assignment[row["project"]] == calibration],
            [row for row in rows if assignment[row["project"]] == outer],
        ]
        for field in ("project", "workflow", "source_workflow_sha256", "task_template"):
            values = [{row[field] for row in group} for group in groups]
            if any(left & right for left, right in combinations(values, 2)):
                raise ValueError("training, calibration and outer project identities overlap: " + field)
        result.append(groups)
    return result


def ranking_metrics(rows, scores, *, phase, supported_only=False, within_class_only=False):
    import numpy as np

    groups, projects = defaultdict(list), {}
    for row, score in zip(rows, scores):
        if supported_only and not row["supported"]:
            continue
        if phase == "initial" and row["snapshot"] != 0:
            continue
        if phase == "ready" and row["features"][R5_FEATURES.index("ready")] <= 0:
            continue
        groups[row["workflow"]].append((row, score))
        projects[row["workflow"]] = row["project"]
    groups = {
        workflow: [values[index] for index in np.unique(np.linspace(
            0, len(values) - 1, min(8, len(values)), dtype=int,
        ))] for workflow, values in groups.items()
    }
    pairs, large, observations = defaultdict(list), defaultdict(list), 0
    for left, right in combinations(sorted(groups), 2):
        if projects[left] == projects[right]:
            continue
        values, wide = [], []
        for a, pa in groups[left]:
            for b, pb in groups[right]:
                if within_class_only and a["true_class"] != b["true_class"]:
                    continue
                delta = a["remaining_work"] - b["remaining_work"]
                if abs(delta) < 1e-9:
                    continue
                score = .5 if abs(pa - pb) < 1e-10 else float((pa - pb) * delta < 0)
                values.append(score)
                if max(a["remaining_work"], b["remaining_work"]) >= 2 * max(
                    1., min(a["remaining_work"], b["remaining_work"]),
                ):
                    wide.append(score)
        key = tuple(sorted((projects[left], projects[right])))
        if values:
            pairs[key].append(float(np.mean(values)))
            observations += len(values)
        if wide:
            large[key].append(float(np.mean(wide)))
    return {
        "project_pair_equal_weight_accuracy": (
            float(np.mean([np.mean(values) for values in pairs.values()])) if pairs else None
        ),
        "work_ratio_at_least_two_accuracy": (
            float(np.mean([np.mean(values) for values in large.values()])) if large else None
        ),
        "project_pairs": len(pairs), "workflow_pairs": sum(map(len, pairs.values())),
        "pair_observations": observations,
    }


def forecast_metrics(rows, *, method):
    import numpy as np

    weights = project_task_weights(rows, np.ones(len(rows)))
    scores = [row[method + "_weight"] for row in rows]
    target = np.asarray([
        remaining_work_weight(row["remaining_work"], row["reference_work"]) for row in rows
    ])
    return {
        "projects": len({row["project"] for row in rows}),
        "executions": len({row["workflow"] for row in rows}), "snapshots": len(rows),
        "supported_project_equal_mass": float(np.sum(weights * [row["supported"] for row in rows])),
        "project_equal_utility_MSE": float(np.sum(weights * (np.asarray(scores) - target) ** 2)),
        "project_equal_log_work_RMSE": float(np.sqrt(np.sum(weights * (
            np.asarray([row[method + "_log_work"] for row in rows])
            - np.log1p([row["remaining_work"] for row in rows])
        ) ** 2))),
        **{
            phase: ranking_metrics(rows, scores, phase=phase)
            for phase in ("initial", "ready")
        },
        "supported_ready": ranking_metrics(rows, scores, phase="ready", supported_only=True),
        "same_true_class_ready": ranking_metrics(rows, scores, phase="ready", within_class_only=True),
    }


def fit_fold(training, calibration, *, epochs, tree_rounds):
    import numpy as np

    settings = {
        "epochs": epochs, "split_mode": "project", "hidden_width": 128,
        "vocabulary_size": 8192, "dropout": .1, "weight_decay": .05,
        "feature_schema": R5_SCHEMA, "training_weighting": READY_BALANCED,
        "ranking_loss_weight": .1, "balance_training_projects": True,
    }
    raw = fit(training, calibration, **settings)
    raw["metadata"]["decision_eligible"] = True
    initial = fit(
        training, calibration, work_thresholds=raw["work_thresholds"],
        numeric_feature_transform=OBSERVED_RATIOS, **settings,
    )
    initial["metadata"]["decision_eligible"] = True
    raw["initial_state_head"] = {
        "kind": "before_observed_work_and_children_v1", "blend": 1., "model": initial,
    }
    base = WorkflowWorkModel(raw)
    train_x, train_valid = matrix(base, training)
    cal_x, cal_valid = matrix(base, calibration)
    chosen = np.asarray([
        use and not before_observed_work(row["features"]) for row, use in zip(training, train_valid)
    ])
    cal_chosen = np.asarray([
        use and not before_observed_work(row["features"]) for row, use in zip(calibration, cal_valid)
    ])
    train_rows = [row for row, use in zip(training, chosen) if use]
    cal_rows = [row for row, use in zip(calibration, cal_chosen) if use]
    if not train_rows or not cal_rows:
        raise ValueError("a project fold lacks supported observed-progress work")
    models, proof = fit_trees(
        train_x[chosen], np.log1p([row["remaining_work"] for row in train_rows])[:, None],
        train_rows, leaves=16, rounds=tree_rounds,
    )
    residual = np.log1p([row["remaining_work"] for row in cal_rows]) - models[0].predict(
        cal_x[cal_chosen], num_threads=1,
    )
    weights = project_task_weights(cal_rows, observation_weights(cal_rows))
    offset = float(np.sum(weights * residual))
    sigma = max(.25, float(np.sqrt(np.sum(weights * (residual - offset) ** 2))))
    raw["conditional_progress_tree_head"] = {
        "kind": HEAD_KIND,
        "features": list(FEATURES) + [
            "frozen_hidden_" + str(index) for index in range(base.arrays["hidden_weight"].shape[1])
        ],
        "representation_sha256": representation_fingerprint(base),
        "trees": [portable_tree(tree["tree_structure"])
                  for tree in models[0].dump_model()["tree_info"]],
        "maximum_depth": 5, "blend": 1.,
        "calibration_offset": offset, "calibration_sigma": sigma,
    }
    model = WorkflowWorkModel(raw)
    metrics = distribution_metrics(model, calibration)
    reasons = calibration_rejections({"all": metrics}, raw)
    raw["metadata"].update(
        decision_eligible=not reasons, decision_rejection_reasons=reasons,
        calibration_distribution_metrics=metrics,
    )
    model = WorkflowWorkModel(raw)
    return model, proof


def evaluate_fold(model, rows):
    import numpy as np

    encodings, output = {}, []
    for row in rows:
        text = row.get("task_text", row["text"])
        if text not in encodings:
            encodings[text] = model.task_encoding(text)
        projection, coverage = encodings[text]
        supported = projection is not None and (
            support_status(model, row["features"], coverage) == "calibrated_distribution"
        )
        if projection is None:
            probability, continuous = (1 / 3.,) * 3, None
        else:
            probability, continuous = model.predict(projection, row["features"], with_work=True)
        binned_log = float(np.dot(
            probability, model.continuous_readout.contract["initial_class_mean_log_work"],
        ))
        output.append({
            **row, "supported": supported,
            "true_class": int(np.searchsorted(model.thresholds, row["remaining_work"], side="right")),
            "reference_work": model.continuous_readout.contract["reference_work"],
            "binned_weight": probability_weight(probability) if supported else .5,
            "continuous_weight": continuous["distribution_effective_weight"] if supported else .5,
            "raw_binned_weight": probability_weight(probability),
            "raw_continuous_weight": continuous["distribution_effective_weight"] if continuous is not None else .5,
            "binned_log_work": binned_log,
            "continuous_log_work": continuous["log1p_remaining_work"] if continuous is not None else binned_log,
        })
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--tree-rounds", type=int, default=96)
    parser.add_argument("--maximum-training-states", type=int, default=0)
    parser.add_argument("--maximum-calibration-states", type=int, default=0)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output_dir.exists():
        raise ValueError("hide CUDA and preserve previous project evaluation evidence")
    if (
        not 1 <= args.epochs <= 10000 or not 1 <= args.tree_rounds <= 128
        or any(value != 0 and not 2 <= value <= 10000 for value in (
            args.maximum_training_states, args.maximum_calibration_states,
        ))
    ):
        raise ValueError("use bounded fitting parameters or zero for every complete observed state")
    source_paths = [Path(__file__)] + [ROOT / path for path in (
        "beliefkv/predictor/workflow_work.py", "beliefkv/predictor/workflow_work_training.py",
        "beliefkv/predictor/workflow_continuous_work.py", "beliefkv/predictor/workflow_conditional_tree.py",
        "beliefkv/predictor/workflow_progress_tree.py", "beliefkv/predictor/workflow_work_distribution.py",
        "beliefkv/predictor/workflow_work_readout.py", "beliefkv/predictor/workflow_task_encoder.py",
        "scripts/train_workflow_work.py", "scripts/train_workflow_branch_call_trees.py",
        "scripts/train_workflow_work_progress_trees.py", "scripts/train_workflow_utility_readout.py",
        "scripts/train_workflow_frozen_readout.py", "scripts/improve_workflow_work_distribution.py",
        "scripts/train_workflow_work_distribution.py",
    )]
    pins = {str(path.resolve()): sha256_file(path) for path in args.dataset + source_paths}
    all_rows = combine_datasets([json.loads(path.read_bytes()) for path in args.dataset])
    folds = project_folds(all_rows, count=args.folds)
    write(args.output_dir / "plan.json", {
        "inputs_sha256": pins, "projects": len({row["project"] for row in all_rows}),
        "executions": len({row["workflow"] for row in all_rows}), "snapshots": len(all_rows),
        "outer_folds": args.folds, "epochs": args.epochs, "tree_rounds": args.tree_rounds,
        "seed": 21, "maximum_training_states_per_execution": args.maximum_training_states or None,
        "maximum_calibration_states_per_execution": args.maximum_calibration_states or None,
        "maximum_evaluation_states_per_execution": 64,
        "training_uses_no_outer_or_calibration_weights": True,
        "vocabulary_scaling_both_MLPs_and_tree_refitted_inside_each_fold": True,
        "calibration_uses_a_separate_project_fold": True,
        "class_thresholds_reference_and_priors_use_fold_training_only": True,
        "candidate_and_hyperparameter_selection_on_outer_results": False,
        "comparison": "same_refitted_R5_forecast_binned_vs_continuous",
        "original_calibration_safety_gates_retained": True,
        "raw_forecast_rank_is_diagnostic_not_deployment_eligibility": True,
        "scope": "exploratory_project_disjoint_cross_validation_not_a_fresh_blind_test",
        "GPU_action": False, "online_policy_changed": False,
        "folds": [{
            name: {
                "projects": sorted({row["project"] for row in group}),
                "executions": sorted({row["source_workflow_sha256"] for row in group}),
            } for name, group in zip(("training", "calibration", "evaluation"), groups)
        } for groups in folds],
    })
    summaries = []
    for index, (training, calibration, evaluation) in enumerate(folds):
        started = time.monotonic()
        print(f"project_fold_{index}: fitting all predictor components", flush=True)
        training_sample = (
            sampled(training, args.maximum_training_states) if args.maximum_training_states else training
        )
        calibration_sample = (
            sampled(calibration, args.maximum_calibration_states)
            if args.maximum_calibration_states else calibration
        )
        model, proof = fit_fold(
            training_sample, calibration_sample, epochs=args.epochs, tree_rounds=args.tree_rounds,
        )
        raw = deepcopy(model.raw)
        raw["continuous_work_readout"] = serving_contract(model, training)
        raw["metadata"].update(
            validation_scope="exploratory_project_disjoint_cross_validation",
            outer_fold=index, GPU_qualified=False,
        )
        model = WorkflowWorkModel(raw)
        evaluated = evaluate_fold(model, sampled(evaluation, 64))
        report = {
            "fold": index, "fit_and_evaluation_seconds": time.monotonic() - started,
            "binned": forecast_metrics(evaluated, method="binned"),
            "continuous": forecast_metrics(evaluated, method="continuous"),
            "calibration_decision_eligible": raw["metadata"]["decision_eligible"],
            "calibration_rejections": raw["metadata"]["decision_rejection_reasons"],
            "raw_ready_diagnostic": {
                method: ranking_metrics(
                    evaluated, [row["raw_" + method + "_weight"] for row in evaluated], phase="ready",
                ) for method in ("binned", "continuous")
            },
        }
        model_path = args.output_dir / f"fold_{index}" / "model.json"
        write(model_path, raw)
        report["model_sha256"] = sha256_file(model_path)
        write(args.output_dir / f"fold_{index}" / "tree_training.json", proof)
        write(args.output_dir / f"fold_{index}" / "predictions.json", evaluated)
        write(args.output_dir / f"fold_{index}" / "evaluation.json", report)
        summaries.append(report)
        print(json.dumps(report), flush=True)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a bound complete-work dataset changed during project evaluation")
    import numpy as np

    write(args.output_dir / "evaluation.json", {
        "folds": summaries, "inputs_sha256": pins,
        "mean_outer_ready_pair_accuracy": {
            method: float(np.mean([
                report[method]["ready"]["project_pair_equal_weight_accuracy"] for report in summaries
            ])) for method in ("binned", "continuous")
        },
        "all_projects_evaluated_once": True, "online_policy_changed": False, "GPU_action": False,
        "cross_fold_predictions_compared_against_each_other": False,
        "fresh_blind_test_or_JCT_improvement_claimed": False,
    })


if __name__ == "__main__":
    main()
