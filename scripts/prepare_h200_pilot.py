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
SWEBENCH_ROOT = REPOSITORY_ROOT / "third_party/SWE-bench"
for path in (REPOSITORY_ROOT, SWEBENCH_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from beliefkv.experiments.p6_split import load_split_manifest, resolve_split
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.swebench_images import swebench_instance_image


DEFAULT_PROJECTS = (
    "django/django",
    "pydata/xarray",
    "pylint-dev/pylint",
    "pytest-dev/pytest",
)
FANOUT_PROFILES = ("natural", "parallel_analysis_2to3")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze the train-only H200 BF16 observed-policy pilot."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--exclude-collection-plan", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--selection-seed", default="beliefkv-h200-bf16-pilot-v1")
    parser.add_argument("--project", action="append", dest="projects")
    parser.add_argument("--tasks-per-project-per-arm", type=int, default=2)
    return parser.parse_args()


def _rank(seed: str, profile: str, row: dict[str, Any]) -> str:
    identity = (
        f"{seed}|{profile}|{row['repo']}|{row['version']}|"
        f"{row['instance_id']}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _excluded_instances(collection_plan: Path) -> set[str]:
    plan = json.loads(collection_plan.read_text(encoding="utf-8"))
    excluded: set[str] = set()
    for batch in plan.get("batches", ()):
        excluded.update(str(item) for item in batch.get("instance_ids", ()))
    if not excluded:
        raise ValueError("exclusion collection contains no instance IDs")
    return excluded


def _select_rows(
    rows: Iterable[dict[str, Any]],
    *,
    projects: tuple[str, ...],
    excluded: set[str],
    seed: str,
    tasks_per_project_per_arm: int,
) -> dict[str, list[dict[str, Any]]]:
    if tasks_per_project_per_arm <= 0:
        raise ValueError("tasks_per_project_per_arm must be positive")
    required = tasks_per_project_per_arm * len(FANOUT_PROFILES)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        project = str(raw["repo"])
        instance_id = str(raw["instance_id"])
        if project in projects and instance_id not in excluded:
            grouped[project].append(dict(raw))

    selected = {profile: [] for profile in FANOUT_PROFILES}
    for project in projects:
        candidates = grouped.get(project, [])
        if len(candidates) < required:
            raise ValueError(
                f"project {project} has {len(candidates)} unused train tasks; "
                f"{required} are required"
            )
        ordered = sorted(
            candidates,
            key=lambda row: (
                _rank(seed, "all", row),
                str(row["version"]),
                str(row["instance_id"]),
            ),
        )
        chosen: list[dict[str, Any]] = []
        used_versions: set[str] = set()
        for row in ordered:
            version = str(row["version"])
            if version not in used_versions:
                chosen.append(row)
                used_versions.add(version)
            if len(chosen) == required:
                break
        if len(chosen) < required:
            seen = {str(row["instance_id"]) for row in chosen}
            chosen.extend(
                row
                for row in ordered
                if str(row["instance_id"]) not in seen
            )
            chosen = chosen[:required]
        distributed = sorted(chosen, key=lambda row: _rank(seed, "arm", row))
        for index, row in enumerate(distributed):
            profile = FANOUT_PROFILES[index % len(FANOUT_PROFILES)]
            selected[profile].append(row)
    for profile in FANOUT_PROFILES:
        selected[profile].sort(
            key=lambda row: (str(row["repo"]), str(row["instance_id"]))
        )
    identities = [
        str(row["instance_id"])
        for profile in FANOUT_PROFILES
        for row in selected[profile]
    ]
    if len(identities) != len(set(identities)):
        raise AssertionError("pilot selection contains duplicate instances")
    return selected


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    args = _parse_args()
    try:
        from datasets import load_from_disk
    except ImportError as error:
        raise SystemExit("the datasets package is required") from error

    projects = tuple(args.projects or DEFAULT_PROJECTS)
    if len(projects) < 4 or len(set(projects)) != len(projects):
        raise ValueError("the pilot requires at least four distinct projects")
    split_path = args.split_manifest.expanduser().resolve()
    split = load_split_manifest(split_path)
    dataset_path = args.dataset_dir.expanduser().resolve()
    try:
        dataset = load_from_disk(str(dataset_path))
    except FileNotFoundError:
        split_path_candidate = dataset_path / "test"
        dataset = load_from_disk(str(split_path_candidate))
        dataset_path = split_path_candidate
    if hasattr(dataset, "values"):
        values = list(dataset.values())
        if len(values) != 1:
            raise ValueError("pilot dataset must contain exactly one split")
        dataset = values[0]
    dataset_rows = dataset.to_list()
    train_rows: list[dict[str, Any]] = []
    for raw in dataset_rows:
        project = str(raw["repo"])
        if project not in projects:
            continue
        resolved = resolve_split(
            split,
            dataset=str(split["dataset"]),
            project=project,
            instance_id=str(raw["instance_id"]),
            base_commit=str(raw["base_commit"]),
        )
        if resolved == "train":
            train_rows.append(dict(raw))

    excluded_path = args.exclude_collection_plan.expanduser().resolve()
    excluded = _excluded_instances(excluded_path)
    selected = _select_rows(
        train_rows,
        projects=projects,
        excluded=excluded,
        seed=args.selection_seed,
        tasks_per_project_per_arm=args.tasks_per_project_per_arm,
    )
    source_root = args.source_root.expanduser().resolve()
    runtime_profile = args.runtime_profile.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    manifests = output_dir / "workload_manifests"
    batches: list[dict[str, Any]] = []
    image_requirements: dict[str, dict[str, Any]] = {}

    for arm_index, profile in enumerate(FANOUT_PROFILES, start=1):
        workloads: list[dict[str, Any]] = []
        for row in selected[profile]:
            project = str(row["repo"])
            source = source_root / project.replace("/", "__")
            if not (source / ".git").exists():
                raise ValueError(f"source repository is unavailable: {source}")
            image = swebench_instance_image(str(row["instance_id"]))
            image_requirements[image] = {
                "image": image,
                "instance_id": str(row["instance_id"]),
                "repo_digest": None,
                "image_id": None,
                "status": "required_not_pulled",
            }
            workloads.append(
                {
                    "instance_id": str(row["instance_id"]),
                    "repo": project,
                    "base_commit": str(row["base_commit"]),
                    "problem_statement": str(row["problem_statement"]),
                    "difficulty": str(row.get("difficulty") or "unknown"),
                    "version": str(row["version"]),
                    "rollout_index": 0,
                    "source_repo": str(source),
                    "docker_image": image,
                    "preflight_command": None,
                }
            )

        batch_id = f"h200-pilot-{arm_index:02d}-{profile}-r0"
        manifest_path = manifests / f"{batch_id}.json"
        workload_manifest = {
            "schema_version": 2,
            "dataset": split["dataset"],
            "dataset_revision": split["dataset_revision"],
            "split_manifest": str(split_path),
            "split": "train",
            "selection_policy": (
                "train-only, excludes every collection_v4 task, fixed-seed "
                "project/version stratification; no model-output filtering"
            ),
            "selection_seed": args.selection_seed,
            "rollout_index": 0,
            "subagent_fanout_profile": profile,
            "workloads": workloads,
        }
        _write_json(manifest_path, workload_manifest)
        batches.append(
            {
                "batch_id": batch_id,
                "split": "train",
                "projects": sorted({item["repo"] for item in workloads}),
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
                    for project in sorted({item["repo"] for item in workloads})
                ],
                "docker_images": sorted(
                    {item["docker_image"] for item in workloads}
                ),
                "concurrency": len(workloads),
                "subagent_fanout_profile": profile,
                "workflow_arrival_interval_ms": 1000,
                "predictive_actions": False,
                "policy": "frozen_p5_observed",
                "preflight_command": None,
            }
        )

    profile_sha = sha256_file(runtime_profile)
    plan = {
        "schema_version": 1,
        "plan_id": "h200-bf16-observed-pilot-v1",
        "frozen": True,
        "development_only": True,
        "dataset": split["dataset"],
        "dataset_revision": split["dataset_revision"],
        "dataset_path": str(dataset_path),
        "split_manifest": str(split_path),
        "split_manifest_sha256": sha256_file(split_path),
        "excluded_collection_plan": str(excluded_path),
        "excluded_collection_plan_sha256": sha256_file(excluded_path),
        "source_root": str(source_root),
        "runtime_profile": str(runtime_profile),
        "runtime_profile_sha256": profile_sha,
        "selection_seed": args.selection_seed,
        "selection_contract": {
            "split": "train",
            "excluded_prior_unique_tasks": len(excluded),
            "project_count": len(projects),
            "projects": list(projects),
            "tasks_per_project_per_arm": args.tasks_per_project_per_arm,
            "fanout_profiles": list(FANOUT_PROFILES),
            "model_output_filtering": False,
        },
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_p5_observed",
        "unique_task_count": sum(len(rows) for rows in selected.values()),
        "workflow_count": sum(len(rows) for rows in selected.values()),
        "repository_count": len(projects),
        "batch_count": len(batches),
        "batches": batches,
    }
    _write_json(output_dir / "collection_plan.json", plan)
    requirement = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "runtime_profile_sha256": profile_sha,
        "lock_state": "requirements_only",
        "image_count": len(image_requirements),
        "images": [
            image_requirements[key] for key in sorted(image_requirements)
        ],
    }
    _write_json(output_dir / "image_requirements.json", requirement)
    print(
        json.dumps(
            {
                "plan": str(output_dir / "collection_plan.json"),
                "workflow_count": plan["workflow_count"],
                "projects": list(projects),
                "batches": [
                    {
                        "batch_id": item["batch_id"],
                        "fanout": item["subagent_fanout_profile"],
                        "workflows": item["workflow_count"],
                    }
                    for item in batches
                ],
                "image_count": len(image_requirements),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
