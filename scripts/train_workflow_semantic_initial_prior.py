"""Test a project-calibrated semantic total-work prior at unserved workflow states."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_task_encoder import WorkflowTaskEncoder, encoder_contract
from beliefkv.predictor.workflow_work import WorkflowWorkModel, before_observed_work
from beliefkv.predictor.workflow_work_training import project_task_weights
from scripts.calibrate_workflow_probability_bias import PredictionTable
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_task_diversity import initial_project_folds, training_population
from scripts.train_workflow_work import probabilities, write


def ridge_fit(x, y, weights, alpha):
    import numpy as np

    x, y, weights = (np.asarray(value, dtype=float) for value in (x, y, weights))
    if (
        x.ndim != 2 or y.shape != (len(x),) or weights.shape != y.shape
        or not np.isfinite(x).all() or not np.isfinite(y).all()
        or not np.isfinite(weights).all() or (weights <= 0).any()
        or not np.isfinite(alpha) or alpha <= 0
    ):
        raise ValueError("semantic prior requires finite project-balanced training data")
    weights = weights / weights.sum()
    center = np.average(x, axis=0, weights=weights)
    scale = np.maximum(.05, np.sqrt(np.average((x - center) ** 2, axis=0, weights=weights)))
    normalized = (x - center) / scale
    bias = float(np.average(y, weights=weights))
    design = normalized * np.sqrt(weights[:, None])
    dual = np.linalg.solve(design @ design.T + alpha * np.eye(len(x)), (y - bias) * np.sqrt(weights))
    return {"center": center.tolist(), "scale": scale.tolist(),
            "coefficient": (design.T @ dual).tolist(), "bias": bias}


def ridge_predict(raw, x):
    import numpy as np

    return ((np.asarray(x) - raw["center"]) / raw["scale"]) @ raw["coefficient"] + raw["bias"]


def fit_semantic_prior(rows, vectors):
    import numpy as np

    x = np.asarray([vectors[row["text"]] for row in rows])
    y = np.log1p([row["total_work"] for row in rows])
    weights = project_task_weights(rows, np.ones(len(rows)))
    folds = initial_project_folds(rows, count=5)
    trials = []
    for alpha in (.1, 1., 10., 100.):
        output = np.full(len(rows), np.nan)
        for training, held_out in folds:
            raw = ridge_fit(x[training], y[training], weights[training], alpha)
            output[held_out] = ridge_predict(raw, x[held_out])
        if not np.isfinite(output).all():
            raise ValueError("semantic prior omitted an out-of-project prediction")
        residual = y - output
        trials.append((float(np.sum(weights * residual ** 2)), alpha, output))
    error, alpha, predictions = min(trials, key=lambda item: item[0])
    raw = ridge_fit(x, y, weights, alpha)
    residual = y - predictions
    adjustment = float(np.sum(weights * residual))
    raw["bias"] += adjustment
    raw["log_total_sigma"] = max(.2, float(np.sqrt(np.sum(weights * (residual - adjustment) ** 2))))
    raw["alpha"] = alpha
    return raw, {
        "project_grouped_folds": [{
            "training_projects": sorted({rows[index]["project"] for index in training}),
            "held_out_projects": sorted({rows[index]["project"] for index in held_out}),
        } for training, held_out in folds],
        "selected_alpha": alpha, "weighted_out_of_project_log_RMSE": error ** .5,
        "trials": [{"alpha": value, "weighted_log_MSE": loss} for loss, value, _pred in trials],
        "out_of_project_log_predictions": predictions.tolist(),
        "training_workflows": len(rows), "training_projects": len({row["project"] for row in rows}),
        "external_calibration_or_development_used_for_ridge_selection": False,
    }


def initial_probabilities(raw, vectors, thresholds):
    import numpy as np
    from scipy.special import ndtr

    means = ridge_predict(raw, vectors)
    cumulative = ndtr((np.log1p(thresholds)[None, :] - means[:, None]) / raw["log_total_sigma"])
    return np.column_stack((cumulative[:, 0], cumulative[:, 1] - cumulative[:, 0], 1. - cumulative[:, 1]))


class SemanticInitialCandidate:
    """Offline candidate only; never load this adapter from a serving model artifact."""

    def __init__(self, base, encoder, prior, blend):
        self.base, self.encoder, self.prior, self.blend = base, encoder, prior, blend

    def __getattr__(self, name):
        return getattr(self.base, name)

    def task_encoding(self, text):
        projection, coverage = self.base.task_encoding(text)
        if projection is None:
            return None, coverage
        vector, semantic_coverage = self.encoder.encode(text)
        return (projection, vector), min(coverage, semantic_coverage)

    def task_projection(self, text):
        return self.task_encoding(text)[0]

    def predict(self, projection, observed):
        base_projection, vector = projection
        result = self.base.predict(base_projection, observed)
        if before_observed_work(observed):
            semantic = initial_probabilities(self.prior, [vector], self.thresholds)[0]
            return tuple(float((1. - self.blend) * value + self.blend * extra)
                         for value, extra in zip(result, semantic))
        return result


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "additional-training-data",
                 "baseline-model", "baseline-evaluation", "encoder", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--encoder-revision", required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier semantic prior studies")
    paths = (args.dataset, args.cohort, args.previous_training_data, args.additional_training_data,
             args.baseline_model, args.baseline_evaluation)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, previous, additional, baseline, evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    if (
        evaluation["model_sha256"] != pins[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
    ):
        raise ValueError("semantic prior must retain the exact model and development split")
    training, calibration, development, population = training_population(
        data, cohort, previous, additional, evaluation, pins[str(args.dataset.resolve())],
    )
    contract = encoder_contract(args.encoder, revision=args.encoder_revision)
    pins.update({str(Path(contract["directory"]) / name): digest
                 for name, digest in contract["files_sha256"].items()})
    for dataset in (data, previous, additional):
        for path, digest in dataset["sources_sha256"].items():
            if path in pins and pins[path] != digest:
                raise ValueError("semantic source identities conflict")
            pins[path] = digest
    pins.update({str(path.resolve()): sha256_file(path) for path in (
        Path(__file__), ROOT / "beliefkv/predictor/workflow_task_encoder.py",
        ROOT / "scripts/train_workflow_task_diversity.py",
        ROOT / "scripts/extend_workflow_prediction_training.py",
        ROOT / "scripts/train_workflow_work.py",
    )})
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a complete source or frozen semantic encoder changed")
    initial = [row for row in training if row["snapshot"] == 0]
    previous_ids = set(population["previous_expanded_split"]["training_execution_sha256"])
    groups = {
        "previous": [row for row in initial if row["source_workflow_sha256"] in previous_ids],
        "expanded": initial,
    }
    settings = {"retained": None}
    settings.update({f"{group}_blend{blend}": (group, blend)
                     for group in groups for blend in (.25, .5, .75, 1.)})
    write(args.output_dir / "plan.json", {
        "settings": settings, **population, "encoder": contract,
        "target": "log1p_complete_logical_model_work", "initial_condition_observed_only": True,
        "progress_predictions_after_initial_condition_unchanged": True,
        "selection": "unchanged_calibration_support_gates_then_existing_ready_ranking_score",
        "development_labels_used_for_selection": False, "inputs_sha256": pins, "GPU_action": False,
    })
    encoder, vectors = WorkflowTaskEncoder(contract), {}

    def vector(text):
        if text not in vectors:
            values, coverage = encoder.encode(text)
            if values is None or coverage < .2:
                raise ValueError("explicit task text has insufficient semantic encoder coverage")
            vectors[text] = values
        return vectors[text]

    for row in initial:
        vector(row["text"])
    priors = {}
    for name, rows in groups.items():
        raw, diagnostic = fit_semantic_prior(rows, vectors)
        priors[name] = raw
        write(args.output_dir / name / "prior.json", raw)
        write(args.output_dir / name / "training_project_validation.json", diagnostic)
    base = WorkflowWorkModel(baseline)

    def predictions(rows, prior, blend):
        values = probabilities(base, rows)
        indices = [index for index, row in enumerate(rows) if before_observed_work(row["features"])]
        semantic = initial_probabilities(
            prior, np.asarray([vector(rows[index]["text"]) for index in indices]),
            baseline["work_thresholds"],
        )
        values[indices] = (1. - blend) * values[indices] + blend * semantic
        return values

    summaries = {}
    for name, specification in settings.items():
        model = base if specification is None else PredictionTable(
            base, calibration, predictions(calibration, priors[specification[0]], specification[1]),
        )
        values = metrics(model, calibration)
        reasons = calibration_rejections(values, baseline)
        summaries[name] = {
            "calibration": values, "selection_score": score(values),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
        }
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({"candidate": name, "score": score(values), "rejections": reasons}), flush=True)
    selected = max((name for name in settings if summaries[name]["decision_eligible"]),
                   key=lambda name: summaries[name]["selection_score"])
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {}
    for name in dict.fromkeys(("retained", selected)):
        specification = settings[name]
        model = base if specification is None else PredictionTable(
            base, development, predictions(development, priors[specification[0]], specification[1]),
        )
        held_out[name] = metrics(model, development)
    raw = deepcopy(baseline)
    if selected != "retained":
        group, blend = settings[selected]
        raw["semantic_initial_prior_experimental"] = {
            "encoder": contract, "prior": priors[group], "blend": blend,
        }
        raw["metadata"].update(
            decision_eligible=False, decision_rejection_reasons=["offline_adapter_not_serving_qualified"],
        )
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen semantic-prior input changed during evaluation")
    write(args.output_dir / "model.json", raw)
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        **population, "inputs_sha256": pins, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "deployment_adapter_implemented": False, "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "initial_ranking": values["initial_pair_ranking"]["accuracy"],
        "first_request_ranking": values["first_request_pairs"]["accuracy"],
        "ready_ranking": values["ready_cross_progress_pairs"]["accuracy"],
        "classification": values["all"]["workflow_equal_weight_accuracy"],
        "long_to_short": values["all"]["long_to_short_rate"],
    } for name, values in held_out.items()}), flush=True)


if __name__ == "__main__":
    main()
