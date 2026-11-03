"""Condition an existing initial work estimate on service already consumed."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_reference import SurvivalWorkCandidate
from scripts.extend_workflow_prediction_training import disjoint_training_rows
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_reference_distribution import CachedCandidate
from scripts.train_workflow_work import capture_split, write
from scripts.train_workflow_work_distribution import split_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "extra-training-data", "baseline-model",
                 "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier conditional-survival studies")
    paths = (args.dataset, args.cohort, args.extra_training_data, args.baseline_model, args.baseline_evaluation)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, extra, raw, evaluation = (json.loads(path.read_bytes()) for path in paths)
    train, calibration, test, split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    if (
        evaluation["model_sha256"] != pins[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
        or extra["training_extension"]["original_dataset_sha256"] != pins[str(args.dataset.resolve())]
        or evaluation["original_split"] != split
    ):
        raise ValueError("conditional survival must use the exact source-bound original data")
    train = disjoint_training_rows(train, extra["rows"], calibration, test)
    expanded = capture_split(train, calibration, test, require_capture_disjoint=False)
    if expanded != evaluation["expanded_split"]:
        raise ValueError("conditional survival changed complete training or held-out executions")
    totals = [row["total_work"] for row in train if row["snapshot"] == 0]
    if len(totals) != len({row["workflow"] for row in train}):
        raise ValueError("each independent training execution must contribute exactly one total")
    settings = [
        {"blend": blend, "after_short_threshold": after_short}
        for blend in (.25, .5) for after_short in (True, False)
    ]
    write(args.output_dir / "plan.json", {
        "settings": settings, "selection": "unchanged_calibration_gates_then_existing_ready_ranking_score",
        "conditioning": "initial_class_prior_training_totals_and_actually_observed_model_work",
        "future_query_workflow_labels_and_elapsed_times_are_not_inputs": True,
        "total_work_reference_count": len(totals), "original_split": split, "expanded_split": expanded,
        "development_labels_used_for_selection": False, "inputs_sha256": pins, "GPU_action": False,
    })
    base = WorkflowWorkModel(raw)
    models = {"baseline": CachedCandidate(base)}
    models.update({
        f"survival_{index}": CachedCandidate(SurvivalWorkCandidate(base, totals, **values))
        for index, values in enumerate(settings)
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
            "ready": values["ready_cross_progress_pairs"]["accuracy"],
            "accuracy": values["all"]["workflow_equal_weight_accuracy"],
            "long_to_short": values["all"]["long_to_short_rate"],
        }), flush=True)
    selected = max(
        (name for name, values in summaries.items() if values["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {name: metrics(models[name], test) for name in dict.fromkeys(("baseline", selected))}
    selected_model = deepcopy(raw)
    if selected != "baseline":
        selected_model["observed_service_survival"] = {
            "training_totals": totals, **summaries[selected]["settings"],
        }
        selected_model["metadata"].update(
            decision_eligible=False, decision_rejection_reasons=["offline_adapter_not_serving_qualified"],
        )
    write(args.output_dir / "model.json", selected_model)
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a conditional-survival input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "original_split": split, "expanded_split": expanded, "inputs_sha256": pins,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
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
