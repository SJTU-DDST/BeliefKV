#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    FrontierModelHyperparameters,
    load_decision_rows,
    runtime_environment_digest,
    validate_training_corpus_diversity,
)
from beliefkv.predictor.action_targets import load_action_target_rows


def _repository_state() -> tuple[str, bool]:
    revision = subprocess.check_output(
        ("git", "rev-parse", "HEAD"),
        cwd=REPOSITORY_ROOT,
        text=True,
    ).strip()
    status = subprocess.check_output(
        ("git", "status", "--porcelain", "--untracked-files=normal"),
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    return revision, not bool(status.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the local P6 FrontierBeliefModel")
    parser.add_argument("--dataset-dir", type=Path, action="append", required=True)
    parser.add_argument("--action-target", type=Path, action="append")
    parser.add_argument("--action-target-report", type=Path)
    parser.add_argument("--deployment-runtime-profile", type=Path)
    parser.add_argument(
        "--split", choices=("train", "development"), default="train"
    )
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--minimum-projects", type=int, default=5)
    parser.add_argument("--minimum-tasks", type=int, default=40)
    parser.add_argument("--minimum-workflows", type=int, default=40)
    parser.add_argument(
        "--hyperparameter-selection",
        type=Path,
        help="LOPO selection manifest generated from the same formal train projects.",
    )
    parser.add_argument("--coverage-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    beliefkv_revision, beliefkv_worktree_clean = _repository_state()
    if args.split == "train" and not beliefkv_worktree_clean:
        raise SystemExit(
            "formal predictor training requires a clean BeliefKV worktree"
        )
    rows, manifests = load_decision_rows(
        args.dataset_dir, allowed_splits=(args.split,)
    )
    action_targets = load_action_target_rows(args.action_target or ())
    if args.split == "train" and (
        not action_targets
        or args.action_target_report is None
        or args.deployment_runtime_profile is None
    ):
        raise SystemExit(
            "formal predictor training requires action targets, their report, "
            "and a deployment runtime profile"
        )
    if not rows:
        raise SystemExit(f"no {args.split} decision points were found")
    projects = sorted({str(row.get("project") or "unknown") for row in rows})
    diversity = None
    if args.split == "train":
        diversity = validate_training_corpus_diversity(
            rows,
            minimum_projects=args.minimum_projects,
            minimum_tasks=args.minimum_tasks,
            minimum_workflows=args.minimum_workflows,
        )
    hyperparameters = FrontierModelHyperparameters()
    selection_digest = None
    if args.hyperparameter_selection is not None:
        selection_raw = json.loads(
            args.hyperparameter_selection.read_text(encoding="utf-8")
        )
        if selection_raw.get("selection_method") != (
            "leave_one_train_project_out_project_macro"
        ):
            raise SystemExit("unsupported hyperparameter selection method")
        if sorted(selection_raw.get("projects", ())) != projects:
            raise SystemExit(
                "hyperparameter selection projects do not match fit projects"
            )
        hyperparameters = FrontierModelHyperparameters.from_dict(
            selection_raw.get("selected_hyperparameters")
        )
        if selection_raw.get("action_target_contract_ids") != sorted(
            {str(row.get("contract_id") or "") for row in action_targets}
        ):
            raise SystemExit(
                "hyperparameter selection used a different action-target contract"
            )
        selection_digest = hashlib.sha256(
            args.hyperparameter_selection.read_bytes()
        ).hexdigest()
    model = FrontierBeliefModel(
        model_version=args.model_version,
        hyperparameters=hyperparameters,
    )
    summary = model.fit(rows, action_targets=action_targets)
    tasks = sorted(
        {
            (
                str(row.get("project") or "unknown"),
                str(row.get("instance_id") or "unknown"),
                str(row.get("base_commit") or "unknown"),
            )
            for row in rows
        }
    )
    manifest_digests = [
        hashlib.sha256(
            json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for item in manifests
    ]
    environment_contracts = [
        (item.get("source") or {}).get("runtime_environment_contract")
        for item in manifests
    ]
    if args.split == "train" and any(not item for item in environment_contracts):
        raise SystemExit("formal train input is missing runtime environment provenance")
    environment_digests = [
        runtime_environment_digest(item)
        for item in environment_contracts
        if item
    ]
    dataset_manifest_file_sha256s = [
        hashlib.sha256((path / "dataset_manifest.json").read_bytes()).hexdigest()
        for path in args.dataset_dir
    ]
    coverage_digest = None
    coverage_warnings = []
    if args.split == "train":
        if args.coverage_report is None:
            raise SystemExit("formal training requires a passed coverage report")
        coverage = json.loads(args.coverage_report.read_text(encoding="utf-8"))
        if coverage.get("coverage_gate_passed") is not True:
            raise SystemExit("formal training coverage gate did not pass")
        if coverage.get("dataset_manifest_sha256") not in dataset_manifest_file_sha256s:
            raise SystemExit("coverage report does not bind the fitting dataset")
        coverage_digest = hashlib.sha256(
            args.coverage_report.read_bytes()
        ).hexdigest()
        coverage_warnings = list(coverage.get("coverage_warnings", ()))
    if args.hyperparameter_selection is not None:
        if selection_raw.get("dataset_manifest_sha256s") != dataset_manifest_file_sha256s:
            raise SystemExit("hyperparameter selection used different dataset manifests")
        if selection_raw.get("runtime_environment_contract_digests") != environment_digests:
            raise SystemExit("hyperparameter selection used a different runtime environment")
    model.save(
        args.output,
        metadata={
            "fit_split": args.split,
            "development_only": args.split == "development",
            "dataset_manifest_digests": manifest_digests,
            "dataset_manifest_file_sha256s": dataset_manifest_file_sha256s,
            "runtime_environment_contracts": environment_contracts,
            "semantic_source_runtime_environment_contracts": environment_contracts,
            "runtime_environment_contract_digests": environment_digests,
            "dataset_dirs": [str(item.resolve()) for item in args.dataset_dir],
            "fit_projects": projects,
            "fit_task_count": len(tasks),
            "formal_diversity_gate": diversity,
            "hyperparameter_selection_path": (
                str(args.hyperparameter_selection.resolve())
                if args.hyperparameter_selection is not None
                else None
            ),
            "hyperparameter_selection_sha256": selection_digest,
            "coverage_report_sha256": coverage_digest,
            "coverage_warnings": coverage_warnings,
            "action_target_schema_version": 4,
            "action_target_count": len(action_targets),
            "action_target_contract_ids": sorted(
                {str(row.get("contract_id") or "") for row in action_targets}
            ),
            "action_target_paths": [
                str(path.resolve()) for path in (args.action_target or ())
            ],
            "action_target_report": (
                str(args.action_target_report.resolve())
                if args.action_target_report is not None
                else None
            ),
            "deployment_runtime_profile": (
                json.loads(
                    args.deployment_runtime_profile.read_text(encoding="utf-8")
                )
                if args.deployment_runtime_profile is not None
                else None
            ),
            "deployment_runtime_profile_path": (
                str(args.deployment_runtime_profile.resolve())
                if args.deployment_runtime_profile is not None
                else None
            ),
            "beliefkv_revision": beliefkv_revision,
            "beliefkv_worktree_clean": beliefkv_worktree_clean,
            "calibration_status": "uncalibrated",
            "online_eligible": False,
            "predictive_action_eligible": False,
        },
    )
    print(
        json.dumps(
            {"output": str(args.output), "summary": summary, "diversity": diversity},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
