#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.predictor.structured_frontier import (
    load_decision_rows,
    runtime_environment_digest,
    select_frontier_hyperparameters,
    validate_training_corpus_diversity,
)
from beliefkv.predictor.action_targets import load_action_target_rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select FrontierBeliefModel parameters within train projects."
    )
    parser.add_argument("--dataset-dir", type=Path, action="append", required=True)
    parser.add_argument("--action-target", type=Path, action="append", required=True)
    parser.add_argument("--minimum-projects", type=int, default=5)
    parser.add_argument("--minimum-tasks", type=int, default=40)
    parser.add_argument("--minimum-workflows", type=int, default=40)
    parser.add_argument("--coverage-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows, manifests = load_decision_rows(
        args.dataset_dir, allowed_splits=("train",)
    )
    action_targets = load_action_target_rows(args.action_target)
    coverage = json.loads(args.coverage_report.read_text(encoding="utf-8"))
    if coverage.get("coverage_gate_passed") is not True:
        raise SystemExit("canonical coverage gate did not pass")
    dataset_manifest_sha256s = [
        hashlib.sha256((path / "dataset_manifest.json").read_bytes()).hexdigest()
        for path in args.dataset_dir
    ]
    if coverage.get("dataset_manifest_sha256") not in dataset_manifest_sha256s:
        raise SystemExit("coverage report does not bind the fitting dataset")
    environment_contract_digests = [
        runtime_environment_digest(
            (item.get("source") or {}).get("runtime_environment_contract") or {}
        )
        for item in manifests
    ]
    diversity = validate_training_corpus_diversity(
        rows,
        minimum_projects=args.minimum_projects,
        minimum_tasks=args.minimum_tasks,
        minimum_workflows=args.minimum_workflows,
    )
    report = select_frontier_hyperparameters(
        rows,
        action_targets=action_targets,
    )
    report["formal_diversity_gate"] = diversity
    report["dataset_dirs"] = [str(path.resolve()) for path in args.dataset_dir]
    report["action_target_paths"] = [
        str(path.resolve()) for path in args.action_target
    ]
    report["action_target_contract_ids"] = sorted(
        {str(row.get("contract_id") or "") for row in action_targets}
    )
    report["dataset_manifest_sha256s"] = dataset_manifest_sha256s
    report["runtime_environment_contract_digests"] = (
        environment_contract_digests
    )
    report["coverage_report_sha256"] = hashlib.sha256(
        args.coverage_report.read_bytes()
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
