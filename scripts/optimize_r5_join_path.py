"""Select a bounded path head on held-out JOIN-aware admission decisions."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
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
from beliefkv.policy.workflow_combination import CombinationCosts, CombinationResources, _evaluate
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from beliefkv.predictor.workflow_completion_path import (
    WORK_PRIOR_KIND, RESIDUAL_KIND, feature_names, model_work_log_seconds,
    path_features, work_forecast_fingerprint,
)
from beliefkv.predictor.workflow_continuous_work import log_work_weight
from beliefkv.predictor.workflow_work import WorkflowWorkModel, work_prediction_features
from beliefkv.predictor.workflow_work_distribution import support_status
from beliefkv.predictor.workflow_work_readout import representation
from scripts.audit_r5_shortest_contract import candidate
from scripts.train_workflow_work import write
from scripts.train_workflow_work_progress_trees import portable_tree


def joint_difference(candidates, actual, oracle):
    """Measure exact visible JOIN progress, including multiple ready siblings."""
    groups = defaultdict(list)
    actual, oracle = set(actual), set(oracle)
    for row in candidates:
        if row.unfinished_members > 1:
            groups[row.workflow, row.group].append(row)
    result = Counter({
        "JOIN_groups": len(groups),
        "multi_ready_sibling_groups": sum(len(peers) > 1 for peers in groups.values()),
    })
    for peers in groups.values():
        identities = {row.identity for row in peers}
        result["different_JOIN_frontiers"] += (identities & actual) != (identities & oracle)
        missed = identities <= oracle and not identities <= actual
        result["missed_oracle_full_visible_JOIN_frontiers"] += missed
        result["missed_oracle_multi_ready_sibling_groups"] += missed and len(identities) > 1
        result["partial_ready_sibling_groups"] += (
            len(identities) > 1 and bool(identities & actual) and not identities <= actual
        )
    return dict(result)


def prepared_decisions(data, costs):
    result = []
    for row in data["decisions"]:
        candidates = [candidate(raw) for raw in row["candidates"]]
        by_id = {value.identity: value for value in candidates}
        required = row["decision"].get("required_reference_members", ())
        if (
            row["resources"] is None or not set(row["decision"]["selected"]) <= by_id.keys()
            or not set(required) <= by_id.keys()
        ):
            continue
        resources = CombinationResources(**row["resources"])
        forced = [by_id[identity] for identity in required]
        feasible = [
            value for value in candidates if value.identity not in required
            and _evaluate([*forced, value], resources, costs) is not None
        ]
        competition = (
            len({value.workflow for value in feasible}) > 1
            and _evaluate([*forced, *feasible], resources, costs) is None
        )
        result.append({
            "raw": row, "candidates": candidates, "resources": resources,
            "required": required, "feasible_workflows": sorted({value.workflow for value in feasible}),
            "competition": competition,
        })
    return result


def choose(item, mapping, costs):
    result = choose_shortest_work_first(
        [replace(value, weight=mapping[value.workflow]) for value in item["candidates"]],
        item["raw"]["decision"].get("reference", ()), item["resources"], costs,
        required=item["required"], strict_remaining_work=True, clock=lambda: 0,
    )
    return result["selected"]


def selection_key(metrics):
    counts = metrics["calibration_whole_windows"]
    pairs = metrics["calibration_pairs"]
    if not counts["boundaries"] or not pairs["pairs"]:
        raise ValueError("require real held-out calibration choices and comparable workflow pairs")
    return (
        counts.get("different_JOIN_frontiers", 0),
        counts["set_differences"],
        (pairs["wrong"] + .5 * pairs["ties"]) / pairs["pairs"],
        metrics["calibration_mean_workflow_absolute_log_error"],
    )


def validation_windows(items, partition):
    # Full-window comparisons never read any fixed18 target.
    return [
        item for item in items if item["competition"]
        and "evaluation" not in {partition[value.workflow] for value in item["candidates"]}
        and any(partition[value.workflow] == "calibration" for value in item["candidates"])
    ]


def evaluate_choices(model, items, projections, costs, partition, *, validation_only):
    counts = Counter({"boundaries": 0})
    measured, joint_windows, common = Counter({"boundaries": 0}), Counter({"boundaries": 0}), Counter({"boundaries": 0})
    pair_counts = Counter({"pairs": 0, "wrong": 0, "ties": 0})
    errors_by_pair = defaultdict(list)
    details = []
    profile = model.completion_path.raw
    for item in items:
        if not item["competition"]:
            continue
        row = item["raw"]
        workflows = {value.workflow for value in item["candidates"]}
        allowed = {wf for wf in workflows if partition[wf] != "evaluation"}
        whole = not validation_only or not workflows - allowed
        if validation_only and not any(partition[wf] == "calibration" for wf in allowed):
            continue
        forecasts, truth = {}, {}
        for wf in workflows if whole else allowed:
            state = row["states"][wf]
            # Targets are only accessed after the held-out filter above.
            truth[wf] = 1. / (1. + state["path"][0] / profile["reference_path_s"])
            projection, coverage = projections[wf]
            _, output = model.predict(
                projection, state["features"], with_work=True, path_timing=state["timing"],
            )
            supported = support_status(model, state["features"], coverage) == "calibrated_distribution"
            forecasts[wf] = (
                1. / (1. + output["remaining_path_s"] / profile["reference_path_s"])
                if supported and "remaining_path_s" in output else
                log_work_weight(
                    output["log1p_remaining_work"],
                    profile["reference_path_s"] / profile["model_seconds_per_work_token"],
                ) if supported else .5
            )
        for left, right in combinations(item["feasible_workflows"], 2):
            if left not in allowed or right not in allowed:
                continue
            if partition[left] != "calibration" and partition[right] != "calibration":
                continue
            difference = truth[left] - truth[right]
            if difference == 0:
                continue
            predicted = forecasts[left] - forecasts[right]
            tied, wrong = predicted == 0, predicted * difference < 0
            pair_counts.update(pairs=1, ties=tied, wrong=wrong)
            errors_by_pair[left, right].append(.5 if tied else float(wrong))
        if not whole:
            continue
        if validation_only and not any(partition[wf] == "calibration" for wf in workflows):
            continue
        selected, oracle = choose(item, forecasts, costs), choose(item, truth, costs)
        joint = joint_difference(item["candidates"], selected, oracle)
        value = {
            "boundaries": 1, "set_differences": set(selected) != set(oracle), **joint,
        }
        counts.update(value)
        if joint["JOIN_groups"]:
            joint_windows.update(value)
        if any(partition[wf] == "evaluation" for wf in workflows):
            measured.update(value)
        if item.get("in_common_window", False):
            common.update(value)
        if not validation_only:
            details.append({
                "monotonic_s": row["monotonic_s"], "source": row["source"],
                "selected": selected, "oracle": oracle, "joint": joint,
                "measured_present": any(partition[wf] == "evaluation" for wf in workflows),
            })
    return {
        "calibration_whole_windows" if validation_only else "all_competitive_windows": dict(counts),
        "JOIN_competitive_windows": dict(joint_windows),
        "measured_competitive_windows": dict(measured), "common_competitive_windows": dict(common),
        "calibration_pairs": {
            **pair_counts,
            "distinct_workflow_pairs": len(errors_by_pair),
            "equal_workflow_pair_error": (
                sum(sum(errors) / len(errors) for errors in errors_by_pair.values()) / len(errors_by_pair)
                if errors_by_pair else None
            ),
        },
        **({"decisions": details} if not validation_only else {}),
    }


def equal_workflow_error(rows, scores, targets, partition, split):
    grouped = defaultdict(list)
    for index, row in enumerate(rows):
        if partition[row["workflow"]] == split:
            grouped[row["workflow"]].append(abs(float(scores[index]) - float(targets[index])))
    return sum(sum(values) / len(values) for values in grouped.values()) / len(grouped)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "previous-model", "previous-evaluation", "previous-audit", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--family", choices=("absolute", "residual"), default="absolute")
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("require a fresh CPU-only study")
    args.output_dir.mkdir(parents=True)
    import lightgbm as lgb
    import numpy as np

    started = time.monotonic()
    data = json.loads(args.dataset.read_bytes())
    prior = WorkflowWorkModel.load(args.previous_model, sha256_file(args.previous_model))
    previous_evaluation = json.loads(args.previous_evaluation.read_bytes())
    previous_audit = json.loads(args.previous_audit.read_bytes())
    partition = previous_evaluation["split"]
    if (
        prior.completion_path.raw["training_source"]["sha256"] != sha256_file(args.dataset)
        or previous_evaluation["model_sha256"] != sha256_file(args.previous_model)
        or {wf for wf, split in partition.items() if split == "evaluation"}
        != set(data["cohort"]["measured_workflow_ids"])
        or set(partition) != set(data["task_text"])
    ):
        raise ValueError("preserve the prior dataset, head and complete fixed18 holdout")
    base = WorkflowWorkModel.load(Path(data["base_model"]), data["base_sha256"])
    projections = {wf: base.task_encoding(text) for wf, text in data["task_text"].items()}
    costs = CombinationCosts(**base.raw["admission_cost_profile"]["costs"])
    items = prepared_decisions(data, costs)
    common = previous_audit["windows"]["common_throughput"]
    for item in items:
        item["in_common_window"] = common[0] <= item["raw"]["monotonic_s"] <= common[1]
    rows = data["rows"]
    settings = [
        ("existing_path_v1", None),
        ("work_prior_64", {"rounds": 64, "num_leaves": 8, "max_depth": 4, "objective": "regression"}),
        ("work_prior_128", {"rounds": 128, "num_leaves": 16, "max_depth": 5, "objective": "regression"}),
        ("work_prior_128_l1", {"rounds": 128, "num_leaves": 16, "max_depth": 5, "objective": "regression_l1"}),
    ]
    if args.family == "residual":
        settings = [
            (name.replace("work_prior_", "residual_"), configuration)
            for name, configuration in settings
        ]
    write(args.output_dir / "plan.json", {
        "variants": settings, "partition": partition, "family": args.family,
        "selection_order": [
            "calibration_visible_JOIN_frontier_differences", "calibration_selected_set_differences",
            "calibration_workflow_pair_error", "calibration_equal_workflow_log_error",
        ],
        "whole_window_validation_excludes_all_evaluation_candidates": True,
        "pair_validation_excludes_all_evaluation_targets": True,
        "fixed18_targets_used_for_model_selection": False,
        "complete_competitive_calibration_windows": len(validation_windows(items, partition)),
        "GPU_action": False,
    })
    x, model_time_logs, baseline_calibration_logs = [], [], []
    for row in rows:
        projection = projections[row["workflow"]][0]
        observed = row["features"]
        _, work = base.predict(projection, observed, with_work=True)
        hidden = representation(base, projection.progress_projection, observed)
        transformed = work_prediction_features(observed, task_only_before_work=base.task_only_before_work)
        x.append(path_features(
            transformed, projection.task_structure, hidden, row["timing"], work_forecast=work,
        ))
        model_time_logs.append(model_work_log_seconds(
            work, prior.completion_path.raw["model_seconds_per_work_token"],
        ))
        if partition[row["workflow"]] == "calibration":
            estimate = prior.completion_path.estimate(transformed, projection.task_structure, hidden, row["timing"])
            baseline_calibration_logs.append(
                math.log1p(estimate["remaining_path_s"]) if "remaining_path_s" in estimate else
                math.log1p(work["remaining_work_point"] * prior.completion_path.raw["model_seconds_per_work_token"])
            )
    x = np.asarray(x)
    model_time_logs = np.asarray(model_time_logs)
    targets = np.log1p([row["path"][0] for row in rows])
    split = np.asarray([partition[row["workflow"]] for row in rows])
    train, calibration = split == "training", split == "calibration"
    counts = Counter(row["workflow"] for row in rows if partition[row["workflow"]] == "training")
    weights = np.asarray([
        1. / counts[row["workflow"]] for row in rows if partition[row["workflow"]] == "training"
    ])
    weights *= len(weights) / weights.sum()
    calibration_counts = Counter(
        row["workflow"] for row in rows if partition[row["workflow"]] == "calibration"
    )
    calibration_weights = np.asarray([
        1. / calibration_counts[row["workflow"]]
        for row in rows if partition[row["workflow"]] == "calibration"
    ])
    print(json.dumps({"stage": "features_ready", "rows": len(rows), "dimensions": x.shape[1]}), flush=True)
    models, validation, predictions = {"existing_path_v1": prior}, {}, {}
    for name, configuration in settings:
        if configuration is None:
            model = prior
            calibration_rows = [row for row in rows if partition[row["workflow"]] == "calibration"]
            validation_error = equal_workflow_error(
                calibration_rows, baseline_calibration_logs, targets[calibration], partition, "calibration",
            )
        else:
            parameters = {
                "num_threads": 1, "deterministic": True, "force_col_wise": True,
                "seed": 21, "verbosity": -1, "learning_rate": .05,
                "min_data_in_leaf": 80, "lambda_l2": 10.,
                **{key: value for key, value in configuration.items() if key != "rounds"},
            }
            trained = lgb.train(
                parameters, lgb.Dataset(
                    x[train],
                    label=targets[train] - model_time_logs[train] if args.family == "residual" else targets[train],
                    weight=weights,
                ),
                num_boost_round=configuration["rounds"],
            )
            tree_score = trained.predict(x, raw_score=True, num_threads=1)
            score = tree_score + model_time_logs if args.family == "residual" else tree_score
            offset = float(np.average(
                targets[calibration] - score[calibration], weights=calibration_weights,
            ))
            raw = deepcopy(base.raw)
            raw["completion_path_readout"] = {
                **deepcopy(prior.completion_path.raw),
                "kind": RESIDUAL_KIND if args.family == "residual" else WORK_PRIOR_KIND,
                "features": list(feature_names(base, with_work_prior=True)),
                "work_forecast_sha256": work_forecast_fingerprint(base),
                "trees": [portable_tree(tree["tree_structure"]) for tree in trained.dump_model()["tree_info"]],
                "maximum_depth": configuration["max_depth"], "calibration_offset": offset,
            }
            model = WorkflowWorkModel(raw)
            errors = [
                abs(model.completion_path.tree.score(x[index]) - tree_score[index])
                for index in np.unique(np.linspace(0, len(rows) - 1, min(256, len(rows)), dtype=int))
            ]
            if max(errors) > 1e-9:
                raise ValueError("portable work-prior head changed its fitted prediction")
            write(args.output_dir / name / "model.json", raw)
            models[name], predictions[name] = model, score + offset
            validation_error = equal_workflow_error(rows, score + offset, targets, partition, "calibration")
        validation[name] = {
            **evaluate_choices(model, items, projections, costs, partition, validation_only=True),
            "calibration_mean_workflow_absolute_log_error": validation_error,
        }
        print(json.dumps({
            "stage": "validation", "variant": name, "selection_key": selection_key(validation[name]),
        }), flush=True)
    selected = min(validation, key=lambda name: selection_key(validation[name]))
    # Persist the choice before using evaluation targets for final diagnostics.
    write(args.output_dir / "selection.json", {
        "selected": selected, "validation": validation,
        "fixed18_targets_used_for_model_selection": False,
        "selection_key": selection_key(validation[selected]), "GPU_action": False,
    })
    final = {}
    for name, model in models.items():
        final[name] = evaluate_choices(model, items, projections, costs, partition, validation_only=False)
        if name in predictions:
            final[name]["mean_workflow_absolute_log_error"] = {
                part: equal_workflow_error(rows, predictions[name], targets, partition, part)
                for part in ("training", "calibration", "evaluation")
            }
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "variants": final,
        "model_paths": {
            name: str(args.previous_model.resolve()) if name == "existing_path_v1"
            else str((args.output_dir / name / "model.json").resolve())
            for name in models
        },
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                args.dataset, args.previous_model, args.previous_evaluation, args.previous_audit,
                Path(__file__), ROOT / "beliefkv/predictor/workflow_completion_path.py",
            )
        },
        "fixed18_targets_used_for_model_selection": False,
        "base_representation_is_reused_development_not_unseen_project_generalization": True,
        "scope": "same_snapshot_planning_choices_not_future_service_transfer_or_JCT_simulation",
        "CPU_elapsed_s": time.monotonic() - started, "GPU_action": False,
    })
    print(json.dumps({
        "selected": selected, "variants": {
            name: {key: value for key, value in values.items() if key != "decisions"}
            for name, values in final.items()
        }, "CPU_elapsed_s": time.monotonic() - started,
    }), flush=True)


if __name__ == "__main__":
    main()
