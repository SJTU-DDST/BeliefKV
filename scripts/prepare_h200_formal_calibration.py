#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for path in (REPOSITORY_ROOT, REPOSITORY_ROOT / "third_party/SWE-bench"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from beliefkv.experiments.p6_split import load_split_manifest, resolve_split
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.swebench_images import swebench_instance_image


CALIBRATION_PROJECTS = ("astropy/astropy", "sphinx-doc/sphinx")
SHARD_SPECS = (
    ("h200-calibration-01-parallel-r0", "parallel_analysis_2to3"),
    ("h200-calibration-02-natural-r0", "natural"),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze the 16-workflow H200 BF16 calibration collection."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--selection-seed", default="beliefkv-h200-bf16-formal-calibration-v1"
    )
    return parser.parse_args()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _rank(seed: str, scope: str, row: dict[str, Any]) -> str:
    identity = (
        f"{seed}|{scope}|{row['repo']}|{row['version']}|{row['instance_id']}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def select_version_diverse_rows(
    rows: Iterable[dict[str, Any]],
    *,
    count: int,
    seed: str,
    project: str,
) -> list[dict[str, Any]]:
    """Select a fixed, version-diverse project sample without outcome filtering."""

    candidates = [dict(row) for row in rows if str(row["repo"]) == project]
    if len(candidates) < count:
        raise ValueError(f"{project} has {len(candidates)} rows; {count} required")
    by_version: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_version[str(row["version"])].append(row)
    for version in by_version:
        by_version[version].sort(key=lambda row: _rank(seed, project, row))
    version_order = sorted(
        by_version,
        key=lambda version: hashlib.sha256(
            f"{seed}|{project}|version|{version}".encode("utf-8")
        ).hexdigest(),
    )
    selected: list[dict[str, Any]] = []
    while len(selected) < count:
        progressed = False
        for version in version_order:
            bucket = by_version[version]
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            progressed = True
            if len(selected) == count:
                break
        if not progressed:
            raise AssertionError("version-diverse selection exhausted unexpectedly")
    return sorted(selected, key=lambda row: _rank(seed, "selected", row))


def partition_calibration_rows(
    selected_by_project: dict[str, list[dict[str, Any]]],
    *,
    seed: str,
) -> list[list[dict[str, Any]]]:
    shards = [[], []]
    for project in CALIBRATION_PROJECTS:
        selected = list(selected_by_project[project])
        if len(selected) != 8:
            raise ValueError(f"{project} must contribute exactly eight tasks")
        ordered = sorted(selected, key=lambda row: _rank(seed, "partition", row))
        shards[0].extend(ordered[::2])
        shards[1].extend(ordered[1::2])
    for index, rows in enumerate(shards):
        if len(rows) != 8:
            raise AssertionError(f"calibration shard {index} does not contain 8 tasks")
        counts = defaultdict(int)
        for row in rows:
            counts[str(row["repo"])] += 1
        if any(counts[project] != 4 for project in CALIBRATION_PROJECTS):
            raise AssertionError("each calibration shard must contain 4+4 projects")
        rows.sort(key=lambda row: _rank(seed, f"shard-{index}", row))
    return shards


def _load_dataset(path: Path) -> tuple[Any, Path]:
    from datasets import load_from_disk

    path = path.expanduser().resolve()
    try:
        dataset = load_from_disk(str(path))
    except FileNotFoundError:
        path = path / "test"
        dataset = load_from_disk(str(path))
    if hasattr(dataset, "values"):
        values = list(dataset.values())
        if len(values) != 1:
            raise ValueError("dataset must contain exactly one split")
        dataset = values[0]
    return dataset, path


def _workload(row: dict[str, Any], source_root: Path) -> dict[str, Any]:
    project = str(row["repo"])
    source = source_root / project.replace("/", "__")
    if not (source / ".git").exists():
        raise ValueError(f"source repository is unavailable: {source}")
    return {
        "instance_id": str(row["instance_id"]),
        "repo": project,
        "base_commit": str(row["base_commit"]),
        "problem_statement": str(row["problem_statement"]),
        "difficulty": str(row.get("difficulty") or "unknown"),
        "version": str(row["version"]),
        "rollout_index": 0,
        "source_repo": str(source),
        "docker_image": swebench_instance_image(str(row["instance_id"])),
        "preflight_command": None,
    }


def main() -> int:
    args = _parse_args()
    split_path = args.split_manifest.expanduser().resolve()
    split = load_split_manifest(split_path)
    dataset, dataset_path = _load_dataset(args.dataset_dir)
    source_root = args.source_root.expanduser().resolve()
    runtime_profile = args.runtime_profile.expanduser().resolve()

    calibration_rows: list[dict[str, Any]] = []
    for raw in dataset.to_list():
        row = dict(raw)
        if resolve_split(
            split,
            dataset=str(split["dataset"]),
            project=str(row["repo"]),
            instance_id=str(row["instance_id"]),
            base_commit=str(row["base_commit"]),
        ) == "calibration":
            calibration_rows.append(row)
    projects = {str(row["repo"]) for row in calibration_rows}
    if projects != set(CALIBRATION_PROJECTS):
        raise ValueError(f"unexpected calibration projects: {sorted(projects)}")

    selected_by_project = {
        project: select_version_diverse_rows(
            calibration_rows,
            count=8,
            seed=args.selection_seed,
            project=project,
        )
        for project in CALIBRATION_PROJECTS
    }
    shard_rows = partition_calibration_rows(
        selected_by_project, seed=args.selection_seed
    )

    output_dir = args.output_dir.expanduser().resolve()
    manifest_dir = output_dir / "workload_manifests"
    image_requirements: dict[str, dict[str, Any]] = {}
    batches: list[dict[str, Any]] = []
    for (batch_id, fanout_profile), rows in zip(SHARD_SPECS, shard_rows):
        workloads = [_workload(row, source_root) for row in rows]
        manifest_path = manifest_dir / f"{batch_id}.json"
        _write_json(
            manifest_path,
            {
                "schema_version": 2,
                "dataset": split["dataset"],
                "dataset_revision": split["dataset_revision"],
                "split_manifest": str(split_path),
                "split": "calibration",
                "selection_policy": (
                    "fixed-seed version-diverse selection; four tasks per frozen "
                    "calibration project in each shard; no outcome filtering"
                ),
                "selection_seed": args.selection_seed,
                "rollout_index": 0,
                "subagent_fanout_profile": fanout_profile,
                "workloads": workloads,
            },
        )
        for item in workloads:
            image_requirements[item["docker_image"]] = {
                "image": item["docker_image"],
                "instance_id": item["instance_id"],
                "repo_digest": None,
                "image_id": None,
                "status": "required_not_locked",
            }
        batches.append(
            {
                "batch_id": batch_id,
                "split": "calibration",
                "projects": list(CALIBRATION_PROJECTS),
                "versions": sorted(
                    {f"{item['repo']}:{item['version']}" for item in workloads}
                ),
                "rollout_index": 0,
                "workflow_count": len(workloads),
                "instance_ids": [item["instance_id"] for item in workloads],
                "workload_manifest": str(manifest_path),
                "workload_manifest_sha256": sha256_file(manifest_path),
                "source_repositories": [
                    {
                        "project": project,
                        "path": str(source_root / project.replace("/", "__")),
                        "url": f"https://github.com/{project}.git",
                    }
                    for project in CALIBRATION_PROJECTS
                ],
                "docker_images": sorted(
                    {item["docker_image"] for item in workloads}
                ),
                "concurrency": 8,
                "subagent_fanout_profile": fanout_profile,
                "workflow_arrival_interval_ms": 500,
                "workflow_arrival_batch_size": 4,
                "workflow_arrival_batch_interval_ms": 20000,
                "predictive_actions": False,
                "policy": "frozen_p5_observed",
                "preflight_command": None,
            }
        )

    instance_ids = [item for batch in batches for item in batch["instance_ids"]]
    if len(instance_ids) != 16 or len(set(instance_ids)) != 16:
        raise AssertionError("formal calibration must contain 16 unique tasks")
    profile_sha = sha256_file(runtime_profile)
    plan = {
        "schema_version": 1,
        "plan_id": "h200-bf16-formal-calibration-v1",
        "frozen": True,
        "development_only": False,
        "dataset": split["dataset"],
        "dataset_revision": split["dataset_revision"],
        "dataset_path": str(dataset_path),
        "split_manifest": str(split_path),
        "split_manifest_sha256": sha256_file(split_path),
        "source_root": str(source_root),
        "runtime_profile": str(runtime_profile),
        "runtime_profile_sha256": profile_sha,
        "selection_seed": args.selection_seed,
        "selection_contract": {
            "split": "calibration",
            "workflow_count": 16,
            "project_quotas": {project: 8 for project in CALIBRATION_PROJECTS},
            "parallel_workflows": 8,
            "natural_workflows": 8,
            "model_output_filtering": False,
            "fit_or_model_selection_use": False,
            "test_id_accessed": False,
        },
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_p5_observed",
        "unique_task_count": 16,
        "workflow_count": 16,
        "repository_count": 2,
        "batch_count": len(batches),
        "batches": batches,
    }
    _write_json(output_dir / "collection_plan.json", plan)
    _write_json(
        output_dir / "image_requirements.json",
        {
            "schema_version": 1,
            "plan_id": plan["plan_id"],
            "runtime_profile_sha256": profile_sha,
            "lock_state": "requirements_only",
            "image_count": len(image_requirements),
            "images": [image_requirements[key] for key in sorted(image_requirements)],
        },
    )
    print(
        json.dumps(
            {
                "plan_id": plan["plan_id"],
                "workflow_count": plan["workflow_count"],
                "projects": list(CALIBRATION_PROJECTS),
                "batches": [
                    {
                        "batch_id": batch["batch_id"],
                        "fanout": batch["subagent_fanout_profile"],
                        "instance_ids": batch["instance_ids"],
                    }
                    for batch in batches
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
