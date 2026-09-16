#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.predictor.action_targets import load_action_target_rows  # noqa: E402
from beliefkv.predictor.structured_frontier import (  # noqa: E402
    FrontierBeliefModel,
    FrontierModelHyperparameters,
    evaluate_frontier_model,
    load_decision_rows,
    load_evaluation_rows,
    summarize_training_corpus,
)


def _paths(values: list[Path]) -> list[str]:
    return [str(path.resolve()) for path in values]


def _load_hyperparameters(path: Path) -> FrontierModelHyperparameters:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("selection_method") != "leave_one_train_project_out_project_macro":
        raise SystemExit("adaptation requires a train-only LOPO selection artifact")
    return FrontierModelHyperparameters.from_dict(
        payload.get("selected_hyperparameters")
    )


def _merge_rows(
    base_rows: list[dict[str, Any]],
    adaptation_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in (*base_rows, *adaptation_rows):
        decision_id = str(row.get("decision_id") or "")
        if not decision_id or decision_id in seen:
            raise SystemExit(f"missing or duplicate decision identity: {decision_id!r}")
        seen.add(decision_id)
        merged.append(row)
    return merged


def _metric_view(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_point_count": metrics["decision_point_count"],
        "workflow_count": metrics["workflow_count"],
        "action_timing": metrics["wait_slack"],
        "action_head_availability": metrics["action_head_availability"],
        "ood_fallback_rate": metrics["ood_fallback_rate"],
        "boundary": metrics["classification"]["boundary"],
        "tool_terminal": metrics["classification"]["tool_terminal"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fit a development-only FrontierBelief adaptation from formal train "
            "plus one current-policy baseline trace."
        )
    )
    parser.add_argument("--base-dataset-dir", type=Path, action="append", required=True)
    parser.add_argument(
        "--adaptation-dataset-dir", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--calibration-dataset-dir", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--adaptation-action-target", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--calibration-action-target", type=Path, action="append", required=True
    )
    parser.add_argument("--hyperparameter-selection", type=Path, required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--uncalibrated-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metrics-output", type=Path, required=True)
    parser.add_argument("--target-coverage", type=float, default=0.9)
    args = parser.parse_args()

    if args.output.resolve() == args.uncalibrated_output.resolve():
        raise SystemExit("calibrated and uncalibrated outputs must differ")

    base_rows, base_manifests = load_decision_rows(
        args.base_dataset_dir, allowed_splits=("train",)
    )
    adaptation_rows, adaptation_manifests = load_decision_rows(
        args.adaptation_dataset_dir, allowed_splits=("development",)
    )
    calibration_rows, calibration_manifests = load_evaluation_rows(
        args.calibration_dataset_dir,
        split="calibration",
        allow_formal_local=True,
    )
    if not adaptation_rows:
        raise SystemExit("baseline adaptation contains no eligible decision rows")

    adaptation_targets = load_action_target_rows(args.adaptation_action_target)
    calibration_targets = load_action_target_rows(args.calibration_action_target)
    if not adaptation_targets or not calibration_targets:
        raise SystemExit("adaptation and calibration action targets are required")
    contract_ids = {
        str(row.get("contract_id") or "")
        for row in (*adaptation_targets, *calibration_targets)
    }
    if len(contract_ids) != 1 or "" in contract_ids:
        raise SystemExit("action-target contracts do not match")

    rows = _merge_rows(base_rows, adaptation_rows)
    model = FrontierBeliefModel(
        model_version=args.model_version,
        hyperparameters=_load_hyperparameters(args.hyperparameter_selection),
    )
    fit_summary = model.fit(rows)
    base_projects = {str(row.get("project") or "unknown") for row in base_rows}
    adaptation_projects = {
        str(row.get("project") or "unknown") for row in adaptation_rows
    }
    calibration_projects = {
        str(row.get("project") or "unknown") for row in calibration_rows
    }
    if base_projects.intersection(calibration_projects):
        raise SystemExit("formal train and calibration projects overlap")

    metadata = {
        "fit_split": "formal_train_plus_baseline_development_adaptation",
        "development_only": True,
        "online_eligible": False,
        "predictive_action_eligible": False,
        "test_id_status": "sealed_not_evaluated",
        "base_dataset_dirs": _paths(args.base_dataset_dir),
        "adaptation_dataset_dirs": _paths(args.adaptation_dataset_dir),
        "calibration_dataset_dirs": _paths(args.calibration_dataset_dir),
        "base_manifest_count": len(base_manifests),
        "adaptation_manifest_count": len(adaptation_manifests),
        "calibration_manifest_count": len(calibration_manifests),
        "base_corpus": summarize_training_corpus(base_rows),
        "adaptation_corpus": summarize_training_corpus(adaptation_rows),
        "combined_corpus": summarize_training_corpus(rows),
        "base_projects": sorted(base_projects),
        "adaptation_projects": sorted(adaptation_projects),
        "calibration_projects": sorted(calibration_projects),
        "hyperparameter_selection_path": str(
            args.hyperparameter_selection.resolve()
        ),
        "action_target_contract_ids": sorted(contract_ids),
        "adaptation_action_target_paths": _paths(args.adaptation_action_target),
        "calibration_action_target_paths": _paths(args.calibration_action_target),
        "adaptation_action_target_count": len(adaptation_targets),
        "calibration_action_target_count": len(calibration_targets),
        "calibration_status": "uncalibrated",
        "scope_warning": (
            "The latest baseline is part of fitting data. This artifact may only "
            "validate prediction-to-action mechanics on that workload; it cannot "
            "establish generalization or unbiased A/B gains."
        ),
    }
    model.save(args.uncalibrated_output, metadata=metadata)

    calibration_summary = model.calibrate(
        calibration_rows,
        target_coverage=args.target_coverage,
        action_targets=calibration_targets,
    )
    metadata.update(
        {
            "calibration_status": "calibrated",
            "calibration_split": "held_out_formal_calibration",
            "calibration_summary": calibration_summary,
        }
    )
    model.save(args.output, metadata=metadata)

    calibration_metrics = evaluate_frontier_model(
        model, calibration_rows, calibration_targets
    )
    payload = {
        "schema_version": 1,
        "model": str(args.output.resolve()),
        "model_version": args.model_version,
        "development_only": True,
        "fit_summary": fit_summary,
        "corpora": {
            "base": metadata["base_corpus"],
            "adaptation": metadata["adaptation_corpus"],
            "combined": metadata["combined_corpus"],
        },
        "held_out_calibration": _metric_view(calibration_metrics),
        "baseline_adaptation_support": {
            "decision_point_count": len(adaptation_rows),
            "action_target_count": len(adaptation_targets),
            "workflow_count": metadata["adaptation_corpus"]["workflow_count"],
            "project_count": metadata["adaptation_corpus"]["project_count"],
        },
        "action_timing_calibration": model.action_timing_calibration,
        "interpretation": {
            "held_out_calibration": "model-quality regression guard",
            "baseline_adaptation_support": (
                "fitting support only; train/development accuracy is deliberately "
                "not computed"
            ),
        },
    }
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
