#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SWEBENCH_ROOT = REPOSITORY_ROOT / "third_party/SWE-bench"
for path in (REPOSITORY_ROOT, SWEBENCH_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from beliefkv.experiments.p6_split import load_split_manifest, resolve_split
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.swebench_images import swebench_instance_image


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extend a frozen H200 pressure workload with unused train tasks."
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--base-workload-manifest", type=Path, required=True)
    parser.add_argument(
        "--exclude-collection-plan", type=Path, action="append", required=True
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--selection-seed", required=True)
    parser.add_argument(
        "--quota",
        action="append",
        required=True,
        help="Per-project quota in repository=count form.",
    )
    parser.add_argument("--arrival-interval-ms", type=int, default=250)
    return parser.parse_args()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_quotas(values: list[str]) -> dict[str, int]:
    quotas: dict[str, int] = {}
    for value in values:
        project, separator, raw_count = value.rpartition("=")
        if not separator or not project:
            raise ValueError(f"invalid quota {value!r}; expected repository=count")
        count = int(raw_count)
        if count <= 0 or project in quotas:
            raise ValueError(f"invalid or duplicate quota {value!r}")
        quotas[project] = count
    return quotas


def _excluded_instances(paths: list[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for batch in payload.get("batches", ()):
            excluded.update(str(value) for value in batch.get("instance_ids", ()))
    return excluded


def _rank(seed: str, row: dict[str, Any]) -> str:
    identity = (
        f"{seed}|{row['repo']}|{row['version']}|{row['instance_id']}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _select(
    rows: list[dict[str, Any]],
    *,
    quotas: dict[str, int],
    excluded: set[str],
    seed: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        project = str(row["repo"])
        if project in quotas and str(row["instance_id"]) not in excluded:
            grouped[project].append(dict(row))

    selected: list[dict[str, Any]] = []
    for project, quota in quotas.items():
        ordered = sorted(
            grouped.get(project, ()),
            key=lambda row: (_rank(seed, row), str(row["instance_id"])),
        )
        if len(ordered) < quota:
            raise ValueError(
                f"project {project} has {len(ordered)} unused train tasks; {quota} required"
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
        chosen_ids = {str(row["instance_id"]) for row in diverse}
        diverse.extend(
            row for row in ordered if str(row["instance_id"]) not in chosen_ids
        )
        selected.extend(diverse[:quota])
    selected.sort(key=lambda row: (_rank(seed, row), str(row["instance_id"])))
    return selected


def main() -> int:
    args = _parse_args()
    from datasets import load_from_disk

    quotas = _parse_quotas(args.quota)
    split_path = args.split_manifest.expanduser().resolve()
    split = load_split_manifest(split_path)
    dataset_path = args.dataset_dir.expanduser().resolve()
    try:
        dataset = load_from_disk(str(dataset_path))
    except FileNotFoundError:
        dataset_path = dataset_path / "test"
        dataset = load_from_disk(str(dataset_path))
    if hasattr(dataset, "values"):
        values = list(dataset.values())
        if len(values) != 1:
            raise ValueError("dataset must contain exactly one split")
        dataset = values[0]

    train_rows: list[dict[str, Any]] = []
    for row in dataset.to_list():
        project = str(row["repo"])
        if project not in quotas:
            continue
        if resolve_split(
            split,
            dataset=str(split["dataset"]),
            project=project,
            instance_id=str(row["instance_id"]),
            base_commit=str(row["base_commit"]),
        ) == "train":
            train_rows.append(dict(row))

    exclusion_paths = [path.expanduser().resolve() for path in args.exclude_collection_plan]
    excluded = _excluded_instances(exclusion_paths)
    selected = _select(
        train_rows, quotas=quotas, excluded=excluded, seed=args.selection_seed
    )
    base_path = args.base_workload_manifest.expanduser().resolve()
    base = json.loads(base_path.read_text(encoding="utf-8"))
    source_root = args.source_root.expanduser().resolve()
    workloads = [dict(row) for row in base["workloads"]]
    for row in selected:
        project = str(row["repo"])
        source = source_root / project.replace("/", "__")
        if not (source / ".git").exists():
            raise ValueError(f"source repository is unavailable: {source}")
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
                "docker_image": swebench_instance_image(str(row["instance_id"])),
                "preflight_command": None,
            }
        )
    identities = [str(row["instance_id"]) for row in workloads]
    if len(identities) != len(set(identities)):
        raise AssertionError("combined pressure workload contains duplicate instances")

    output_dir = args.output_dir.expanduser().resolve()
    batch_id = "h200-pressure-02-parallel32-r0"
    manifest_path = output_dir / "workload_manifests" / f"{batch_id}.json"
    workload_manifest = {
        "schema_version": 2,
        "dataset": split["dataset"],
        "dataset_revision": split["dataset_revision"],
        "split_manifest": str(split_path),
        "split": "train",
        "selection_policy": (
            "16 frozen H200 pressure-v1 tasks plus 16 fixed-seed, fixed-quota "
            "unused train tasks; no outcome or model-output filtering"
        ),
        "selection_seed": args.selection_seed,
        "rollout_index": 0,
        "subagent_fanout_profile": "parallel_analysis_2to3",
        "workloads": workloads,
    }
    _write_json(manifest_path, workload_manifest)

    runtime_profile = args.runtime_profile.expanduser().resolve()
    projects = sorted({str(row["repo"]) for row in workloads})
    batch = {
        "batch_id": batch_id,
        "split": "train",
        "projects": projects,
        "versions": sorted(
            {f"{row['repo']}:{row['version']}" for row in workloads}
        ),
        "rollout_index": 0,
        "workflow_count": len(workloads),
        "instance_ids": identities,
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
        "docker_images": sorted({str(row["docker_image"]) for row in workloads}),
        "concurrency": len(workloads),
        "subagent_fanout_profile": "parallel_analysis_2to3",
        "workflow_arrival_interval_ms": args.arrival_interval_ms,
        "predictive_actions": False,
        "policy": "frozen_p5_observed",
        "preflight_command": None,
    }
    plan = {
        "schema_version": 1,
        "plan_id": "h200-bf16-observed-pressure-v2",
        "frozen": True,
        "development_only": True,
        "dataset": split["dataset"],
        "dataset_revision": split["dataset_revision"],
        "dataset_path": str(dataset_path),
        "split_manifest": str(split_path),
        "split_manifest_sha256": sha256_file(split_path),
        "excluded_collection_plans": [str(path) for path in exclusion_paths],
        "excluded_collection_plan_sha256": [sha256_file(path) for path in exclusion_paths],
        "base_workload_manifest": str(base_path),
        "base_workload_manifest_sha256": sha256_file(base_path),
        "source_root": str(source_root),
        "runtime_profile": str(runtime_profile),
        "runtime_profile_sha256": sha256_file(runtime_profile),
        "selection_seed": args.selection_seed,
        "selection_contract": {
            "split": "train",
            "base_workflow_count": len(base["workloads"]),
            "extension_quotas": quotas,
            "excluded_prior_unique_tasks": len(excluded),
            "model_output_filtering": False,
        },
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_p5_observed",
        "unique_task_count": len(workloads),
        "workflow_count": len(workloads),
        "repository_count": len(projects),
        "batch_count": 1,
        "batches": [batch],
    }
    _write_json(output_dir / "collection_plan.json", plan)
    requirement = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "runtime_profile_sha256": plan["runtime_profile_sha256"],
        "lock_state": "requirements_only",
        "image_count": len(batch["docker_images"]),
        "images": [
            {
                "image": image,
                "instance_id": next(
                    str(row["instance_id"])
                    for row in workloads
                    if str(row["docker_image"]) == image
                ),
                "repo_digest": None,
                "image_id": None,
                "status": "required_not_pulled",
            }
            for image in batch["docker_images"]
        ],
    }
    _write_json(output_dir / "image_requirements.json", requirement)
    print(
        json.dumps(
            {
                "plan": str(output_dir / "collection_plan.json"),
                "workflow_manifest": str(manifest_path),
                "workflow_count": len(workloads),
                "extension_count": len(selected),
                "projects": projects,
                "images": len(batch["docker_images"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
