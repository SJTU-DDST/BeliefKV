"""Reuse complete original traces for training without changing fixed evaluation work."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import capture_split, write
from scripts.train_workflow_work_distribution import export, split_rows


def disjoint_training_rows(original, additional, calibration, evaluation):
    """An added project/task/execution must be absent from both held-out groups."""
    if not additional:
        raise ValueError("additional complete training executions are required")
    for field in ("workflow", "source_workflow_sha256", "project", "task_template"):
        incoming = {row[field] for row in additional}
        if incoming & {row[field] for row in original + calibration + evaluation}:
            raise ValueError("additional training " + field + " overlaps an existing split")
    capture_split(original + additional, calibration, evaluation, require_capture_disjoint=False)
    return original + additional


def diverse_task_training_rows(original, additional, calibration, evaluation):
    """Add different original tasks within training projects, never within holdout projects."""
    from collections import Counter

    if not original or not additional:
        raise ValueError("task diversification requires complete original and additional executions")
    training_projects = {row["project"] for row in original}
    held_out_projects = {row["project"] for row in calibration + evaluation}
    incoming_projects = {row["project"] for row in additional}
    if (
        training_projects & held_out_projects or not incoming_projects <= training_projects
        or incoming_projects & held_out_projects
    ):
        raise ValueError("new tasks must stay within existing training projects and exclude holdouts")
    for field in ("workflow", "source_workflow_sha256", "task_template"):
        if {row[field] for row in additional} & {
            row[field] for row in original + calibration + evaluation
        }:
            raise ValueError("additional original task " + field + " overlaps an existing execution")
    initial = Counter(row["workflow"] for row in additional if row["snapshot"] == 0)
    if set(initial) != {row["workflow"] for row in additional} or any(
        value != 1 for value in initial.values()
    ):
        raise ValueError("every new original task needs exactly one complete execution initial state")
    identities = {}
    for row in additional:
        fields = tuple(row[name] for name in (
            "project", "task_template", "source_capture", "source_workflow_sha256",
        ))
        if row["workflow"] in identities and identities[row["workflow"]] != fields:
            raise ValueError("new original task ownership changes within its complete execution")
        identities[row["workflow"]] = fields
    for field in (1, 3):
        values = [identity[field] for identity in identities.values()]
        if len(set(values)) != len(values):
            raise ValueError("new original tasks contain duplicate task or execution identities")
    capture_split(original + additional, calibration, evaluation, require_capture_disjoint=False)
    return original + additional


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "cohort", "additional-labels", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous complete-source export attempts")
    inputs = {str(path.resolve()): sha256_file(path) for path in (
        args.dataset, args.cohort, args.additional_labels,
    )}
    dataset = json.loads(args.dataset.read_bytes())
    cohort = json.loads(args.cohort.read_bytes())
    train, calibration, evaluation, split = split_rows(dataset["rows"], cohort["measured_workflow_ids"])
    labels = json.loads(args.additional_labels.read_bytes())
    composition = Path(labels["source_binding"]["source_arm"]) / "composite_source.json"
    declaration = json.loads(composition.read_bytes())
    used_projects = {row["project"] for row in dataset["rows"]}
    used_execution = {row["source_workflow_sha256"] for row in dataset["rows"]}
    additional = [
        row for row in declaration["selected_workflows"]
        if row["project"] not in used_projects and row["event_sha256"] not in used_execution
    ]
    if not additional:
        raise ValueError("no independent original project remains for training")
    write(args.output_dir / "plan.json", {
        "selected": additional, "original_split": split,
        "selection_uses_work_duration_JCT_or_prediction_outcomes": False,
        "selection": "all_declared_complete_original_projects_absent_from_the_existing_dataset",
        "inputs_sha256": inputs, "GPU_action": False,
    })
    exported = export(
        args.additional_labels, workflow_ids=[row["workflow_id"] for row in additional],
    )
    expanded = disjoint_training_rows(train, exported["rows"], calibration, evaluation)
    expanded_split = capture_split(expanded, calibration, evaluation, require_capture_disjoint=False)
    exported["training_extension"] = {
        "original_dataset": str(args.dataset.resolve()),
        "original_dataset_sha256": inputs[str(args.dataset.resolve())],
        "original_split": split, "expanded_split": expanded_split,
        "training_only": True, "original_thresholds_must_be_preserved": True,
        "original_training_workflows": len({row["workflow"] for row in train}),
        "expanded_training_workflows": len({row["workflow"] for row in expanded}),
        "calibration_rows_sha256": hashlib.sha256(json.dumps(calibration, sort_keys=True).encode()).hexdigest(),
        "evaluation_rows_sha256": hashlib.sha256(json.dumps(evaluation, sort_keys=True).encode()).hexdigest(),
    }
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("an extension export input changed")
    write(args.output_dir / "dataset.json", exported)
    print(json.dumps({
        "new_training_workflows": len(additional),
        "training": exported["training_extension"]["expanded_training_workflows"],
        "calibration": len({row["workflow"] for row in calibration}),
        "development": len({row["workflow"] for row in evaluation}),
        "new_snapshots": len(exported["rows"]), "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
