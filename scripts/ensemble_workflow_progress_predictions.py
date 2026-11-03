"""Test complementary trained progress heads without changing initial predictions."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel, before_observed_work
from beliefkv.predictor.workflow_work_distribution import support_status
from scripts.calibrate_workflow_probability_bias import PredictionTable
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_work import probabilities, write
from scripts.train_workflow_work_distribution import split_rows


def mixed_predictions(base, other, rows, *, blend, progress_only):
    import numpy as np

    first, second = probabilities(base, rows), probabilities(other, rows)
    coverages = {}
    for row in rows:
        text = row.get("task_text", row["text"])
        if text not in coverages:
            coverages[text] = other.task_encoding(text)[1]
    eligible = np.asarray([
        (not progress_only or not before_observed_work(row["features"]))
        and support_status(other, row["features"], coverages[row.get("task_text", row["text"])])
        == "calibrated_distribution" for row in rows
    ])
    result = first.copy()
    result[eligible] = (1. - blend) * first[eligible] + blend * second[eligible]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "baseline-model", "baseline-evaluation",
                 "other-model", "other-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier independent-model ensemble studies")
    paths = (
        args.dataset, args.cohort, args.baseline_model, args.baseline_evaluation,
        args.other_model, args.other_evaluation,
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, raw, evaluation, other_raw, other_evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    _train, calibration, test, split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    for model, report, path in (
        (raw, evaluation, args.baseline_model), (other_raw, other_evaluation, args.other_model),
    ):
        if (
            report["model_sha256"] != pins[str(path.resolve())]
            or report["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
            or model["work_thresholds"] != raw["work_thresholds"]
        ):
            raise ValueError("the ensemble models must share exact original data and frozen work units")
    if evaluation["original_split"] != split or other_evaluation["split_contract"] != split:
        raise ValueError("do not ensemble models trained on held-out executions")
    settings = [
        {"blend": blend, "progress_only": progress_only}
        for blend in (.25, .5, .75) for progress_only in (True, False)
    ]
    write(args.output_dir / "plan.json", {
        "settings": settings, "original_split": split, "expanded_split": evaluation["expanded_split"],
        "selection": "unchanged_calibration_support_gates_then_existing_ready_ranking_score",
        "unsupported_secondary_predictions_fall_back_to_base": True,
        "models_trained_on_different_independent_project_populations": True,
        "development_labels_used_for_selection": False,
        "inputs_sha256": pins, "GPU_action": False,
    })
    base, other = WorkflowWorkModel(raw), WorkflowWorkModel(other_raw)
    models = {"baseline": PredictionTable(base, calibration, probabilities(base, calibration))}
    models.update({
        f"ensemble_{index}": PredictionTable(base, calibration, mixed_predictions(
            base, other, calibration, **values,
        )) for index, values in enumerate(settings)
    })
    summaries = {}
    for name, model in models.items():
        values = metrics(model, calibration)
        reasons = calibration_rejections(values, raw)
        summaries[name] = {
            "calibration": values, "selection_score": score(values), "decision_eligible": not reasons,
            "decision_rejection_reasons": reasons,
            "settings": None if name == "baseline" else settings[int(name.split("_")[1])],
        }
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({
            "candidate": name, "score": score(values), "rejections": reasons,
            "initial": values["initial_pair_ranking"]["accuracy"],
            "first_request": values["first_request_pairs"]["accuracy"],
            "ready": values["ready_cross_progress_pairs"]["accuracy"],
            "accuracy": values["all"]["workflow_equal_weight_accuracy"],
        }), flush=True)
    selected = max(
        (name for name, values in summaries.items() if values["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {}
    for name in dict.fromkeys(("baseline", selected)):
        predictions = (
            probabilities(base, test) if name == "baseline"
            else mixed_predictions(base, other, test, **summaries[name]["settings"])
        )
        held_out[name] = metrics(PredictionTable(base, test, predictions), test)
    selected_model = deepcopy(raw)
    if selected != "baseline":
        selected_model["progress_ensemble"] = {
            "model": other_raw, **summaries[selected]["settings"],
        }
        selected_model["metadata"].update(
            decision_eligible=False, decision_rejection_reasons=["offline_adapter_not_serving_qualified"],
        )
    write(args.output_dir / "model.json", selected_model)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen complementary-model input changed")
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
