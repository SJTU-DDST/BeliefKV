"""Test additional original training tasks without changing holdouts or serving policy."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import OBSERVED_RATIOS, R5_SCHEMA, WorkflowWorkModel
from beliefkv.predictor.workflow_work_training import READY_BALANCED
from scripts.extend_workflow_prediction_training import (
    disjoint_training_rows, diverse_task_training_rows,
)
from scripts.improve_workflow_work_distribution import calibration_rejections
from scripts.train_workflow_ready_ranking import metrics, score
from scripts.train_workflow_work import capture_split, fit, write
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


def choose_candidate(summaries):
    eligible = [name for name in CANDIDATES if summaries[name]["decision_eligible"]]
    if not eligible:
        raise ValueError("no candidate passes the unchanged calibration support gates")
    return max(eligible, key=lambda name: summaries[name]["selection_score"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "previous-training-data", "additional-training-data",
                 "baseline-model", "baseline-evaluation", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous task-diversity training and selection outcomes")
    paths = (
        args.dataset, args.cohort, args.previous_training_data, args.additional_training_data,
        args.baseline_model, args.baseline_evaluation,
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
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
