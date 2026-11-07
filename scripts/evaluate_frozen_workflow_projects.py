"""Evaluate the retained predictor on existing source projects absent from all prior fitting data."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from scripts.evaluate_workflow_continuous_projects import (
    combine_datasets, evaluate_fold, forecast_metrics, ranking_metrics,
)
from scripts.train_workflow_work import write
from scripts.train_workflow_work_distribution import distribution_metrics, export


def unseen_workflows(composition, population):
    projects = [
        {
            project for phase in ("training", "calibration", "evaluation")
            for project in fold[phase]["projects"]
        } for fold in population["folds"]
    ]
    if not projects or any(group != projects[0] for group in projects):
        raise ValueError("the prior complete prediction population is inconsistent across folds")
    result = [row for row in composition["selected_workflows"] if row["project"] not in projects[0]]
    if not result or len({row["project"] for row in result}) != len(result):
        raise ValueError("require complete previously unseen original projects")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("labels", "population-plan", "model", "continuous-model", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output_dir.exists():
        raise ValueError("hide CUDA and preserve prior project-heldout evidence")
    paths = [args.labels, args.population_plan, args.model, args.continuous_model, Path(__file__),
             ROOT / "scripts/evaluate_workflow_continuous_projects.py",
             ROOT / "scripts/train_workflow_work_distribution.py",
             ROOT / "beliefkv/predictor/workflow_work.py",
             ROOT / "beliefkv/predictor/workflow_continuous_work.py"]
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    labels, population, raw, continuous = (
        json.loads(path.read_bytes()) for path in (
            args.labels, args.population_plan, args.model, args.continuous_model,
        )
    )
    composition_path = Path(labels["source_binding"]["source_arm"]) / "composite_source.json"
    composition = json.loads(composition_path.read_bytes())
    selected = unseen_workflows(composition, population)
    if continuous["metadata"].get("continuous_work_source_model_sha256") != pins[str(args.model.resolve())]:
        raise ValueError("the continuous readout belongs to a different frozen predictor")
    write(args.output_dir / "plan.json", {
        "inputs_sha256": pins,
        "selected_projects": [row["project"] for row in selected],
        "selected_workflows": [row["workflow_id"] for row in selected],
        "selection": "all_source_projects_absent_from_training_calibration_and_development_population",
        "model_retrained_or_calibrated_on_selected_projects": False,
        "GPU_action": False, "new_blind_population_or_large_sample_claimed": False,
    })
    print("exporting existing natural sources for unseen projects", flush=True)
    dataset = export(args.labels, workflow_ids=[row["workflow_id"] for row in selected])
    combine_datasets([dataset])
    pins.update(dataset["sources_sha256"])
    write(args.output_dir / "dataset.json", dataset)
    original = WorkflowWorkModel(raw)
    candidate_raw = deepcopy(raw)
    candidate_raw["continuous_work_readout"] = continuous["continuous_work_readout"]
    candidate = WorkflowWorkModel(candidate_raw)
    predictions = evaluate_fold(candidate, dataset["rows"])
    result = {
        "inputs_sha256": pins, "projects": [row["project"] for row in selected],
        "frozen_model_distribution": distribution_metrics(original, dataset["rows"]),
        "binned": forecast_metrics(predictions, method="binned"),
        "continuous": forecast_metrics(predictions, method="continuous"),
        "raw_ready_diagnostic": {
            name: ranking_metrics(
                predictions, [row["raw_" + name + "_weight"] for row in predictions], phase="ready",
            ) for name in ("binned", "continuous")
        },
        "sample_scope": "retrospective_unseen_projects_existing_natural_traces",
        "no_training_calibration_or_model_selection_used_these_projects": True,
        "too_few_projects_for_broad_generalization_or_JCT_claim": True,
        "GPU_action": False, "serving_model_or_experiment_changed": False,
    }
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("an exact held-out source or frozen model changed")
    write(args.output_dir / "predictions.json", predictions)
    write(args.output_dir / "evaluation.json", result)
    print(json.dumps({
        "projects": result["projects"], "distribution": result["frozen_model_distribution"],
        "binned": result["binned"], "continuous": result["continuous"],
    }), flush=True)


if __name__ == "__main__":
    main()
