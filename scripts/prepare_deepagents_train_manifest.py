#!/usr/bin/env python3
"""Freeze unused SWE-bench train tasks for a mixed-project child-timing run."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

from beliefkv.experiments.swebench_source import verify_workload_source_objects
if __package__:
    from scripts.prepare_deepagents_swebench_manifest import git_output, sha256
else:
    from prepare_deepagents_swebench_manifest import git_output, sha256


def parse_quota(values: list[str]) -> dict[str, int]:
    quotas = {}
    for value in values:
        repo, sep, raw_count = value.partition("=")
        if not sep or repo in quotas:
            raise ValueError(f"invalid or duplicate project quota: {value}")
        count = int(raw_count)
        if count <= 0:
            raise ValueError(f"project quota must be positive: {value}")
        quotas[repo] = count
    if not quotas:
        raise ValueError("at least one project quota is required")
    return quotas


def selected_workloads(
    split: dict, rows: dict[str, dict], source_root: Path,
    quotas: dict[str, int], excluded: set[str],
) -> list[dict]:
    train = {
        project["project"]: project["tasks"]
        for project in split["projects"] if project["split"] == "train"
    }
    if set(quotas) - set(train):
        raise ValueError(f"quota outside frozen train projects: {set(quotas) - set(train)}")
    selected = []
    for repo, count in quotas.items():
        source = (source_root / repo.replace("/", "__")).resolve()
        if not (source / ".git").exists():
            raise FileNotFoundError(f"source repository is absent: {source}")
        available = [
            task for task in train[repo]
            if task["instance_id"] not in excluded
        ]
        if len(available) < count:
            raise ValueError(f"{repo} has only {len(available)} unused train tasks")
        for task in available[:count]:
            instance_id = task["instance_id"]
            row = rows[instance_id]
            if row["repo"] != repo or str(row["base_commit"]) != task["base_commit"]:
                raise ValueError(f"frozen split and Arrow disagree: {instance_id}")
            namespace, separator, issue = instance_id.partition("__")
            if not separator or namespace != repo.split("/")[0]:
                raise ValueError(f"invalid SWE-bench instance ID: {instance_id}")
            selected.append({
                "instance_id": instance_id,
                "repo": repo,
                "base_commit": str(row["base_commit"]),
                "difficulty": str(row.get("difficulty") or "unknown"),
                "problem_statement": str(row["problem_statement"]),
                "source_repo": str(source),
                "docker_image": (
                    f"swebench/sweb.eval.x86_64.{namespace}_1776_{issue}:latest"
                ),
            })
    verify_workload_source_objects(
        (Path(item["source_repo"]), item["base_commit"]) for item in selected
    )
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arrow", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--quota", action="append", default=[], required=True)
    parser.add_argument("--exclude-workflows", type=Path, action="append", default=[])
    parser.add_argument("--require-cached-images", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(output)
    quotas = parse_quota(args.quota)
    split_path = args.split_manifest.expanduser().resolve()
    split = json.loads(split_path.read_text(encoding="utf-8"))
    arrow = args.arrow.expanduser().resolve()
    table = ipc.open_stream(pa.memory_map(str(arrow), "r")).read_all()
    rows = {str(row["instance_id"]): row for row in table.to_pylist()}
    excluded = set()
    exclusion_sources = {}
    for root in args.exclude_workflows:
        root = root.expanduser().resolve()
        paths = sorted(root.glob("*/runtime_events.deepagents.jsonl"))
        if not paths:
            raise FileNotFoundError(f"no workflow events to exclude: {root}")
        excluded.update(path.parent.name for path in paths)
        exclusion_sources[str(root)] = len(paths)
    workloads = selected_workloads(
        split, rows, args.source_root.expanduser().resolve(), quotas, excluded,
    )
    if args.require_cached_images:
        for image in {item["docker_image"] for item in workloads}:
            subprocess.run(
                ["docker", "image", "inspect", image],
                check=True, capture_output=True, timeout=30,
            )
    sources = {item["source_repo"] for item in workloads}
    payload = {
        "schema_version": 1,
        "dataset": str(split["dataset"]),
        "dataset_revision": str(split["dataset_revision"]),
        "split": "train",
        "split_manifest": str(split_path),
        "split_manifest_sha256": sha256(split_path),
        "arrow_path": str(arrow),
        "arrow_sha256": sha256(arrow),
        "source_heads": {
            source: git_output(Path(source), "rev-parse", "HEAD")
            for source in sorted(sources)
        },
        "selection_policy": "first unused task per frozen train project order",
        "project_quotas": quotas,
        "excluded_workflow_roots": exclusion_sources,
        "gold_fields_exposed": False,
        "workloads": workloads,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps({
        "output": str(output),
        "manifest_sha256": sha256(output),
        "workload_count": len(workloads),
        "project_quotas": quotas,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
