"""Whole-workflow cross-validation for joint short-workflow/JOIN admission."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.predictor.workflow_completion_path import (
    KIND, WORK_PRIOR_KIND, RESIDUAL_KIND, feature_names, model_work_log_seconds,
    path_features, work_forecast_fingerprint,
)
from beliefkv.predictor.workflow_continuous_work import log_work_weight
from beliefkv.predictor.workflow_work import WorkflowWorkModel, work_prediction_features
from beliefkv.predictor.workflow_work_distribution import support_status
from beliefkv.predictor.workflow_work_readout import representation
from scripts.optimize_r5_join_path import choose, evaluate_choices, joint_difference, prepared_decisions
from scripts.train_workflow_work import write
from scripts.train_workflow_work_progress_trees import portable_tree


VARIANTS = {
    "original_absolute": {"kind": KIND, "rounds": 64, "leaves": 8, "depth": 4, "objective": "regression"},
    "prior_absolute": {"kind": WORK_PRIOR_KIND, "rounds": 128, "leaves": 16, "depth": 5, "objective": "regression"},
    "prior_residual": {"kind": RESIDUAL_KIND, "rounds": 128, "leaves": 16, "depth": 5, "objective": "regression"},
    "prior_residual_l1": {"kind": RESIDUAL_KIND, "rounds": 128, "leaves": 16, "depth": 5, "objective": "regression_l1"},
}


def fold_assignment(projects, measured, *, folds=4):
    if type(folds) is not int or folds < 2 or not measured <= projects.keys():
        raise ValueError("require complete project identity and fixed measurement membership")
    return {
        wf: int(hashlib.sha256(project.encode()).hexdigest(), 16) % folds
        for wf, project in projects.items() if wf not in measured
    }


def workflow_weights(workflows):
    import numpy as np

    counts = Counter(workflows)
    result = np.asarray([1. / counts[wf] for wf in workflows])
    return result * len(result) / result.sum()


def summarize_cv(per_workflow):
    choice, joint = [], []
    for counts in per_workflow.values():
        if counts["choice_opportunities"]:
            choice.append(counts["choice_errors"] / counts["choice_opportunities"])
        if counts["JOIN_opportunities"]:
            joint.append(counts["JOIN_errors"] / counts["JOIN_opportunities"])
    if not choice or not joint:
        raise ValueError("cross-validation needs held-out workflow and JOIN choices")
    return {
        "validated_workflows": len(choice), "validated_JOIN_workflows": len(joint),
        "workflow_equal_choice_error": sum(choice) / len(choice),
        "workflow_equal_JOIN_error": sum(joint) / len(joint),
        "per_workflow": {wf: dict(counts) for wf, counts in per_workflow.items()},
    }


def select_variant(reviews):
    baseline = reviews["original_absolute"]
    eligible = [
        name for name, values in reviews.items()
        if values["workflow_equal_choice_error"] <= baseline["workflow_equal_choice_error"]
        and values["workflow_equal_JOIN_error"] <= baseline["workflow_equal_JOIN_error"]
    ]
    return min(eligible, key=lambda name: (
        reviews[name]["workflow_equal_JOIN_error"], reviews[name]["workflow_equal_choice_error"],
        reviews[name]["workflow_equal_absolute_log_error"],
    ))


def cv_choices(items, costs, predicted, truth, offsets, validation_workflows):
    per_workflow = defaultdict(Counter)
    for item in items:
        if not item["competition"]:
            continue
        wfs = set(offsets[id(item)])
        heldout = set(item["feasible_workflows"]) & validation_workflows
        if not heldout:
            continue
        selection = choose(item, {wf: predicted[index] for wf, index in offsets[id(item)].items()}, costs)
        oracle = choose(item, {wf: truth[index] for wf, index in offsets[id(item)].items()}, costs)
        for wf in heldout:
            identities = {value.identity for value in item["candidates"] if value.workflow == wf}
            counts = per_workflow[wf]
            counts["choice_opportunities"] += 1
            counts["choice_errors"] += (identities & set(selection)) != (identities & set(oracle))
            peers = [value for value in item["candidates"] if value.workflow == wf]
            groups = joint_difference(peers, selection, oracle)
            if groups["JOIN_groups"]:
                counts["JOIN_opportunities"] += 1
                counts["JOIN_errors"] += groups.get("different_JOIN_frontiers", 0) > 0
                counts["missed_multi_ready_sibling_groups"] += groups.get(
                    "missed_oracle_multi_ready_sibling_groups", 0,
                )
    return per_workflow


def merge_counts(target, source):
    for wf, counts in source.items():
        target[wf].update(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "previous-model", "previous-evaluation", "previous-audit", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve prior CPU studies and hide CUDA")
    args.output_dir.mkdir(parents=True)
    import lightgbm as lgb
    import numpy as np

    started = time.monotonic()
    data = json.loads(args.dataset.read_bytes())
    old = WorkflowWorkModel.load(args.previous_model, sha256_file(args.previous_model))
    base = WorkflowWorkModel.load(Path(data["base_model"]), data["base_sha256"])
    prior_evaluation = json.loads(args.previous_evaluation.read_bytes())
    audit = json.loads(args.previous_audit.read_bytes())
    measured = set(data["cohort"]["measured_workflow_ids"])
    if (
        old.completion_path.raw["training_source"]["sha256"] != sha256_file(args.dataset)
        or prior_evaluation["model_sha256"] != sha256_file(args.previous_model)
        or {wf for wf, part in prior_evaluation["split"].items() if part == "evaluation"} != measured
    ):
        raise ValueError("preserve the fixed18 dataset and previous predictor")
    oracle = json.loads(Path(data["oracle_artifact"]).read_bytes())
    composition_path = Path(oracle["source_binding"]["source_arm"]) / "composite_source.json"
    composition = json.loads(composition_path.read_bytes())
    projects = {row["workflow_id"]: row["project"] for row in composition["selected_workflows"]}
    assignment = fold_assignment(projects, measured)
    if set(assignment) | measured != set(data["task_text"]):
        raise ValueError("whole-project folds do not cover the exact original population")
    write(args.output_dir / "plan.json", {
        "variants": VARIANTS, "folds": assignment, "fold_rule": "sha256_original_project_mod4",
        "fixed18_targets_used_for_selection": False,
        "whole_evaluation_windows_excluded_from_cross_validation": True,
        "one_count_per_heldout_workflow_per_boundary": True,
        "selection_requires_both_workflow_and_JOIN_error_noninferior_to_crossfit_baseline": True,
        "GPU_action": False,
    })
    costs = CombinationCosts(**base.raw["admission_cost_profile"]["costs"])
    items = prepared_decisions(data, costs)
    projections = {wf: base.task_encoding(text) for wf, text in data["task_text"].items()}
    samples = list(data["rows"])
    offsets = {}
    for item in items:
        mapping = {}
        for wf, state in item["raw"]["states"].items():
            mapping[wf] = len(samples)
            samples.append({"workflow": wf, **state})
        offsets[id(item)] = mapping
        common = audit["windows"]["common_throughput"]
        item["in_common_window"] = common[0] <= item["raw"]["monotonic_s"] <= common[1]
    x, model_logs, support, timing = [], [], [], []
    for state in samples:
        projection, coverage = projections[state["workflow"]]
        values = state["features"]
        _, work = base.predict(projection, values, with_work=True)
        hidden = representation(base, projection.progress_projection, values)
        transformed = work_prediction_features(values, task_only_before_work=base.task_only_before_work)
        x.append(path_features(
            transformed, projection.task_structure, hidden, state["timing"], work_forecast=work,
        ))
        model_logs.append(model_work_log_seconds(work, old.completion_path.raw["model_seconds_per_work_token"]))
        support.append(support_status(base, values, coverage) == "calibrated_distribution")
        timing.append(state["timing"])
    x, model_logs, timing, support = (
        np.asarray(x), np.asarray(model_logs), np.asarray(timing), np.asarray(support),
    )
    row_count = len(data["rows"])
    y = np.log1p([state["path"][0] for state in samples])
    workflows = np.asarray([state["workflow"] for state in samples])
    assignments = np.asarray([assignment.get(wf, -1) for wf in workflows])
    train_indices = np.arange(len(samples)) < row_count
    np.savez(
        args.output_dir / "feature_cache.npz", x=x, model_logs=model_logs,
        timing=timing, support=support, y=y,
    )
    v1_columns = [
        index for index, name in enumerate(feature_names(base, with_work_prior=True))
        if not name.startswith("predicted_log_")
    ]
    cv_items = [
        item for item in items
        if not set(offsets[id(item)]) & measured
    ]
    reference = old.completion_path.raw["reference_path_s"]
    truth = np.asarray([log_work_weight(float(value), reference) for value in y])
    fallback = np.asarray([log_work_weight(float(value), reference) for value in model_logs])
    per_variant = {name: defaultdict(Counter) for name in VARIANTS}
    oof = {name: np.full(row_count, np.nan) for name in VARIANTS}
    fold_reports = []
    print(json.dumps({"stage": "features_ready", "rows": len(samples), "fold_sizes": dict(Counter(assignment.values()))}), flush=True)

    def fit(configuration, training_mask):
        columns = v1_columns if configuration["kind"] == KIND else slice(None)
        inputs = x[:, columns]
        targets = y - model_logs if configuration["kind"] == RESIDUAL_KIND else y
        weights = workflow_weights(workflows[training_mask])
        parameters = {
            "objective": configuration["objective"], "num_leaves": configuration["leaves"],
            "max_depth": configuration["depth"], "learning_rate": .05,
            "min_data_in_leaf": 80, "lambda_l2": 10., "num_threads": 1,
            "deterministic": True, "force_col_wise": True, "seed": 21, "verbosity": -1,
        }
        booster = lgb.train(
            parameters, lgb.Dataset(inputs[training_mask], label=targets[training_mask], weight=weights),
            num_boost_round=configuration["rounds"],
        )
        tree_score = booster.predict(inputs, raw_score=True, num_threads=1)
        prediction = tree_score + model_logs if configuration["kind"] == RESIDUAL_KIND else tree_score
        return booster, prediction, tree_score

    for fold in range(4):
        training = train_indices & (assignments >= 0) & (assignments != fold)
        validation = train_indices & (assignments == fold)
        heldout = {wf for wf, group in assignment.items() if group == fold}
        low, high = timing[training].min(axis=0), timing[training].max(axis=0)
        margin = base.distribution_contract["feature_log_margin"]
        path_support = ((timing >= low - margin) & (timing <= high + margin)).all(axis=1)
        for name, configuration in VARIANTS.items():
            _booster, prediction, _tree_score = fit(configuration, training)
            bias = float(np.average(y[training] - prediction[training], weights=workflow_weights(workflows[training])))
            prediction = prediction + bias
            oof[name][validation[:row_count]] = prediction[:row_count][validation[:row_count]]
            weights = np.asarray([log_work_weight(float(max(0., value)), reference) for value in prediction])
            weights[~path_support] = fallback[~path_support]
            weights[~support] = .5
            counts = cv_choices(cv_items, costs, weights, truth, offsets, heldout)
            merge_counts(per_variant[name], counts)
            fold_reports.append({
                "fold": fold, "variant": name, "heldout_workflows": len(heldout),
                "decision_counts": {wf: dict(value) for wf, value in counts.items()},
                "calibration_bias_from_training_only": bias,
            })
        print(json.dumps({"stage": "fold_complete", "fold": fold}), flush=True)
    reviews = {}
    pooled = np.asarray([wf in assignment for wf in workflows[:row_count]])
    for name in VARIANTS:
        if not np.isfinite(oof[name][pooled]).all():
            raise ValueError("every nonmeasured workflow needs an out-of-fold path prediction")
        values = summarize_cv(per_variant[name])
        errors = defaultdict(list)
        for wf, error in zip(
            workflows[:row_count][pooled], abs(oof[name][pooled] - y[:row_count][pooled]),
        ):
            errors[wf].append(float(error))
        values["workflow_equal_absolute_log_error"] = sum(
            sum(errors[wf]) / len(errors[wf]) for wf in errors
        ) / len(errors)
        reviews[name] = values
    selected = select_variant(reviews)
    write(args.output_dir / "selection.json", {
        "selected": selected, "reviews": reviews, "fold_reports": fold_reports,
        "fixed18_targets_used_for_selection": False, "GPU_action": False,
    })
    configuration = VARIANTS[selected]
    booster, _prediction, tree_score = fit(configuration, train_indices & (assignments >= 0))
    bias = float(np.average(
        y[:row_count][pooled] - oof[selected][pooled],
        weights=workflow_weights(workflows[:row_count][pooled]),
    ))
    profile = deepcopy(old.completion_path.raw)
    profile.update(
        kind=configuration["kind"],
        features=list(feature_names(base, with_work_prior=configuration["kind"] != KIND)),
        trees=[portable_tree(tree["tree_structure"]) for tree in booster.dump_model()["tree_info"]],
        maximum_depth=configuration["depth"], calibration_offset=bias,
        feature_log_min=timing[train_indices & (assignments >= 0)].min(axis=0).tolist(),
        feature_log_max=timing[train_indices & (assignments >= 0)].max(axis=0).tolist(),
    )
    if configuration["kind"] != KIND:
        profile["work_forecast_sha256"] = work_forecast_fingerprint(base)
    raw = deepcopy(base.raw)
    raw["completion_path_readout"] = profile
    model = WorkflowWorkModel(raw)
    columns = v1_columns if configuration["kind"] == KIND else slice(None)
    if max(
        abs(model.completion_path.tree.score(x[index, columns]) - tree_score[index])
        for index in np.unique(np.linspace(0, len(samples) - 1, 256, dtype=int))
    ) > 1e-9:
        raise ValueError("portable crossfit-selected path forecast changed")
    write(args.output_dir / "model.json", raw)
    partition = {wf: "evaluation" if wf in measured else "training" for wf in workflows}
    results = {
        name: evaluate_choices(serving, items, projections, costs, partition, validation_only=False)
        for name, serving in (("previous", old), ("crossfit_selected", model))
    }
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "results": results, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "reviews": {name: {key: value for key, value in values.items() if key != "per_workflow"}
                    for name, values in reviews.items()},
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                args.dataset, args.previous_model, args.previous_evaluation, args.previous_audit,
                composition_path, Path(__file__),
                ROOT / "beliefkv/predictor/workflow_completion_path.py",
            )
        },
        "calibration_is_whole_project_out_of_fold": True, "fixed18_targets_used_for_selection": False,
        "base_representation_and_repeated_fixed18_are_development_not_unseen_generalization": True,
        "same_snapshot_planning_not_future_service_or_JCT": True,
        "CPU_elapsed_s": time.monotonic() - started, "GPU_action": False,
    })
    print(json.dumps({
        "selected": selected, "reviews": {
            name: {key: value for key, value in values.items() if key != "per_workflow"}
            for name, values in reviews.items()
        },
        "results": {name: {key: value for key, value in values.items() if key != "decisions"}
                    for name, values in results.items()},
        "CPU_elapsed_s": time.monotonic() - started,
    }), flush=True)


if __name__ == "__main__":
    main()
