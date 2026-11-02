"""Compare bounded ready-ranking objectives on unchanged complete workflow splits."""

import argparse
from copy import deepcopy
from itertools import combinations
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import OBSERVED_RATIOS, R5_FEATURES, R5_SCHEMA, WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import support_status
from beliefkv.predictor.workflow_work_training import READY_BALANCED, READY_MIXED_PAIRS
from scripts.extend_workflow_prediction_training import disjoint_training_rows
from scripts.improve_workflow_work_distribution import calibration_rejections, slices
from scripts.train_workflow_work import capture_split, fit, model_text, probabilities, write
from scripts.train_workflow_work_distribution import split_rows


def pair_quality(model, rows, *, first_request=False, samples_per_workflow=8):
    """Diagnostic pair population, not a claim that pairs competed in serving."""
    import numpy as np

    ready, completed = (R5_FEATURES.index(name) for name in ("ready", "completed_calls"))
    groups = {}
    for row in rows:
        if (
            row["event"] == "llm_submit" and row["features"][completed] == 0
            if first_request else row["features"][ready] > 0
        ):
            groups.setdefault(row["workflow"], []).append(row)
    selected, ranges = [], {}
    for workflow, values in sorted(groups.items()):
        indices = np.linspace(0, len(values) - 1, min(len(values), samples_per_workflow), dtype=int)
        ranges[workflow] = range(len(selected), len(selected) + len(indices))
        selected.extend(values[index] for index in indices)
    if len(groups) < 2:
        return {"accuracy": None, "workflow_pairs": 0, "snapshot_pairs": 0}
    scores = probabilities(model, selected) @ np.asarray((1., .5, .25))
    coverage = {}
    supported = []
    for row in selected:
        text = model_text(model, row)
        if text not in coverage:
            coverage[text] = model.task_encoding(text)[1]
        supported.append(support_status(model, row["features"], coverage[text]) == "calibrated_distribution")
    effective = np.where(supported, scores, .5)
    summaries, pairs, ties, supported_pairs = [], 0, 0, 0
    for a, b in combinations(sorted(groups), 2):
        values = []
        for left in ranges[a]:
            for right in ranges[b]:
                difference = selected[left]["remaining_work"] - selected[right]["remaining_work"]
                if abs(difference) < 1e-9:
                    continue
                margin = effective[left] - effective[right]
                tie = abs(margin) < 1e-10
                values.append(.5 if tie else float(margin * difference < 0))
                pairs += 1
                ties += int(tie)
                supported_pairs += supported[left] and supported[right]
        if values:
            summaries.append(sum(values) / len(values))
    return {
        "accuracy": sum(summaries) / len(summaries) if summaries else None,
        "workflow_pairs": len(summaries), "snapshot_pairs": pairs,
        "effective_ties": ties, "both_supported_pairs": supported_pairs,
        "population": "equal_workflow_pairs_of_observed_states_not_actual_competition",
        "maximum_states_per_workflow": samples_per_workflow,
    }


def metrics(model, rows):
    return {
        **slices(model, rows),
        "ready_cross_progress_pairs": pair_quality(model, rows),
        "first_request_pairs": pair_quality(model, rows, first_request=True),
    }


def score(values):
    return (
        values["initial_pair_ranking"]["accuracy"]
        + values["first_request_pairs"]["accuracy"]
        + values["ready_cross_progress_pairs"]["accuracy"]
        + values["natural_ready"]["workflow_equal_weight_accuracy"]
    ) / 4.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "extra-training-data", "baseline-model",
                 "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--family", choices=("ready-pairs", "observed-ratios"), default="ready-pairs")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve the completed and rejected ready-ranking studies")
    paths = (args.dataset, args.cohort, args.extra_training_data,
             args.baseline_model, args.baseline_evaluation)
    inputs = {str(path.resolve()): sha256_file(path) for path in paths}
    data, cohort, extra, baseline, evaluation = (json.loads(path.read_bytes()) for path in paths)
    train, calibration, test, original_split = split_rows(data["rows"], cohort["measured_workflow_ids"])
    if (
        data["schema"] != R5_SCHEMA or data["prefill_token_weight"] != .01
        or evaluation["model_sha256"] != inputs[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != inputs[str(args.dataset.resolve())]
        or extra["training_extension"]["original_dataset_sha256"] != inputs[str(args.dataset.resolve())]
    ):
        raise ValueError("require the exact source-bound expanded predictor")
    train = disjoint_training_rows(train, extra["rows"], calibration, test)
    split = capture_split(train, calibration, test, require_capture_disjoint=False)
    if split != evaluation["expanded_split"] or split != extra["training_extension"]["expanded_split"]:
        raise ValueError("preserve the original complete-workflow holdouts and expanded training")
    shared = {
        "hidden_width": 128, "vocabulary_size": 8192, "dropout": .1,
        "weight_decay": .05, "training_weighting": READY_BALANCED,
        "ranking_pair_mode": READY_MIXED_PAIRS,
    }
    candidates = [
        ("retained_phase_rank", None),
        ("ready_mixed_rank0.1", {"ranking_loss_weight": .1}),
        ("ready_mixed_rank0.3", {"ranking_loss_weight": .3}),
        ("ready_mixed_rank0.1_ordinal0.5", {"ranking_loss_weight": .1, "ordinal_loss_weight": .5}),
        ("phase_rank0.1_ordinal0.5", {
            "ranking_loss_weight": .1, "ordinal_loss_weight": .5, "ranking_pair_mode": "phase_matched",
        }),
    ]
    if args.family == "observed-ratios":
        candidates = [
            ("retained_phase_rank", None),
            ("ratios_phase_rank0.1", {
                "ranking_loss_weight": .1, "ranking_pair_mode": "phase_matched",
                "numeric_feature_transform": OBSERVED_RATIOS,
            }),
            ("ratios_ready_rank0.1", {
                "ranking_loss_weight": .1, "numeric_feature_transform": OBSERVED_RATIOS,
            }),
            ("ratios_classification", {
                "ranking_loss_weight": 0., "ranking_pair_mode": "phase_matched",
                "numeric_feature_transform": OBSERVED_RATIOS,
            }),
        ]
    write(args.output_dir / "plan.json", {
        "candidates": [(name, None if settings is None else {**shared, **settings})
                       for name, settings in candidates],
        "epochs": 200, "original_split": original_split, "expanded_split": split,
        "frozen_work_thresholds": baseline["work_thresholds"],
        "selection": "unchanged_support_gates_then_mean_initial_first_request_ready_ranking_ready_accuracy",
        "development_labels_used_for_selection": False,
        "ready_pairs_are_constructed_diagnostics_not_actual_competition": True,
        "runtime_model_hidden_width_unchanged": True,
        "raw_observation_events_task_scope_and_workload_unchanged": True,
        "derived_numeric_features_added": args.family == "observed-ratios",
        "inputs_sha256": inputs, "GPU_action": False,
    })
    models, summaries = {}, {}
    for name, settings in candidates:
        print("fitting: " + name, flush=True)
        started = time.monotonic()
        raw = deepcopy(baseline) if settings is None else fit(
            train, calibration, epochs=200, feature_schema=R5_SCHEMA, split_mode="workflow",
            work_thresholds=baseline["work_thresholds"], **{**shared, **settings},
        )
        if settings is not None:
            raw["metadata"].update(
                prefill_token_weight=.01, split_contract=split, decision_eligible=True,
                progress_observation_contract=data["progress_observation_contract"],
                online_features_use_observed_prefix_only=True,
                unseen_project_generalization_claimed=False,
                training_dataset_sha256=inputs[str(args.dataset.resolve())],
                training_extension_sha256=inputs[str(args.extra_training_data.resolve())],
                candidate_selection_uses_calibration_only=True,
            )
            for key in ("admission_cost_profile", "free_dispatch_coalescing_window_ms"):
                raw[key] = deepcopy(baseline[key])
        values = metrics(WorkflowWorkModel(raw), calibration)
        reasons = calibration_rejections(values, raw)
        raw["metadata"].update(
            decision_eligible=not reasons, decision_rejection_reasons=reasons,
            calibration_distribution_metrics=values["all"],
        )
        models[name] = raw
        summaries[name] = {
            "calibration": values, "selection_score": score(values),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
            "fit_and_calibration_seconds": time.monotonic() - started,
        }
        write(args.output_dir / name / "model.json", raw)
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({
            "candidate": name, "score": score(values), "rejections": reasons,
            "ready_pairs": values["ready_cross_progress_pairs"]["accuracy"],
            "initial_pairs": values["initial_pair_ranking"]["accuracy"],
            "first_request_pairs": values["first_request_pairs"]["accuracy"],
        }), flush=True)
    selected = max(
        (name for name, row in summaries.items() if row["decision_eligible"]),
        key=lambda name: summaries[name]["selection_score"],
    )
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {name: metrics(WorkflowWorkModel(models[name]), test)
                for name in dict.fromkeys(("retained_phase_rank", selected))}
    write(args.output_dir / "model.json", models[selected])
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("a frozen ready-ranking input changed")
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        "original_split": original_split, "expanded_split": split,
        "inputs_sha256": inputs, "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "tasks_tokens_tools_arrivals_cohort_and_work_thresholds_preserved": True,
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "ready_pairs": row["ready_cross_progress_pairs"]["accuracy"],
        "initial_pairs": row["initial_pair_ranking"]["accuracy"],
        "first_request_pairs": row["first_request_pairs"]["accuracy"],
        "accuracy": row["all"]["workflow_equal_weight_accuracy"],
        "NLL": row["all"]["workflow_equal_weight_NLL"],
        "long_to_short": row["all"]["long_to_short_rate"],
    } for name, row in held_out.items()}), flush=True)


if __name__ == "__main__":
    main()
