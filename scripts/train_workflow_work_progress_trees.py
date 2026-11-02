"""Compare small causal progress heads on a fixed complete-workflow split."""

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_progress_tree import (
    FEATURES, KIND, ProgressTreeHead, TreeProjection, features, project,
)
from beliefkv.predictor.workflow_task_encoder import task_features
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel
from beliefkv.predictor.workflow_work_training import ready_weights
from scripts.improve_workflow_work_distribution import calibration_rejections, selection_score, slices
from scripts.train_workflow_work import probabilities, write
from scripts.train_workflow_work_distribution import split_rows


class ExperimentalTreeWorkModel(WorkflowWorkModel):
    """CPU study adapter; failed tree candidates add no production inference path."""

    def __init__(self, raw):
        head = raw.get("progress_tree_head")
        if head is not None and (
            raw["schema"] != R5_SCHEMA or raw.get("observed_delegation_inputs")
            or any(raw.get(name) is not None for name in ("task_encoder", "total_work_prior"))
        ):
            raise ValueError("progress trees require the explicit-task R5 progress contract")
        super().__init__({key: value for key, value in raw.items() if key != "progress_tree_head"})
        self.progress_head = ProgressTreeHead(head) if head is not None else None

    def task_encoding(self, text):
        projection, coverage = super().task_encoding(text)
        if self.progress_head is not None and projection is not None:
            projection = project(projection, text)
        return projection, coverage

    def predict(self, projection, observed):
        if self.progress_head is None:
            return super().predict(projection, observed)
        if not isinstance(projection, TreeProjection):
            raise ValueError("progress tree prediction requires its cached task structure")
        base = super().predict(projection.progress_projection, observed)
        return self.progress_head.combine(base, observed, projection.task_structure, self.thresholds)


def matrix(rows):
    import numpy as np

    static = {}
    result = []
    for row in rows:
        text = row.get("task_text", row["text"])
        if text not in static:
            static[text] = task_features(text)
        result.append(features(row["features"], static[text]))
    return np.asarray(result)


def portable_tree(root):
    nodes = []

    def visit(node):
        index = len(nodes)
        nodes.append(None)
        if "leaf_value" in node:
            nodes[index] = [-1, 0., -1, -1, float(node["leaf_value"])]
        else:
            if node["decision_type"] != "<=":
                raise ValueError("progress heads require numeric tree splits")
            left, right = visit(node["left_child"]), visit(node["right_child"])
            nodes[index] = [int(node["split_feature"]), float(node["threshold"]), left, right, 0.]
        return index

    visit(root)
    return nodes


def fit_head(rows, *, objective, leaves, thresholds, rounds=64):
    import lightgbm as lgb
    import numpy as np

    counts = Counter(row["workflow"] for row in rows)
    base = np.asarray([1. / len(counts) / counts[row["workflow"]] for row in rows])
    weights = ready_weights(rows, base)
    # Keep whole-workflow mass equal, including initial and tool-blocked snapshots.
    weights *= len(rows) / weights.sum()
    x = matrix(rows)
    if objective == "multiclass":
        target = np.searchsorted(thresholds, [row["remaining_work"] for row in rows], side="right")
    else:
        target = np.log1p([row["remaining_work"] for row in rows])
    settings = {
        "objective": "multiclass" if objective == "multiclass" else "regression",
        "num_threads": 1, "deterministic": True, "force_col_wise": True,
        "seed": 21, "verbosity": -1, "num_leaves": leaves, "max_depth": 4,
        "learning_rate": .05, "min_data_in_leaf": 80, "lambda_l2": 10.,
        "feature_fraction": 1., "bagging_fraction": 1.,
    }
    if objective == "multiclass":
        settings["num_class"] = 3
    fitted = lgb.train(settings, lgb.Dataset(x, label=target, weight=weights), num_boost_round=rounds)
    raw = {
        "kind": KIND, "features": list(FEATURES), "objective": objective,
        "maximum_depth": 4, "blend": 1., "temperature": 1.,
        "regression_offset": 0., "regression_scale": 1.,
        "trees": [portable_tree(tree["tree_structure"]) for tree in fitted.dump_model()["tree_info"]],
    }
    # Bind deployment semantics to the actual training library, including leaf offsets.
    head = ProgressTreeHead(raw)
    selected = np.unique(np.linspace(0, len(rows) - 1, min(64, len(rows)), dtype=int))
    actual = np.asarray([head.score(x[index]) for index in selected])
    expected = fitted.predict(x[selected], raw_score=True, num_threads=1)
    if not np.allclose(actual, expected, atol=1e-10, rtol=1e-10):
        raise ValueError("portable progress tree scores differ from the fitted library")
    return raw, {"training_parameters": settings, "portable_score_max_abs_error": float(
        np.max(np.abs(actual - expected)),
    ), "library": "lightgbm", "library_version": lgb.__version__}


def calibrate(head_raw, rows, thresholds):
    import numpy as np
    from scipy.optimize import minimize, minimize_scalar

    head = ProgressTreeHead(head_raw)
    scores = np.asarray([head.score(row) for row in matrix(rows)])
    labels = np.searchsorted(thresholds, [row["remaining_work"] for row in rows], side="right")
    counts = Counter(row["workflow"] for row in rows)
    weights = np.asarray([1. / len(counts) / counts[row["workflow"]] for row in rows])
    if head.objective == "multiclass":
        def nll(log_temperature):
            logits = scores / math.exp(log_temperature)
            logits -= logits.max(axis=1, keepdims=True)
            return float(np.sum(weights * (
                np.log(np.exp(logits).sum(1)) - logits[np.arange(len(rows)), labels]
            )))

        fit = minimize_scalar(nll, bounds=(math.log(.05), math.log(20.)), method="bounded")
        return {**head_raw, "temperature": math.exp(float(fit.x))}
    from scipy.special import ndtr

    def nll(parameters):
        offset, log_scale = parameters
        cdf = ndtr((np.log1p(thresholds)[None, :] - scores[:, None] - offset) / math.exp(log_scale))
        probability = np.column_stack((cdf[:, 0], cdf[:, 1] - cdf[:, 0], 1. - cdf[:, 1]))
        return float(-np.sum(weights * np.log(np.maximum(
            probability[np.arange(len(rows)), labels], 1e-12,
        ))))

    fit = minimize(nll, (0., 0.), method="L-BFGS-B",
                   bounds=((-5., 5.), (math.log(.05), math.log(10.))))
    return {**head_raw, "regression_offset": float(fit.x[0]),
            "regression_scale": math.exp(float(fit.x[1]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "baseline-model", "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--extra-training-data", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve prior predictor experiments")
    inputs = {str(path.resolve()): sha256_file(path) for path in (
        args.dataset, args.cohort, args.baseline_model, args.baseline_evaluation,
    )}
    if args.extra_training_data:
        inputs[str(args.extra_training_data.resolve())] = sha256_file(args.extra_training_data)
    dataset, baseline, evaluation, cohort = (
        json.loads(path.read_bytes()) for path in (
            args.dataset, args.baseline_model, args.baseline_evaluation, args.cohort,
        )
    )
    if (
        dataset["schema"] != R5_SCHEMA or dataset["prefill_token_weight"] != .01
        or evaluation["model_sha256"] != inputs[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve()))
        != inputs[str(args.dataset.resolve())]
    ):
        raise ValueError("progress heads require the exact source-bound baseline")
    train, calibration, test, split = split_rows(dataset["rows"], cohort["measured_workflow_ids"])
    if split != evaluation["split_contract"]:
        raise ValueError("preserve the complete-workflow predictor split")
    if args.extra_training_data:
        from scripts.extend_workflow_prediction_training import disjoint_training_rows
        from scripts.train_workflow_work import capture_split

        extension = json.loads(args.extra_training_data.read_bytes())
        binding = extension["training_extension"]
        if binding["original_dataset_sha256"] != inputs[str(args.dataset.resolve())]:
            raise ValueError("additional training data belongs to a different source dataset")
        train = disjoint_training_rows(train, extension["rows"], calibration, test)
        split = capture_split(train, calibration, test, require_capture_disjoint=False)
        if split != binding["expanded_split"]:
            raise ValueError("the expanded execution split changed")
    shortlist = [(objective, leaves) for objective in ("multiclass", "log_remaining_work") for leaves in (4, 8)]
    write(args.output_dir / "plan.json", {
        "shortlist": shortlist, "blends": [.5, 1.], "boost_rounds": 64,
        "calibration_selection": "unchanged_eligibility_gates_then_early_ranking_score",
        "training_inputs": "bounded_explicit_task_structure_and_observed_progress_only",
        "training_weights": "three_quarters_ready_equal_workflow",
        "test_labels_used_for_selection": False, "split_contract": split,
        "inputs_sha256": inputs, "GPU_action": False,
    })
    models, summaries = {"baseline": baseline}, {}

    def record(name, raw, duration=0.):
        metrics = slices(ExperimentalTreeWorkModel(raw), calibration)
        reasons = calibration_rejections(metrics, raw)
        raw["metadata"].update(
            decision_eligible=not reasons, decision_rejection_reasons=reasons,
            calibration_distribution_metrics=metrics["all"],
        )
        summaries[name] = {
            "calibration": metrics, "selection_score": selection_score(metrics),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
            "fit_seconds": duration,
        }
        models[name] = raw
        write(args.output_dir / name / "model.json", raw)
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({"candidate": name, "selection_score": selection_score(metrics),
                          "rejections": reasons, "all_accuracy": metrics["all"]["workflow_equal_weight_accuracy"],
                          "initial_pair": metrics["initial_pair_ranking"]["accuracy"]}), flush=True)

    record("baseline", deepcopy(baseline))
    for objective, leaves in shortlist:
        print(f"fitting: {objective}_{leaves}", flush=True)
        started = time.monotonic()
        fitted, proof = fit_head(
            train, objective=objective, leaves=leaves, thresholds=baseline["work_thresholds"],
        )
        fitted = calibrate(fitted, calibration, baseline["work_thresholds"])
        for blend in (.5, 1.):
            name = f"{objective}_{leaves}_blend{blend}"
            raw = deepcopy(baseline)
            raw.pop("total_work_prior", None)
            raw["progress_tree_head"] = {**fitted, "blend": blend}
            raw["metadata"].update(
                progress_head=proof, progress_head_split=split,
                candidate_selection_used_test_labels=False,
            )
            record(name, raw, time.monotonic() - started)
    eligible = [name for name, row in summaries.items() if row["decision_eligible"]]
    if not eligible:
        raise ValueError("no progress head passed the original calibration gates")
    selected = max(eligible, key=lambda name: summaries[name]["selection_score"])
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_test: " + selected, flush=True)
    held_out = {name: slices(ExperimentalTreeWorkModel(models[name]), test) for name in {selected, "baseline"}}
    write(args.output_dir / "model.json", models[selected])
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("a predictor experiment input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "split_contract": split, "inputs_sha256": inputs,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "fixed_development_workflow_holdout_not_new_confirmatory_test",
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "accuracy": value["all"]["workflow_equal_weight_accuracy"],
        "early_accuracy": value["first_third"]["workflow_equal_weight_accuracy"],
        "initial_pair": value["initial_pair_ranking"],
        "long_to_short": value["all"]["long_to_short_rate"],
    } for name, value in held_out.items()}), flush=True)


if __name__ == "__main__":
    main()
