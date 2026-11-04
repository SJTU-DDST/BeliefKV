"""Study decision-state adaptation without moving any complete workflow across splits."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import fit_training_reference
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts.train_workflow_branch_call_trees import matrix
from scripts.train_workflow_task_diversity import training_population
from scripts.train_workflow_utility_readout import baseline_predictions, sampled, utility_metrics
from scripts.train_workflow_work import write


def decision_rows(partitions, audit):
    aliases = {}
    for split, rows in partitions.items():
        for row in rows:
            identity = row["logical_workflow_id"]
            if identity in aliases and aliases[identity][0] != split:
                raise ValueError("one logical workflow cannot cross predictor partitions")
            aliases[identity] = split, row
    result = {name: [] for name in partitions}
    seen = {}
    for decision in audit["decisions"]:
        if not decision["planning_capacity_competition"]:
            continue
        for candidate in decision["ordinary_workflows"]:
            identity = candidate["workflow"]
            if identity not in aliases:
                raise ValueError("the decision state has no complete-workflow split binding")
            split, original = aliases[identity]
            key = identity, tuple(candidate["features"])
            remaining = candidate["remaining_model_work"]
            if key in seen:
                if abs(seen[key] - remaining) > 1e-6:
                    raise ValueError("one observed input has conflicting remaining-work labels")
                continue
            seen[key] = remaining
            result[split].append({
                **original, "snapshot": len(result[split]) + 1,
                "features": candidate["features"], "remaining_work": remaining,
                "event": "actual_ready_paused_competition",
                "decision_state": True,
            })
    return result


def adapted_weights(rows):
    import numpy as np

    counts = Counter((row["workflow"], bool(row.get("decision_state"))) for row in rows)
    workflows = {row["workflow"] for row in rows}
    return np.asarray([
        1. / len(workflows) / counts[row["workflow"], bool(row.get("decision_state"))]
        / (2 if all((row["workflow"], source) in counts for source in (False, True)) else 1)
        for row in rows
    ])


def main():
    import lightgbm as lgb
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "additional-training-data",
                 "model", "model-evaluation", "decision-audit", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output_dir.exists():
        raise ValueError("hide CUDA and preserve prior decision-state adaptations")
    paths = tuple(getattr(args, name.replace("-", "_")) for name in (
        "dataset", "cohort", "previous-training-data", "additional-training-data",
        "model", "model-evaluation", "decision-audit",
    ))
    source_paths = (
        Path(__file__), ROOT / "scripts/train_workflow_utility_readout.py",
        ROOT / "scripts/train_workflow_branch_call_trees.py",
        ROOT / "scripts/train_workflow_task_diversity.py",
        ROOT / "beliefkv/predictor/workflow_work.py",
        ROOT / "beliefkv/predictor/workflow_continuous_work.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in (*paths, *source_paths)}
    data, cohort, previous, additional, raw, evaluation, decisions = (
        json.loads(path.read_bytes()) for path in paths
    )
    if (
        evaluation["model_sha256"] != pins[str(args.model.resolve())]
        or decisions["inputs_sha256"].get(str(args.model.resolve())) != pins[str(args.model.resolve())]
        or decisions["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
    ):
        raise ValueError("decision adaptation requires exact source-bound model and work labels")
    population_evaluation = {**evaluation, "expanded_split": evaluation["previous_expanded_split"]}
    train, calibration, test, population = training_population(
        data, cohort, previous, additional, population_evaluation, pins[str(args.dataset.resolve())],
    )
    live = decision_rows({"training": train, "calibration": calibration, "development": test}, decisions)
    if any(len({row["workflow"] for row in values}) < 2 for values in live.values()):
        raise ValueError("each decision-state split needs multiple independent workflow identities")
    model = WorkflowWorkModel(raw)
    base = WorkflowWorkModel({
        key: value for key, value in raw.items() if key != "conditional_progress_tree_head"
    })
    contract = fit_training_reference(train, model.thresholds)
    train = sampled(train, 128) + live["training"]
    calibration, test = sampled(calibration, 64), sampled(test, 64)
    write(args.output_dir / "plan.json", {
        "inputs_sha256": pins, "population": population, "reference": contract,
        "complete_workflow_splits_unchanged": True,
        "observed_decision_states": {
            split: {"rows": len(rows), "workflows": len({row["workflow"] for row in rows})}
            for split, rows in live.items()
        },
        "source_mass_per_workflow": "half_natural_half_decision_when_both_exist",
        "calibration_gate": "natural_ready_accuracy_at_most_0.5_percentage_point_below_reference",
        "selection": "mean_natural_and_decision_calibration_ready_pair_accuracy",
        "candidate_selection_used_development_labels": False,
        "decision_sampling_depends_on_prior_shared_workload_execution": True,
        "unseen_workload_generalization_claimed": False, "GPU_action": False,
    })
    x, valid = matrix(base, train)
    if not valid.all():
        raise ValueError("training decision states lack their bounded task encoding")
    target = np.asarray([
        contract["reference_work"] / (contract["reference_work"] + row["remaining_work"])
        for row in train
    ])
    populations = {"natural": calibration, "decision": live["calibration"]}
    references, xs, validities, initials = {}, {}, {}, {}
    for name, rows in populations.items():
        xs[name], validities[name] = matrix(base, rows)
        _binned, references[name], initials[name] = baseline_predictions(model, rows, contract)
    summaries = {"retained_continuous": {
        name: utility_metrics(rows, references[name], contract["reference_work"])
        for name, rows in populations.items()
    }}
    heads = {}
    for leaves in (8, 16):
        parameters = {
            "objective": "regression", "num_threads": 1, "deterministic": True,
            "force_col_wise": True, "seed": 21, "verbosity": -1, "num_leaves": leaves,
            "max_depth": 5, "learning_rate": .05, "min_data_in_leaf": 80, "lambda_l2": 20.,
        }
        fitted = lgb.train(parameters, lgb.Dataset(
            x, label=target, weight=adapted_weights(train) * len(train),
        ), num_boost_round=96)
        for blend in (.5, 1.):
            name = f"decision_leaves{leaves}_blend{blend}"
            heads[name] = fitted, blend
            summaries[name] = {}
            for scope, rows in populations.items():
                forecast = np.clip(fitted.predict(xs[scope], num_threads=1), 0., 1.)
                forecast = np.where(initials[scope] | ~validities[scope], references[scope], forecast)
                forecast = blend * forecast + (1 - blend) * references[scope]
                summaries[name][scope] = utility_metrics(rows, forecast, contract["reference_work"])
            write(args.output_dir / name / "head.json", {
                "model": fitted.model_to_string(), "blend": blend, "parameters": parameters,
                "kind": "offline_decision_state_workflow_utility_v1", "decision_eligible": False,
            })
    reference_ready = summaries["retained_continuous"]["natural"]["ready"]["pair_equal_weight_accuracy"]
    eligible = [
        name for name, values in summaries.items()
        if values["natural"]["ready"]["pair_equal_weight_accuracy"] >= reference_ready - .005
    ]
    selected = max(eligible, key=lambda name: (
        sum(summaries[name][scope]["ready"]["pair_equal_weight_accuracy"] for scope in populations) / 2,
        name == "retained_continuous",
    ))
    write(args.output_dir / "selection.json", {
        "selected": selected, "eligible": eligible, "calibration": summaries,
        "candidate_selection_used_development_labels": False,
    })
    development = {}
    for scope, rows in (("natural", test), ("decision", live["development"])):
        binned, continuous, initial = baseline_predictions(model, rows, contract)
        selected_weights = continuous
        if selected in heads:
            fitted, blend = heads[selected]
            xx, valid = matrix(base, rows)
            forecast = np.clip(fitted.predict(xx, num_threads=1), 0., 1.)
            forecast = np.where(initial | ~valid, continuous, forecast)
            selected_weights = blend * forecast + (1 - blend) * continuous
        development[scope] = {
            name: utility_metrics(rows, values, contract["reference_work"])
            for name, values in (
                ("retained_binned", binned), ("retained_continuous", continuous),
                ("selected", selected_weights),
            )
        }
    result = {
        "selected": selected, "calibration": summaries, "development": development,
        "inputs_sha256": pins, "selection_used_development_labels": False,
        "development_has_been_reused": True, "GPU_action": False,
        "online_policy_changed": False, "whole_run_JCT_gain_proven": False,
    }
    if any(sha256_file(Path(name)) != digest for name, digest in pins.items()):
        raise ValueError("a decision-state model input changed during training or evaluation")
    write(args.output_dir / "evaluation.json", result)
    print(json.dumps({"selected": selected, "development": development}), flush=True)


if __name__ == "__main__":
    main()
