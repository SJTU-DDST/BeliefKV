"""Test additional original training tasks without changing holdouts or serving policy."""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import (
    OBSERVED_RATIOS, R5_SCHEMA, WorkflowWorkModel, before_observed_work,
)
from beliefkv.predictor.workflow_work_distribution import support_status
from beliefkv.predictor.workflow_work_training import READY_BALANCED, project_task_weights, ready_weights
from scripts.extend_workflow_prediction_training import (
    disjoint_training_rows, diverse_task_training_rows,
)
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_work import capture_split, fit, probabilities, write
from scripts.train_workflow_work_distribution import split_rows


CANDIDATES = ("retained", "new_progress", "new_initial", "new_progress_and_initial")


def training_population(dataset, cohort, previous, additional, evaluation, dataset_sha256):
    if (
        any(value.get("schema") != R5_SCHEMA or value.get("prefill_token_weight") != .01
            for value in (dataset, previous, additional))
        or additional.get("progress_observation_contract") != dataset["progress_observation_contract"]
        or previous.get("progress_observation_contract") != dataset["progress_observation_contract"]
        or additional.get("observed_delegation_inputs")
        or previous["training_extension"]["original_dataset_sha256"] != dataset_sha256
    ):
        raise ValueError("new task training must preserve the exact causal feature and label contract")
    original, calibration, development, original_split = split_rows(
        dataset["rows"], cohort["measured_workflow_ids"],
    )
    if original_split != evaluation["original_split"]:
        raise ValueError("the original complete-workflow split changed")
    existing = disjoint_training_rows(original, previous["rows"], calibration, development)
    previous_split = capture_split(existing, calibration, development, require_capture_disjoint=False)
    if (
        previous_split != previous["training_extension"]["expanded_split"]
        or previous_split != evaluation["expanded_split"]
    ):
        raise ValueError("the retained model and previous training executions differ")
    expanded = diverse_task_training_rows(existing, additional["rows"], calibration, development)
    return expanded, calibration, development, {
        "original_split": original_split, "previous_expanded_split": previous_split,
        "expanded_split": capture_split(expanded, calibration, development, require_capture_disjoint=False),
        "previous_training_executions": len({row["workflow"] for row in existing}),
        "expanded_training_executions": len({row["workflow"] for row in expanded}),
        "training_projects": len({row["project"] for row in expanded}),
        "new_independent_projects": 0,
    }


def candidate_model(name, baseline, progress=None, initial=None):
    if name not in CANDIDATES:
        raise ValueError("unknown prespecified task-diversity candidate")
    if name == "retained":
        return deepcopy(baseline)
    needs_progress = name in ("new_progress", "new_progress_and_initial")
    needs_initial = name in ("new_initial", "new_progress_and_initial")
    if needs_progress and progress is None or needs_initial and initial is None:
        raise ValueError("the selected task-diversity component has not been trained")
    raw = deepcopy(progress if needs_progress else baseline)
    if needs_initial:
        if "initial_state_head" in initial:
            raise ValueError("an initial component cannot contain a nested conditional head")
        raw["initial_state_head"] = {
            "kind": "before_observed_work_and_children_v1", "blend": 1.,
            "model": deepcopy(initial),
        }
    elif "initial_state_head" in baseline:
        raw["initial_state_head"] = deepcopy(baseline["initial_state_head"])
    for key in ("admission_cost_profile", "free_dispatch_coalescing_window_ms"):
        raw[key] = deepcopy(baseline[key])
    return raw


def choose_candidate(summaries, order=CANDIDATES):
    eligible = [name for name in order if summaries[name]["decision_eligible"]]
    if not eligible:
        raise ValueError("no candidate passes the unchanged calibration support gates")
    return max(eligible, key=lambda name: summaries[name]["selection_score"])


def initial_training_rows(rows):
    selected = [row for row in rows if before_observed_work(row["features"])]
    if (
        {row["workflow"] for row in selected} != {row["workflow"] for row in rows}
        or any(row["attained_work"] != 0. for row in selected)
    ):
        raise ValueError("conditional initial training requires an unserved state of every complete task")
    return selected


def initial_project_folds(rows, *, count=5):
    import numpy as np

    projects = sorted({row["project"] for row in rows})
    if not 3 <= count <= len(projects):
        raise ValueError("initial calibration requires at least three complete project folds")
    shuffled = np.random.default_rng(21).permutation(projects).tolist()
    assignments = {project: index % count for index, project in enumerate(shuffled)}
    return [
        ([index for index, row in enumerate(rows) if assignments[row["project"]] != fold],
         [index for index, row in enumerate(rows) if assignments[row["project"]] == fold])
        for fold in range(count)
    ]


def cross_project_initial_calibration(rows, head, settings, *, width):
    """Temperature and class support use predictions excluding each complete query project."""
    import numpy as np
    from scipy.optimize import minimize_scalar

    folds = initial_project_folds(rows, count=min(5, len({row["project"] for row in rows})))
    output, coverage, receipts = np.full((len(rows), 3), np.nan), np.zeros(len(rows), dtype=int), []
    for training_indices, validation_indices in folds:
        training = [rows[index] for index in training_indices]
        validation = [rows[index] for index in validation_indices]
        raw = fit(
            training, validation, hidden_width=width,
            **{**settings, "split_mode": "project"},
        )
        raw["temperature"] = 1.
        output[validation_indices] = probabilities(WorkflowWorkModel(raw), validation)
        coverage[validation_indices] += 1
        receipts.append({
            "training_projects": sorted({row["project"] for row in training}),
            "held_out_projects": sorted({row["project"] for row in validation}),
            "training_workflows": sorted({row["workflow"] for row in training}),
            "held_out_workflows": sorted({row["workflow"] for row in validation}),
            "unscaled_model_sha256": hashlib.sha256(
                json.dumps(raw, sort_keys=True).encode(),
            ).hexdigest(),
        })
    if not np.isfinite(output).all() or not (coverage == 1).all():
        raise ValueError("initial cross-project calibration must cover every training state exactly once")
    labels = np.searchsorted(
        head["work_thresholds"], [row["remaining_work"] for row in rows], side="right",
    )
    counts = Counter(row["workflow"] for row in rows)
    weights = np.asarray([1. / counts[row["workflow"]] for row in rows])
    weights /= weights.sum()
    weights = project_task_weights(rows, ready_weights(rows, weights))
    logs = np.log(np.maximum(output, 1e-12))

    def loss(value):
        scaled = logs / math.exp(value)
        scaled -= scaled.max(axis=1, keepdims=True)
        return float(np.sum(weights * (
            np.log(np.exp(scaled).sum(axis=1)) - scaled[np.arange(len(labels)), labels]
        )))

    temperature = math.exp(float(minimize_scalar(loss, bounds=(-1.6, 3.), method="bounded").x))
    workflow_support = [
        len({row["workflow"] for row, label in zip(rows, labels) if label == target})
        for target in range(3)
    ]
    project_support = [
        len({row["project"] for row, label in zip(rows, labels) if label == target})
        for target in range(3)
    ]
    fixed_support = head["metadata"]["calibration_class_workflows"]
    head["temperature"] = temperature
    head["metadata"].update(
        calibration_status="project_heldout_temperature",
        calibration_class_workflows=workflow_support,
        calibration_class_projects=project_support,
        initial_fixed_calibration_class_workflows=fixed_support,
        initial_probability_calibration="training_projects_out_of_fold_unscaled_predictions",
        initial_calibration_folds=len(folds),
        fixed_calibration_and_development_projects_used_for_internal_fitting=False,
    )
    return {
        "temperature": temperature, "folds": receipts,
        "project_class_support": project_support, "workflow_class_support": workflow_support,
        "original_fixed_calibration_class_workflows": fixed_support,
        "full_training_project_held_out_for_each_prediction": True,
        "fold_model_temperatures_discarded": True,
        "fixed_calibration_group_used_only_for_final_candidate_selection_and_validation": True,
        "records": [
            {"workflow": row["workflow"], "project": row["project"], "snapshot": row["snapshot"],
             "unscaled_probabilities": values.tolist(), "class": int(label)}
            for row, values, label in zip(rows, output, labels)
        ],
    }


def initial_head_usage(raw, rows):
    model = WorkflowWorkModel(raw)
    head = model.initial_head
    if head is None:
        return {"supported": 0, "states": 0, "reasons": {}}
    reasons = Counter()
    encodings = {}
    for row in initial_training_rows(rows):
        text = row.get("task_text", row["text"])
        if text not in encodings:
            encodings[text] = head.task_encoding(text)
        reasons[support_status(head, row["features"], encodings[text][1])] += 1
    return {
        "supported": reasons["calibrated_distribution"], "states": sum(reasons.values()),
        "reasons": dict(reasons),
    }


def conditional_initial_study(
    training, calibration, development, baseline, population, pins, output, *,
    cross_project_calibration=False,
):
    previous_ids = set(population["previous_expanded_split"]["training_execution_sha256"])
    existing = [row for row in training if row["source_workflow_sha256"] in previous_ids]
    initial_previous = initial_training_rows(existing)
    initial_expanded = initial_training_rows(training)
    initial_calibration = initial_training_rows(calibration)
    settings = {
        "epochs": 200, "feature_schema": R5_SCHEMA, "split_mode": "workflow",
        "vocabulary_size": 8192, "dropout": .3, "weight_decay": .1,
        "training_weighting": READY_BALANCED, "ranking_loss_weight": .1,
        "ranking_pair_mode": "phase_matched", "balance_training_projects": True,
        "numeric_feature_transform": OBSERVED_RATIOS,
        "work_thresholds": baseline["work_thresholds"],
    }
    candidates = {
        "retained": None,
        "initial_previous32": (initial_previous, 32),
        "initial_expanded32": (initial_expanded, 32),
        "initial_expanded64": (initial_expanded, 64),
    }
    write(output / "plan.json", {
        "candidates": {
            name: None if specification is None else {
                "training_workflows": len({row["workflow"] for row in specification[0]}),
                "initial_training_states": len(specification[0]), "hidden_width": specification[1],
            } for name, specification in candidates.items()
        },
        "training_settings": settings, **population,
        "condition_uses_only_observed_features": True,
        "initial_condition": "zero_observed_input_output_and_no_observed_child",
        "progress_probabilities_after_condition_are_original_exactly": True,
        "calibration_states": len(initial_calibration),
        "initial_temperature_calibration": (
            "training_project_grouped_out_of_fold"
            if cross_project_calibration else "original_fixed_initial_states"
        ),
        "selection": "unchanged_calibration_support_gates_then_existing_ready_ranking_score",
        "development_labels_used_for_selection": False, "inputs_sha256": pins, "GPU_action": False,
    })
    models, summaries = {}, {}
    for name, specification in candidates.items():
        if specification is None:
            raw = deepcopy(baseline)
        else:
            rows, width = specification
            print("fitting_conditional_initial: " + name, flush=True)
            head = fit(rows, initial_calibration, hidden_width=width, **settings)
            if cross_project_calibration:
                receipt = cross_project_initial_calibration(rows, head, settings, width=width)
                write(output / name / "cross_project_calibration.json", receipt)
            head["metadata"].update(
                prefill_token_weight=.01, decision_eligible=True,
                online_features_use_observed_prefix_only=True,
                progress_observation_contract=baseline["metadata"]["progress_observation_contract"],
                training_dataset_sha256=baseline["metadata"]["training_dataset_sha256"],
                candidate_selection_uses_calibration_only=True,
                conditional_initial_training_states=len(rows),
                conditional_initial_training_workflows=len({row["workflow"] for row in rows}),
                initial_condition="zero_observed_input_output_and_no_observed_child",
            )
            raw = candidate_model("new_initial", baseline, initial=head)
            write(output / name / "component.json", head)
        values = metrics(WorkflowWorkModel(raw), calibration)
        reasons = calibration_rejections(values, raw)
        usage = initial_head_usage(raw, calibration)
        if specification is not None and not usage["supported"]:
            reasons.append("conditional_initial_head_has_no_supported_activation")
        if specification is not None:
            raw["metadata"].update(
                decision_eligible=not reasons, decision_rejection_reasons=reasons,
                calibration_distribution_metrics=values["all"],
                initial_head_selection_uses_calibration_only=True,
                initial_head_training_on_actual_activation_condition_only=True,
            )
        models[name] = raw
        summaries[name] = {
            "calibration": values, "selection_score": score(values),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
            "initial_head_usage": usage,
        }
        write(output / name / "model.json", raw)
        write(output / name / "calibration.json", summaries[name])
        print(json.dumps({"candidate": name, "score": score(values), "rejections": reasons}), flush=True)
    selected = choose_candidate(summaries, candidates)
    write(output / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {name: metrics(WorkflowWorkModel(models[name]), development)
                for name in dict.fromkeys(("retained", selected))}
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a frozen conditional initial training input changed")
    write(output / "model.json", models[selected])
    write(output / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        **population, "inputs_sha256": pins, "model_sha256": sha256_file(output / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "initial_ranking": values["initial_pair_ranking"]["accuracy"],
        "first_request_ranking": values["first_request_pairs"]["accuracy"],
        "ready_ranking": values["ready_cross_progress_pairs"]["accuracy"],
        "classification": values["all"]["workflow_equal_weight_accuracy"],
        "long_to_short": values["all"]["long_to_short_rate"],
    } for name, values in held_out.items()}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "additional-training-data",
                 "baseline-model", "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--family", choices=("whole_task", "conditional_initial"), default="whole_task")
    parser.add_argument("--initial-calibration", choices=("fixed", "cross_project"), default="fixed")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous task-diversity training and selection outcomes")
    paths = (
        args.dataset, args.cohort, args.previous_training_data, args.additional_training_data,
        args.baseline_model, args.baseline_evaluation,
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    pins.update({str(path.resolve()): sha256_file(path) for path in (
        Path(__file__), ROOT / "scripts/train_workflow_work.py",
        ROOT / "beliefkv/predictor/workflow_work_training.py",
        ROOT / "beliefkv/predictor/workflow_work.py",
        ROOT / "beliefkv/predictor/workflow_work_distribution.py",
    )})
    dataset, cohort, previous, additional, baseline, evaluation = (
        json.loads(path.read_bytes()) for path in paths
    )
    dataset_hash = pins[str(args.dataset.resolve())]
    if (
        evaluation["model_sha256"] != pins[str(args.baseline_model.resolve())]
        or evaluation["inputs_sha256"].get(str(args.dataset.resolve())) != dataset_hash
    ):
        raise ValueError("task-diversity training requires the exact retained model and evaluation")
    for value in (dataset, previous, additional):
        if not value.get("sources_sha256"):
            raise ValueError("require a source-bound complete natural execution export")
        for path, digest in value["sources_sha256"].items():
            if path in pins and pins[path] != digest:
                raise ValueError("conflicting complete-source training identities")
            pins[path] = digest
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a complete training execution or label source changed")
    training, calibration, development, population = training_population(
        dataset, cohort, previous, additional, evaluation, dataset_hash,
    )
    if args.family == "conditional_initial":
        conditional_initial_study(
            training, calibration, development, baseline, population, pins, args.output_dir,
            cross_project_calibration=args.initial_calibration == "cross_project",
        )
        return
    settings = {
        "epochs": 200, "feature_schema": R5_SCHEMA, "split_mode": "workflow",
        "hidden_width": 128, "vocabulary_size": 8192, "dropout": .1, "weight_decay": .05,
        "training_weighting": READY_BALANCED, "ranking_loss_weight": .1,
        "ranking_pair_mode": "phase_matched", "balance_training_projects": True,
        "work_thresholds": baseline["work_thresholds"],
    }
    write(args.output_dir / "plan.json", {
        "candidates": list(CANDIDATES), "training_settings": settings, **population,
        "initial_numeric_transform": OBSERVED_RATIOS,
        "initial_condition": "zero_observed_input_output_and_no_observed_child",
        "selection": "unchanged_calibration_support_gates_then_existing_ready_ranking_score",
        "development_labels_used_for_selection": False,
        "formal_workload_arrivals_tools_and_serving_policy_unchanged": True,
        "inputs_sha256": pins, "GPU_action": False,
    })
    components = {}
    for name, transform in (("progress", None), ("initial", OBSERVED_RATIOS)):
        print("fitting_component: " + name, flush=True)
        started = time.monotonic()
        raw = fit(training, calibration, numeric_feature_transform=transform, **settings)
        raw["metadata"].update(
            prefill_token_weight=.01, split_contract=population["expanded_split"],
            decision_eligible=True,
            progress_observation_contract=dataset["progress_observation_contract"],
            online_features_use_observed_prefix_only=True,
            training_dataset_sha256=dataset_hash,
            training_extension_sha256=pins[str(args.additional_training_data.resolve())],
            candidate_selection_uses_calibration_only=True,
            unseen_project_generalization_claimed=False,
        )
        for key in ("admission_cost_profile", "free_dispatch_coalescing_window_ms"):
            raw[key] = deepcopy(baseline[key])
        components[name] = raw
        write(args.output_dir / "components" / (name + ".json"), raw)
        print(json.dumps({
            "component": name, "fit_seconds": time.monotonic() - started,
        }), flush=True)
    models, summaries = {}, {}
    for name in CANDIDATES:
        raw = candidate_model(name, baseline, **components)
        values = metrics(WorkflowWorkModel(raw), calibration)
        reasons = calibration_rejections(values, raw)
        if name != "retained":
            raw["metadata"].update(
                calibration_distribution_metrics=values["all"],
                decision_eligible=not reasons, decision_rejection_reasons=reasons,
                original_training_executions=population["previous_training_executions"],
                expanded_training_executions=population["expanded_training_executions"],
                initial_head_selection_uses_calibration_only=True,
            )
        models[name] = raw
        summaries[name] = {
            "calibration": values, "selection_score": score(values),
            "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
        }
        write(args.output_dir / name / "model.json", raw)
        write(args.output_dir / name / "calibration.json", summaries[name])
        print(json.dumps({"candidate": name, "score": score(values), "rejections": reasons}), flush=True)
    selected = choose_candidate(summaries)
    write(args.output_dir / "selection.json", {"selected": selected, "calibration_summary": summaries})
    print("selected_before_development: " + selected, flush=True)
    held_out = {name: metrics(WorkflowWorkModel(models[name]), development)
                for name in dict.fromkeys(("retained", selected))}
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a source or fixed training input changed during task-diversity learning")
    write(args.output_dir / "model.json", models[selected])
    write(args.output_dir / "evaluation.json", {
        "selected": selected, "held_out": held_out, "calibration_summary": summaries,
        **population, "inputs_sha256": pins,
        "model_sha256": sha256_file(args.output_dir / "model.json"),
        "candidate_selection_used_test_labels": False,
        "validation_scope": "reused_fixed_development_holdout_not_confirmatory",
        "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({name: {
        "initial_ranking": values["initial_pair_ranking"]["accuracy"],
        "first_request_ranking": values["first_request_pairs"]["accuracy"],
        "constructed_ready_ranking": values["ready_cross_progress_pairs"]["accuracy"],
        "classification_accuracy": values["all"]["workflow_equal_weight_accuracy"],
        "long_to_short": values["all"]["long_to_short_rate"],
    } for name, values in held_out.items()}), flush=True)


if __name__ == "__main__":
    main()
