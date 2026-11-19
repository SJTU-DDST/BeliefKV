"""Fit workflow-unit ranks, export seconds trees, and validate joint choices."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.predictor.workflow_completion_path import WORK_PRIOR_KIND, feature_names, work_forecast_fingerprint
from beliefkv.predictor.workflow_continuous_work import log_work_weight
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel
from scripts.crossvalidate_r5_join_path import (
    DECISION_SCORE_SCOPE, heldout_choice_review, workflow_weights,
)
from scripts.optimize_r5_join_path import evaluate_choices, prepared_decisions
from scripts.train_workflow_work import write
from scripts.train_workflow_work_progress_trees import portable_tree


def query_labels(indices, targets):
    values = sorted({float(targets[index]) for index in indices}, reverse=True)
    order = {value: rank for rank, value in enumerate(values)}
    return [order[float(targets[index])] for index in indices]


def rank_queries(items, offsets, workflows, labels, eligible, *, synthetic_count, seed):
    actual = []
    for item in items:
        if item["competition"]:
            ids = [offsets[id(item)][wf] for wf in item["feasible_workflows"] if wf in eligible]
            if len(ids) >= 2:
                actual.append(ids)
    groups = defaultdict(list)
    for index, wf in enumerate(workflows):
        if wf in eligible:
            groups[wf].append(index)
    rng, synthetic = random.Random(seed), []
    for _ in range(synthetic_count):
        owners = rng.sample(sorted(groups), min(6, len(groups)))
        synthetic.append([rng.choice(groups[wf]) for wf in owners])
    return actual, synthetic


def training_queries(sources, targets, workflows, *, weighting):
    if weighting not in ("query", "workflow"):
        raise ValueError("unknown rank training weighting")
    indices, relevance, sizes, weights = [], [], [], []
    for source in sources:
        counts = Counter(workflows[index] for query in source for index in query)
        for query in source:
            if len({workflows[index] for index in query}) != len(query):
                raise ValueError("each rank query must contain distinct workflows")
            indices.extend(query)
            relevance.extend(query_labels(query, targets))
            sizes.append(len(query))
            weights.extend(
                [1. / len(source) / len(query)] * len(query) if weighting == "query"
                else [1. / len(counts) / counts[workflows[index]] for index in query]
            )
    if not indices:
        raise ValueError("rank training requires observed or sampled workflow queries")
    scale = len(weights) / sum(weights)
    return indices, relevance, sizes, [value * scale for value in weights]


def calibration(scores, targets, weights):
    import numpy as np

    mean_score = float(np.average(scores, weights=weights))
    mean_target = float(np.average(targets, weights=weights))
    variance = float(np.average((scores - mean_score) ** 2, weights=weights))
    if variance <= 0:
        raise ValueError("ranker has no calibratable score variation")
    slope = float(np.average((scores - mean_score) * (targets - mean_target), weights=weights)) / variance
    if not slope < 0 or not math.isfinite(slope):
        raise ValueError("higher short-workflow relevance must imply a shorter path")
    return slope, mean_target - slope * mean_score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "previous-model", "previous-audit", "crossfit-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--weighting", choices=("query", "workflow"), default="query")
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("require fresh CPU-only rank evidence")
    args.output_dir.mkdir(parents=True)
    import lightgbm as lgb
    import numpy as np

    started = time.monotonic()
    data = json.loads(args.dataset.read_bytes())
    crossfit = json.loads((args.crossfit_dir / "evaluation.json").read_bytes())
    plan = json.loads((args.crossfit_dir / "plan.json").read_bytes())
    if crossfit.get("decision_score_scope") != DECISION_SCORE_SCOPE:
        raise ValueError("rank validation requires the same per-candidate held-out crossfit baseline")
    if crossfit["inputs_sha256"].get(str(args.dataset.resolve())) != sha256_file(args.dataset):
        raise ValueError("feature cache belongs to another source dataset")
    if crossfit["inputs_sha256"].get(str(args.previous_audit.resolve())) != sha256_file(args.previous_audit):
        raise ValueError("common window differs from the crossfit source")
    common = json.loads(args.previous_audit.read_bytes())["windows"]["common_throughput"]
    old = WorkflowWorkModel.load(args.previous_model, sha256_file(args.previous_model))
    base = WorkflowWorkModel.load(Path(data["base_model"]), data["base_sha256"])
    previous = WorkflowWorkModel.load(
        args.crossfit_dir / "model.json", crossfit["model_sha256"],
    )
    assignment, measured = plan["folds"], set(data["cohort"]["measured_workflow_ids"])
    if set(assignment) & measured or set(assignment) | measured != set(data["task_text"]):
        raise ValueError("preserve whole-workflow crossfit and the fixed18 holdout")
    cache_path = args.crossfit_dir / "feature_cache.npz"
    cache = np.load(cache_path)
    x, y, timing, support, model_logs = (
        cache[name] for name in ("x", "y", "timing", "support", "model_logs")
    )
    costs = CombinationCosts(**base.raw["admission_cost_profile"]["costs"])
    items, samples = prepared_decisions(data, costs), list(data["rows"])
    offsets = {}
    for item in items:
        offsets[id(item)] = {}
        for wf, state in item["raw"]["states"].items():
            offsets[id(item)][wf] = len(samples)
            samples.append({"workflow": wf, **state})
        item["in_common_window"] = common[0] <= item["raw"]["monotonic_s"] <= common[1]
    if len(samples) != len(x):
        raise ValueError("cached states changed their exact decision order")
    workflows = np.asarray([row["workflow"] for row in samples])
    folds = np.asarray([assignment.get(wf, -1) for wf in workflows])
    count = len(data["rows"])
    events = np.arange(len(samples)) < count
    choices = [item for item in items if not set(offsets[id(item)]) & measured]
    # Synthetic training queries use observed ready/running states; they do not
    # claim that these workflows competed in a real serving boundary.
    ready = [R5_FEATURES.index(name) for name in ("ready", "running")]
    query_workflows = [
        wf if index < count and sum(samples[index]["features"][part] for part in ready) > 0 else ""
        for index, wf in enumerate(workflows)
    ]
    configurations = {"actual": 0, "balanced": 4096}
    write(args.output_dir / "plan.json", {
        "configurations": configurations, "folds": assignment,
        "one_instance_per_workflow_not_per_sibling": True,
        "relevance_is_true_remaining_model_tool_path_order": True,
        "synthetic_queries_are_training_only_not_competition_evidence": True,
        "training_weighting": args.weighting,
        "decision_score_scope": DECISION_SCORE_SCOPE,
        "runtime_policy_and_sibling_capacity_checks_unchanged": True,
        "fixed18_used_for_selection": False, "GPU_action": False,
    })
    reference = previous.completion_path.raw["reference_path_s"]
    truth = np.asarray([log_work_weight(float(value), reference) for value in y])
    fallback = np.asarray([log_work_weight(float(value), reference) for value in model_logs])
    outcomes, out_of_fold, folds_evidence = {}, {}, []

    def fit(eligible, synthetic_count):
        actual, synthetic = rank_queries(
            choices, offsets, query_workflows, y, eligible,
            synthetic_count=synthetic_count, seed=21,
        )
        sources = [actual] + ([synthetic] if synthetic else [])
        indices, relevance, sizes, weights = training_queries(
            sources, y, workflows, weighting=args.weighting,
        )
        parameters = {
            "objective": "lambdarank", "label_gain": list(range(8)),
            "lambdarank_truncation_level": 8,
            "num_leaves": 16, "max_depth": 5, "learning_rate": .05,
            "min_data_in_leaf": 20, "lambda_l2": 10., "num_threads": 1,
            "deterministic": True, "force_col_wise": True, "seed": 21, "verbosity": -1,
        }
        fitted = lgb.train(
            parameters, lgb.Dataset(x[indices], label=relevance, group=sizes, weight=weights),
            num_boost_round=128,
        )
        return fitted, fitted.predict(x, raw_score=True, num_threads=1), {
            "actual_queries": len(actual), "synthetic_queries": len(synthetic),
        }

    for name, synthetic_count in configurations.items():
        raw_oof, decision_oof = np.full(count, np.nan), np.full(len(samples), np.nan)
        for fold in range(4):
            eligible = {wf for wf, group in assignment.items() if group != fold}
            training = events & (folds >= 0) & (folds != fold)
            validation = folds[:count] == fold
            _fitted, raw_scores, evidence = fit(eligible, synthetic_count)
            slope, intercept = calibration(
                raw_scores[training], y[training], workflow_weights(workflows[training]),
            )
            raw_oof[validation] = raw_scores[:count][validation]
            prediction = intercept + slope * raw_scores
            weights = np.asarray([log_work_weight(float(max(0., value)), reference) for value in prediction])
            low, high = timing[training].min(axis=0), timing[training].max(axis=0)
            margin = base.distribution_contract["feature_log_margin"]
            available = ((timing >= low - margin) & (timing <= high + margin)).all(axis=1)
            weights[~available] = fallback[~available]
            weights[~support] = .5
            decision_oof[folds == fold] = weights[folds == fold]
            folds_evidence.append({
                "variant": name, "fold": fold, "slope": slope, "intercept": intercept, **evidence,
            })
            print(json.dumps({"stage": "rank_fold", "variant": name, "fold": fold}), flush=True)
        outcomes[name] = heldout_choice_review(choices, costs, decision_oof, truth, offsets, assignment)
        out_of_fold[name] = raw_oof
    baseline = crossfit["reviews"][crossfit["selected"]]
    eligible = [
        name for name, value in outcomes.items()
        if value["workflow_equal_choice_error"] <= baseline["workflow_equal_choice_error"]
        and value["workflow_equal_JOIN_error"] <= baseline["workflow_equal_JOIN_error"]
    ]
    selected = min(eligible, key=lambda name: (
        outcomes[name]["workflow_equal_JOIN_error"], outcomes[name]["workflow_equal_choice_error"],
    )) if eligible else "keep_crossfit"
    write(args.output_dir / "selection.json", {
        "selected": selected, "outcomes": outcomes, "baseline": baseline,
        "decision_score_scope": DECISION_SCORE_SCOPE,
        "folds": folds_evidence, "fixed18_used_for_selection": False, "GPU_action": False,
    })
    if selected == "keep_crossfit":
        write(args.output_dir / "evaluation.json", {
            "selected": selected, "outcomes": outcomes,
            "baseline": baseline, "no_ranker_promoted": True, "GPU_action": False,
            "decision_score_scope": DECISION_SCORE_SCOPE,
            "training_weighting": args.weighting,
            "CPU_elapsed_s": time.monotonic() - started,
            "inputs_sha256": {
                str(path.resolve()): sha256_file(path) for path in (
                    args.dataset, args.previous_model, args.previous_audit,
                    args.crossfit_dir / "evaluation.json", cache_path, Path(__file__),
                )
            },
        })
        print(json.dumps({
            "selected": selected, "outcomes": {
                name: {key: value for key, value in values.items() if key != "per_workflow"}
                for name, values in outcomes.items()
            },
        }), flush=True)
        return
    fitted, raw_scores, evidence = fit(set(assignment), configurations[selected])
    pooled = folds[:count] >= 0
    slope, intercept = calibration(
        out_of_fold[selected][pooled], y[:count][pooled], workflow_weights(workflows[:count][pooled]),
    )
    trees = [portable_tree(tree["tree_structure"]) for tree in fitted.dump_model()["tree_info"]]
    for tree in trees:
        for node in tree:
            node[4] *= slope
    raw = deepcopy(base.raw)
    raw["completion_path_readout"] = {
        **deepcopy(previous.completion_path.raw), "kind": WORK_PRIOR_KIND,
        "features": list(feature_names(base, with_work_prior=True)),
        "work_forecast_sha256": work_forecast_fingerprint(base),
        "trees": trees, "maximum_depth": 5, "calibration_offset": intercept,
    }
    model = WorkflowWorkModel(raw)
    if max(
        abs(model.completion_path.tree.score(x[index]) + intercept - (slope * raw_scores[index] + intercept))
        for index in np.unique(np.linspace(0, len(x) - 1, 256, dtype=int))
    ) > 1e-9:
        raise ValueError("ranker export changed its calibrated seconds or order")
    write(args.output_dir / "model.json", raw)
    projections = {wf: base.task_encoding(text) for wf, text in data["task_text"].items()}
    partition = {wf: "evaluation" if wf in measured else "training" for wf in data["task_text"]}
    results = {
        name: evaluate_choices(serving, items, projections, costs, partition, validation_only=False)
        for name, serving in (("old_path", old), ("crossfit_path", previous), ("ranked_path", model))
    }
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "results": results, "outcomes": outcomes, "baseline": baseline,
        "decision_score_scope": DECISION_SCORE_SCOPE,
        "slope": slope, "intercept": intercept, "final_training": evidence,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                args.dataset, args.previous_model, args.previous_audit,
                args.crossfit_dir / "evaluation.json",
                cache_path, Path(__file__),
            )
        },
        "fixed18_used_for_selection": False,
        "cached_features_and_development_population_reused": True,
        "no_future_service_or_JCT_simulation": True,
        "CPU_elapsed_s": time.monotonic() - started, "GPU_action": False,
    })
    print(json.dumps({
        "selected": selected, "outcomes": {
            name: {key: value for key, value in values.items() if key != "per_workflow"}
            for name, values in outcomes.items()
        }, "results": {
            name: {key: value for key, value in values.items() if key != "decisions"}
            for name, values in results.items()
        }, "CPU_elapsed_s": time.monotonic() - started,
    }), flush=True)


if __name__ == "__main__":
    main()
