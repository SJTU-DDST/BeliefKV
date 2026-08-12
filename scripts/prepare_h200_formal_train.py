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


SHARD_SPECS = (
    ("h200-train-01-parallel-r0", "parallel_analysis_2to3", None),
    (
        "h200-train-02-parallel-r0",
        "parallel_analysis_2to3",
        {
            "django/django": 8,
            "pydata/xarray": 3,
            "pytest-dev/pytest": 2,
            "pylint-dev/pylint": 1,
            "psf/requests": 1,
            "pallets/flask": 1,
        },
    ),
    (
        "h200-train-03-natural-r0",
        "natural",
        {
            "django/django": 6,
            "pydata/xarray": 4,
            "pytest-dev/pytest": 3,
            "pylint-dev/pylint": 2,
            "psf/requests": 1,
        },
    ),
    (
        "h200-train-04-natural-r0",
        "natural",
        {
            "django/django": 6,
            "pydata/xarray": 3,
            "pytest-dev/pytest": 3,
            "pylint-dev/pylint": 2,
            "psf/requests": 2,
        },
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze the 64-workflow H200 BF16 formal train collection."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--development-plan", type=Path, required=True)
    parser.add_argument("--prefrozen-extension-plan", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--selection-seed", default="beliefkv-h200-bf16-formal-train-v1"
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


def _instance_ids(plan: dict[str, Any]) -> set[str]:
    return {
        str(instance_id)
        for batch in plan.get("batches", ())
        for instance_id in batch.get("instance_ids", ())
    }


def _rank(seed: str, shard_id: str, row: dict[str, Any]) -> str:
    identity = (
        f"{seed}|{shard_id}|{row['repo']}|{row['version']}|"
        f"{row['instance_id']}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _select_quota(
    rows: Iterable[dict[str, Any]],
    *,
    quotas: dict[str, int],
    excluded: set[str],
    seed: str,
    shard_id: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["repo"] in quotas and row["instance_id"] not in excluded:
            grouped[str(row["repo"])].append(dict(row))

    selected: list[dict[str, Any]] = []
    for project, quota in quotas.items():
        ordered = sorted(
            grouped.get(project, ()),
            key=lambda row: (
                _rank(seed, shard_id, row),
                str(row["version"]),
                str(row["instance_id"]),
            ),
        )
        if len(ordered) < quota:
            raise ValueError(
                f"{shard_id}: project {project} has {len(ordered)} candidates; "
                f"{quota} required"
            )
        diverse: list[dict[str, Any]] = []
        versions: set[str] = set()
        for row in ordered:
            version = str(row["version"])
            if version not in versions:
                diverse.append(row)
                versions.add(version)
            if len(diverse) == quota:
                break
        chosen = {str(row["instance_id"]) for row in diverse}
        diverse.extend(
            row for row in ordered if str(row["instance_id"]) not in chosen
        )
        selected.extend(diverse[:quota])
    selected.sort(key=lambda row: _rank(seed, shard_id, row))
    return selected


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
    development_path = args.development_plan.expanduser().resolve()
    extension_path = args.prefrozen_extension_plan.expanduser().resolve()
    development = json.loads(development_path.read_text(encoding="utf-8"))
    extension = json.loads(extension_path.read_text(encoding="utf-8"))

    rows_by_id: dict[str, dict[str, Any]] = {}
    train_rows: list[dict[str, Any]] = []
    for raw in dataset.to_list():
        row = dict(raw)
        instance_id = str(row["instance_id"])
        rows_by_id[instance_id] = row
        if resolve_split(
            split,
            dataset=str(split["dataset"]),
            project=str(row["repo"]),
            instance_id=instance_id,
            base_commit=str(row["base_commit"]),
        ) == "train":
            train_rows.append(row)

    development_ids = _instance_ids(development)
    extension_ids = _instance_ids(extension) - development_ids
    if len(extension_ids) != 16:
        raise ValueError(
            "prefrozen extension must contribute exactly 16 non-development tasks; "
            f"found {len(extension_ids)}"
        )
    if not extension_ids.issubset(rows_by_id):
        raise ValueError("prefrozen extension contains tasks absent from the dataset")

    output_dir = args.output_dir.expanduser().resolve()
    manifest_dir = output_dir / "workload_manifests"
    selected_ids = set(development_ids)
    batches: list[dict[str, Any]] = []
    image_requirements: dict[str, dict[str, Any]] = {}

    for shard_id, fanout_profile, quotas in SHARD_SPECS:
        if quotas is None:
            selected_rows = sorted(
                (rows_by_id[instance_id] for instance_id in extension_ids),
                key=lambda row: _rank(args.selection_seed, shard_id, row),
            )
        else:
            selected_rows = _select_quota(
                train_rows,
                quotas=quotas,
                excluded=selected_ids | extension_ids,
                seed=args.selection_seed,
                shard_id=shard_id,
            )
        if len(selected_rows) != 16:
            raise AssertionError(f"{shard_id} selected {len(selected_rows)} tasks")
        selected_ids.update(str(row["instance_id"]) for row in selected_rows)
        workloads = [_workload(row, source_root) for row in selected_rows]
        manifest_path = manifest_dir / f"{shard_id}.json"
        _write_json(
            manifest_path,
            {
                "schema_version": 2,
                "dataset": split["dataset"],
                "dataset_revision": split["dataset_revision"],
                "split_manifest": str(split_path),
                "split": "train",
                "selection_policy": (
                    "pre-frozen H200 extension followed by fixed-seed, fixed-quota "
                    "selection; excludes H200 development gate; no outcome filtering"
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
        projects = sorted({item["repo"] for item in workloads})
        batches.append(
            {
                "batch_id": shard_id,
                "split": "train",
                "projects": projects,
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
                    for project in projects
                ],
                "docker_images": sorted(
                    {item["docker_image"] for item in workloads}
                ),
                "concurrency": 16,
                "subagent_fanout_profile": fanout_profile,
                "workflow_arrival_interval_ms": 500,
                "workflow_arrival_batch_size": 8,
                "workflow_arrival_batch_interval_ms": 20000,
                "predictive_actions": False,
                "policy": "frozen_p5_observed",
                "preflight_command": None,
            }
        )

    formal_ids = [instance_id for batch in batches for instance_id in batch["instance_ids"]]
    if len(formal_ids) != 64 or len(set(formal_ids)) != 64:
        raise AssertionError("formal train collection must contain 64 unique tasks")
    if development_ids.intersection(formal_ids):
        raise AssertionError("formal train collection contains a development task")

    profile_sha = sha256_file(runtime_profile)
    plan = {
        "schema_version": 1,
        "plan_id": "h200-bf16-formal-train-v1",
        "frozen": True,
        "development_only": False,
        "dataset": split["dataset"],
        "dataset_revision": split["dataset_revision"],
        "dataset_path": str(dataset_path),
        "split_manifest": str(split_path),
        "split_manifest_sha256": sha256_file(split_path),
        "development_exclusion_plan": str(development_path),
        "development_exclusion_plan_sha256": sha256_file(development_path),
        "prefrozen_extension_plan": str(extension_path),
        "prefrozen_extension_plan_sha256": sha256_file(extension_path),
        "source_root": str(source_root),
        "runtime_profile": str(runtime_profile),
        "runtime_profile_sha256": profile_sha,
        "selection_seed": args.selection_seed,
        "selection_contract": {
            "split": "train",
            "workflow_count": 64,
            "shard_size": 16,
            "parallel_workflows": 32,
            "natural_workflows": 32,
            "development_tasks_excluded": len(development_ids),
            "model_output_filtering": False,
            "failed_task_replacement": "same pre-frozen instance only",
        },
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_p5_observed",
        "unique_task_count": 64,
        "workflow_count": 64,
        "repository_count": len(
            {str(rows_by_id[instance_id]["repo"]) for instance_id in formal_ids}
        ),
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
                "repository_count": plan["repository_count"],
                "batches": [
                    {
                        "batch_id": batch["batch_id"],
                        "fanout": batch["subagent_fanout_profile"],
                        "workflow_count": batch["workflow_count"],
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
