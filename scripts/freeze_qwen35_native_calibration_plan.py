#!/usr/bin/env python3
"""Freeze project-disjoint Qwen3.5 native calibration tasks and arrival plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.p6_split import load_split_manifest, resolve_split
from beliefkv.experiments.swebench_images import swebench_instance_image


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def freeze_calibration_plan(
    dataset_dir: Path, split_path: Path, source_root: Path,
    workload_path: Path, plan_path: Path,
) -> dict:
    from datasets import load_from_disk

    split_path = split_path.resolve()
    dataset_dir = dataset_dir.resolve()
    source_root = source_root.resolve()
    split = load_split_manifest(split_path)
    if not (dataset_dir / "dataset_info.json").is_file() and not (
        dataset_dir / "dataset_dict.json"
    ).is_file():
        dataset_dir /= "test"
    dataset = load_from_disk(str(dataset_dir))
    if hasattr(dataset, "values"):
        datasets = list(dataset.values())
        if len(datasets) != 1:
            raise ValueError("expected one SWE-bench Verified dataset split")
        dataset = datasets[0]
    rows = []
    for row in dataset.to_list():
        if resolve_split(
            split, dataset=split["dataset"], project=row["repo"],
            instance_id=row["instance_id"], base_commit=row["base_commit"],
        ) == "calibration":
            rows.append(row)
    rows.sort(key=lambda row: (row["repo"], row["instance_id"]))
    projects = sorted({row["repo"] for row in rows})
    frozen_projects = {
        item["project"]: item["task_count"]
        for item in split["projects"] if item["split"] == "calibration"
    }
    if {project: sum(row["repo"] == project for row in rows) for project in projects} != frozen_projects:
        raise ValueError("calibration rows do not match the frozen project split")
    if len(rows) != 66 or len({row["instance_id"] for row in rows}) != 66:
        raise ValueError("expected 66 unique frozen calibration tasks")
    workloads = []
    for row in rows:
        source = source_root / row["repo"].replace("/", "__")
        if not (source / ".git").exists():
            raise ValueError(f"missing calibration source repository: {source}")
        workloads.append({
            "instance_id": row["instance_id"],
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "problem_statement": row["problem_statement"],
            "difficulty": row.get("difficulty") or "unknown",
            "version": row["version"],
            "rollout_index": 0,
            "source_repo": str(source),
            "docker_image": swebench_instance_image(row["instance_id"]),
            "preflight_command": None,
        })
    workload_path = workload_path.resolve()
    plan_path = plan_path.resolve()
    if workload_path.exists() or plan_path.exists():
        raise FileExistsError("refusing to overwrite frozen calibration evidence")
    _write_new(workload_path, {
        "schema_version": 2,
        "dataset": split["dataset"],
        "dataset_revision": split["dataset_revision"],
        "split_manifest": str(split_path),
        "split": "calibration",
        "selection_policy": "all frozen calibration projects and tasks, no outcome filtering",
        "rollout_index": 0,
        "workload_count": len(workloads),
        "workloads": workloads,
        "arrival_schedule": {
            "mode": "two_waves",
            "wave_sizes": [33, 33],
            "second_wave_delay_s": 60.0,
            "server_instances": 1,
        },
    })
    batch = {
        "batch_id": "qwen35-native-reactive-calibration-66root-r0",
        "split": "calibration",
        "projects": projects,
        "versions": sorted({f"{row['repo']}:{row['version']}" for row in rows}),
        "rollout_index": 0,
        "workflow_count": len(rows),
        "instance_ids": [row["instance_id"] for row in rows],
        "workload_manifest": str(workload_path),
        "workload_manifest_sha256": _digest(workload_path),
        "source_repositories": [
            {"project": project, "path": str(source_root / project.replace("/", "__"))}
            for project in projects
        ],
        "docker_images": sorted({item["docker_image"] for item in workloads}),
        "concurrency": 66,
        "subagent_fanout_profile": "native_dynamic_1to4",
        "workflow_arrival_interval_ms": 0.0,
        "workflow_arrival_batch_size": 33,
        "workflow_arrival_batch_interval_ms": 60000.0,
        "saturated_root_backlog": False,
        "predictive_actions": False,
        "semantic_gate_stop_after_first_join": False,
        "policy": "frozen_native_reactive_v0520",
        "preflight_command": None,
    }
    plan = {
        "schema_version": 1,
        "plan_id": "qwen35-native-reactive-v0520-v1-calibration-66root",
        "frozen": True,
        "dataset": split["dataset"],
        "dataset_revision": split["dataset_revision"],
        "dataset_path": str(dataset_dir),
        "split_manifest": str(split_path),
        "split_manifest_sha256": _digest(split_path),
        "source_plan": str(split_path),
        "source_plan_sha256": _digest(split_path),
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_native_reactive_v0520",
        "arrival_contract": {
            "root_count": 66,
            "client_inflight": 66,
            "server_max_running_requests": 48,
            "server_instances": 1,
            "waves": [
                {"wave": 1, "root_count": 33, "offset_seconds": 0},
                {"wave": 2, "root_count": 33, "offset_seconds": 60},
            ],
        },
        "selection_contract": {
            "split": "calibration",
            "workflow_count": 66,
            "project_quotas": frozen_projects,
            "model_output_filtering": False,
            "fit_or_model_selection_use": False,
            "test_id_accessed": False,
        },
        "unique_task_count": 66,
        "workflow_count": 66,
        "repository_count": len(projects),
        "batch_count": 1,
        "batches": [batch],
    }
    _write_new(plan_path, plan)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = freeze_calibration_plan(
        args.dataset_dir, args.split_manifest, args.source_root,
        args.workload, args.plan,
    )
    print(f"Frozen {plan['workflow_count']} calibration tasks: {args.plan}")


if __name__ == "__main__":
    main()
