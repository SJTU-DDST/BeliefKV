"""Choose bounded probability corrections using whole calibration workflow folds."""

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel, before_observed_work
from beliefkv.predictor.workflow_work_calibration import (
    KIND, CalibratedWorkCandidate, correct_probabilities, fit_probability_correction,
)
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_reference_distribution import CachedCandidate, projection_key
from scripts.train_workflow_work import probabilities, write
from scripts.train_workflow_work_distribution import split_rows


class PredictionTable:
    def __init__(self, base, rows, predictions):
        self.base, self.lookup = base, {}
        projections = {}
        for row, prediction in zip(rows, predictions):
            text = row.get("task_text", row["text"])
            if text not in projections:
                projections[text] = base.task_projection(text)
            if projections[text] is not None:
                key = projection_key(projections[text]), tuple(row["features"])
                if key in self.lookup and tuple(prediction) != self.lookup[key]:
                    raise ValueError("different calibration folds collide at a task/progress state")
                self.lookup[key] = tuple(prediction)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def predict(self, projection, features):
        return self.lookup[projection_key(projection), tuple(features)]


def fold_fit(rows, scores, thresholds, *, regularization, progress_only, held_out=None):
    import numpy as np

    eligible = np.asarray([
        (not progress_only or not before_observed_work(row["features"]))
        and row["workflow"] != held_out for row in rows
    ])
    selected = [row for row, use in zip(rows, eligible) if use]
    counts = Counter(row["workflow"] for row in selected)
    weights = [1. / counts[row["workflow"]] for row in selected]
    targets = np.searchsorted(
        thresholds, [row["remaining_work"] for row in selected], side="right",
    )
    return fit_probability_correction(
        scores[eligible], targets, weights, regularization=regularization,
    )


def main():
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "baseline-model", "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve prior probability correction studies")
    paths = (args.dataset, args.cohort, args.baseline_model, args.baseline_evaluation)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, raw, evaluation = (json.loads(path.read_bytes()) for path in paths)
    _train, calibration, test, split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    if (
        data["schema"] != R5_SCHEMA or data["prefill_token_weight"] != .01
        or evaluation["model_sha256"] != pins[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
        or evaluation["original_split"] != split
    ):
        raise ValueError("calibrate the exact source-bound predictor with unchanged whole-workflow holdouts")
    settings = [
        {"regularization": regularization, "progress_only": progress_only}
        for regularization in (.01, .1, 1.) for progress_only in (True, False)
    ]
    write(args.output_dir / "plan.json", {
        "settings": settings, "selection": "whole_workflow_leave_one_out_ready_ranking_score",
        "unchanged_calibration_support_gates_required": True,
        "fitted_parameters": "one_log_inverse_temperature_two_class_offsets",
        "base_model_has_preexisting_calibration_temperature": True,
        "fold_results_are_conditional_on_fixed_precalibrated_base_not_blind_generalization": True,
        "development_labels_used_for_selection": False, "GPU_action": False,
        "original_split": split, "expanded_split": evaluation["expanded_split"],
        "inputs_sha256": pins,
    })
    base = WorkflowWorkModel(raw)
    base_scores = probabilities(base, calibration)
    models, summaries = {"baseline": CachedCandidate(base)}, {}
    baseline_values = metrics(models["baseline"], calibration)
    baseline_rejections = calibration_rejections(baseline_values, raw)
    summaries["baseline"] = {
        "calibration": baseline_values, "fold_calibration": baseline_values,
        "selection_score": score(baseline_values), "decision_eligible": not baseline_rejections,
        "decision_rejection_reasons": baseline_rejections, "settings": None,
    }
    workflows = sorted({row["workflow"] for row in calibration})
    for index, values in enumerate(settings):
        started = time.monotonic()
        name = f"correction_{index}"
        params = fold_fit(calibration, base_scores, base.thresholds, **values)
        model = CachedCandidate(CalibratedWorkCandidate(
            base, params, progress_only=values["progress_only"],
        ))
        out_of_fold = base_scores.copy()
        for workflow in workflows:
            parameters = fold_fit(
                calibration, base_scores, base.thresholds, held_out=workflow, **values,
            )
            mask = np.asarray([
                row["workflow"] == workflow
                and (not values["progress_only"] or not before_observed_work(row["features"]))
                for row in calibration
            ])
            out_of_fold[mask] = correct_probabilities(base_scores[mask], parameters)
        actual_metrics = metrics(model, calibration)
        fold_metrics = metrics(PredictionTable(base, calibration, out_of_fold), calibration)
        reasons = sorted(set(
            calibration_rejections(actual_metrics, raw) + calibration_rejections(fold_metrics, raw),
        ))
        models[name] = model
        summaries[name] = {
            "calibration": actual_metrics, "fold_calibration": fold_metrics,
            "selection_score": score(fold_metrics), "settings": values, "parameters": params,
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
            "evaluation_seconds": time.monotonic() - started,
        }
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({
            "candidate": name, "parameters": params, "score": score(fold_metrics), "rejections": reasons,
            "initial": fold_metrics["initial_pair_ranking"]["accuracy"],
            "first_request": fold_metrics["first_request_pairs"]["accuracy"],
            "ready": fold_metrics["ready_cross_progress_pairs"]["accuracy"],
            "accuracy": fold_metrics["all"]["workflow_equal_weight_accuracy"],
            "long_to_short": fold_metrics["all"]["long_to_short_rate"],
        }), flush=True)
    selected = max(
        (name for name, values in summaries.items() if values["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {
        name: metrics(models[name], test) for name in dict.fromkeys(("baseline", selected))
    }
    selected_model = deepcopy(raw)
    if selected != "baseline":
        selected_model["probability_correction"] = {
            "kind": KIND, "parameters": summaries[selected]["parameters"],
            "progress_only": summaries[selected]["settings"]["progress_only"],
        }
        selected_model["metadata"].update(
            decision_eligible=False, decision_rejection_reasons=["offline_adapter_not_serving_qualified"],
        )
    write(args.output_dir / "model.json", selected_model)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a probability calibration input changed during evaluation")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "original_split": split, "expanded_split": evaluation["expanded_split"],
        "inputs_sha256": pins, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "deployment_adapter_implemented": False, "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "initial": values["initial_pair_ranking"]["accuracy"],
        "first_request": values["first_request_pairs"]["accuracy"],
        "ready": values["ready_cross_progress_pairs"]["accuracy"],
        "accuracy": values["all"]["workflow_equal_weight_accuracy"],
        "NLL": values["all"]["workflow_equal_weight_NLL"],
        "long_to_short": values["all"]["long_to_short_rate"],
    } for name, values in held_out.items()}), flush=True)


if __name__ == "__main__":
    main()
