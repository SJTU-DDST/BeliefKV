"""Select a CPU predictor on calibration workflows, then evaluate fixed held-out work."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_task_encoder import encoder_contract
from beliefkv.predictor.workflow_work import R5_SCHEMA, R5_FEATURES, WORD_ENCODING, WorkflowWorkModel
from beliefkv.predictor.workflow_work_training import PHASE_BALANCED, READY_BALANCED
from scripts.train_workflow_work import fit, probabilities, write
from scripts.train_workflow_work_distribution import distribution_metrics, split_rows


def initial_pair_accuracy(model, rows):
    import numpy as np

    initial = [row for row in rows if row["snapshot"] == 0]
    scores = probabilities(model, initial) @ np.asarray((1., .5, .25))
    correct, pairs = 0., 0
    for left in range(len(initial)):
        for right in range(left):
            difference = initial[left]["remaining_work"] - initial[right]["remaining_work"]
            if not difference:
                continue
            predicted = scores[left] - scores[right]
            correct += .5 if abs(predicted) < 1e-10 else float(predicted * difference < 0)
            pairs += 1
    return {"pairs": pairs, "accuracy": correct / pairs if pairs else None}


def slices(model, rows):
    ready_index = R5_FEATURES.index("ready")
    completed_index = R5_FEATURES.index("completed_calls")
    groups = {
        "all": rows, "initial": [row for row in rows if row["snapshot"] == 0],
        "first_third": [row for row in rows if row["attained_work"] / row["total_work"] < 1 / 3],
        "middle_third": [
            row for row in rows if 1 / 3 <= row["attained_work"] / row["total_work"] < 2 / 3
        ],
        "last_third": [row for row in rows if row["attained_work"] / row["total_work"] >= 2 / 3],
        "natural_ready": [row for row in rows if row["features"][ready_index] > 0],
        "first_request": [
            row for row in rows if row["event"] == "llm_submit"
            and row["features"][completed_index] == 0
        ],
    }
    return {
        **{name: distribution_metrics(model, values) for name, values in groups.items() if values},
        "initial_pair_ranking": initial_pair_accuracy(model, rows),
    }


def selection_score(metrics):
    """Predeclared calibration-only score; full-execution accuracy cannot hide early errors."""
    first = metrics["first_third"]
    return (
        metrics["initial"]["initial_accuracy"]
        + metrics["initial_pair_ranking"]["accuracy"]
        + first["workflow_equal_weight_accuracy"]
    ) / 3. - max(0., first["long_to_short_rate"] - .1)


def calibration_rejections(metrics, raw):
    values = metrics["all"]
    reasons = []
    if values["macro_f1"] < .5:
        reasons.append("calibration_macro_F1_below_0_50")
    if values["long_to_short_rate"] > .1:
        reasons.append("calibration_long_to_short_above_0_10")
    if min(raw["metadata"]["calibration_class_workflows"]) < 2:
        reasons.append("insufficient_complete_execution_class_support")
    if values["workflow_equal_weight_ECE_10"] > .15:
        reasons.append("calibration_ECE_above_0_15")
    if values["supported_distribution_coverage"] < .5:
        reasons.append("calibration_supported_distribution_coverage_below_0_50")
    return reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "baseline-model", "baseline-evaluation", "encoder", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--encoder-revision", required=True)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--family", choices=("phase", "words", "ready", "delegations"), default="phase")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve predictor studies and their held-out outcomes")
    args.output_dir.mkdir(parents=True)
    inputs = {str(path.resolve()): sha256_file(path) for path in (
        args.dataset, args.cohort, args.baseline_model, args.baseline_evaluation,
    )}
    dataset = json.loads(args.dataset.read_bytes())
    evaluation = json.loads(args.baseline_evaluation.read_bytes())
    baseline_path, baseline_dataset = str(args.dataset.resolve()), inputs[str(args.dataset.resolve())]
    if dataset.get("observed_delegation_inputs") and dataset.get("parent_dataset"):
        parent = dataset["parent_dataset"]
        if (
            parent.get("original_fields_except_text_equal") is not True
            or sha256_file(Path(parent["path"])) != parent["sha256"]
        ):
            raise ValueError("the original causal snapshot dataset changed")
        baseline_path, baseline_dataset = parent["path"], parent["sha256"]
    if (
        dataset["schema"] != R5_SCHEMA or dataset["prefill_token_weight"] != .01
        or evaluation["model_sha256"] != inputs[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(baseline_path) != baseline_dataset
    ):
        raise ValueError("improve the exact source-bound baseline dataset")
    train, calibration, test, split = split_rows(
        dataset["rows"], json.loads(args.cohort.read_bytes())["measured_workflow_ids"],
    )
    if split != evaluation["split_contract"]:
        raise ValueError("predictor improvement must preserve the complete-execution split")
    baseline = json.loads(args.baseline_model.read_bytes())
    encoder = encoder_contract(args.encoder, revision=args.encoder_revision)
    # A small fixed shortlist is chosen before any new held-out prediction is evaluated.
    candidates = [
        ("baseline", None),
        ("phase_char32", {"hidden_width": 32, "dropout": .2, "weight_decay": .1}),
        ("phase_semantic64", {
            "hidden_width": 64, "dropout": .2, "weight_decay": .05, "task_encoder": encoder,
        }),
        ("phase_semantic32", {
            "hidden_width": 32, "dropout": .3, "weight_decay": .1, "task_encoder": encoder,
        }),
    ]
    if args.family == "words":
        candidates = [
            ("baseline", None),
            ("words16", {
                "hidden_width": 16, "dropout": .3, "weight_decay": .1,
                "text_encoding": WORD_ENCODING, "vocabulary_size": 1024,
            }),
            ("words32", {
                "hidden_width": 32, "dropout": .3, "weight_decay": .1,
                "text_encoding": WORD_ENCODING, "vocabulary_size": 2048,
            }),
            ("words32_phase", {
                "hidden_width": 32, "dropout": .3, "weight_decay": .1,
                "text_encoding": WORD_ENCODING, "training_weighting": PHASE_BALANCED,
                "vocabulary_size": 2048,
            }),
        ]
    if args.family == "ready":
        candidates = [
            ("baseline", None),
            ("char128_phase", {
                "hidden_width": 128, "dropout": .1, "weight_decay": .05,
                "training_weighting": PHASE_BALANCED,
            }),
            ("char128_ready", {
                "hidden_width": 128, "dropout": .1, "weight_decay": .05,
                "training_weighting": READY_BALANCED,
            }),
            ("char64_ready", {
                "hidden_width": 64, "dropout": .2, "weight_decay": .1,
                "training_weighting": READY_BALANCED,
            }),
        ]
    if args.family == "delegations":
        if not dataset.get("observed_delegation_inputs"):
            raise ValueError("delegation training requires causal per-snapshot text")
        candidates = [
            ("baseline", None),
            ("delegations128", {
                "hidden_width": 128, "dropout": .1, "weight_decay": .05,
                "observed_delegations": True,
            }),
            ("delegations128_ready", {
                "hidden_width": 128, "dropout": .1, "weight_decay": .05,
                "training_weighting": READY_BALANCED, "observed_delegations": True,
            }),
        ]
    write(args.output_dir / "study_plan.json", {
        "candidate_settings": candidates, "epochs": args.epochs,
        "selection": "mean(calibration_initial_accuracy,initial_pair_accuracy,first_third_accuracy)"
                     "-max(0,calibration_first_third_long_to_short-0.1);eligible_only",
        "split_contract": split, "inputs_sha256": inputs,
        "test_labels_used_for_candidate_selection": False, "GPU_action": False,
    })
    models, summaries = {}, {}
    for name, settings in candidates:
        print("fitting_and_calibrating: " + name, flush=True)
        started = time.monotonic()
        if settings is None:
            raw = deepcopy(baseline)
        else:
            settings = dict(settings)
            weighting = settings.pop(
                "training_weighting", PHASE_BALANCED if args.family == "phase" else None,
            )
            vocabulary = settings.pop("vocabulary_size", 8192)
            raw = fit(
                train, calibration, epochs=args.epochs, split_mode="workflow",
                vocabulary_size=vocabulary, feature_schema=R5_SCHEMA,
                training_weighting=weighting, **settings,
            )
            raw["metadata"].update(
                prefill_token_weight=.01, split_contract=split, decision_eligible=True,
                progress_observation_contract=dataset["progress_observation_contract"],
                online_features_use_observed_prefix_only=True,
                unseen_project_generalization_claimed=False,
                training_dataset_sha256=inputs[str(args.dataset.resolve())],
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
        print(json.dumps({"candidate": name, **{
            key: summaries[name][key] for key in (
                "selection_score", "decision_eligible", "fit_and_calibration_seconds",
            )
        }, "initial_accuracy": metrics["initial"]["initial_accuracy"],
            "first_third_accuracy": metrics["first_third"]["workflow_equal_weight_accuracy"]}), flush=True)
    eligible = [name for name, row in summaries.items() if row["decision_eligible"]]
    if not eligible:
        raise ValueError("no candidate satisfies the unchanged calibration eligibility gates")
    selected = max(eligible, key=lambda name: summaries[name]["selection_score"])
    write(args.output_dir / "selection.json", {
        "selected": selected, "calibration_summary": summaries, "held_out_evaluated": False,
    })
    print("selected_before_test_evaluation: " + selected, flush=True)
    # Only the chosen predictor and the unchanged reference see the fixed evaluation group.
    held_out = {name: slices(WorkflowWorkModel(models[name]), test) for name in {selected, "baseline"}}
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("a predictor study input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "split_contract": split, "inputs_sha256": inputs,
        "model_sha256": sha256_file(args.output_dir / selected / "model.json"),
        "candidate_selection_used_test_labels": False,
        "prior_baseline_test_metrics_already_known": True,
        "validation_scope": "fixed_development_workflow_holdout_not_new_confirmatory_test",
        "source_provenance_reused_from_bound_dataset": True,
        "actual_ready_paused_accuracy": "not_inferred_from_natural_source_snapshots",
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({
        name: {
            "initial_accuracy": row["initial"]["initial_accuracy"],
            "initial_pair_ranking": row["initial_pair_ranking"],
            "first_third_accuracy": row["first_third"]["workflow_equal_weight_accuracy"],
            "all_accuracy": row["all"]["workflow_equal_weight_accuracy"],
            "long_to_short": row["all"]["long_to_short_rate"],
        } for name, row in held_out.items()
    }), flush=True)


if __name__ == "__main__":
    main()
