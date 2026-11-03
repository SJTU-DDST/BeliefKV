"""Test conditional work/call regressors on frozen task representations and observed progress."""

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_progress_tree import FEATURES, TreeProjection, features
from beliefkv.predictor.workflow_task_encoder import task_features
from beliefkv.predictor.workflow_work import R5_FEATURES, WorkflowWorkModel, before_observed_work
from beliefkv.predictor.workflow_work_readout import representation, representation_fingerprint
from beliefkv.predictor.workflow_work_training import remaining_call_counts
from scripts.calibrate_workflow_probability_bias import PredictionTable
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_frozen_readout import cost_priors, observation_weights
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_task_diversity import training_population
from scripts.train_workflow_work import probabilities, write


KIND = "offline_conditional_workflow_tree_readout_v1"


def tree_inputs(base, projection, observed, task_structure):
    """Only arguments available at the current observed boundary define a tree input."""
    import numpy as np

    return np.concatenate((
        features(observed, task_structure), representation(base, projection, observed),
    ))


def matrix(base, rows):
    import numpy as np

    texts, result, valid = {}, [], []
    width = len(FEATURES) + base.arrays["hidden_weight"].shape[1]
    for row in rows:
        text = row.get("task_text", row["text"])
        if text not in texts:
            texts[text] = base.task_projection(text), task_features(text)
        projection, structure = texts[text]
        valid.append(projection is not None)
        result.append(
            tree_inputs(base, projection, row["features"], structure)
            if projection is not None else np.zeros(width)
        )
    return np.asarray(result), np.asarray(valid)


def log_work(prediction, observed, target, priors, strength=4.):
    import numpy as np

    prediction = np.asarray(prediction, dtype=float)
    if target == "work":
        return prediction[:, 0]
    if target != "branch_calls":
        raise ValueError("unknown conditional workflow tree target")
    observed = np.asarray(observed, dtype=float)
    costs = []
    for owner in ("root", "child"):
        count = observed[:, R5_FEATURES.index(owner + "_completed_calls")]
        work = .01 * observed[:, R5_FEATURES.index(owner + "_input_tokens")]
        work += observed[:, R5_FEATURES.index(owner + "_output_tokens")]
        costs.append((work + strength * priors[owner]) / (count + strength))
    return np.log1p((np.expm1(np.clip(prediction, 0., 20.)) * np.column_stack(costs)).sum(1))


def work_probabilities(log_mean, thresholds, offset, sigma):
    import numpy as np
    from scipy.special import ndtr

    cumulative = ndtr((np.log1p(thresholds)[None, :] - log_mean[:, None] - offset) / sigma)
    return np.column_stack((cumulative[:, 0], cumulative[:, 1] - cumulative[:, 0], 1. - cumulative[:, 1]))


def fit_trees(x, y, rows, *, leaves, rounds=96):
    import lightgbm as lgb
    import numpy as np

    if y.ndim != 2 or len(x) != len(y) or len(rows) != len(x):
        raise ValueError("conditional tree targets differ from their complete training observations")
    weights = observation_weights(rows) * len(rows)
    settings = {
        "objective": "regression", "num_threads": 1, "deterministic": True,
        "force_col_wise": True, "seed": 21, "verbosity": -1, "num_leaves": leaves,
        "max_depth": 5, "learning_rate": .05, "min_data_in_leaf": 100,
        "lambda_l2": 20., "feature_fraction": 1., "bagging_fraction": 1.,
    }
    models = [lgb.train(
        settings, lgb.Dataset(x, label=y[:, channel], weight=weights), num_boost_round=rounds,
    ) for channel in range(y.shape[1])]
    exported = [model.model_to_string() for model in models]
    restored = [lgb.Booster(model_str=value) for value in exported]
    sample = np.unique(np.linspace(0, len(x) - 1, min(64, len(x)), dtype=int))
    errors = [
        float(np.max(np.abs(
            original.predict(x[sample], num_threads=1) - loaded.predict(x[sample], num_threads=1),
        ))) for original, loaded in zip(models, restored)
    ]
    if max(errors) > 1e-12:
        raise ValueError("serialized conditional tree predictions differ from training")
    return models, {
        "models": exported, "parameters": settings, "rounds": rounds,
        "library_version": lgb.__version__, "serialized_prediction_max_abs_error": max(errors),
    }


class ConditionalTreeCandidate:
    """Explicit CPU adapter; its outer artifact is not a serving-model schema."""

    def __init__(self, base, head, blend):
        import lightgbm as lgb

        if (
            head.get("kind") != KIND or head.get("target") not in ("work", "branch_calls")
            or head.get("representation_sha256") != representation_fingerprint(base)
            or head.get("features") != list(FEATURES) + [
                "frozen_hidden_" + str(index) for index in range(base.arrays["hidden_weight"].shape[1])
            ]
            or not math.isfinite(blend) or not 0 < blend <= 1
            or not math.isfinite(head["calibration_offset"])
            or not math.isfinite(head["calibration_sigma"]) or head["calibration_sigma"] <= 0
            or len(head["models"]) != (1 if head["target"] == "work" else 2)
            or any(not math.isfinite(head["per_call_prior"][owner]) or head["per_call_prior"][owner] <= 0
                   for owner in ("root", "child"))
        ):
            raise ValueError("invalid offline conditional workflow tree readout")
        self.base, self.head, self.blend = base, head, blend
        self.trees = [lgb.Booster(model_str=value) for value in head["models"]]
        if any(model.num_feature() != len(head["features"]) for model in self.trees):
            raise ValueError("conditional tree input dimensions differ")

    def __getattr__(self, name):
        return getattr(self.base, name)

    def task_encoding(self, text):
        projection, coverage = self.base.task_encoding(text)
        return (
            TreeProjection(projection, tuple(task_features(text))) if projection is not None else None,
            coverage,
        )

    def task_projection(self, text):
        return self.task_encoding(text)[0]

    def predicted_log_work(self, x, observed):
        import numpy as np

        predicted = np.column_stack([model.predict(x, num_threads=1) for model in self.trees])
        return log_work(predicted, observed, self.head["target"], self.head["per_call_prior"])

    def predict(self, projection, observed):
        import numpy as np

        if not isinstance(projection, TreeProjection):
            raise ValueError("conditional trees require their cached task representation")
        original = self.base.predict(projection.progress_projection, observed)
        if before_observed_work(observed):
            return original
        x = tree_inputs(self.base, projection.progress_projection, observed, projection.task_structure)
        mean = self.predicted_log_work(np.asarray([x]), [observed])
        own = work_probabilities(
            mean, self.thresholds, self.head["calibration_offset"], self.head["calibration_sigma"],
        )[0]
        return tuple(float((1. - self.blend) * old + self.blend * new)
                     for old, new in zip(original, own))


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "additional-training-data",
                 "baseline-model", "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve completed or rejected conditional tree studies")
    paths = (
        args.dataset, args.cohort, args.previous_training_data, args.additional_training_data,
        args.baseline_model, args.baseline_evaluation,
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, previous, additional, baseline, evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    dataset_sha = pins[str(args.dataset.resolve())]
    if (
        evaluation["model_sha256"] != pins[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != dataset_sha
    ):
        raise ValueError("conditional trees require the exact retained model and causal dataset")
    training, calibration, development, population = training_population(
        data, cohort, previous, additional, evaluation, dataset_sha,
    )
    for dataset in (data, previous, additional):
        for path, digest in dataset["sources_sha256"].items():
            if path in pins and pins[path] != digest:
                raise ValueError("conditional tree source identities conflict")
            pins[path] = digest
    for path in (
        Path(__file__), ROOT / "beliefkv/predictor/workflow_work.py",
        ROOT / "beliefkv/predictor/workflow_work_readout.py",
        ROOT / "beliefkv/predictor/workflow_progress_tree.py",
        ROOT / "beliefkv/predictor/workflow_work_training.py",
        ROOT / "scripts/train_workflow_task_diversity.py",
        ROOT / "scripts/train_workflow_frozen_readout.py",
        ROOT / "scripts/train_workflow_ready_ranking.py",
        ROOT / "scripts/improve_workflow_work_distribution.py",
    ):
        pins[str(path.resolve())] = sha256_file(path)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a conditional tree input changed")
    settings = [(target, leaves, blend)
                for target in ("work", "branch_calls") for leaves in (8, 16) for blend in (.5, 1.)]
    write(args.output_dir / "plan.json", {
        "settings": settings, **population, "boost_rounds": 96,
        "initial_predictions_unchanged": True, "remaining_calls_are_offline_labels_only": True,
        "inputs": "explicit_task_structure_frozen_representation_observed_R5_counters_and_ratios",
        "weights": "project_task_equal_three_quarters_ready", "observed_cost_prior_strength": 4.,
        "selection": "unchanged_calibration_support_gates_then_existing_ready_ranking_score",
        "development_labels_used_for_selection": False, "inputs_sha256": pins, "GPU_action": False,
    })
    base = WorkflowWorkModel(baseline)
    x, valid = matrix(base, training)
    calls, _totals = remaining_call_counts(training)
    targets = {
        "work": np.log1p([row["remaining_work"] for row in training])[:, None],
        "branch_calls": np.log1p(calls),
    }
    chosen = np.asarray([use and not before_observed_work(row["features"])
                         for row, use in zip(training, valid)])
    selected_rows = [row for row, use in zip(training, chosen) if use]
    priors = cost_priors(training)
    cal_x, cal_valid = matrix(base, calibration)
    cal_observed = np.asarray([row["features"] for row in calibration])
    cal_chosen = np.asarray([use and not before_observed_work(row["features"])
                             for row, use in zip(calibration, cal_valid)])
    cal_rows = [row for row, use in zip(calibration, cal_chosen) if use]
    cal_weights = observation_weights(cal_rows)
    cal_targets = np.log1p([row["remaining_work"] for row in cal_rows])
    base_cal = probabilities(base, calibration)
    base_values = metrics(PredictionTable(base, calibration, base_cal), calibration)
    base_reasons = calibration_rejections(base_values, baseline)
    summaries = {"retained": {
        "calibration": base_values, "selection_score": score(base_values),
        "decision_eligible": not base_reasons, "decision_rejection_reasons": base_reasons,
    }}
    heads = {}
    for target in targets:
        for leaves in (8, 16):
            started = time.monotonic()
            name = target + "_leaves" + str(leaves)
            print("fitting_conditional_tree: " + name, flush=True)
            trees, fitted = fit_trees(x[chosen], targets[target][chosen], selected_rows, leaves=leaves)
            raw_cal = np.column_stack([model.predict(cal_x, num_threads=1) for model in trees])
            mean = log_work(raw_cal, cal_observed, target, priors)
            residual = cal_targets - mean[cal_chosen]
            offset = float(np.sum(cal_weights * residual))
            sigma = max(.25, float(np.sqrt(np.sum(cal_weights * (residual - offset) ** 2))))
            head = {
                **fitted, "kind": KIND, "target": target,
                "features": list(FEATURES) + [
                    "frozen_hidden_" + str(index) for index in range(base.arrays["hidden_weight"].shape[1])
                ],
                "representation_sha256": representation_fingerprint(base),
                "per_call_prior": priors, "calibration_offset": offset, "calibration_sigma": sigma,
            }
            heads[name] = head
            own = work_probabilities(mean, base.thresholds, offset, sigma)
            for blend in (.5, 1.):
                candidate_name = name + "_blend" + str(blend)
                combined = base_cal.copy()
                combined[cal_chosen] = (
                    (1. - blend) * base_cal[cal_chosen] + blend * own[cal_chosen]
                )
                values = metrics(PredictionTable(base, calibration, combined), calibration)
                reasons = calibration_rejections(values, baseline)
                summaries[candidate_name] = {
                    "head": name, "blend": blend, "calibration": values,
                    "selection_score": score(values), "decision_eligible": not reasons,
                    "decision_rejection_reasons": reasons, "fit_and_calibration_seconds": time.monotonic() - started,
                }
                write(args.output_dir / candidate_name / "calibration.json", summaries[candidate_name])
                print(json.dumps({
                    "candidate": candidate_name, "score": score(values), "rejections": reasons,
                    "ready_ranking": values["ready_cross_progress_pairs"]["accuracy"],
                    "long_to_short": values["all"]["long_to_short_rate"],
                }), flush=True)
            write(args.output_dir / name / "head.json", head)
    selected = max((name for name, row in summaries.items() if row["decision_eligible"]),
                   key=lambda name: summaries[name]["selection_score"])
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {"retained": metrics(base, development)}
    exported = deepcopy(baseline)
    if selected != "retained":
        choice = summaries[selected]
        candidate = ConditionalTreeCandidate(base, heads[choice["head"]], choice["blend"])
        dev_x, dev_valid = matrix(base, development)
        dev_observed = np.asarray([row["features"] for row in development])
        dev_chosen = np.asarray([use and not before_observed_work(row["features"])
                                 for row, use in zip(development, dev_valid)])
        original = probabilities(base, development)
        mean = candidate.predicted_log_work(dev_x, dev_observed)
        own = work_probabilities(
            mean, base.thresholds, candidate.head["calibration_offset"], candidate.head["calibration_sigma"],
        )
        combined = original.copy()
        combined[dev_chosen] = (
            (1. - candidate.blend) * original[dev_chosen] + candidate.blend * own[dev_chosen]
        )
        held_out[selected] = metrics(PredictionTable(base, development, combined), development)
        sample = np.unique(np.linspace(0, len(development) - 1, min(64, len(development)), dtype=int))
        for index in sample:
            row = development[index]
            projection = candidate.task_projection(row.get("task_text", row["text"]))
            if projection is not None and not np.allclose(
                candidate.predict(projection, row["features"]), combined[index], atol=1e-12, rtol=0.,
            ):
                raise ValueError("single-state tree predictions differ from batch evaluation")
        exported = {
            "kind": KIND, "base_model": baseline, "head": candidate.head, "blend": candidate.blend,
            "metadata": {"decision_eligible": False, "reason": "offline_tree_readout_not_serving_qualified"},
        }
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a conditional tree input changed during evaluation")
    write(args.output_dir / "model.json", exported)
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries, **population,
        "inputs_sha256": pins, "model_sha256": sha256_file(args.output_dir / "model.json"),
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
