#!/usr/bin/env python3
"""Freeze train-only Qwen3.5 native-reactive batches from an existing split."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from collections.abc import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beliefkv.experiments.p6_collection import load_collection_batch
from beliefkv.experiments.p6_split import load_split_manifest, resolve_split


def freeze_native_reactive_train_plan(
    source: Path, output: Path, *, join_batch_id: str | None = None
) -> dict:
    source = source.resolve()
    original = source.read_bytes()
    plan = json.loads(original)
    if (
        not isinstance(plan, dict) or plan.get("frozen") is not True
        or plan.get("runtime_policy") != "frozen_p5_observed"
        or plan.get("predictor_enabled") is not False
        or plan.get("predictive_actions_enabled") is not False
    ):
        raise ValueError("source must be a frozen, predictor-off P5 plan")
    train = [
        item for item in plan.get("batches", ())
        if isinstance(item, dict) and item.get("split") == "train"
    ]
    if not train or len({item.get("batch_id") for item in train}) != len(train):
        raise ValueError("source must have uniquely identified train batches")
    if join_batch_id is not None and join_batch_id not in {
        item["batch_id"] for item in train
    }:
        raise ValueError("JOIN batch must be in the frozen train split")
    for batch in train:
        load_collection_batch(source, batch["batch_id"])
    instance_ids = {
        str(instance_id)
        for batch in train
        for instance_id in batch.get("instance_ids", ())
    }
    projects = {
        str(project) for batch in train
        for project in batch.get("projects", ())
    }
    native = {
        **plan,
        "plan_id": (
            "qwen35-native-reactive-v0520-v2"
            if join_batch_id is not None
            else "qwen35-native-reactive-v0520-v1"
        ),
        "runtime_policy": "frozen_native_reactive_v0520",
        "source_plan_sha256": hashlib.sha256(original).hexdigest(),
        "source_plan": str(source),
        "batches": [
            {
                **item,
                "policy": "frozen_native_reactive_v0520",
                **(
                    {"subagent_fanout_profile": "native_subagent_2to3"}
                    if item["batch_id"] == join_batch_id
                    else {}
                ),
            }
            for item in train
        ],
        "batch_count": len(train),
        "unique_task_count": len(instance_ids),
        "repository_count": len(projects),
        "workflow_count": sum(int(item["workflow_count"]) for item in train),
        "unique_task_counts_by_split": {"train": len(instance_ids)},
        "workflow_counts_by_split": {
            "train": sum(int(item["workflow_count"]) for item in train)
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(native, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return native


def freeze_high_pressure_join_train_plan(
    source: Path,
    additional: Sequence[Path],
    split_path: Path,
    output: Path,
    workload_output: Path,
    *,
    locally_available_images: set[str] | None = None,
) -> dict:
    source = source.resolve()
    split_path = split_path.resolve()
    original = source.read_bytes()
    plan = json.loads(original)
    if (
        plan.get("frozen") is not True
        or plan.get("runtime_policy") != "frozen_p5_observed"
        or plan.get("predictor_enabled") is not False
        or plan.get("predictive_actions_enabled") is not False
    ):
        raise ValueError("source must be a frozen predictor-off P5 plan")
    frozen_split = load_split_manifest(split_path)
    batches = [item for item in plan["batches"] if item["split"] == "train"]
    anchor = next(
        item for item in batches if item["batch_id"] == "p6-017-train-mixed-r0"
    )
    ordered = [anchor] + sorted(
        (item for item in batches if item is not anchor),
        key=lambda item: (int(item.get("rollout_index", 0)), item["batch_id"]),
    )
    sources = [(Path(batch["workload_manifest"]).resolve(), batch) for batch in ordered]
    for plan_path in additional:
        supplemental = json.loads(plan_path.resolve().read_text(encoding="utf-8"))
        if supplemental.get("frozen") is not True:
            raise ValueError("supplemental train plan must be frozen")
        sources.extend(
            (Path(batch["workload_manifest"]).resolve(), batch)
            for batch in supplemental["batches"]
            if batch.get("split") == "train"
        )
    unique: dict[str, dict] = {}
    digests: dict[str, str] = {}
    for path, batch in sources:
        if batch in ordered:
            load_collection_batch(source, batch["batch_id"])
        content = path.read_bytes()
        digests[str(path)] = hashlib.sha256(content).hexdigest()
        if (batch.get("workload_manifest_sha256") is not None
                and batch["workload_manifest_sha256"] != digests[str(path)]):
            raise ValueError(f"source workload manifest changed: {path}")
        manifest = json.loads(content)
        if manifest.get("split") != "train":
            raise ValueError(f"non-train workload manifest: {path}")
        for item in manifest["workloads"]:
            if resolve_split(
                frozen_split,
                dataset=manifest["dataset"],
                project=item["repo"],
                instance_id=item["instance_id"],
                base_commit=item["base_commit"],
            ) != "train":
                raise ValueError(f"non-train task: {item['instance_id']}")
            if locally_available_images is not None and (
                item.get("docker_image") not in locally_available_images
            ):
                continue
            if not item.get("docker_image") or not item.get("source_repo"):
                continue
            unique.setdefault(item["instance_id"], item)
    if len(unique) < 64:
        raise ValueError(f"64 distinct train instances required; found {len(unique)}")
    selected = list(unique.values())[:64]
    manifest = {
        "schema_version": 2,
        "dataset": frozen_split["dataset"],
        "dataset_revision": frozen_split["dataset_revision"],
        "split": "train",
        "split_manifest": str(split_path),
        "selection_policy": "first eight p6-017 train tasks, then distinct frozen train tasks",
        "rollout_index": 0,
        "workload_count": 64,
        "source_workload_manifest_sha256": digests,
        "local_image_selection": locally_available_images is not None,
        "workloads": selected,
    }
    batch = {
        **anchor,
        "batch_id": "qwen35-native-join64-train-r0",
        "instance_ids": [item["instance_id"] for item in selected],
        "docker_images": sorted({item["docker_image"] for item in selected}),
        "projects": sorted({item["repo"] for item in selected}),
        "source_repositories": sorted(
            {
                (item["repo"], item["source_repo"])
                for item in selected
            }
        ),
        "versions": sorted({
            f"{item['repo']}:{item['version']}" for item in selected
            if "version" in item
        }),
        "policy": "frozen_native_reactive_v0520",
        "subagent_fanout_profile": "native_subagent_2to3",
        "workflow_count": 64,
        "concurrency": 64,
        "saturated_root_backlog": True,
        "workload_manifest": str(workload_output.resolve()),
    }
    batch["source_repositories"] = [
        {"project": project, "path": path}
        for project, path in batch["source_repositories"]
    ]
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    batch["workload_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    native = {
        **plan,
        "plan_id": "qwen35-native-reactive-v0520-v2",
        "runtime_policy": "frozen_native_reactive_v0520",
        "source_plan": str(source),
        "source_plan_sha256": hashlib.sha256(original).hexdigest(),
        "batches": [batch],
        "batch_count": 1,
        "workflow_count": 64,
        "unique_task_count": 64,
        "repeat_rollouts": 1,
        "unique_task_counts_by_split": {"train": 64},
        "workflow_counts_by_split": {"train": 64},
    }
    workload_output.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with workload_output.open("xb") as stream:
        stream.write(manifest_bytes)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(native, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    load_collection_batch(output, batch["batch_id"])
    return native


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--join-batch-id", help="Require native SPAWN/JOIN in one train batch")
    parser.add_argument("--additional-train-plan", type=Path, action="append")
    parser.add_argument("--require-local-images", action="store_true")
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--workload-output", type=Path)
    args = parser.parse_args()
    if args.additional_train_plan:
        if args.join_batch_id or not args.split_manifest or not args.workload_output:
            parser.error("64-root plan requires --split-manifest and --workload-output")
        images = (
            set(subprocess.check_output(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                text=True,
            ).splitlines())
            if args.require_local_images else None
        )
        plan = freeze_high_pressure_join_train_plan(
            args.source, args.additional_train_plan, args.split_manifest,
            args.output, args.workload_output,
            locally_available_images=images,
        )
    else:
        plan = freeze_native_reactive_train_plan(
            args.source, args.output, join_batch_id=args.join_batch_id
        )
    print(json.dumps({
        "plan_id": plan["plan_id"],
        "batch_count": plan["batch_count"],
        "workflow_count": plan["workflow_count"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
