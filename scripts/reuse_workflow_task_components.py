"""Check complementary new-task components on calibration before held-out evaluation."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts.calibrate_workflow_probability_bias import PredictionTable
from scripts.ensemble_workflow_progress_predictions import mixed_predictions
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_work import write
from scripts.train_workflow_work_distribution import split_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "baseline-model", "task-study", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve prior component reuse studies")
    evaluation_path = args.task_study / "evaluation.json"
    progress_path = args.task_study / "components/progress.json"
    ratio_path = args.task_study / "components/initial.json"
    paths = (args.dataset, args.cohort, args.baseline_model, evaluation_path, progress_path, ratio_path)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, baseline, evaluation, progress, ratio = (
        json.loads(path.read_bytes()) for path in paths
    )
    _train, calibration, development, split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    if (
        evaluation["original_split"] != split
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
        or evaluation["inputs_sha256"].get(str(args.baseline_model.resolve()))
        != pins[str(args.baseline_model.resolve())]
        or any(
            raw["work_thresholds"] != baseline["work_thresholds"]
            or raw["metadata"]["split_contract"] != evaluation["expanded_split"]
            or raw["metadata"].get("candidate_selection_uses_calibration_only") is not True
            for raw in (progress, ratio)
        )
    ):
        raise ValueError("reuse only the exact original task study with unchanged holdouts and work units")
    settings = {"retained": None, "ratio_progress": None}
    settings.update({f"progress_blend_{blend}": blend for blend in (.25, .5, .75)})
    write(args.output_dir / "plan.json", {
        "candidates": settings,
        "initial_branch_preserved_for_all_probability_mixtures": True,
        "selection": "unchanged_calibration_support_gates_then_existing_ready_ranking_score",
        "new_training_or_GPU_action": False, "development_labels_used_for_selection": False,
        "original_split": split, "expanded_split": evaluation["expanded_split"],
        "inputs_sha256": pins,
    })
    base, other, numeric = (WorkflowWorkModel(raw) for raw in (baseline, progress, ratio))

    def model_for(name, rows):
        if name == "retained":
            return base
        if name == "ratio_progress":
            return numeric
        return PredictionTable(base, rows, mixed_predictions(
            base, other, rows, blend=settings[name], progress_only=True,
        ))

    summaries = {}
    for name in settings:
        values = metrics(model_for(name, calibration), calibration)
        reasons = calibration_rejections(values, ratio if name == "ratio_progress" else baseline)
        summaries[name] = {
            "calibration": values, "selection_score": score(values),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
        }
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({"candidate": name, "score": score(values), "rejections": reasons}), flush=True)
    selected = max(
        (name for name in settings if summaries[name]["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {name: metrics(model_for(name, development), development)
                for name in dict.fromkeys(("retained", selected))}
    raw = deepcopy(ratio if selected == "ratio_progress" else baseline)
    if selected.startswith("progress_blend_"):
        raw["progress_ensemble"] = {
            "model": progress, "blend": settings[selected], "progress_only": True,
        }
        raw["metadata"].update(
            decision_eligible=False,
            decision_rejection_reasons=["offline_adapter_not_serving_qualified"],
        )
    elif selected == "ratio_progress":
        raw["metadata"].update(
            decision_eligible=summaries[selected]["decision_eligible"],
            decision_rejection_reasons=summaries[selected]["decision_rejection_reasons"],
            calibration_distribution_metrics=summaries[selected]["calibration"]["all"],
        )
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen component reuse input changed")
    write(args.output_dir / "model.json", raw)
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "original_split": split, "expanded_split": evaluation["expanded_split"],
        "inputs_sha256": pins, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "GPU_action": False, "JCT_gain_proven": False,
        "deployment_adapter_implemented": not selected.startswith("progress_blend_"),
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
