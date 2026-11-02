"""Train on additional original projects while keeping the original bins and holdouts."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel
from beliefkv.predictor.workflow_work_training import PHASE_BALANCED, READY_BALANCED
from scripts.extend_workflow_prediction_training import disjoint_training_rows
from scripts.improve_workflow_work_distribution import calibration_rejections, selection_score, slices
from scripts.train_workflow_work import capture_split, fit, write
from scripts.train_workflow_work_distribution import split_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "extra-training-data", "baseline-model",
                 "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--family", choices=("regularization", "ranking", "stable-initial"),
                        default="regularization")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve prior expanded-training studies")
    paths = (args.dataset, args.cohort, args.extra_training_data, args.baseline_model, args.baseline_evaluation)
    inputs = {str(path.resolve()): sha256_file(path) for path in paths}
    dataset, cohort, extra, baseline, baseline_evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    if (
        dataset["schema"] != R5_SCHEMA or dataset["prefill_token_weight"] != .01
        or baseline_evaluation["model_sha256"] != inputs[str(args.baseline_model.resolve())]
        or baseline_evaluation["inputs_sha256"].get(str(args.dataset.resolve()))
        != inputs[str(args.dataset.resolve())]
        or extra["training_extension"]["original_dataset_sha256"] != inputs[str(args.dataset.resolve())]
    ):
        raise ValueError("expanded training must bind the exact original source dataset")
    train, calibration, test, old_split = split_rows(dataset["rows"], cohort["measured_workflow_ids"])
    if old_split != baseline_evaluation["split_contract"]:
        raise ValueError("the fixed original execution split changed")
    train = disjoint_training_rows(train, extra["rows"], calibration, test)
    split = capture_split(train, calibration, test, require_capture_disjoint=False)
    if split != extra["training_extension"]["expanded_split"]:
        raise ValueError("the declared expanded execution split changed")
    candidates = [
        ("baseline", None),
        ("expanded128", {"hidden_width": 128, "dropout": .1, "weight_decay": .05}),
        ("expanded64", {"hidden_width": 64, "dropout": .2, "weight_decay": .1}),
        ("expanded128_ready", {
            "hidden_width": 128, "dropout": .1, "weight_decay": .05,
            "training_weighting": READY_BALANCED,
        }),
        ("expanded128_phase", {
            "hidden_width": 128, "dropout": .1, "weight_decay": .05,
            "training_weighting": PHASE_BALANCED,
        }),
    ]
    if args.family == "ranking":
        candidates = [
            ("baseline", None),
            ("expanded128_ready_rank0.1", {
                "hidden_width": 128, "dropout": .1, "weight_decay": .05,
                "training_weighting": READY_BALANCED, "ranking_loss_weight": .1,
            }),
            ("expanded128_ready_rank0.3", {
                "hidden_width": 128, "dropout": .1, "weight_decay": .05,
                "training_weighting": READY_BALANCED, "ranking_loss_weight": .3,
            }),
        ]
    if args.family == "stable-initial":
        candidates = [
            ("baseline", None),
            ("expanded128_ready_stable", {
                "hidden_width": 128, "dropout": .1, "weight_decay": .05,
                "training_weighting": READY_BALANCED, "task_only_before_work": True,
            }),
            ("expanded128_ready_stable_rank0.1", {
                "hidden_width": 128, "dropout": .1, "weight_decay": .05,
                "training_weighting": READY_BALANCED, "ranking_loss_weight": .1,
                "task_only_before_work": True,
            }),
        ]
    write(args.output_dir / "plan.json", {
        "candidates": candidates, "epochs": 200, "frozen_thresholds": baseline["work_thresholds"],
        "original_split": old_split, "expanded_split": split,
        "selection": "unchanged_calibration_gates_then_early_ranking_score",
        "test_labels_used_for_selection": False, "inputs_sha256": inputs, "GPU_action": False,
    })
    models, summaries = {}, {}
    for name, settings in candidates:
        print("fitting: " + name, flush=True)
        started = time.monotonic()
        if settings is None:
            raw = deepcopy(baseline)
        else:
            raw = fit(
                train, calibration, epochs=200, vocabulary_size=8192,
                feature_schema=R5_SCHEMA, split_mode="workflow",
                work_thresholds=baseline["work_thresholds"], **settings,
            )
            raw["metadata"].update(
                prefill_token_weight=.01, split_contract=split, decision_eligible=True,
                progress_observation_contract=dataset["progress_observation_contract"],
                online_features_use_observed_prefix_only=True,
                unseen_project_generalization_claimed=False,
                training_dataset_sha256=inputs[str(args.dataset.resolve())],
                training_extension_sha256=inputs[str(args.extra_training_data.resolve())],
                candidate_selection_uses_calibration_only=True,
            )
            raw["admission_cost_profile"] = deepcopy(baseline["admission_cost_profile"])
            raw["free_dispatch_coalescing_window_ms"] = baseline["free_dispatch_coalescing_window_ms"]
        metrics = slices(WorkflowWorkModel(raw), calibration)
        reasons = calibration_rejections(metrics, raw)
        raw["metadata"].update(
            decision_eligible=not reasons, decision_rejection_reasons=reasons,
            calibration_distribution_metrics=metrics["all"],
        )
        models[name] = raw
        summaries[name] = {
            "calibration": metrics, "selection_score": selection_score(metrics),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
            "fit_and_calibration_seconds": time.monotonic() - started,
        }
        write(args.output_dir / name / "model.json", raw)
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({
            "candidate": name, "selection_score": selection_score(metrics), "rejections": reasons,
            "all_accuracy": metrics["all"]["workflow_equal_weight_accuracy"],
            "initial_pair": metrics["initial_pair_ranking"]["accuracy"],
            "early_accuracy": metrics["first_third"]["workflow_equal_weight_accuracy"],
        }), flush=True)
    selected = max(
        (name for name, row in summaries.items() if row["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {name: slices(WorkflowWorkModel(models[name]), test) for name in {selected, "baseline"}}
    write(args.output_dir / "model.json", models[selected])
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("an expanded predictor input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "original_split": old_split, "expanded_split": split,
        "inputs_sha256": inputs, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "fixed_development_workflow_holdout_not_new_confirmatory_test",
        "original_task_tokens_tools_arrivals_cohort_and_thresholds_preserved": True,
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
