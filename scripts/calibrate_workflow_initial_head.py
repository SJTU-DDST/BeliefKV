"""Calibrate an initial-only workflow head while preserving the established progress model."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel, before_observed_work
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics
from scripts.train_workflow_work import write
from scripts.train_workflow_work_distribution import split_rows


def initial_score(values):
    return (
        values["initial_pair_ranking"]["accuracy"]
        + values["first_request_pairs"]["accuracy"]
        + values["all"]["workflow_equal_weight_accuracy"]
    ) / 3.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "baseline-model", "baseline-evaluation",
                 "initial-model", "initial-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier conditional-head calibration and development checks")
    paths = (args.dataset, args.cohort, args.baseline_model, args.baseline_evaluation,
             args.initial_model, args.initial_evaluation)
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, base, baseline_evaluation, head, head_evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    _train, calibration, test, split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    for raw, evaluation, path in (
        (base, baseline_evaluation, args.baseline_model), (head, head_evaluation, args.initial_model),
    ):
        if (
            evaluation["model_sha256"] != pins[str(path.resolve())]
            or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != pins[str(args.dataset.resolve())]
            or evaluation["original_split"] != split
            or evaluation["expanded_split"] != baseline_evaluation["expanded_split"]
            or raw["work_thresholds"] != base["work_thresholds"]
        ):
            raise ValueError("the initial head and progress model must share exact data and splits")
    cold = [row for row in calibration if before_observed_work(row["features"])]
    write(args.output_dir / "plan.json", {
        "blends": [0., .25, .5, 1.],
        "condition": "zero_observed_input_output_and_no_observed_child",
        "calibration_only_selection": True, "frozen_work_thresholds": base["work_thresholds"],
        "original_split": split, "expanded_split": baseline_evaluation["expanded_split"],
        "progress_probabilities_after_condition_are_original_exactly": True,
        "development_labels_already_observed_in_prior_exploration": True,
        "inputs_sha256": pins, "GPU_action": False,
    })
    models, summary = {}, {}
    for blend in (0., .25, .5, 1.):
        raw = deepcopy(base)
        if blend:
            raw["initial_state_head"] = {
                "kind": "before_observed_work_and_children_v1", "blend": blend,
                "model": deepcopy(head),
            }
        model = WorkflowWorkModel(raw)
        full, initial = metrics(model, calibration), metrics(model, cold)
        reasons = calibration_rejections(full, raw)
        if blend:
            raw["metadata"].update(
                calibration_distribution_metrics=full["all"],
                decision_eligible=not reasons, decision_rejection_reasons=reasons,
                initial_head_selection_uses_calibration_only=True,
                initial_head_condition="zero_observed_input_output_and_no_observed_child",
            )
        models[blend] = raw
        summary[blend] = {
            "full": full, "initial_condition": initial, "score": initial_score(initial),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
        }
    selected = max(
        (blend for blend in summary if summary[blend]["decision_eligible"]),
        key=lambda blend: summary[blend]["score"],
    )
    write(args.output_dir / "selection.json", {"selected_blend": selected, "calibration_summary": summary})
    print("selected_blend_before_development: " + str(selected), flush=True)
    held_out = {str(blend): metrics(WorkflowWorkModel(models[blend]), test)
                for blend in dict.fromkeys((0., selected))}
    write(args.output_dir / "model.json", models[selected])
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a conditional-head input changed during evaluation")
    write(args.output_dir / "evaluation.json", {
        "selected_blend": selected, "held_out": held_out, "calibration_summary": summary,
        "original_split": split, "expanded_split": baseline_evaluation["expanded_split"],
        "inputs_sha256": pins, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "GPU_action": False, "JCT_gain_proven": False,
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
