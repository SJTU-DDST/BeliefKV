"""Test a frozen 0.6B semantic encoder for continuous initial workflow work."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import (
    fit_training_reference, log_work_weight, normal_log_work_weight, remaining_work_weight,
)
from beliefkv.predictor.workflow_qwen_encoder import QwenTaskEncoder, encoder_contract
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts.train_workflow_semantic_initial_prior import fit_semantic_prior, ridge_predict
from scripts.train_workflow_task_diversity import training_population
from scripts.train_workflow_work import probabilities, write


def initial_metrics(rows, log_predictions, utility, reference):
    import numpy as np

    truth = np.asarray([row["total_work"] for row in rows])
    logs = np.log1p(truth)
    target = np.asarray([remaining_work_weight(float(value), reference) for value in truth])
    correct, pairs = 0., 0
    for left in range(len(rows)):
        for right in range(left):
            difference = truth[left] - truth[right]
            if abs(difference) < 1e-9:
                continue
            margin = utility[left] - utility[right]
            correct += .5 if abs(margin) < 1e-10 else float(margin * difference < 0)
            pairs += 1
    return {
        "workflows": len(rows), "pair_observations": pairs,
        "pair_ranking_accuracy": correct / pairs if pairs else None,
        "mean_squared_continuous_utility_error": float(np.mean((utility - target) ** 2)),
        "mean_absolute_log1p_work_error": float(np.mean(np.abs(log_predictions - logs))),
        "log1p_work_RMSE": float(np.mean((log_predictions - logs) ** 2) ** .5),
        "target": "initial_logical_model_work_not_waiting_or_JCT",
    }


def fit_class_conditional_priors(rows, vectors, thresholds):
    from bisect import bisect_right

    groups = [
        [row for row in rows if bisect_right(thresholds, row["total_work"]) == label]
        for label in range(3)
    ]
    if min(map(len, groups)) < 5:
        raise ValueError("class-conditional work requires five training executions in each class")
    priors, diagnostics = [], []
    for values in groups:
        fitted, diagnostic = fit_semantic_prior(values, vectors)
        priors.append(fitted)
        diagnostics.append(diagnostic)
    return {"kind": "semantic_class_conditional_log_work_v1", "class_conditional_priors": priors}, {
        "class_training_execution_counts": list(map(len, groups)),
        "class_training_project_validation": diagnostics,
        "future_training_class_is_a_label_not_a_query_input": True,
    }


def prior_predictions(prior, rows, vectors, reference, *, probabilities=None, thresholds=None):
    import numpy as np

    values = np.asarray([vectors[row["text"]] for row in rows])
    if prior.get("kind") == "semantic_class_conditional_log_work_v1":
        if (
            probabilities is None or thresholds is None or len(thresholds) != 2
            or np.asarray(probabilities).shape != (len(rows), 3)
            or len(prior["class_conditional_priors"]) != 3
        ):
            raise ValueError("conditional work experts require observed class probabilities and fixed thresholds")
        nodes, mass = np.polynomial.hermite.hermgauss(12)
        nodes, mass = nodes * np.sqrt(2.), mass / np.sqrt(np.pi)
        lower = (0., np.log1p(thresholds[0]), np.log1p(thresholds[1]))
        upper = (np.nextafter(lower[1], 0.), np.nextafter(lower[2], 0.), 700.)
        means, utility = [], []
        for label, expert in enumerate(prior["class_conditional_priors"]):
            predicted = ridge_predict(expert, values)
            draws = np.clip(
                predicted[:, None] + expert["log_total_sigma"] * nodes[None, :],
                lower[label], upper[label],
            )
            means.append(draws @ mass)
            utility.append(np.asarray([
                [log_work_weight(float(value), reference) for value in row] for row in draws
            ]) @ mass)
        probability = np.asarray(probabilities)
        return (
            (probability * np.column_stack(means)).sum(1),
            (probability * np.column_stack(utility)).sum(1),
        )
    logs = ridge_predict(prior, values)
    utilities = np.asarray([
        normal_log_work_weight(float(value), prior["log_total_sigma"], reference) for value in logs
    ])
    return logs, utilities


def calibration_choice(summaries):
    baseline = summaries["retained"]
    eligible = [
        name for name, value in summaries.items()
        if value["pair_ranking_accuracy"] >= baseline["pair_ranking_accuracy"]
    ]
    return min(eligible, key=lambda name: summaries[name]["mean_squared_continuous_utility_error"])


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "additional-training-data",
                 "model", "model-evaluation", "training-evaluation", "encoder", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--family", choices=("global", "class_conditional"), default="global")
    parser.add_argument("--reuse-embeddings", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve prior semantic initial-work studies")
    paths = tuple(getattr(args, name.replace("-", "_")) for name in (
        "dataset", "cohort", "previous-training-data", "additional-training-data",
        "model", "model-evaluation", "training-evaluation",
    ))
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    if args.reuse_embeddings is not None:
        pins[str(args.reuse_embeddings.resolve())] = sha256_file(args.reuse_embeddings)
    data, cohort, previous, additional, raw, evaluation, training_evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    if (
        evaluation["model_sha256"] != pins[str(args.model.resolve())]
        or evaluation["offline_candidate_evaluation_sha256"] != pins[str(args.training_evaluation.resolve())]
        or evaluation["inputs_sha256"].get(str(args.training_evaluation.resolve()))
        != pins[str(args.training_evaluation.resolve())]
        or any(training_evaluation["inputs_sha256"].get(str(path.resolve())) != pins[str(path.resolve())]
               for path in (args.dataset, args.cohort, args.previous_training_data, args.additional_training_data))
        or evaluation["candidate_selection_used_test_labels"] is not False
    ):
        raise ValueError("semantic initial work requires the exact retained predictor and complete training executions")
    population_evaluation = {**evaluation, "expanded_split": evaluation["previous_expanded_split"]}
    training, calibration, development, population = training_population(
        data, cohort, previous, additional, population_evaluation, pins[str(args.dataset.resolve())],
    )
    if population["expanded_split"] != evaluation["expanded_split"]:
        raise ValueError("semantic initial training changed the retained complete-workflow holdouts")
    for dataset in (data, previous, additional):
        for path, digest in dataset["sources_sha256"].items():
            if path in pins and pins[path] != digest:
                raise ValueError("semantic initial training source identities conflict")
            pins[path] = digest
    contract = encoder_contract(args.encoder)
    for name, digest in contract["files_sha256"].items():
        pins[str(Path(contract["directory"]) / name)] = digest
    for path in (
        Path(__file__), ROOT / "beliefkv/predictor/workflow_qwen_encoder.py",
        ROOT / "beliefkv/predictor/workflow_continuous_work.py",
        ROOT / "scripts/train_workflow_semantic_initial_prior.py",
        ROOT / "scripts/train_workflow_task_diversity.py",
    ):
        pins[str(path.resolve())] = sha256_file(path)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a semantic initial-work input changed")
    initial = {
        "training": [row for row in training if row["snapshot"] == 0],
        "calibration": [row for row in calibration if row["snapshot"] == 0],
        "development": [row for row in development if row["snapshot"] == 0],
    }
    base = WorkflowWorkModel(raw)
    reference = fit_training_reference(training, base.thresholds)
    previous_ids = set(population["previous_expanded_split"]["training_execution_sha256"])
    groups = {
        "previous59": [row for row in initial["training"] if row["source_workflow_sha256"] in previous_ids],
        "expanded77": initial["training"],
    }
    write(args.output_dir / "plan.json", {
        "encoder": contract, "settings": list(groups), "training_population": population,
        "family": args.family,
        "target": "continuous_initial_log1p_complete_model_work",
        "initial_only_progress_predictions_unchanged": True,
        "selection": "minimum_fixed_calibration_utility_MSE_without_pair_ranking_regression",
        "ridge_regularization_uses_training_project_folds_only": True,
        "uncertainty_uses_training_project_fold_log_residuals": True,
        "development_labels_used_for_selection": False, "inputs_sha256": pins, "GPU_action": False,
    })
    encoder, vectors = QwenTaskEncoder(contract), {}
    if args.reuse_embeddings is not None:
        cached = json.loads(args.reuse_embeddings.read_bytes())
        if (
            cached.get("encoder") != contract or cached.get("explicit_task_text_only") is not True
            or cached.get("future_work_labels_in_embeddings") is not False
        ):
            raise ValueError("reused semantic vectors require the same frozen encoder and explicit task inputs")
        vectors = {text: np.asarray(vector, dtype=np.float32) for text, vector in cached["vectors"].items()}
        if any(vector.shape != (encoder.dimension,) or not np.isfinite(vector).all() for vector in vectors.values()):
            raise ValueError("reused semantic task vectors have incompatible dimensions or values")
    # Cache each explicit task once. Calibration text is visible, while its
    # labels cannot enter ridge fitting or regularization selection.
    text_rows = initial["training"] + initial["calibration"]
    for index, row in enumerate(text_rows):
        text = row["text"]
        if text not in vectors:
            vectors[text] = encoder.encode(text)[0]
        if (index + 1) % 10 == 0:
            print(json.dumps({"stage": "task_embeddings", "encoded": len(vectors)}), flush=True)
    priors, summaries = {}, {}
    cal_rows = initial["calibration"]
    base_cal = probabilities(base, cal_rows)
    base_logs = base_cal @ np.asarray(reference["initial_class_mean_log_work"])
    base_utility = base_cal @ np.asarray(reference["initial_class_expected_utility"])
    summaries["retained"] = initial_metrics(
        cal_rows, base_logs, base_utility, reference["reference_work"],
    )
    for name, rows in groups.items():
        prior, diagnostic = (
            fit_class_conditional_priors(rows, vectors, base.thresholds)
            if args.family == "class_conditional" else fit_semantic_prior(rows, vectors)
        )
        priors[name] = prior
        logs, utility = prior_predictions(
            prior, cal_rows, vectors, reference["reference_work"],
            probabilities=base_cal, thresholds=base.thresholds,
        )
        summaries[name] = initial_metrics(cal_rows, logs, utility, reference["reference_work"])
        write(args.output_dir / name / "prior.json", prior)
        write(args.output_dir / name / "training_project_validation.json", diagnostic)
        print(json.dumps({"candidate": name, "calibration": summaries[name]}), flush=True)
    selected = calibration_choice(summaries)
    write(args.output_dir / "selection.json", {"selected": selected, "calibration": summaries})
    print("selected_before_development: " + selected, flush=True)
    dev_rows = initial["development"]
    base_dev = probabilities(base, dev_rows)
    held_out = {"retained": initial_metrics(
        dev_rows, base_dev @ np.asarray(reference["initial_class_mean_log_work"]),
        base_dev @ np.asarray(reference["initial_class_expected_utility"]), reference["reference_work"],
    )}
    artifact = None
    if selected != "retained":
        for row in dev_rows:
            if row["text"] not in vectors:
                vectors[row["text"]] = encoder.encode(row["text"])[0]
        logs, utility = prior_predictions(
            priors[selected], dev_rows, vectors, reference["reference_work"],
            probabilities=base_dev, thresholds=base.thresholds,
        )
        held_out[selected] = initial_metrics(dev_rows, logs, utility, reference["reference_work"])
        artifact = {
            "kind": "offline_qwen_semantic_initial_work_v1", "encoder": contract,
            "prior": deepcopy(priors[selected]), "reference": reference,
            "family": args.family,
            "metadata": {"decision_eligible": False, "reason": "offline_initial_semantic_readout_not_serving_qualified"},
        }
        write(args.output_dir / "model.json", artifact)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen semantic initial-work input changed during training")
    write(args.output_dir / "task_embeddings.json", {
        "encoder": contract, "vectors": {text: vector.tolist() for text, vector in vectors.items()},
        "explicit_task_text_only": True, "future_work_labels_in_embeddings": False,
        "offline_cache_not_precomputed_online_future_arrivals": True,
    })
    durations = [value["wall_seconds"] for value in encoder.measurements]
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "calibration": summaries, "held_out": held_out,
        "family": args.family,
        "training_population": population, "inputs_sha256": pins, "reference": reference,
        "task_embeddings_sha256": sha256_file(args.output_dir / "task_embeddings.json"),
        "model_sha256": sha256_file(args.output_dir / "model.json") if artifact is not None else None,
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "encoder_cost": {
            "startup_wall_seconds": encoder.startup_seconds, "unique_texts": len(vectors),
            "encoding_wall_seconds_total": sum(durations),
            "encoding_wall_seconds_p50": float(np.quantile(durations, .5)) if durations else None,
            "encoding_wall_seconds_p99": float(np.quantile(durations, .99)) if durations else None,
            "measurements": encoder.measurements,
        },
        "GPU_action": False, "deployment_adapter_implemented": False, "JCT_gain_proven": False,
    })
    print(json.dumps({"selected": selected, "held_out": held_out,
                      "encoder_seconds_p50": float(np.quantile(durations, .5)) if durations else None}), flush=True)


if __name__ == "__main__":
    main()
